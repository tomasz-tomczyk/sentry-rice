"""Deterministic RICE scoring — NO AI.

Reach is "how many *recent* users an issue affects": env-relative volume on a log
curve × a recency decay. All environments are treated equally; fairness comes
from per-environment references (see db.reference_volumes), not a penalty.

The AI's job is the judgment fields (impact category, confidence, effort,
reasoning, codebase findings). Reach and the final RICE score are computed here.
The recency-decay curve and references are supplied by the caller (from config).
"""
import math
from datetime import datetime, timezone


def recency_factor(last_seen, decay, floor=0.0, now=None):
    """0–1 multiplier from how recently the issue was last seen.

    `decay` is an ordered iterable of (max_age_days, factor); first match wins,
    anything older than the last knot gets `floor`.
    """
    if not last_seen:
        return 0.0
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_days = (now - dt).total_seconds() / 86400.0
    if age_days < 0:
        age_days = 0.0
    for max_age, factor in decay:
        if age_days <= max_age:
            return factor
    return floor


def _log_reach(volume, reference):
    """Map a volume onto 0–10 along a log curve where `reference` ≈ 10."""
    if volume <= 0 or reference <= 0:
        return 0.0
    return min(10.0, math.log10(1 + volume) / math.log10(1 + reference) * 10.0)


def base_reach(user_count, event_count, user_reference, event_reference):
    """Reach (0–10) from raw volume, relative to the env's busiest issue.

    Background jobs (Oban workers, crons) log 0 users, so for those we count
    *events* against the busiest worker instead — each kind on its own yardstick.
    """
    user_count = user_count or 0
    event_count = event_count or 0
    if user_count > 0:
        return _log_reach(user_count, user_reference)
    return _log_reach(event_count, event_reference)


def compute_reach(user_count, event_count, last_seen,
                  user_reference, event_reference, decay, floor=0.0, now=None):
    """Recent reach: env-relative volume × recency, rounded to 0.1."""
    reach = (base_reach(user_count, event_count, user_reference, event_reference)
             * recency_factor(last_seen, decay, floor, now))
    return round(reach, 1)


def compute_rice(reach, impact, confidence, effort):
    return round((reach * impact * confidence) / max(0.5, float(effort)), 2)
