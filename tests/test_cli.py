import json
import os

from sentryrice.claude import render_claude_templates
from sentryrice.cli import main


def test_render_claude_templates_substitutes_paths(config, config_path, tmp_path):
    dest = tmp_path / "consumer"
    written = render_claude_templates(config, config_path, str(dest))
    # All four files land under <dest>/.claude/.
    rel = {os.path.relpath(p, str(dest)) for p in written}
    assert rel == {
        ".claude/commands/reimport.md",
        ".claude/commands/reclassify.md",
        ".claude/commands/score-issue.md",
        ".claude/workflows/score-issues.js",
    }
    workflow = (dest / ".claude/workflows/score-issues.js").read_text()
    # Placeholders are gone; concrete paths from the config are baked in.
    assert "__CODEBASE_PATH__" not in workflow and "__RUBRIC_PATH__" not in workflow
    assert "__RICE_BIN__" not in workflow
    assert "/tmp/fake-repo" in workflow            # codebase_path from the fixture config
    assert config.rubric_path() in workflow
    # The sentry-rice binary path is baked in (not a bare PATH-dependent command).
    reimport = (dest / ".claude/commands/reimport.md").read_text()
    assert "sentry-rice" in reimport and "__RICE_BIN__" not in reimport


def test_init_scaffolds_and_does_not_clobber(tmp_path):
    from sentryrice.scaffold import init_project

    written, skipped = init_project(str(tmp_path))
    assert {os.path.basename(p) for p in written} == {"config.yaml", "rubric.md"}
    assert (tmp_path / "config.yaml").exists() and (tmp_path / "rubric.md").exists()
    assert "sentry:" in (tmp_path / "config.yaml").read_text()

    # Re-running skips existing files (no clobber)…
    written2, skipped2 = init_project(str(tmp_path))
    assert written2 == [] and len(skipped2) == 2
    # …unless --force.
    written3, _ = init_project(str(tmp_path), force=True)
    assert len(written3) == 2


def test_cli_upsert_missing_field_exits_nonzero(config_path, tmp_path, monkeypatch, capsys):
    """upsert with a payload missing a required field exits non-zero and prints a
    clear message to stderr — no raw traceback."""
    monkeypatch.setenv("RICE_DB_PATH", str(tmp_path / "rice.db"))
    main(["--config", config_path, "initdb"])

    bad = {
        "sentry_id": "CLI-BAD", "title": "boom", "url": "u",
        # missing: confidence, effort, impact_category
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))

    import pytest
    with pytest.raises(SystemExit) as exc_info:
        main(["--config", config_path, "upsert", str(p)])
    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "confidence" in err or "effort" in err or "impact_category" in err
    assert "Traceback" not in err


def test_cli_upsert_malformed_json_exits_nonzero(config_path, tmp_path, monkeypatch, capsys):
    """upsert with malformed JSON exits non-zero with a clear stderr message."""
    monkeypatch.setenv("RICE_DB_PATH", str(tmp_path / "rice.db"))
    main(["--config", config_path, "initdb"])

    p = tmp_path / "broken.json"
    p.write_text("{not valid json}")

    import pytest
    with pytest.raises(SystemExit) as exc_info:
        main(["--config", config_path, "upsert", str(p)])
    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "JSON" in err or "json" in err
    assert "Traceback" not in err


def test_cli_initdb_then_upsert_roundtrips(config_path, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RICE_DB_PATH", str(tmp_path / "rice.db"))
    main(["--config", config_path, "initdb"])

    score = {
        "sentry_id": "CLI-1", "title": "boom", "url": "u",
        "environment": "production", "app": "api",
        "last_seen": "2026-06-01T00:00:00+00:00",
        "user_count": 10, "event_count": 10,
        "impact_category": "billing", "confidence": 7, "effort": 2,
    }
    p = tmp_path / "score.json"
    p.write_text(json.dumps(score))
    main(["--config", config_path, "upsert", str(p)])

    out = capsys.readouterr().out
    assert "Upserted CLI-1" in out and "RICE:" in out
