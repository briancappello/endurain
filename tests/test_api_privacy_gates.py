#!/usr/bin/env python3
"""Regression guard for the per-activity privacy gates in custom/api.py.

These tests exist so the two gates cannot be silently reopened by a later
refactor. Both were fixed after being verified by hand against a live server;
nothing else in CI covers them.

The behaviour mirrored is upstream Endurain's own:
- ``hide_map``  -> GPS geometry is withheld, as in
  activities/activity_streams/crud.py. The rule is "hide geometry, keep
  everything else": every ``latlngs`` comes back empty while the
  non-geometric fields stay fully populated.
- ``hide_laps`` -> the laps payload is an empty list, as in
  activities/activity_laps/crud.py. ``[]`` rather than a 404, so a hidden
  activity is indistinguishable from one that simply has no laps.

Runs standalone, no framework, no network and no database. custom.api imports
fastapi and sqlalchemy, so use the build venv:
    build/app/.venv/bin/python tests/test_api_privacy_gates.py

Only the module's load-time dependencies are stubbed. The payload builders
themselves run for real against a fake ``db`` that returns canned rows, and are
always reached through the module object (``api._laps_payload``) so a mutation
harness can swap them out at runtime.
"""

import sys
import types
from pathlib import Path

OVERLAY = Path(__file__).resolve().parent.parent / "overlay" / "backend" / "app"
sys.path.insert(0, str(OVERLAY))


def _stub(name, **attrs):
    """Register a stub module (and expose it on its parent package)."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    if "." in name:
        parent, _, child = name.rpartition(".")
        setattr(sys.modules[parent], child, mod)
    return mod


def _unused(*_a, **_k):  # pragma: no cover - route deps are never invoked
    raise AssertionError("route dependency called; builders are tested directly")


# --- stub what custom.api pulls in at import time ---------------------------
_stub("core")
_stub("core.logger", print_to_log=lambda *a, **k: None)
_stub("core.config", ROOT_PATH="/api/v1")
_stub("core.database", get_db=_unused)

_stub("activities")
_stub("activities.activity")
_stub(
    "activities.activity.crud",
    get_activity_by_id_from_user_id_or_has_visibility=_unused,
    get_activity_by_id_if_is_public=_unused,
)
_stub("activities.activity.dependencies", validate_activity_id=_unused)

_stub("auth")
_stub("auth.security", check_scopes=_unused, get_sub_from_access_token=_unused)

# custom/__init__ imports alembic and the ORM models; the package is only
# needed as a namespace for custom.api.
custom_pkg = types.ModuleType("custom")
custom_pkg.__path__ = [str(OVERLAY / "custom")]
sys.modules["custom"] = custom_pkg

import custom.api as api  # noqa: E402

# --- canned data ------------------------------------------------------------

WAYPOINTS = [
    {"lat": 45.0 + i * 0.001, "lon": 7.0 + i * 0.001} for i in range(5)
]

# 18 columns, matching the segment_efforts/segments SELECT.
SEGMENT_ROW = (
    101,  # 0  se.id
    "Test Climb",  # 1  s.name
    600,  # 2  se.elapsed_time
    580,  # 3  se.moving_time
    1500.0,  # 4  se.distance
    0,  # 5  se.start_index
    4,  # 6  se.end_index
    150,  # 7  average_heartrate
    170,  # 8  max_heartrate
    220,  # 9  average_watts
    85,  # 10 average_cadence
    2,  # 11 pr_rank
    None,  # 12 kom_rank
    1490.0,  # 13 segment_distance
    5.5,  # 14 average_grade
    9.0,  # 15 maximum_grade
    1,  # 16 climb_category
    987654,  # 17 segment_strava_id
)

# 16 columns, matching the activity_laps SELECT. Start/end positions sit on
# the first and last waypoint, so nearest_idx spans the whole track.
LAP_ROW = (
    7,  # 0  id
    WAYPOINTS[0]["lat"],  # 1  start_position_lat
    WAYPOINTS[0]["lon"],  # 2  start_position_long
    WAYPOINTS[-1]["lat"],  # 3  end_position_lat
    WAYPOINTS[-1]["lon"],  # 4  end_position_long
    300.0,  # 5  total_elapsed_time
    295.0,  # 6  total_timer_time
    1000.0,  # 7  total_distance
    42,  # 8  total_ascent
    145,  # 9  avg_heart_rate
    165,  # 10 max_heart_rate
    88,  # 11 avg_cadence
    210,  # 12 avg_power
    "active",  # 13 intensity
    3.3,  # 14 enhanced_avg_speed
    5.05,  # 15 enhanced_avg_pace
)

TRAIL = {
    "name": "Sentiero dei Fiori",
    "highway": "path",
    "fraction": 0.8,
    "points_near": 4,
    "avg_distance_m": 3.2,
    "distance_m": 1234.5,
    "ranges": [[0, 4]],
}

TRAIL_METADATA_ROW = ("matched", [TRAIL])


class _FakeResult:
    """Stand-in for sqlalchemy's Result, with only what the builders use."""

    def __init__(self, rows=None, row=None):
        self._rows = rows
        self._row = row

    def fetchall(self):
        assert self._rows is not None, "fetchall() on a single-row query"
        return list(self._rows)

    def fetchone(self):
        assert self._rows is None, "fetchone() on a multi-row query"
        return self._row


class _FakeDB:
    """Routes each builder query to canned rows by the table it touches.

    Records the tables queried, so a test can assert the GPS stream was not
    merely emptied but never read at all.
    """

    def __init__(self, waypoints=WAYPOINTS, segment_rows=(), lap_rows=(), metadata_row=None):
        self.waypoints = waypoints
        self.segment_rows = segment_rows
        self.lap_rows = lap_rows
        self.metadata_row = metadata_row
        self.tables = []

    def execute(self, clause, params=None):
        sql = str(clause)
        if "activities_streams" in sql:
            self.tables.append("activities_streams")
            return _FakeResult(row=(self.waypoints,) if self.waypoints else None)
        if "segment_efforts" in sql:
            self.tables.append("segment_efforts")
            return _FakeResult(rows=self.segment_rows)
        if "activity_laps" in sql:
            self.tables.append("activity_laps")
            return _FakeResult(rows=self.lap_rows)
        if "activity_metadata" in sql:
            self.tables.append("activity_metadata")
            return _FakeResult(row=self.metadata_row)
        raise AssertionError(f"unexpected query: {sql}")


def _lap_db():
    return _FakeDB(lap_rows=[LAP_ROW])


def _segment_db():
    return _FakeDB(segment_rows=[SEGMENT_ROW])


def _trail_db():
    return _FakeDB(metadata_row=TRAIL_METADATA_ROW)


# --- hide_laps --------------------------------------------------------------


def test_laps_visible_when_not_hidden():
    db = _lap_db()
    laps = api._laps_payload(1, db, api._Privacy())
    assert len(laps) == 1, laps
    for lap in laps:
        assert lap["latlngs"], f"expected geometry, got {lap['latlngs']!r}"
    assert laps[0]["id"] == 7, laps[0]
    assert laps[0]["total_distance"] == 1000.0, laps[0]


def test_hide_laps_returns_empty_list_despite_lap_rows():
    """The gate, not "no rows": the fake db is fully loaded with laps."""
    db = _lap_db()
    laps = api._laps_payload(1, db, api._Privacy(hide_laps=True))
    assert laps == [], laps
    # A hidden activity must not even be queried for laps.
    assert db.tables == [], db.tables


def test_hide_map_empties_lap_geometry_but_keeps_the_laps():
    db = _lap_db()
    laps = api._laps_payload(1, db, api._Privacy(hide_map=True))
    assert len(laps) == 1, laps
    for lap in laps:
        assert lap["latlngs"] == [], f"geometry leaked: {lap['latlngs']!r}"
    # everything non-geometric survives
    assert laps[0]["id"] == 7, laps[0]
    assert laps[0]["total_elapsed_time"] == 300.0, laps[0]
    assert laps[0]["total_distance"] == 1000.0, laps[0]
    assert laps[0]["avg_heart_rate"] == 145, laps[0]
    assert "activities_streams" not in db.tables, db.tables


# --- hide_map: trail description -------------------------------------------


def test_trail_geometry_present_when_not_hidden():
    db = _trail_db()
    payload = api._trail_description_payload(1, db, hide_map=False)
    trails = payload["trails"]
    assert len(trails) == 1, trails
    assert trails[0]["latlngs"], f"expected geometry, got {trails[0]['latlngs']!r}"
    # one section, holding the whole 5-point range
    assert len(trails[0]["latlngs"]) == 1, trails[0]["latlngs"]
    assert len(trails[0]["latlngs"][0]) == 5, trails[0]["latlngs"][0]


def test_hide_map_empties_trail_geometry_but_keeps_everything_else():
    """Pins "hide geometry, keep everything else".

    A later "simplification" that blanks the whole trail payload, or returns
    the bare empty shape, must fail here.
    """
    db = _trail_db()
    payload = api._trail_description_payload(1, db, hide_map=True)
    trails = payload["trails"]
    assert len(trails) == 1, trails
    assert trails[0]["latlngs"] == [], f"geometry leaked: {trails[0]['latlngs']!r}"
    # the descriptive payload must survive intact
    assert payload["description"] == TRAIL["name"], payload["description"]
    assert trails[0]["name"] == TRAIL["name"], trails[0]
    assert trails[0]["highway"] == "path", trails[0]
    assert trails[0]["distance_m"] == 1234.5, trails[0]
    assert trails[0]["fraction"] == 0.8, trails[0]
    assert trails[0]["points_near"] == 4, trails[0]
    assert trails[0]["avg_distance_m"] == 3.2, trails[0]
    assert "activities_streams" not in db.tables, db.tables


# --- hide_map: segments -----------------------------------------------------


def test_segment_geometry_present_when_not_hidden():
    db = _segment_db()
    segments = api._segments_payload(1, db, api._Privacy())
    assert len(segments) == 1, segments
    for seg in segments:
        assert seg["latlngs"], f"expected geometry, got {seg['latlngs']!r}"
    assert len(segments[0]["latlngs"]) == 5, segments[0]["latlngs"]


def test_hide_map_empties_segment_geometry_but_keeps_sql_fields():
    db = _segment_db()
    segments = api._segments_payload(1, db, api._Privacy(hide_map=True))
    assert len(segments) == 1, segments
    for seg in segments:
        assert seg["latlngs"] == [], f"geometry leaked: {seg['latlngs']!r}"
    # SQL-derived fields survive
    assert segments[0]["id"] == 101, segments[0]
    assert segments[0]["name"] == "Test Climb", segments[0]
    assert segments[0]["elapsed_time"] == 600, segments[0]
    assert segments[0]["moving_time"] == 580, segments[0]
    assert segments[0]["distance"] == 1500.0, segments[0]
    assert segments[0]["segment_distance"] == 1490.0, segments[0]
    assert segments[0]["average_heartrate"] == 150, segments[0]
    assert "activities_streams" not in db.tables, db.tables


# --- metric flags: laps ----------------------------------------------------
#
# Upstream strips these per-stream on public streams
# (activities/activity_streams/crud.py); the custom Laps/Segments payloads embed
# the same per-effort metrics, so they must strip the same fields or a hidden
# metric leaks. Owners keep everything -- _Privacy() with all flags False.


def test_laps_metrics_present_when_no_flags():
    """Baseline: with nothing hidden, every metric survives."""
    laps = api._laps_payload(1, _lap_db(), api._Privacy())[0]
    assert laps["avg_heart_rate"] == 145, laps
    assert laps["max_heart_rate"] == 165, laps
    assert laps["avg_cadence"] == 88, laps
    assert laps["avg_power"] == 210, laps
    assert laps["enhanced_avg_speed"] == 3.3, laps
    assert laps["enhanced_avg_pace"] == 5.05, laps
    assert laps["total_ascent"] == 42, laps


def test_hide_hr_nulls_lap_heart_rate_only():
    laps = api._laps_payload(1, _lap_db(), api._Privacy(hide_hr=True))[0]
    assert laps["avg_heart_rate"] is None, laps
    assert laps["max_heart_rate"] is None, laps
    # other metrics untouched
    assert laps["avg_power"] == 210, laps
    assert laps["enhanced_avg_speed"] == 3.3, laps


def test_hide_speed_and_pace_and_power_and_cadence_and_elevation_null_laps():
    laps = api._laps_payload(
        1,
        _lap_db(),
        api._Privacy(
            hide_speed=True,
            hide_pace=True,
            hide_power=True,
            hide_cadence=True,
            hide_elevation=True,
        ),
    )[0]
    assert laps["enhanced_avg_speed"] is None, laps
    assert laps["enhanced_avg_pace"] is None, laps
    assert laps["avg_power"] is None, laps
    assert laps["avg_cadence"] is None, laps
    assert laps["total_ascent"] is None, laps
    # HR was not flagged, so it stays
    assert laps["avg_heart_rate"] == 145, laps
    # non-personal totals always survive
    assert laps["total_distance"] == 1000.0, laps
    assert laps["total_elapsed_time"] == 300.0, laps


# --- metric flags: segments -------------------------------------------------


def test_hide_hr_nulls_segment_heart_rate_only():
    segs = api._segments_payload(1, _segment_db(), api._Privacy(hide_hr=True))[0]
    assert segs["average_heartrate"] is None, segs
    assert segs["max_heartrate"] is None, segs
    # power/cadence untouched
    assert segs["average_watts"] == 220, segs
    assert segs["average_cadence"] == 85, segs


def test_hide_power_and_cadence_null_segment_metrics():
    segs = api._segments_payload(
        1, _segment_db(), api._Privacy(hide_power=True, hide_cadence=True)
    )[0]
    assert segs["average_watts"] is None, segs
    assert segs["average_cadence"] is None, segs
    # HR not flagged, survives; grade fields are terrain, always survive
    assert segs["average_heartrate"] == 150, segs
    assert segs["average_grade"] == 5.5, segs


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
