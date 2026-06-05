"""Render the bundled .claude scoring commands/workflow into a consumer repo,
substituting concrete paths from the loaded config. Claude Code reads Markdown /
JS directly (it doesn't parse your YAML), so we bake the paths in at init time.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_ROOT = os.path.join(_HERE, "claude_templates")

# Relative paths under claude_templates/ that get rendered into <dest>/.claude/.
_FILES = [
    "commands/reimport.md",
    "commands/reclassify.md",
    "commands/score-issue.md",
    "workflows/score-issues.js",
]


def _substitutions(config, config_path):
    # The sentry-rice binary next to the interpreter running init-claude — i.e. the
    # user's venv. Baked in so the rendered commands/workflow don't depend on PATH
    # (Workflow sub-agents in particular don't inherit an activated venv).
    rice_bin = os.path.join(os.path.dirname(sys.executable), "sentry-rice")
    if not os.path.exists(rice_bin):
        rice_bin = "sentry-rice"   # fall back to PATH if not found next to python
    fallback = "/absolute/path/to/your/repo"
    codebase_map = {
        proj["name"]: proj["codebase_path"] or fallback
        for proj in config.sentry.projects.values()
    }
    return {
        "__PROJECT_NAME__": config.project.name,
        "__CODEBASE_MAP__": json.dumps(codebase_map),
        "__RUBRIC_PATH__": config.rubric_path(),
        "__CONFIG_PATH__": os.path.abspath(config_path),
        "__DB_PATH__": config.db_path,
        "__RICE_BIN__": rice_bin,
    }


def render_claude_templates(config, config_path, dest):
    """Write rendered .claude files under `dest`. Returns the list written."""
    subs = _substitutions(config, config_path)
    written = []
    for rel in _FILES:
        with open(os.path.join(_TEMPLATE_ROOT, rel)) as f:
            text = f.read()
        for key, val in subs.items():
            text = text.replace(key, val)
        out_path = os.path.join(dest, ".claude", rel)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write(text)
        written.append(out_path)
    return written
