"""Command-line entrypoint: `sentry-rice <command>`.

Config is discovered from --config, then $SENTRY_RICE_CONFIG, then ./config.yaml.

    sentry-rice init [DEST]            scaffold a starter config.yaml + rubric.md
    sentry-rice initdb                 create/migrate the database
    sentry-rice sync [--days N]        pull Sentry, import new, prune, recompute
    sentry-rice serve [--port 5001]    run the web UI
    sentry-rice dump [--all] [PATH]    write issues to JSON for the scoring fan-out
    sentry-rice recompute              re-derive reach + RICE for all issues
    sentry-rice upsert [PATH]          upsert one scored issue (JSON; stdin if no PATH)
    sentry-rice init-claude [DEST]     render the .claude scoring commands into DEST
"""
import argparse
import json
import os
import sys

from sentryrice.config import load_config


def _resolve_config_path(arg):
    path = arg or os.environ.get("SENTRY_RICE_CONFIG") or "config.yaml"
    if not os.path.exists(path):
        sys.exit(f"config not found: {path}\n"
                 "Pass --config, set SENTRY_RICE_CONFIG, or add ./config.yaml "
                 "(copy config.example.yaml).")
    return path


def _load(args):
    return load_config(_resolve_config_path(args.config))


def cmd_init(args):
    from sentryrice.scaffold import init_project
    written, skipped = init_project(args.dest, force=args.force)
    for p in written:
        print(f"created {p}")
    for p in skipped:
        print(f"skipped {p} (exists — use --force to overwrite)")
    if written:
        print("\nNext: edit config.yaml (sentry.org, projects with codebase_path per project, "
              "categories), then run `sentry-rice sync`.")


def cmd_initdb(args):
    from sentryrice.db import init_db
    cfg = _load(args)
    init_db(cfg)
    print(f"Database initialised at {cfg.db_path}")


def cmd_sync(args):
    from sentryrice.sentry import sync_all
    sync_all(_load(args), days=args.days)


def cmd_serve(args):
    from sentryrice.web import create_app
    cfg = _load(args)
    app = create_app(cfg)
    print(f"Serving {cfg.project.name} on http://127.0.0.1:{args.port}")
    app.run(debug=args.debug, port=args.port)


def cmd_dump(args):
    from sentryrice.store import dump_issues
    rows = dump_issues(_load(args), all_issues=args.all, out_path=args.out)
    kind = "all unresolved" if args.all else "unscored"
    print(f"Wrote {len(rows)} {kind} issues to {args.out}")


def cmd_recompute(args):
    from sentryrice.store import recompute_all
    n = recompute_all(_load(args))
    print(f"Recomputed reach + RICE for {n} scored issues (deterministic).")


def cmd_upsert(args):
    from sentryrice.store import upsert_score
    raw = open(args.path).read() if args.path else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON input — {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        info = upsert_score(_load(args), payload)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Upserted {info['sentry_id']} [{info['category']}/{info['app']}/"
          f"{info['environment']}] — RICE: {info['rice_score']:.2f}")


def cmd_init_claude(args):
    from sentryrice.claude import render_claude_templates
    cfg = _load(args)
    written = render_claude_templates(cfg, _resolve_config_path(args.config), args.dest)
    print(f"Wrote {len(written)} files under {os.path.join(args.dest, '.claude')}:")
    for p in written:
        print(f"  {p}")


def build_parser():
    p = argparse.ArgumentParser(prog="sentry-rice", description="RICE-prioritise your Sentry issues.")
    p.add_argument("--config", help="path to config.yaml (default: $SENTRY_RICE_CONFIG or ./config.yaml)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="scaffold a starter config.yaml + rubric.md")
    s.add_argument("dest", nargs="?", default=".", help="target dir (default: cwd)")
    s.add_argument("--force", action="store_true", help="overwrite existing files")
    s.set_defaults(func=cmd_init)

    sub.add_parser("initdb", help="create/migrate the database").set_defaults(func=cmd_initdb)

    s = sub.add_parser("sync", help="pull Sentry, import, prune, recompute")
    s.add_argument("--days", type=int, default=None, help="window in days (default: config)")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5001)))
    s.add_argument("--debug", action="store_true")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("dump", help="write issues to JSON for the scoring fan-out")
    s.add_argument("out", nargs="?", default="/tmp/unscored.json")
    s.add_argument("--all", action="store_true", help="all unresolved (re-score), not just unscored")
    s.set_defaults(func=cmd_dump)

    sub.add_parser("recompute", help="re-derive reach + RICE").set_defaults(func=cmd_recompute)

    s = sub.add_parser("upsert", help="upsert one scored issue (JSON via PATH or stdin)")
    s.add_argument("path", nargs="?", default=None)
    s.set_defaults(func=cmd_upsert)

    s = sub.add_parser("init-claude", help="render the .claude scoring commands into a repo")
    s.add_argument("dest", nargs="?", default=".", help="target repo dir (default: cwd)")
    s.set_defaults(func=cmd_init_claude)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
