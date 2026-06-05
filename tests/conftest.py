"""Shared fixtures: a minimal valid config written to a temp dir, plus a Config
and an initialised temp DB built from it."""
import textwrap

import pytest

MINIMAL_YAML = """
project:
  name: "Test Project"
  codebase_path: /tmp/fake-repo
  rubric_file: rubric.md
sentry:
  org: test-org
  projects:
    "111": api
    "222": web
  environments:
    - { query: production, store: production }
    - { query: staging,    store: staging }
  prod_environments: [production, prod]
thresholds:
  sync_days: 7
  min_events: { production: 100, staging: 0 }
  reference_floor: 10
  rice_bands: { high: 40, medium: 15 }
categories:
  billing: { score: 8, icon: credit-card, color: '#FFB454' }
  ui_display: { score: 5, icon: monitor, color: '#A396DA' }
  noise: { score: 1, icon: volume-x, color: '#6E6480' }
"""


@pytest.fixture
def config_path(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(MINIMAL_YAML))
    (tmp_path / "rubric.md").write_text("# Test rubric\nScore it.\n")
    return str(p)


@pytest.fixture
def config(config_path, tmp_path, monkeypatch):
    # Keep the DB inside the tmp dir.
    monkeypatch.setenv("RICE_DB_PATH", str(tmp_path / "rice.db"))
    from sentryrice.config import load_config
    return load_config(config_path)


@pytest.fixture
def initialised_db(config):
    from sentryrice.db import init_db
    init_db(config)
    return config.db_path
