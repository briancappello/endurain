#!/usr/bin/env python3
"""Pure-logic tests for trail_matcher: grouping, index mapping, Overpass failures.

trail_matcher imports shapely/pyproj/requests, so run with the build venv:
    build/app/.venv/bin/python -m pytest tests/test_trail_ranges.py

Only core.logger is stubbed; everything else is the real module. The Overpass
tests stub trail_matcher's `requests` and `time` so nothing touches the network
and nothing actually sleeps. The core stubs are installed only for the import
and reverted immediately (see tests/_isolation.py), so nothing leaks into the
shared pytest session.
"""

import types

import requests

from _isolation import import_isolated

_core = types.ModuleType("core")
_core_logger = types.ModuleType("core.logger")
_core_logger.print_to_log = lambda *a, **k: None
_core_logger.print_to_log_and_console = lambda *a, **k: None
_core.logger = _core_logger

tm = import_isolated(
    "trail_matcher",
    stubs={"core": _core, "core.logger": _core_logger},
)


def test_consecutive_points_collapse_to_one_run():
    coord_indices = [0, 1, 2, 3, 4, 5]
    runs = tm._group_into_runs([1, 2, 3], coord_indices, max_gap=3)
    assert runs == [[1, 3]], runs


def test_large_gap_splits_into_two_runs():
    coord_indices = list(range(20))
    # positions 1,2 then a gap of 8 skipped points, then 11,12
    runs = tm._group_into_runs([1, 2, 11, 12], coord_indices, max_gap=3)
    assert runs == [[1, 2], [11, 12]], runs


def test_gap_within_tolerance_merges():
    coord_indices = list(range(20))
    # positions 1,2 then 3 skipped (3,4,5) then 6 -> gap == max_gap, merge
    runs = tm._group_into_runs([1, 2, 6], coord_indices, max_gap=3)
    assert runs == [[1, 6]], runs


def test_gap_one_past_tolerance_splits():
    coord_indices = list(range(20))
    # positions 1,2 then 4 skipped (3..6) then 7 -> gap == max_gap+1, split
    runs = tm._group_into_runs([1, 2, 7], coord_indices, max_gap=3)
    assert runs == [[1, 2], [7, 7]], runs


def test_index_mapping_is_exact_under_decimation():
    """_group_into_runs must emit coord_indices[pos], never pos itself.

    The construction of coord_indices is covered by the _index_and_decimate
    tests; here coord_indices is hand-fed, so no decimation actually happens.
    """
    coord_indices = [0, 2, 4, 6, 8, 10]  # as if decimated with step 2
    runs = tm._group_into_runs([2, 3], coord_indices, max_gap=3)
    assert runs == [[4, 6]], runs


def test_index_mapping_survives_missing_latlon():
    """_group_into_runs must handle a non-uniform coord_indices mapping.

    Stands in for a track where only originals 0,1,4,5,6 have coordinates, so
    position 2 is original index 4. The mapping is hand-fed; the filtering that
    produces such a mapping is covered by the _index_and_decimate tests.
    """
    coord_indices = [0, 1, 4, 5, 6]
    runs = tm._group_into_runs([2, 3, 4], coord_indices, max_gap=3)
    assert runs == [[4, 6]], runs


def test_single_isolated_point_yields_degenerate_run():
    coord_indices = list(range(10))
    runs = tm._group_into_runs([5], coord_indices, max_gap=3)
    assert runs == [[5, 5]], runs


def test_no_near_points_yields_no_ranges():
    assert tm._group_into_runs([], [0, 1, 2], max_gap=3) == []


def test_range_distance_sums_only_covered_waypoints():
    # ~111.19 m per 0.001 degree of latitude at the equator
    waypoints = [
        {"lat": 0.000, "lon": 0.0},
        {"lat": 0.001, "lon": 0.0},
        {"lat": 0.002, "lon": 0.0},
        {"lat": 50.0, "lon": 50.0},  # far away, outside the range
    ]
    d = tm._range_distance_m(waypoints, [[0, 2]])
    assert 200 < d < 240, d


def test_range_distance_skips_null_coordinates():
    waypoints = [
        {"lat": 0.000, "lon": 0.0},
        {"lat": None, "lon": None},
        {"lat": 0.001, "lon": 0.0},
    ]
    d = tm._range_distance_m(waypoints, [[0, 2]])
    assert 100 < d < 125, d


def test_range_distance_of_empty_ranges_is_zero():
    assert tm._range_distance_m([{"lat": 0.0, "lon": 0.0}], []) == 0.0


def _wps(*latlons):
    return [{"lat": lat, "lon": lon} for lat, lon in latlons]


def test_index_and_decimate_identity_when_no_decimation():
    # lat is offset by +100 from lon so a (lon, lat) swap cannot pass
    wps = _wps((100.0, 0.0), (101.0, 1.0), (102.0, 2.0))
    coord_indices, coords = tm._index_and_decimate(wps, max_points=500)
    assert coord_indices == [0, 1, 2], coord_indices
    assert coords == [(0.0, 100.0), (1.0, 101.0), (2.0, 102.0)], coords


def test_index_and_decimate_indices_correct_under_decimation():
    """coord_indices must be carried through, never reconstructed as pos * step.

    Original 2 is unusable, so 11 originals give 10 usable points and
    max_points=5 -> step = 10 // 5 = 2. The kept originals are 0,3,5,7,9 --
    pos * step would wrongly give 0,2,4,6,8. lat is offset by +100 from lon so
    a (lon, lat) swap cannot pass either.
    """
    wps = [
        {"lat": float(i) + 100.0, "lon": float(i)} if i != 2 else {"lat": None, "lon": None}
        for i in range(11)
    ]
    coord_indices, coords = tm._index_and_decimate(wps, max_points=5)
    assert coord_indices == [0, 3, 5, 7, 9], coord_indices
    assert coords == [
        (0.0, 100.0),
        (3.0, 103.0),
        (5.0, 105.0),
        (7.0, 107.0),
        (9.0, 109.0),
    ], coords


def test_index_and_decimate_drops_missing_keys_and_keeps_indices():
    wps = [
        {"lat": 0.0, "lon": 10.0},
        {"lat": 1.0, "lon": 11.0},
        {},  # no lat/lon keys at all
        {"lat": 3.0},  # lon key missing
        {"lat": 4.0, "lon": 14.0},
        {"lat": 5.0, "lon": 15.0},
    ]
    coord_indices, coords = tm._index_and_decimate(wps, max_points=500)
    assert coord_indices == [0, 1, 4, 5], coord_indices
    assert coords == [(10.0, 0.0), (11.0, 1.0), (14.0, 4.0), (15.0, 5.0)], coords


def test_index_and_decimate_drops_explicit_none_and_keeps_indices():
    wps = [
        {"lat": 0.0, "lon": 10.0},
        {"lat": None, "lon": None},
        {"lat": 2.0, "lon": None},
        {"lat": 3.0, "lon": 13.0},
        {"lat": 4.0, "lon": 14.0},
    ]
    coord_indices, coords = tm._index_and_decimate(wps, max_points=500)
    assert coord_indices == [0, 3, 4], coord_indices
    assert coords == [(10.0, 0.0), (13.0, 3.0), (14.0, 4.0)], coords


def test_index_and_decimate_indices_survive_gap_plus_decimation():
    # originals 0..11 with 2,3 missing -> 10 usable; max_points=5 -> step 2
    wps = [
        {"lat": float(i), "lon": float(i)} if i not in (2, 3) else {"lat": None}
        for i in range(12)
    ]
    coord_indices, coords = tm._index_and_decimate(wps, max_points=5)
    # usable originals: 0,1,4,5,6,7,8,9,10,11 -> every 2nd: 0,4,6,8,10
    assert coord_indices == [0, 4, 6, 8, 10], coord_indices
    assert len(coords) == 5, coords
    # the mapping must survive into the run grouping
    assert tm._group_into_runs([1, 2], coord_indices, max_gap=3) == [[4, 6]]


def test_index_and_decimate_too_few_usable_points_is_empty():
    assert tm._index_and_decimate([], max_points=500) == ([], [])
    assert tm._index_and_decimate(_wps((0.0, 1.0)), max_points=500) == ([], [])
    one_usable = [{"lat": 0.0, "lon": 1.0}, {"lat": None, "lon": None}]
    assert tm._index_and_decimate(one_usable, max_points=500) == ([], [])


def test_index_and_decimate_coords_are_lon_lat_ordered():
    # Distinct magnitudes so a swap cannot pass: lat 12.0, lon 77.0
    coord_indices, coords = tm._index_and_decimate(
        _wps((12.0, 77.0), (13.0, 78.0)), max_points=500
    )
    assert coord_indices == [0, 1], coord_indices
    assert coords[0] == (77.0, 12.0), coords[0]
    assert coords[1] == (78.0, 13.0), coords[1]


# --- Overpass failure vs. genuine empty result ---
#
# The bug these cover: a 429 used to be swallowed into `return []`, which the
# pipeline recorded as "no_trails" and stamped terminal, destroying real matches.
# Failure must raise; only a successful-but-empty query may return [].

_TRAIL_LAT = 45.0
_TRAIL_LONS = [7.0 + i * 0.0001 for i in range(10)]


def _line_waypoints():
    """A track running straight along the stubbed trail, so fraction == 1.0."""
    return [{"lat": _TRAIL_LAT, "lon": lon} for lon in _TRAIL_LONS]


_NAMED_WAY_PAYLOAD = {
    "elements": [
        {"type": "node", "id": 1, "lat": _TRAIL_LAT, "lon": _TRAIL_LONS[0]},
        {"type": "node", "id": 2, "lat": _TRAIL_LAT, "lon": _TRAIL_LONS[-1]},
        {
            "type": "way",
            "id": 10,
            "nodes": [1, 2],
            "tags": {"name": "Test Trail", "highway": "path"},
        },
    ]
}

_UNNAMED_WAY_PAYLOAD = {
    "elements": [
        {"type": "node", "id": 1, "lat": _TRAIL_LAT, "lon": _TRAIL_LONS[0]},
        {"type": "node", "id": 2, "lat": _TRAIL_LAT, "lon": _TRAIL_LONS[-1]},
        {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "path"}},
    ]
}


class _FakeResp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"elements": []}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error")

    def json(self):
        return self._payload


class _StubOverpass:
    """Swap trail_matcher's `requests` and `time` for the duration of a test.

    `responses` is consumed one item per requests.post call: a _FakeResp to
    return, or an Exception instance to raise. Running past the end is a test
    failure, not a silent repeat. Sleeps are recorded and advance a fake clock,
    so throttling and backoff are exercised without real waiting.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.sleeps = []
        self._now = 0.0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        assert self._responses, "requests.post called more times than stubbed"
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self._now += seconds

    def monotonic(self):
        return self._now

    def __enter__(self):
        self._saved = (tm.requests, tm.time, tm._last_request_at)
        tm.requests = types.SimpleNamespace(post=self.post)
        tm.time = types.SimpleNamespace(sleep=self.sleep, monotonic=self.monotonic)
        tm._last_request_at = float("-inf")
        return self

    def __exit__(self, *_exc):
        tm.requests, tm.time, tm._last_request_at = self._saved
        return False


def test_overpass_transport_error_raises_and_is_not_an_empty_result():
    stub = _StubOverpass(
        [requests.ConnectionError("boom")] * tm.OVERPASS_MAX_ATTEMPTS
    )
    with stub:
        try:
            result = tm.match_trails(_line_waypoints())
        except tm.TrailMatchUnavailable as e:
            assert "boom" in str(e), e
        else:
            raise AssertionError(f"failure was swallowed, returned {result!r}")
    assert stub.calls == tm.OVERPASS_MAX_ATTEMPTS, stub.calls


def test_overpass_429_then_success_returns_matches():
    stub = _StubOverpass([_FakeResp(429), _FakeResp(200, _NAMED_WAY_PAYLOAD)])
    with stub:
        matches = tm.match_trails(_line_waypoints())
    assert stub.calls == 2, stub.calls
    assert [m["name"] for m in matches] == ["Test Trail"], matches
    # exactly one backoff sleep, and it used the configured base
    assert stub.sleeps == [tm.OVERPASS_BACKOFF_BASE_S], stub.sleeps


def test_overpass_exhausting_all_attempts_raises():
    stub = _StubOverpass([_FakeResp(429)] * tm.OVERPASS_MAX_ATTEMPTS)
    with stub:
        try:
            tm.match_trails(_line_waypoints())
        except tm.TrailMatchUnavailable as e:
            assert "429" in str(e), e
        else:
            raise AssertionError("exhausted attempts did not raise")
    assert stub.calls == tm.OVERPASS_MAX_ATTEMPTS, stub.calls
    # backoff between attempts only, never after the final one
    assert len(stub.sleeps) == tm.OVERPASS_MAX_ATTEMPTS - 1, stub.sleeps
    assert stub.sleeps[0] == tm.OVERPASS_BACKOFF_BASE_S, stub.sleeps
    assert stub.sleeps[1] == tm.OVERPASS_BACKOFF_BASE_S * 2, stub.sleeps


def test_successful_response_with_no_named_ways_is_empty_not_an_error():
    for payload in ({"elements": []}, _UNNAMED_WAY_PAYLOAD):
        stub = _StubOverpass([_FakeResp(200, payload)])
        with stub:
            assert tm.match_trails(_line_waypoints()) == [], payload
        assert stub.calls == 1, stub.calls


def test_too_few_waypoints_returns_empty_without_touching_network():
    # empty response list: any requests.post call trips the stub's assert
    stub = _StubOverpass([])
    with stub:
        assert tm.match_trails([]) == []
        assert tm.match_trails([{"lat": 1.0, "lon": 2.0}]) == []
        # two waypoints but only one usable -> still nothing to query for
        one_usable = [{"lat": 1.0, "lon": 2.0}, {"lat": None, "lon": None}]
        assert tm.match_trails(one_usable) == []
    assert stub.calls == 0, stub.calls



