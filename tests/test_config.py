import textwrap

import pytest

from sentryrice.config import load_config


def test_loads_minimal_config(config):
    assert config.project.name == "Test Project"
    assert config.sentry.org == "test-org"
    assert config.sentry.projects == {
        "111": {"name": "api", "codebase_path": "/tmp/fake-repo"},
        "222": {"name": "web", "codebase_path": ""},
    }
    assert config.category_scores()["billing"] == 8
    assert config.thresholds.sync_days == 7
    assert config.thresholds.rice_high == 40 and config.thresholds.rice_medium == 15


def test_derived_accessors(config):
    assert config.cat_meta()["billing"] == ["credit-card", "#FFB454"]
    assert config.env_queries() == [("production", "production"), ("staging", "staging")]
    assert config.is_prod("production") is True
    assert config.is_prod("staging") is False
    assert config.rubric_path().endswith("rubric.md")


def test_defaults_applied_when_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("RICE_DB_PATH", str(tmp_path / "rice.db"))
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        sentry:
          org: o
          projects: { "1": api }
        categories:
          x: { score: 5 }
    """))
    cfg = load_config(str(p))
    # Recency decay + reference floor + rice bands fall back to sane defaults.
    assert cfg.thresholds.reference_floor == 10.0
    assert cfg.thresholds.recency_decay[0] == (3, 1.0)
    assert cfg.thresholds.rice_high == 40.0
    assert cfg.categories["x"].icon == "shapes"   # default icon
    assert cfg.project.name == "My Project"


def test_missing_org_raises(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("sentry:\n  projects: { '1': api }\ncategories:\n  x: { score: 5 }\n")
    with pytest.raises(ValueError, match="sentry.org"):
        load_config(str(p))


def test_missing_categories_raises(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("sentry:\n  org: o\n  projects: { '1': api }\n")
    with pytest.raises(ValueError, match="categories"):
        load_config(str(p))


def test_category_score_out_of_range_raises(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("sentry:\n  org: o\n  projects: { '1': api }\ncategories:\n  x: { score: 99 }\n")
    with pytest.raises(ValueError, match="0–10"):
        load_config(str(p))


def test_relative_db_path_resolves_against_config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("RICE_DB_PATH", raising=False)
    p = tmp_path / "sub" / "c.yaml"
    p.parent.mkdir()
    p.write_text("sentry:\n  org: o\n  projects: { '1': api }\ncategories:\n  x: { score: 5 }\ndb:\n  path: db/rice.db\n")
    cfg = load_config(str(p))
    # Resolved against the config dir (…/sub), not the test's CWD.
    assert cfg.db_path == str(tmp_path / "sub" / "db" / "rice.db")


def test_db_path_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("RICE_DB_PATH", "/tmp/override-rice.db")
    p = tmp_path / "c.yaml"
    p.write_text("sentry:\n  org: o\n  projects: { '1': api }\ncategories:\n  x: { score: 5 }\ndb:\n  path: /tmp/ignored.db\n")
    cfg = load_config(str(p))
    assert cfg.db_path == "/tmp/override-rice.db"
