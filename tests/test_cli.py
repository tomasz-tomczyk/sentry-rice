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
    assert "/tmp/fake-repo" in workflow            # codebase_path from the fixture config
    assert config.rubric_path() in workflow


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
