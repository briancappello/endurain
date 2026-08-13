#!/usr/bin/env python3
"""Real-DB tests for activities_crud.get_activity_raw_by_id.

The /raw builder must return the RAW per-activity ``_hide_*`` settings
(None=inherit / True / False) DIRECTLY, never the resolved hybrid value. It is
owner-only: a 404 for a non-owner user_id or a missing activity id.

These run through the real ORM against real Postgres (see conftest.py) so the
raw-column read and the owner filter are proven end to end.

Run:  build/app/.venv/bin/python -m pytest tests/test_activity_raw_builder.py -q
"""

import datetime

import pytest


def _make_user(db, globals_map=None, tag="t"):
    """Insert a minimal user + its privacy row. globals_map overrides globals.

    ``tag`` keeps username/email unique so a test can create multiple users.
    """
    from users.users.models import Users
    from users.users_privacy_settings.models import UsersPrivacySettings

    u = Users(
        name=tag, username=tag, email=f"{tag}@t", password="x",
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
        user_id=user_id, name="act", distance=0, activity_type=1, start_time=now,
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


def test_raw_builder_echoes_raw_values(db):
    """None stays None (inherit), True/False echoed -- never resolved."""
    import activities.activity.crud as crud

    # Global would resolve hide_hr to True, but raw is None -> must stay None.
    u = _make_user(db, {"hide_activity_hr": True})
    a = _make_activity(
        db, u.id, {"hide_hr": None, "hide_laps": True, "hide_speed": False}
    )

    raw = crud.get_activity_raw_by_id(a.id, user_id=u.id, db=db)

    assert raw.hide_hr is None, raw.hide_hr        # inherit, not resolved to True
    assert raw.hide_laps is True, raw.hide_laps
    assert raw.hide_speed is False, raw.hide_speed
    assert raw.id == a.id


def test_raw_builder_404_for_non_owner(db):
    import activities.activity.crud as crud
    from fastapi import HTTPException

    owner = _make_user(db, tag="owner")
    other = _make_user(db, tag="other")
    a = _make_activity(db, owner.id, {"hide_hr": True})

    with pytest.raises(HTTPException) as exc:
        crud.get_activity_raw_by_id(a.id, user_id=other.id, db=db)
    assert exc.value.status_code == 404


def test_raw_builder_404_for_missing_activity(db):
    import activities.activity.crud as crud
    from fastapi import HTTPException

    u = _make_user(db)

    with pytest.raises(HTTPException) as exc:
        crud.get_activity_raw_by_id(999999, user_id=u.id, db=db)
    assert exc.value.status_code == 404
