from datetime import datetime, timedelta, timezone

from sentryrice.scoring import (
    recency_factor, base_reach, compute_reach, compute_rice, _log_reach,
)

DECAY = ((3, 1.0), (7, 0.9), (14, 0.6), (30, 0.5), (60, 0.25), (180, 0.1))


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_recency_factor_buckets():
    assert recency_factor(_ago(1), DECAY) == 1.0
    assert recency_factor(_ago(5), DECAY) == 0.9
    assert recency_factor(_ago(25), DECAY) == 0.5
    assert recency_factor(_ago(400), DECAY, floor=0.0) == 0.0
    assert recency_factor(None, DECAY) == 0.0


def test_log_reach_is_relative_to_reference():
    # An issue at the reference volume scores ~10; half the (log) scale still high.
    assert _log_reach(1000, 1000) == 10.0
    assert _log_reach(0, 1000) == 0.0
    # A volume equal to its reference always pins to 10 regardless of magnitude.
    assert _log_reach(50, 50) == 10.0


def test_base_reach_uses_events_when_no_users():
    # 0 users (background job) → measured against the event reference instead.
    assert base_reach(0, 500, user_reference=1000, event_reference=500) == 10.0
    # With users present, the user reference is used.
    assert base_reach(1000, 5, user_reference=1000, event_reference=500) == 10.0


def test_envs_are_equal_via_per_env_references():
    """A staging issue that's big *for staging* matches a prod issue big for prod —
    same reach despite far smaller raw volume, because each uses its own reference."""
    recent = _ago(1)
    prod = compute_reach(900, 0, recent, user_reference=900, event_reference=10, decay=DECAY)
    stg = compute_reach(8, 0, recent, user_reference=8, event_reference=10, decay=DECAY)
    assert prod == stg == 10.0


def test_compute_reach_applies_recency():
    full = compute_reach(900, 0, _ago(1), 900, 10, DECAY)
    faded = compute_reach(900, 0, _ago(25), 900, 10, DECAY)
    assert full == 10.0
    assert faded == 5.0   # 10 * 0.5 recency


def test_compute_rice():
    assert compute_rice(10, 8, 9, 2) == 360.0
    assert compute_rice(5, 8, 7, 0.0) == compute_rice(5, 8, 7, 0.5)  # effort floored at 0.5
