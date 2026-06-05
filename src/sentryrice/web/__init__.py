"""Flask app factory. `create_app(config)` wires the issues table, filtering,
overrides, resolve, disagreements and about pages — every opinionated bit
(categories, colours, prod envs, RICE bands, fix-prompt) comes from `config`.
"""
import sqlite3
from datetime import datetime

from flask import Flask, request, render_template, redirect, url_for, jsonify

from sentryrice.config import Config
from sentryrice.db import connect
from sentryrice.sentry import read_token, resolve_on_sentry

SORTABLE = {"title", "reach", "impact", "confidence", "effort", "rice_score",
            "eff_rice", "last_seen", "user_count"}
OVERRIDE_FIELDS = ("reach", "impact", "confidence", "effort")


class _Safe(dict):
    """str.format_map mapping that yields '' for any missing key, so a custom
    fix-prompt template can't 500 the page by referencing an unknown field."""
    def __missing__(self, key):
        return ""


def _fix_prompt(config: Config, row) -> str:
    fields = _Safe(
        sentry_id=row["sentry_id"], url=row["url"], app=row["app"],
        impact_category=row["impact_category"], impact=round(row["eff_impact"]),
        reach=round(row["eff_reach"], 1), confidence=round(row["eff_confidence"], 1),
        effort=round(row["eff_effort"], 1), user_count=row["user_count"],
        event_count=row["event_count"], last_seen=row["last_seen"],
        reasoning=row["reasoning"] or "(none)",
        code_findings=row["code_findings"] or "(none recorded)",
    )
    try:
        return config.fix_prompt_template.format_map(fields)
    except (ValueError, IndexError):
        return config.fix_prompt_template   # malformed template → show it raw


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    app.config["RICE"] = config
    prod_envs = tuple(e.lower() for e in config.sentry.prod_environments)

    def get_db():
        conn = connect(config.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def wants_json():
        return (request.headers.get("X-Requested-With") == "fetch"
                or "application/json" in request.headers.get("Accept", ""))

    # ── Template filters ────────────────────────────────────────────────────────
    @app.template_filter("score_color")
    def score_color(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return "#A79EB4"
        if v <= 2:
            return "#8B93A0"
        if v <= 4:
            return "#4DC771"
        if v <= 6:
            return "#F5B000"
        if v <= 9:
            return "#FF7738"
        return "#F55459"

    @app.template_filter("commas")
    def commas(n):
        try:
            return f"{int(n):,}"
        except (TypeError, ValueError):
            return n

    @app.template_filter("reltime")
    def reltime(value):
        if not value:
            return "—"
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)[:10]
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        delta = now - dt
        secs = delta.total_seconds()
        if secs < 0:
            return "just now"
        days = delta.days
        if secs < 3600:
            m = int(secs // 60)
            return "just now" if m < 1 else f"{m}m ago"
        if days < 1:
            return "today"
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days}d ago"
        if days < 30:
            return f"{days // 7}w ago"
        if days < 365:
            return f"{days // 30}mo ago"
        return f"{days // 365}yr ago"

    # Presentation shared by every page (category meta, RICE bands, project name).
    @app.context_processor
    def inject_globals():
        return {
            "cat_meta": config.cat_meta(),
            "rice_high": config.thresholds.rice_high,
            "rice_medium": config.thresholds.rice_medium,
            "project_name": config.project.name,
            "prod_envs": prod_envs,
        }

    # ── Routes ──────────────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        sort = request.args.get("sort", "eff_rice")
        order = request.args.get("order", "desc")
        search = request.args.get("q", "").strip()
        min_reach = request.args.get("min_reach", type=float)
        max_reach = request.args.get("max_reach", type=float)
        min_impact = request.args.get("min_impact", type=float)
        max_impact = request.args.get("max_impact", type=float)
        min_rice = request.args.get("min_rice", type=float)
        env = request.args.get("env", "prod")
        app_filter = request.args.get("app", "").strip()
        cat_filter = request.args.get("cat", "").strip()
        new_only = request.args.get("new") in ("1", "true", "on")

        if sort not in SORTABLE:
            sort = "eff_rice"
        if order not in ("asc", "desc"):
            order = "desc"

        where = ["COALESCE(i.status, 'unresolved') <> 'resolved'"]
        params = []
        if search:
            where.append("(i.title LIKE ? OR i.sentry_id LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if env == "prod":
            where.append(f"LOWER(i.environment) IN ({','.join('?' for _ in prod_envs)})")
            params += list(prod_envs)
        elif env and env != "all":
            where.append("i.environment = ?")
            params.append(env)
        if app_filter and app_filter != "all":
            where.append("i.app = ?")
            params.append(app_filter)
        if cat_filter and cat_filter != "all":
            where.append("s.impact_category = ?")
            params.append(cat_filter)
        if new_only:
            where.append("i.first_scan_id = (SELECT MAX(id) FROM scans)")
        if min_reach is not None:
            where.append("COALESCE(ov_r.your_value, s.reach) >= ?"); params.append(min_reach)
        if max_reach is not None:
            where.append("COALESCE(ov_r.your_value, s.reach) <= ?"); params.append(max_reach)
        if min_impact is not None:
            where.append("COALESCE(ov_i.your_value, s.impact) >= ?"); params.append(min_impact)
        if max_impact is not None:
            where.append("COALESCE(ov_i.your_value, s.impact) <= ?"); params.append(max_impact)
        if min_rice is not None:
            where.append("eff_rice >= ?"); params.append(min_rice)

        where_sql = "WHERE " + " AND ".join(where)
        query = f"""
            SELECT i.id, i.sentry_id, i.title, i.url, i.environment, i.app,
                   i.last_seen, i.user_count, i.event_count,
                   CASE WHEN i.first_scan_id = (SELECT MAX(id) FROM scans)
                        THEN 1 ELSE 0 END AS is_new,
                   s.reach, s.impact, s.impact_category, s.confidence, s.effort,
                   s.rice_score, s.reasoning, s.code_findings,
                   COALESCE(ov_r.your_value, s.reach)      AS eff_reach,
                   COALESCE(ov_i.your_value, s.impact)     AS eff_impact,
                   COALESCE(ov_c.your_value, s.confidence) AS eff_confidence,
                   COALESCE(ov_e.your_value, s.effort)     AS eff_effort,
                   (COALESCE(ov_r.your_value, s.reach) *
                    COALESCE(ov_i.your_value, s.impact) *
                    COALESCE(ov_c.your_value, s.confidence)) /
                   MAX(0.5, COALESCE(ov_e.your_value, s.effort)) AS eff_rice
            FROM issues i
            JOIN scores s ON s.issue_id = i.id
            LEFT JOIN overrides ov_r ON ov_r.issue_id = i.id AND ov_r.field = 'reach'
            LEFT JOIN overrides ov_i ON ov_i.issue_id = i.id AND ov_i.field = 'impact'
            LEFT JOIN overrides ov_c ON ov_c.issue_id = i.id AND ov_c.field = 'confidence'
            LEFT JOIN overrides ov_e ON ov_e.issue_id = i.id AND ov_e.field = 'effort'
            {where_sql}
            ORDER BY {sort} {order}
        """
        conn = get_db()
        issues = conn.execute(query, params).fetchall()
        apps = [r[0] for r in conn.execute(
            "SELECT DISTINCT app FROM issues WHERE app IS NOT NULL ORDER BY app").fetchall()]
        environments = [r[0] for r in conn.execute(
            "SELECT DISTINCT environment FROM issues WHERE environment IS NOT NULL "
            "ORDER BY environment").fetchall()]
        category_list = [r[0] for r in conn.execute(
            "SELECT impact_category FROM scores GROUP BY impact_category ORDER BY MAX(impact) DESC"
        ).fetchall()]
        conn.close()

        fix_prompts = {r["id"]: _fix_prompt(config, r) for r in issues}
        template = "_table.html" if request.args.get("partial") else "index.html"
        return render_template(
            template, issues=issues, sort=sort, order=order, search=search, env=env,
            app_filter=app_filter, cat_filter=cat_filter, new_only=new_only,
            environments=environments, apps=apps, category_list=category_list,
            fix_prompts=fix_prompts, filters=request.args,
        )

    @app.route("/issues/<int:issue_id>/override", methods=["POST"])
    def override(issue_id):
        reason = request.form.get("reason", "")
        provided = {}
        for field in OVERRIDE_FIELDS:
            raw = request.form.get(field)
            if raw is None or raw.strip() == "":
                continue
            try:
                value = float(raw)
            except ValueError:
                return f"{field} must be a number", 400
            if not (0 <= value <= 10):
                return f"{field} must be between 0 and 10", 400
            if field == "effort" and value < 0.5:
                return "effort must be at least 0.5", 400
            provided[field] = value

        conn = get_db()
        try:
            score = conn.execute(
                "SELECT reach, impact, confidence, effort FROM scores WHERE issue_id=?",
                (issue_id,)).fetchone()
            if score is None:
                return "Issue not found", 404
            for field, value in provided.items():
                ai_value = score[field]
                if abs(value - ai_value) < 1e-9:
                    conn.execute("DELETE FROM overrides WHERE issue_id=? AND field=?",
                                 (issue_id, field))
                else:
                    conn.execute("""
                        INSERT INTO overrides (issue_id, field, ai_value, your_value, reason)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(issue_id, field) DO UPDATE SET
                            your_value = excluded.your_value, reason = excluded.reason
                    """, (issue_id, field, ai_value, value, reason))
            conn.commit()
            eff = conn.execute("""
                SELECT s.reach, s.impact, s.confidence, s.effort,
                    COALESCE(ov_r.your_value, s.reach)      AS eff_reach,
                    COALESCE(ov_i.your_value, s.impact)     AS eff_impact,
                    COALESCE(ov_c.your_value, s.confidence) AS eff_confidence,
                    COALESCE(ov_e.your_value, s.effort)     AS eff_effort,
                    (COALESCE(ov_r.your_value, s.reach) *
                     COALESCE(ov_i.your_value, s.impact) *
                     COALESCE(ov_c.your_value, s.confidence)) /
                    MAX(0.5, COALESCE(ov_e.your_value, s.effort)) AS eff_rice
                FROM scores s
                LEFT JOIN overrides ov_r ON ov_r.issue_id = s.issue_id AND ov_r.field = 'reach'
                LEFT JOIN overrides ov_i ON ov_i.issue_id = s.issue_id AND ov_i.field = 'impact'
                LEFT JOIN overrides ov_c ON ov_c.issue_id = s.issue_id AND ov_c.field = 'confidence'
                LEFT JOIN overrides ov_e ON ov_e.issue_id = s.issue_id AND ov_e.field = 'effort'
                WHERE s.issue_id = ?
            """, (issue_id,)).fetchone()
        finally:
            conn.close()

        if wants_json():
            return jsonify({k: eff[k] for k in eff.keys()})
        return redirect(url_for("index"))

    @app.route("/issues/<int:issue_id>/resolve", methods=["POST"])
    def resolve_issue(issue_id):
        conn = get_db()
        row = conn.execute("SELECT sentry_id FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None:
            conn.close()
            return "Issue not found", 404
        sentry_id = row["sentry_id"]
        try:
            resolve_on_sentry(config, sentry_id, read_token())
        except Exception as e:  # noqa: BLE001 — surface any failure, keep local row
            conn.close()
            msg = f"Could not resolve {sentry_id} on Sentry: {e}"
            if wants_json():
                return jsonify({"error": msg}), 502
            return msg, 502
        try:
            conn.execute("DELETE FROM overrides WHERE issue_id=?", (issue_id,))
            conn.execute("DELETE FROM scores WHERE issue_id=?", (issue_id,))
            conn.execute("DELETE FROM issues WHERE id=?", (issue_id,))
            conn.commit()
        finally:
            conn.close()
        if wants_json():
            return jsonify({"ok": True, "sentry_id": sentry_id, "id": issue_id})
        return redirect(url_for("index"))

    @app.route("/disagreements")
    def disagreements():
        conn = get_db()
        rows = conn.execute("""
            SELECT i.title, i.url, o.field, o.ai_value, o.your_value, o.reason, o.created_at
            FROM overrides o JOIN issues i ON i.id = o.issue_id
            ORDER BY o.created_at DESC
        """).fetchall()
        conn.close()
        return render_template("disagreements.html", rows=rows)

    @app.route("/about")
    def about():
        categories = sorted(config.category_scores().items(), key=lambda x: -x[1])
        return render_template("about.html", categories=categories)

    return app
