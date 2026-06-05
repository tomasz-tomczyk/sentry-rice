"""Configuration: everything an opinionated deployment would hardcode lives in a
single YAML file and is loaded into a typed `Config` here.

`load_config(path)` reads the YAML, applies generic defaults, validates, and
returns a `Config`. The engine (db, scoring, sentry, store, web) is handed this
object — there are no module-level constants baked to one org/project.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Category:
    """One impact category: its fixed impact score (0–10) and UI presentation."""
    score: float
    icon: str = "shapes"        # a lucide icon name
    color: str = "#9AA0AA"      # hex, used for badges/charts


@dataclass(frozen=True)
class SentryConfig:
    org: str
    region_url: str = "https://us.sentry.io"
    # {sentry_project_id: app_label} — the label tags issues by sub-app.
    projects: dict = field(default_factory=dict)
    # Ordered [(sentry_env_query, stored_env)] — lets several Sentry env spellings
    # (e.g. 'prod' and 'production') fold into one stored env.
    environments: list = field(default_factory=list)
    # Stored envs that count as production (default view, badge styling).
    prod_environments: tuple = ("production", "prod")
    max_pages: int = 40         # pagination safety cap per (project, query)


@dataclass(frozen=True)
class Thresholds:
    sync_days: int = 7
    # Per-stored-env import floor on event_count (envs absent → 0).
    min_events: dict = field(default_factory=dict)
    reference_floor: float = 10.0
    # Recency decay knots [(max_age_days, factor)]; first match wins.
    recency_decay: tuple = ((3, 1.0), (7, 0.9), (14, 0.6), (30, 0.5), (60, 0.25), (180, 0.1))
    recency_floor: float = 0.0
    # RICE colour bands for the table (>= high → red, >= medium → amber, else green).
    rice_high: float = 40.0
    rice_medium: float = 15.0


@dataclass(frozen=True)
class ProjectConfig:
    name: str = "My Project"
    # Absolute path the scoring agents trace into (your codebase). Empty = unset.
    codebase_path: str = ""
    # Scoring rubric (Markdown), resolved relative to the config file's directory.
    rubric_file: str = "rubric.md"


DEFAULT_FIX_PROMPT = (
    "Investigate Sentry issue {sentry_id} ({url}).\n\n"
    "App: {app}. Confirm the root cause in the code and fix it. "
    "Treat the assessment below as a hint and validate it independently:\n"
    "- Category: {impact_category} (impact {impact})\n"
    "- Reach {reach} · Confidence {confidence} · Effort {effort}\n"
    "- Users {user_count} · Events {event_count} · Last seen {last_seen}\n\n"
    "Reasoning:\n{reasoning}\n\nCodebase findings (validate, don't trust):\n{code_findings}"
)


@dataclass(frozen=True)
class Config:
    project: ProjectConfig
    sentry: SentryConfig
    thresholds: Thresholds
    categories: dict            # {name: Category}
    fix_prompt_template: str
    db_path: str
    config_dir: str             # directory of the loaded config file

    # ── Derived accessors used across the engine ────────────────────────────────
    def category_scores(self) -> dict:
        return {name: cat.score for name, cat in self.categories.items()}

    def cat_meta(self) -> dict:
        """{name: [icon, color]} — the shape the templates expect."""
        return {name: [cat.icon, cat.color] for name, cat in self.categories.items()}

    def env_queries(self) -> list:
        """[(sentry_query_env, stored_env)] for the sync."""
        return [(e["query"], e["store"]) for e in self.sentry.environments]

    def is_prod(self, environment) -> bool:
        return (environment or "").strip().lower() in {
            e.lower() for e in self.sentry.prod_environments
        }

    def rubric_path(self) -> str:
        p = Path(self.project.rubric_file)
        return str(p if p.is_absolute() else Path(self.config_dir) / p)


def _require(d: dict, key: str, ctx: str):
    if key not in d or d[key] in (None, ""):
        raise ValueError(f"config: missing required '{ctx}{key}'")
    return d[key]


def load_config(path: str) -> Config:
    """Load and validate a sentry-rice YAML config into a `Config`."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        raise FileNotFoundError(f"config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    config_dir = os.path.dirname(path)

    proj = raw.get("project", {}) or {}
    project = ProjectConfig(
        name=proj.get("name", "My Project"),
        codebase_path=os.path.expanduser(proj.get("codebase_path", "") or ""),
        rubric_file=proj.get("rubric_file", "rubric.md"),
    )

    sraw = raw.get("sentry", {}) or {}
    sentry = SentryConfig(
        org=_require(sraw, "org", "sentry."),
        region_url=sraw.get("region_url", "https://us.sentry.io").rstrip("/"),
        projects={str(k): v for k, v in (sraw.get("projects", {}) or {}).items()},
        environments=list(sraw.get("environments", []) or []),
        prod_environments=tuple(sraw.get("prod_environments", ["production", "prod"])),
        max_pages=int(sraw.get("max_pages", 40)),
    )
    if not sentry.projects:
        raise ValueError("config: 'sentry.projects' must list at least one project id → app")
    for e in sentry.environments:
        if "query" not in e or "store" not in e:
            raise ValueError("config: each sentry.environments entry needs 'query' and 'store'")

    traw = raw.get("thresholds", {}) or {}
    bands = traw.get("rice_bands", {}) or {}
    decay = traw.get("recency_decay")
    thresholds = Thresholds(
        sync_days=int(traw.get("sync_days", 7)),
        min_events={str(k): int(v) for k, v in (traw.get("min_events", {}) or {}).items()},
        reference_floor=float(traw.get("reference_floor", 10.0)),
        recency_decay=tuple(tuple(x) for x in decay) if decay else Thresholds.recency_decay,
        recency_floor=float(traw.get("recency_floor", 0.0)),
        rice_high=float(bands.get("high", 40.0)),
        rice_medium=float(bands.get("medium", 15.0)),
    )

    craw = raw.get("categories", {}) or {}
    if not craw:
        raise ValueError("config: 'categories' must define at least one category")
    categories = {}
    for name, spec in craw.items():
        spec = spec or {}
        score = float(_require(spec, "score", f"categories.{name}."))
        if not (0 <= score <= 10):
            raise ValueError(f"config: categories.{name}.score must be 0–10 (got {score})")
        categories[name] = Category(
            score=score,
            icon=spec.get("icon", "shapes"),
            color=spec.get("color", "#9AA0AA"),
        )

    ui = raw.get("ui", {}) or {}
    fix_prompt = ui.get("fix_prompt_template") or DEFAULT_FIX_PROMPT

    env_db = os.environ.get("RICE_DB_PATH")
    if env_db:
        db_path = os.path.abspath(os.path.expanduser(env_db))
    else:
        configured = (raw.get("db", {}) or {}).get("path")
        db_path = os.path.expanduser(configured) if configured else os.path.join("db", "rice.db")
        # A relative db path is resolved against the config file's directory (like
        # rubric_file), so the CLI works from any working directory.
        if not os.path.isabs(db_path):
            db_path = os.path.join(config_dir, db_path)
        db_path = os.path.abspath(db_path)

    return Config(
        project=project,
        sentry=sentry,
        thresholds=thresholds,
        categories=categories,
        fix_prompt_template=fix_prompt,
        db_path=db_path,
        config_dir=config_dir,
    )
