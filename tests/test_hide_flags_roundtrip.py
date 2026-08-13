#!/usr/bin/env python3
"""Round-trip safety: GET /raw -> PUT edit -> raw values must be unchanged.

The danger this guards: a client GETs an activity via the RESOLVED read (where an
inherited NULL shows as the global's value), edits it, and PUTs it back -- which
would freeze inheritance into an explicit override. The fix is that writes speak
``ActivityRaw`` (raw None/True/False), so re-submitting the raw payload is a
no-op. This test proves that end to end through the real ORM (see conftest.py).

Run:  build/app/.venv/bin/python -m pytest tests/test_hide_flags_roundtrip.py -q
"""

import datetime


def _make_user(db, globals_map=None, tag="rt"):
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


def test_raw_round_trip_preserves_inherit_and_overrides(db):
    """GET /raw then PUT the same body must not change stored raw values.

    Owner's global hide_hr=True; the activity leaves hide_hr NULL (inherit) and
    sets hide_laps=True (explicit override). After re-submitting the /raw payload
    through edit_activity, the raw columns must be byte-for-byte the same: NULL
    stays NULL (NOT frozen to True), the override stays True.
    """
    import activities.activity.crud as crud
    from sqlalchemy import text

    owner = _make_user(db, globals_map={"hide_activity_hr": True})
    act = _make_activity(
        db, owner.id, raw_map={"hide_hr": None, "hide_laps": True, "hide_map": False}
    )

    # GET /raw -- must show the RAW values (hide_hr None, not the resolved True).
    raw = crud.get_activity_raw_by_id(act.id, user_id=owner.id, db=db)
    assert raw.hide_hr is None, raw.hide_hr
    assert raw.hide_laps is True, raw.hide_laps
    assert raw.hide_map is False, raw.hide_map

    # PUT the identical raw payload back through the edit path.
    crud.edit_activity(owner.id, raw, db)
    db.flush()

    # Re-read the RAW columns straight from the row.
    row = db.execute(
        text(
            "SELECT hide_hr, hide_laps, hide_map FROM activities WHERE id = :id"
        ),
        {"id": act.id},
    ).fetchone()
    assert row[0] is None, f"inherit froze into override: hide_hr={row[0]!r}"
    assert row[1] is True, f"override lost: hide_laps={row[1]!r}"
    assert row[2] is False, f"override lost: hide_map={row[2]!r}"


def test_edit_can_change_override_back_to_inherit(db):
    """Setting a raw field to None via edit_activity resets it to inherit.

    Proves the setter path writes NULL (not "leave unchanged"): an activity with
    an explicit hide_hr=True, edited to hide_hr=None, must end up NULL so it
    inherits the global again.
    """
    import activities.activity.crud as crud
    from activities.activity.schema import ActivityRaw
    from sqlalchemy import text

    owner = _make_user(db, globals_map={"hide_activity_hr": True}, tag="rt2")
    act = _make_activity(db, owner.id, raw_map={"hide_hr": True})

    # Sanity: starts as an explicit override.
    row = db.execute(
        text("SELECT hide_hr FROM activities WHERE id = :id"), {"id": act.id}
    ).fetchone()
    assert row[0] is True

    # Edit hide_hr -> None (inherit). model_dump(exclude_unset) includes it
    # because we set it explicitly on the payload.
    payload = ActivityRaw(
        id=act.id, name="act", activity_type=1, hide_hr=None
    )
    crud.edit_activity(owner.id, payload, db)
    db.flush()

    row = db.execute(
        text("SELECT hide_hr FROM activities WHERE id = :id"), {"id": act.id}
    ).fetchone()
    assert row[0] is None, f"edit to None did not reset to inherit: {row[0]!r}"


def test_new_activity_inserts_null_hide_flags(db):
    """A freshly created activity (import path no longer stamps globals) has all
    12 hide_* raw columns NULL, so they inherit the owner's global. This is the
    payoff of dropping the import stamping (patch 0008): no stamp + nullable
    column + no server default => NULL.
    """
    from sqlalchemy import text

    owner = _make_user(db, tag="rt3")
    act = _make_activity(db, owner.id)  # no raw_map -> nothing set, like an import

    row = db.execute(
        text(
            "SELECT hide_start_time, hide_location, hide_map, hide_hr, hide_power,"
            " hide_cadence, hide_elevation, hide_speed, hide_pace, hide_laps,"
            " hide_workout_sets_steps, hide_gear FROM activities WHERE id = :id"
        ),
        {"id": act.id},
    ).fetchone()
    assert all(v is None for v in row), f"expected all NULL (inherit), got {row!r}"
