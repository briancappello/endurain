#!/usr/bin/env python3
"""Real-DB tests for the Activity hide_* inheritance resolver.

Each of the 12 per-activity hide_* flags is a hybrid_property backed by a
nullable ``_hide_X`` column: NULL means "inherit the owner's global privacy
setting", a set value overrides it. These tests exercise that through the real
ORM against a real Postgres (see conftest.py) so the column<->hybrid pairing,
the user_privacy_settings relationship, and selectin loading are all covered
end to end -- the thing a Python-only fake could not prove.

Run:  build/app/.venv/bin/python -m pytest tests/test_hide_flags_resolver.py -v
"""

import datetime

import pytest

# (activity flag, corresponding global column on users_privacy_settings)
FLAGS = [
    ("hide_start_time", "hide_activity_start_time"),
    ("hide_location", "hide_activity_location"),
    ("hide_map", "hide_activity_map"),
    ("hide_hr", "hide_activity_hr"),
    ("hide_power", "hide_activity_power"),
    ("hide_cadence", "hide_activity_cadence"),
    ("hide_elevation", "hide_activity_elevation"),
    ("hide_speed", "hide_activity_speed"),
    ("hide_pace", "hide_activity_pace"),
    ("hide_laps", "hide_activity_laps"),
    ("hide_workout_sets_steps", "hide_activity_workout_sets_steps"),
    ("hide_gear", "hide_activity_gear"),
]


def _make_user(db, globals_map=None):
    """Insert a minimal user + its privacy row. globals_map overrides globals."""
    from users.users.models import Users
    from users.users_privacy_settings.models import UsersPrivacySettings

    u = Users(
        name="t", username="t", email="t@t", password="x",
        preferred_language="en", mfa_enabled=False, email_verified=True,
        pending_admin_approval=False, active=True, gender="0",
        access_type="1", first_day_of_week="1", units="1", currency="1",
    )
    db.add(u)
    db.flush()
    ps = UsersPrivacySettings(
        user_id=u.id, default_activity_visibility="public", **(globals_map or {})
    )
    db.add(ps)
    db.flush()
    return u


def _make_activity(db, user_id, raw_map=None):
    """Insert an activity owned by user_id. raw_map sets _hide_X raw values."""
    from activities.activity.models import Activity

    now = datetime.datetime.now(datetime.timezone.utc)
    a = Activity(
        user_id=user_id, distance=0, activity_type=1, start_time=now,
        end_time=now, created_at=now, visibility=0, total_elapsed_time=0,
        total_timer_time=0, is_hidden=False,
    )
    db.add(a)
    db.flush()
    for flag, value in (raw_map or {}).items():
        setattr(a, flag, value)  # hybrid setter -> writes _hide_X
    db.flush()
    db.refresh(a)
    return a


@pytest.mark.parametrize("flag,global_col", FLAGS)
def test_null_inherits_global_true(db, flag, global_col):
    u = _make_user(db, {global_col: True})
    a = _make_activity(db, u.id, {flag: None})
    assert getattr(a, flag) is True


@pytest.mark.parametrize("flag,global_col", FLAGS)
def test_null_inherits_global_false(db, flag, global_col):
    u = _make_user(db, {global_col: False})
    a = _make_activity(db, u.id, {flag: None})
    assert getattr(a, flag) is False


@pytest.mark.parametrize("flag,global_col", FLAGS)
def test_explicit_true_overrides_global_false(db, flag, global_col):
    u = _make_user(db, {global_col: False})
    a = _make_activity(db, u.id, {flag: True})
    assert getattr(a, flag) is True


@pytest.mark.parametrize("flag,global_col", FLAGS)
def test_explicit_false_overrides_global_true(db, flag, global_col):
    u = _make_user(db, {global_col: True})
    a = _make_activity(db, u.id, {flag: False})
    assert getattr(a, flag) is False


def test_setter_writes_raw_column(db):
    u = _make_user(db)
    a = _make_activity(db, u.id)
    a.hide_hr = None
    db.flush()
    assert a._hide_hr is None
    a.hide_hr = True
    db.flush()
    assert a._hide_hr is True
    a.hide_hr = False
    db.flush()
    assert a._hide_hr is False


def test_missing_privacy_row_raises_loud(db):
    """No fallback: a missing privacy row must fail loud, not silently default.

    Every user is guaranteed a privacy row, so its absence is a broken invariant
    the resolver surfaces rather than papering over.
    """
    from activities.activity.models import Activity
    from users.users.models import Users

    u = Users(
        name="t2", username="t2", email="t2@t", password="x",
        preferred_language="en", mfa_enabled=False, email_verified=True,
        pending_admin_approval=False, active=True, gender="0",
        access_type="1", first_day_of_week="1", units="1", currency="1",
    )
    db.add(u)
    db.flush()
    a = _make_activity(db, u.id, {"hide_hr": None})
    with pytest.raises(AttributeError):
        _ = a.hide_hr  # user_privacy_settings is None -> attribute error
