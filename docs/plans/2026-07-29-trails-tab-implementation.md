# Trails Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third "Trails" tab beside Segments and Laps where hovering a trail row highlights the sections of the user's own GPS track that ran on that trail.

**Architecture:** `trail_matcher.match_trails()` already computes which GPS points are near each named OSM trail, then throws that away. Persist it as `ranges` (inclusive index pairs into the original stream) plus `distance_m` inside the existing `activity_metadata.trail_match_result` JSONB. The API slices those ranges into `latlngs` sections exactly as the Segments and Laps endpoints already do, so the existing hover machinery works with minimal change.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, PostgreSQL (JSONB), shapely + pyproj, Vue 3 (`<script setup>`), Leaflet, Bootstrap 5.

**Design spec:** `docs/plans/2026-07-29-trails-tab.md`

## Workflow: verify on the VM first, formalize patches last

This repo is a distro-patchset, but the working order is **code → deploy →
verify → then patches → commit**. Do not author patches while iterating.

- `overlay/` holds our own files. Edit these **directly** — they are the source
  of truth and `build.sh` copies them verbatim.
- Upstream files (`ActivityMapComponent.vue`, `ActivityView.vue`) exist only in
  the composed tree. Edit them in **`build/source/`**, which is a git repo whose
  single `pristine` commit is untouched upstream `v0.17.7`. Its working tree is
  already patches + overlay applied, so `git -C build/source diff -- <file>`
  regenerates that file's cumulative patch on demand.
- **Never run `./build.sh` while iterating** — its first act is
  `rm -rf build`, which destroys the composed tree including your in-progress
  upstream edits. Rebuild only the frontend (Task 5). `./build.sh` is run once
  at the very end, in Task 6, to prove the patches reproduce what was verified.
- Patches are regenerated from the verified tree in Task 6, never hand-edited.
  Never hand-edit hunk line numbers.

## Global Constraints
- Do not hand-roll unit conversion in the frontend. Use upstream's `utils/activityUtils.js` helpers.
- Do **not** use `formatDistanceRaw` for trail distances: it applies `maximumFractionDigits: 0` unconditionally (the `round` parameter does not control it), so 4,120 m renders "4 km" and any trail under 500 m renders "0 km".
- `ranges` are **inclusive** `[start, end]` index pairs into the original `activities_streams.stream_waypoints` array for `stream_type = 7`.
- Percentages are computed from distance, never from the stored `fraction` (which counts GPS points and inflates wherever the user stopped).
- Existing flat-array callers of `highlightSegment` (Segments, Laps) must keep working unchanged.
- Backward compatibility: `trail_match_result` rows written before this change have no `ranges`. They must render as rows with no highlight, never crash.
- Conventional commits. No AI attribution or `Co-Authored-By` trailers.

---

### Task 1: Persist per-trail index ranges and distance in the matcher

**Files:**
- Modify: `overlay/backend/app/trail_matcher.py`
- Test: `tests/test_trail_ranges.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `_haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float`
  - `_group_into_runs(near_positions: list[int], coord_indices: list[int], max_gap: int = MAX_RUN_GAP_POINTS) -> list[list[int]]` — returns inclusive `[start, end]` original-waypoint index pairs
  - `_range_distance_m(waypoints: list[dict], ranges: list[list[int]]) -> float`
  - `MAX_RUN_GAP_POINTS: int = 3`
  - `match_trails(...)` result dicts gain two keys: `ranges: list[list[int]]` and `distance_m: float`

- [ ] **Step 1: Write the failing test**

Create `tests/test_trail_ranges.py`:

```python
#!/usr/bin/env python3
"""Self-check for trail_matcher's run grouping and index mapping.

trail_matcher imports shapely/pyproj/requests, so run this with the build venv:
    ./build.sh   # once, if build/ is missing
    build/app/.venv/bin/python tests/test_trail_ranges.py

Only core.logger is stubbed; everything else is the real module.
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "overlay" / "backend" / "app"))

core = types.ModuleType("core")
core_logger = types.ModuleType("core.logger")
core_logger.print_to_log = lambda *a, **k: None
core_logger.print_to_log_and_console = lambda *a, **k: None
core.logger = core_logger
sys.modules["core"] = core
sys.modules["core.logger"] = core_logger

import trail_matcher as tm  # noqa: E402


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
    """Decimated position i must map to its true original index, not i*step."""
    coord_indices = [0, 2, 4, 6, 8, 10]  # step of 2
    runs = tm._group_into_runs([2, 3], coord_indices, max_gap=3)
    assert runs == [[4, 6]], runs


def test_index_mapping_survives_missing_latlon():
    """This is the case that breaks the i*step shortcut: originals 0,1,4,5,6
    survive filtering, so decimated position 2 is original index 4."""
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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `build/app/.venv/bin/python tests/test_trail_ranges.py`

Expected: FAIL with `AttributeError: module 'trail_matcher' has no attribute '_group_into_runs'`

If `build/app/.venv` does not exist, run `./build.sh` first.

- [ ] **Step 3: Add the module constant and helpers**

In `overlay/backend/app/trail_matcher.py`, add `import math` to the imports block.

Add after `MAX_GPS_POINTS = 500`:

```python
# Merge near-point runs separated by at most this many decimated points.
# A real GPS track briefly wanders past DISTANCE_THRESHOLD_M (tree cover,
# switchbacks, a parallel spur); without this a single trail shatters into
# dozens of one-point slivers. At a typical decimation step of 2 and ~1s
# sampling this is roughly a 6-second excursion. Tune against real tracks.
MAX_RUN_GAP_POINTS = 3
```

Add these module-level functions above `match_trails`:

```python
def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    radius = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _group_into_runs(
    near_positions: list[int],
    coord_indices: list[int],
    max_gap: int = MAX_RUN_GAP_POINTS,
) -> list[list[int]]:
    """Group decimated near-point positions into original-index ranges.

    Args:
        near_positions: ascending positions into the decimated coord arrays.
        coord_indices: maps a decimated position to its original waypoint index.
        max_gap: merge runs separated by at most this many decimated points.

    Returns:
        List of inclusive [start, end] original-waypoint index pairs.
    """
    if not near_positions:
        return []

    runs: list[list[int]] = []
    start = prev = near_positions[0]
    for pos in near_positions[1:]:
        # Consecutive positions differ by 1, so a gap of N skipped points
        # shows up as a difference of N + 1.
        if pos - prev <= max_gap + 1:
            prev = pos
            continue
        runs.append([coord_indices[start], coord_indices[prev]])
        start = prev = pos
    runs.append([coord_indices[start], coord_indices[prev]])
    return runs


def _range_distance_m(waypoints: list[dict], ranges: list[list[int]]) -> float:
    """Sum distance travelled along the waypoints covered by ranges."""
    total = 0.0
    for start, end in ranges:
        prev = None
        for wp in waypoints[start : end + 1]:
            lat = wp.get("lat")
            lon = wp.get("lon")
            if lat is None or lon is None:
                continue
            if prev is not None:
                total += _haversine_m(prev[0], prev[1], lat, lon)
            prev = (lat, lon)
    return round(total, 1)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `build/app/.venv/bin/python tests/test_trail_ranges.py`

Expected: PASS, `11 passed`

- [ ] **Step 5: Preserve original indices through filtering and decimation**

In `match_trails`, replace this block (currently `trail_matcher.py:83-89`):

```python
    # --- Extract and downsample GPS points ---
    coords = [(w["lon"], w["lat"]) for w in waypoints if "lat" in w and "lon" in w]
    if len(coords) < 2:
        return []

    step = max(1, len(coords) // MAX_GPS_POINTS)
    coords = coords[::step]
```

with:

```python
    # --- Extract and downsample GPS points ---
    # Carry each point's ORIGINAL waypoint index through both the filter and
    # the decimation. Do not reconstruct it later as position * step: waypoints
    # missing lat/lon are dropped here, so that arithmetic is silently wrong for
    # any activity with a gap in its GPS stream.
    # Guarding on `is not None` (rather than key presence) also keeps an
    # explicit null lat/lon from reaching Point() and failing the whole match.
    indexed = [
        (i, w["lon"], w["lat"])
        for i, w in enumerate(waypoints)
        if w.get("lat") is not None and w.get("lon") is not None
    ]
    if len(indexed) < 2:
        return []

    step = max(1, len(indexed) // MAX_GPS_POINTS)
    indexed = indexed[::step]
    coord_indices = [i for i, _lon, _lat in indexed]
    coords = [(lon, lat) for _i, lon, lat in indexed]
```

- [ ] **Step 6: Collect near-point positions and emit the new keys**

Replace the match loop (currently `trail_matcher.py:177-199`):

```python
    results = []
    for name, segments in name_trails.items():
        # For each GPS point, check if it's near ANY segment of this trail
        points_near = 0
        total_distance = 0.0
        for pt in gps_points_utm:
            min_dist = min(pt.distance(seg["geometry_utm"]) for seg in segments)
            if min_dist <= distance_threshold_m:
                points_near += 1
                total_distance += min_dist

        fraction = points_near / len(gps_points_utm)
        if fraction >= min_match_fraction:
            results.append(
                {
                    "name": name,
                    "highway": segments[0]["highway"],
                    "points_near": points_near,
                    "points_total": len(gps_points_utm),
                    "fraction": round(fraction, 4),
                    "avg_distance_m": round(total_distance / max(points_near, 1), 1),
                }
            )
```

with:

```python
    results = []
    for name, segments in name_trails.items():
        # For each GPS point, check if it's near ANY segment of this trail
        points_near = 0
        total_distance = 0.0
        near_positions = []
        for pos, pt in enumerate(gps_points_utm):
            min_dist = min(pt.distance(seg["geometry_utm"]) for seg in segments)
            if min_dist <= distance_threshold_m:
                points_near += 1
                total_distance += min_dist
                near_positions.append(pos)

        fraction = points_near / len(gps_points_utm)
        if fraction >= min_match_fraction:
            ranges = _group_into_runs(near_positions, coord_indices)
            results.append(
                {
                    "name": name,
                    "highway": segments[0]["highway"],
                    "points_near": points_near,
                    "points_total": len(gps_points_utm),
                    "fraction": round(fraction, 4),
                    "avg_distance_m": round(total_distance / max(points_near, 1), 1),
                    "ranges": ranges,
                    "distance_m": _range_distance_m(waypoints, ranges),
                }
            )
```

- [ ] **Step 7: Update the docstring**

In `match_trails`, replace the Returns block:

```python
    Returns:
        List of matched trail dicts sorted by match strength:
        [{"name": str, "highway": str, "points_near": int,
          "points_total": int, "fraction": float, "avg_distance_m": float}, ...]
```

with:

```python
    Returns:
        List of matched trail dicts sorted by match strength:
        [{"name": str, "highway": str, "points_near": int,
          "points_total": int, "fraction": float, "avg_distance_m": float,
          "ranges": list[list[int]], "distance_m": float}, ...]

        "ranges" are inclusive [start, end] index pairs into the ORIGINAL
        waypoints list, grouped into contiguous runs. "distance_m" is the
        distance actually travelled on that trail.
```

- [ ] **Step 8: Verify the whole test suite still passes**

Run: `build/app/.venv/bin/python tests/test_trail_ranges.py && python3 tests/test_trigger.py`

Expected: `11 passed` then `5 passed`

- [ ] **Step 9: Commit**

```bash
git add overlay/backend/app/trail_matcher.py tests/test_trail_ranges.py
git commit -m "feat(trails): persist per-trail index ranges and distance"
```

---

### Task 2: Expose trail geometry through the API and force a re-match

**Files:**
- Modify: `overlay/backend/app/custom/api.py` (`get_activity_trail_description`, currently lines 121-157)
- Modify: `overlay/backend/app/custom/pipeline.py:26`

**Interfaces:**
- Consumes: `ranges` and `distance_m` keys produced by Task 1.
- Produces: `GET /api/v1/custom/activities/{id}/trail-description` returns
  `{"description": str | None, "trails": [{"name": str, "highway": str, "fraction": float, "points_near": int, "avg_distance_m": float, "distance_m": float | None, "latlngs": list[list[list[float]]]}]}`.
  `latlngs` is a list of sections; each section is a list of `[lat, lon]` pairs.

- [ ] **Step 1: Replace the endpoint body**

In `overlay/backend/app/custom/api.py`, replace the whole
`get_activity_trail_description` function with:

```python
@router.get("/activities/{activity_id}/trail-description")
def get_activity_trail_description(activity_id: int, db=Depends(get_db)):
    """Return trail match data for an activity.

    Each trail includes a latlngs array of SECTIONS (each a list of [lat, lon]
    pairs) sliced from the activity's GPS stream, so the map can highlight the
    parts of the track that ran on that trail. A trail is traversed in disjoint
    stretches, so sections must stay separate -- flattening them would draw
    straight lines across the map between them.
    """
    import json

    row = db.execute(
        text("""
        SELECT trail_match_status, trail_match_result
        FROM activity_metadata
        WHERE activity_id = :aid
    """),
        {"aid": activity_id},
    ).fetchone()

    if not row or row[0] != "matched" or not row[1]:
        return {"description": None, "trails": []}

    trails = row[1]
    trail_names = [t["name"] for t in trails[:5]]

    if len(trail_names) == 1:
        desc = trail_names[0]
    elif len(trail_names) == 2:
        desc = f"{trail_names[0]} and {trail_names[1]}"
    else:
        desc = f"{', '.join(trail_names[:-1])}, and {trail_names[-1]}"

    # Fetch the GPS stream once for slicing
    stream_row = db.execute(
        text(
            "SELECT stream_waypoints FROM activities_streams "
            "WHERE activity_id = :aid AND stream_type = 7"
        ),
        {"aid": activity_id},
    ).fetchone()

    waypoints = None
    if stream_row and stream_row[0]:
        wp_data = stream_row[0]
        waypoints = json.loads(wp_data) if isinstance(wp_data, str) else wp_data

    result_trails = []
    for t in trails:
        # Rows matched before ranges were persisted have none. They still
        # render, they just do not highlight.
        latlngs = []
        if waypoints:
            for rng in t.get("ranges") or []:
                section = [
                    [wp["lat"], wp["lon"]]
                    for wp in waypoints[rng[0] : rng[1] + 1]
                    if wp.get("lat") is not None and wp.get("lon") is not None
                ]
                # Drop degenerate sections; a 1-point polyline draws nothing.
                if len(section) >= 2:
                    latlngs.append(section)

        result_trails.append(
            {
                "name": t["name"],
                "highway": t.get("highway", ""),
                "fraction": t["fraction"],
                "points_near": t["points_near"],
                "avg_distance_m": t["avg_distance_m"],
                "distance_m": t.get("distance_m"),
                "latlngs": latlngs,
            }
        )

    return {"description": desc, "trails": result_trails}
```

- [ ] **Step 2: Bump the pipeline version so existing rows re-match**

In `overlay/backend/app/custom/pipeline.py`, change line 26 from:

```python
CURRENT_PIPELINE_VERSION = 3
```

to:

```python
# 4: trail matches now carry per-trail "ranges" + "distance_m" for map hover.
CURRENT_PIPELINE_VERSION = 4
```

- [ ] **Step 3: Verify the module still compiles**

Run: `python3 -m py_compile overlay/backend/app/custom/api.py overlay/backend/app/custom/pipeline.py && echo OK`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add overlay/backend/app/custom/api.py overlay/backend/app/custom/pipeline.py
git commit -m "feat(trails): return per-trail track sections from the API"
```

---

### Task 3: Teach the map to highlight multiple disjoint sections

**Files:**
- Modify: `build/source/frontend/app/src/components/Activities/ActivityMapComponent.vue`

Patch regeneration for this file happens in Task 6, not here.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `highlightSegment(input)` on the `ActivityMapComponent` ref now accepts **either** a flat `[[lat, lon], ...]` array (existing Segments/Laps callers) **or** a nested `[[[lat, lon], ...], ...]` array of sections (Trails). `clearSegmentHighlight()` is unchanged.

- [ ] **Step 1: Confirm the composed tree is present and patched**

```bash
cd /home/brian/dev/endurain-fork/build/source && git log --oneline && git diff --stat -- frontend/app/src/components/Activities/ActivityMapComponent.vue
```

Expected: one `pristine` commit, and a non-empty diffstat for
`ActivityMapComponent.vue` (patch 0003 is already applied in the working tree).

If `build/source` does not exist, stop and report NEEDS_CONTEXT — the controller
must run `./build.sh` once before this task.

- [ ] **Step 2: Refactor `highlightSegment`**

Edit `build/source/frontend/app/src/components/Activities/ActivityMapComponent.vue`.

Replace the line:

```js
  const highlightSegment = (latlngs) => {
    clearSegmentHighlight()
    if (!leafletMap.value || !latlngs || latlngs.length < 2) return

    // Main highlight polyline
    segmentHighlightLayer = L.polyline(latlngs, {
      color: '#f97316',
      weight: 5,
      opacity: 0.9,
      lineJoin: 'round',
      lineCap: 'round',
    }).addTo(leafletMap.value)

    // Build cumulative distance array
```

with:

```js
  // Accepts either a flat [[lat,lon],...] array (segments, laps) or a nested
  // [[[lat,lon],...],...] array of disjoint sections (trails). Leaflet renders
  // nested coordinate arrays as a multi-polyline natively, so layer management
  // and clearSegmentHighlight() need no special casing.
  const highlightSegment = (input) => {
    clearSegmentHighlight()
    if (!leafletMap.value || !Array.isArray(input) || input.length === 0) return

    const isNested = Array.isArray(input[0]) && Array.isArray(input[0][0])
    const sections = (isNested ? input : [input]).filter(
      (s) => Array.isArray(s) && s.length >= 2,
    )
    if (sections.length === 0) return

    segmentHighlightLayer = L.polyline(sections, {
      color: '#f97316',
      weight: 5,
      opacity: 0.9,
      lineJoin: 'round',
      lineCap: 'round',
    }).addTo(leafletMap.value)

    sections.forEach((section) => _addArrowheadsForSection(section))
  }

  // Place directional arrowheads along a single contiguous section.
  const _addArrowheadsForSection = (latlngs) => {
    if (latlngs.length < 2) return

    // Build cumulative distance array
```

That single edit is sufficient: everything from `// Build cumulative distance
array` down to the closing `segmentArrowheads.push(arrow)` loop now forms the
body of `_addArrowheadsForSection`, and `clearSegmentHighlight` below it is
untouched.

Two consequences to be aware of, both desirable:

- The early `if (totalDist < 50) return` now skips arrows for one short
  section instead of aborting the entire highlight. Previously a short segment
  meant no arrows at all; now a trail's short sections are simply arrow-free
  while its long sections still get arrows.
- All references to `latlngs` inside the moved code now refer to the single
  section passed in, which is exactly the intent. Do not rename them.

Confirm no stray references remain by checking that `latlngs` appears nowhere in
`highlightSegment` itself after the edit:

```bash
cd /home/brian/dev/endurain-fork/build/source && sed -n '/const highlightSegment/,/^  const _addArrowheadsForSection/p' \
  frontend/app/src/components/Activities/ActivityMapComponent.vue | grep -v '_addArrowheadsForSection' | grep -c latlngs
```

Expected: `0`

(The `grep -v` is required: `sed`'s range is inclusive of its terminator line,
which is the `_addArrowheadsForSection = (latlngs) =>` signature itself.)

- [ ] **Step 3: Verify the file still parses as valid Vue**

The full frontend rebuild happens in Task 5. For a fast syntax check now, confirm
balanced delimiters in the edited block:

```bash
cd /home/brian/dev/endurain-fork/build/source && node --input-type=module -e "
import fs from 'fs';
const s = fs.readFileSync('frontend/app/src/components/Activities/ActivityMapComponent.vue','utf8');
const script = s.slice(s.indexOf('<script'), s.lastIndexOf('</script>'));
let d=0; for (const c of script) { if(c==='{')d++; if(c==='}')d--; }
if (d!==0) { console.error('UNBALANCED BRACES:', d); process.exit(1); }
console.log('braces balanced');
"
```

Expected: `braces balanced`

- [ ] **Step 4: Report, do not commit**

Do **not** commit and do **not** touch `patches/`. Task 6 regenerates the patch
from this verified tree. Report the exact file you changed so the controller can
track it.

---

### Task 4: Add the Trails tab

**Files:**
- Create: `overlay/frontend/app/src/components/Activities/ActivityTrailsComponent.vue`
- Modify: `overlay/frontend/app/src/components/Activities/ActivitySegmentsLapsComponent.vue`
- Modify: `build/source/frontend/app/src/views/ActivityView.vue`

Patch regeneration for `ActivityView.vue` happens in Task 6, not here.

**Interfaces:**
- Consumes: the API shape from Task 2 (`trails[].name/highway/distance_m/latlngs`), and `highlightSegment(nestedArray)` from Task 3.
- Produces: `ActivitySegmentsLapsComponent` accepts a `trails` array plus `hoveredTrailId` (a **trail name string**, since trails have no database id) and emits `trailHover` / `trailLeave`.

- [ ] **Step 1: Create the trails table component**

Create `overlay/frontend/app/src/components/Activities/ActivityTrailsComponent.vue`:

```vue
<template>
  <div v-if="trails && trails.length > 0" class="mt-2 mb-2">
    <div style="overflow-x: auto">
      <table class="table table-sm" style="font-size: 0.83rem; margin-bottom: 0">
        <thead>
          <tr>
            <th style="min-width: 180px">Trail</th>
            <th style="width: 80px">Type</th>
            <th class="text-end" style="width: 80px">Distance</th>
            <th class="text-end" style="width: 55px">%</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="trail in trails"
            :key="trail.name"
            style="cursor: pointer"
            @mouseenter="$emit('trailHover', trail)"
            @mouseleave="$emit('trailLeave')"
            :class="{ 'custom-segment-hover': hoveredId === trail.name }"
          >
            <td>{{ trail.name }}</td>
            <td class="text-secondary">{{ trail.highway || '\u2014' }}</td>
            <td class="text-end">{{ fmtTrailDistance(trail) }}</td>
            <td class="text-end">{{ fmtPercent(trail) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
// Reuse upstream's formatter so the user's metric/imperial setting is honoured.
// formatDistance takes a lap-shaped object, so pass an adapter. Do NOT use
// formatDistanceRaw: it forces maximumFractionDigits: 0, which would render a
// 4120 m trail as "4 km" and any sub-500 m trail as "0 km".
import { formatDistance } from '@/utils/activityUtils'

const props = defineProps({
  trails: { type: Array, default: () => [] },
  // Trails have no database id, so the hovered key is the trail NAME.
  hoveredId: { type: String, default: null },
  units: { type: String, default: 'metric' },
  activity: { type: Object, default: null },
})

defineEmits(['trailHover', 'trailLeave'])

const { t } = useI18n()

function fmtTrailDistance(trail) {
  if (trail.distance_m === null || trail.distance_m === undefined) return '\u2014'
  return formatDistance(t, props.activity, props.units, {
    total_distance: trail.distance_m,
  })
}

function fmtPercent(trail) {
  const total = props.activity?.distance
  if (!total || trail.distance_m === null || trail.distance_m === undefined) {
    return '\u2014'
  }
  // Percentages intentionally do not sum to 100%: trails overlap where they run
  // concurrently or where OSM has parallel ways, and stretches on no named
  // trail count toward neither.
  return `${Math.round((trail.distance_m / total) * 100)}%`
}
</script>

<style scoped>
:deep(.custom-segment-hover),
:deep(.custom-segment-hover) td {
  background-color: rgba(249, 115, 22, 0.35) !important;
}
</style>
```

- [ ] **Step 2: Add the third tab**

Replace the entire contents of
`overlay/frontend/app/src/components/Activities/ActivitySegmentsLapsComponent.vue`
with:

```vue
<template>
  <div
    v-if="
      (segments && segments.length > 0) ||
      (laps && laps.length > 0) ||
      (trails && trails.length > 0)
    "
    class="mt-2 mb-2"
  >
    <ul class="nav nav-pills mb-2 justify-content-center" role="tablist">
      <li v-if="segments && segments.length > 0" class="nav-item" role="presentation">
        <button
          class="nav-link link-body-emphasis py-1 px-3"
          :class="{ active: activeTab === 'segments' }"
          @click="activeTab = 'segments'"
          type="button"
          role="tab"
        >
          Segments
          <span class="text-secondary ms-1" style="font-size: 0.85rem">({{ segments.length }})</span>
        </button>
      </li>
      <li v-if="laps && laps.length > 0" class="nav-item" role="presentation">
        <button
          class="nav-link link-body-emphasis py-1 px-3"
          :class="{ active: activeTab === 'laps' }"
          @click="activeTab = 'laps'"
          type="button"
          role="tab"
        >
          Laps
          <span class="text-secondary ms-1" style="font-size: 0.85rem">({{ laps.length }})</span>
        </button>
      </li>
      <li v-if="trails && trails.length > 0" class="nav-item" role="presentation">
        <button
          class="nav-link link-body-emphasis py-1 px-3"
          :class="{ active: activeTab === 'trails' }"
          @click="activeTab = 'trails'"
          type="button"
          role="tab"
        >
          Trails
          <span class="text-secondary ms-1" style="font-size: 0.85rem">({{ trails.length }})</span>
        </button>
      </li>
    </ul>

    <div v-if="activeTab === 'segments' && segments && segments.length > 0">
      <ActivitySegmentsComponent
        :segments="segments"
        :hoveredId="hoveredSegmentId"
        @segmentHover="$emit('segmentHover', $event)"
        @segmentLeave="$emit('segmentLeave')"
      />
    </div>

    <div v-if="activeTab === 'laps' && laps && laps.length > 0">
      <ActivityLapsTableComponent
        :laps="laps"
        :hoveredId="hoveredLapId"
        :units="units"
        :activity="activity"
        @lapHover="$emit('lapHover', $event)"
        @lapLeave="$emit('lapLeave')"
      />
    </div>

    <div v-if="activeTab === 'trails' && trails && trails.length > 0">
      <ActivityTrailsComponent
        :trails="trails"
        :hoveredId="hoveredTrailId"
        :units="units"
        :activity="activity"
        @trailHover="$emit('trailHover', $event)"
        @trailLeave="$emit('trailLeave')"
      />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ActivitySegmentsComponent from './ActivitySegmentsComponent.vue'
import ActivityLapsTableComponent from './ActivityLapsTableComponent.vue'
import ActivityTrailsComponent from './ActivityTrailsComponent.vue'

const props = defineProps({
  segments: { type: Array, default: () => [] },
  laps: { type: Array, default: () => [] },
  trails: { type: Array, default: () => [] },
  hoveredSegmentId: { type: Number, default: null },
  hoveredLapId: { type: Number, default: null },
  // A trail name, not an id.
  hoveredTrailId: { type: String, default: null },
  units: { type: String, default: 'metric' },
  activity: { type: Object, default: null },
})

defineEmits([
  'segmentHover',
  'segmentLeave',
  'lapHover',
  'lapLeave',
  'trailHover',
  'trailLeave',
])

// Precedence is unchanged: segments, then laps, then trails last.
const activeTab = ref(
  props.segments && props.segments.length > 0
    ? 'segments'
    : props.laps && props.laps.length > 0
      ? 'laps'
      : 'trails',
)
</script>
```

- [ ] **Step 3: Wire the trails props and handlers in ActivityView**

Edit `build/source/frontend/app/src/views/ActivityView.vue` (patch 0004 is
already applied in that working tree).

Change the `ActivitySegmentsLapsComponent` usage from:

```
    <ActivitySegmentsLapsComponent
      v-if="(activitySegments && activitySegments.length > 0) || (customActivityLaps && customActivityLaps.length > 0)"
      :segments="activitySegments"
      :laps="customActivityLaps"
      :hoveredSegmentId="hoveredSegmentId"
      :hoveredLapId="hoveredLapId"
      :units="units"
      :activity="activity"
      @segmentHover="(seg) => { hoveredSegmentId = seg.id; activityMapRef?.highlightSegment(seg.latlngs) }"
      @segmentLeave="() => { hoveredSegmentId = null; activityMapRef?.clearSegmentHighlight() }"
      @lapHover="(lap) => { hoveredLapId = lap.id; activityMapRef?.highlightSegment(lap.latlngs) }"
      @lapLeave="() => { hoveredLapId = null; activityMapRef?.clearSegmentHighlight() }"
    />
```

to:

```
    <ActivitySegmentsLapsComponent
      v-if="(activitySegments && activitySegments.length > 0) || (customActivityLaps && customActivityLaps.length > 0) || (activityTrails && activityTrails.length > 0)"
      :segments="activitySegments"
      :laps="customActivityLaps"
      :trails="activityTrails"
      :hoveredSegmentId="hoveredSegmentId"
      :hoveredLapId="hoveredLapId"
      :hoveredTrailId="hoveredTrailId"
      :units="units"
      :activity="activity"
      @segmentHover="(seg) => { hoveredSegmentId = seg.id; activityMapRef?.highlightSegment(seg.latlngs) }"
      @segmentLeave="() => { hoveredSegmentId = null; activityMapRef?.clearSegmentHighlight() }"
      @lapHover="(lap) => { hoveredLapId = lap.id; activityMapRef?.highlightSegment(lap.latlngs) }"
      @lapLeave="() => { hoveredLapId = null; activityMapRef?.clearSegmentHighlight() }"
      @trailHover="(trail) => { hoveredTrailId = trail.name; activityMapRef?.highlightSegment(trail.latlngs) }"
      @trailLeave="() => { hoveredTrailId = null; activityMapRef?.clearSegmentHighlight() }"
    />
```

- [ ] **Step 4: Add the `hoveredTrailId` ref and the trails data source**

Still in `build/source/frontend/app/src/views/ActivityView.vue`.

5a. Replace this refs block:

```js
const activitySegments = ref([])
const customActivityLaps = ref([])
const trailDescription = ref(null)
const hoveredSegmentId = ref(null)
const hoveredLapId = ref(null)
```

with:

```js
const activitySegments = ref([])
const customActivityLaps = ref([])
const trailDescription = ref(null)
const activityTrails = ref([])
const hoveredSegmentId = ref(null)
const hoveredLapId = ref(null)
const hoveredTrailId = ref(null)
```

5b. The view already fetches `/trail-description` for the summary paragraph, so
reuse that same response. Replace:

```js
      activitySegments.value = segs || []
      customActivityLaps.value = laps || []
      trailDescription.value = trails?.description || null
      console.log('[custom] Loaded segments:', activitySegments.value.length, 'laps:', customActivityLaps.value.length, 'trail:', trailDescription.value)
```

with:

```js
      activitySegments.value = segs || []
      customActivityLaps.value = laps || []
      trailDescription.value = trails?.description || null
      activityTrails.value = trails?.trails || []
      console.log('[custom] Loaded segments:', activitySegments.value.length, 'laps:', customActivityLaps.value.length, 'trails:', activityTrails.value.length)
```

5c. Reset the new refs on activity change. Replace:

```js
      activitySegments.value = []
      customActivityLaps.value = []
      trailDescription.value = null
      hoveredSegmentId.value = null
      hoveredLapId.value = null
```

with:

```js
      activitySegments.value = []
      customActivityLaps.value = []
      trailDescription.value = null
      activityTrails.value = []
      hoveredSegmentId.value = null
      hoveredLapId.value = null
      hoveredTrailId.value = null
```

5d. In the `catch` block of the same fetch, find:

```js
      trailDescription.value = null
```

and confirm it sits alongside an `activityTrails.value = []` reset; if the catch
block only nulls `trailDescription`, add `activityTrails.value = []` after it.

- [ ] **Step 5: Check braces balance in the edited view**

The full frontend rebuild happens in Task 5. Fast syntax sanity check now:

```bash
cd /home/brian/dev/endurain-fork/build/source && node --input-type=module -e "
import fs from 'fs';
const s = fs.readFileSync('frontend/app/src/views/ActivityView.vue','utf8');
const script = s.slice(s.indexOf('<script'), s.lastIndexOf('</script>'));
let d=0; for (const c of script) { if(c==='{')d++; if(c==='}')d--; }
if (d!==0) { console.error('UNBALANCED BRACES:', d); process.exit(1); }
console.log('braces balanced');
"
```

Expected: `braces balanced`

- [ ] **Step 6: Report, do not commit**

Do **not** commit and do **not** touch `patches/`. Task 6 regenerates patch 0004
from this verified tree. Report the exact files you changed.

---

### Task 5: Deploy to the dev VM and verify live

**Files:**
- None modified. Verification only.

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: a verified working feature on `https://fitness.homelab.dev/activity/22`.

**Context:** `make ansible-endurain` clones `briancappello/endurain.git@main` and
gates the whole build on a `<fork_sha>@<upstream_ref>` stamp in
`/opt/endurain/.version`, so it cannot see uncommitted local work. This task
hand-stages the build output instead. The playbook path only becomes available
after Task 6 commits and the fork is pushed.

Shorthand used in these steps:

```bash
PVE="ssh root@localhost -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
```

- [ ] **Step 1: Sync overlay into the composed tree**

Tasks 1, 2 and 4 edited `overlay/`, which the composed tree does not see until
copied. This is the same copy `build.sh` step 3 performs.

```bash
cd /home/brian/dev/endurain-fork
cp -a overlay/backend/.  build/source/backend/
cp -a overlay/frontend/. build/source/frontend/
echo synced
```

Expected: `synced`

- [ ] **Step 2: Rebuild ONLY the frontend**

Do **not** run `./build.sh` — it begins with `rm -rf build`, which would destroy
the Task 3 and Task 4 edits to `ActivityMapComponent.vue` and `ActivityView.vue`.

```bash
cd /home/brian/dev/endurain-fork/build/source/frontend/app
NODE_OPTIONS="--max-old-space-size=2048" npm run build 2>&1 | tail -15
cp -a dist/. ../../../frontend/
echo "frontend staged"
```

Expected: a Vite build summary with no errors, then `frontend staged`. A Vue
compile error here means Task 3 or Task 4 broke syntax — fix before continuing.

- [ ] **Step 3: Stage the backend files into LXC 116**

These come straight from `overlay/`, which `build.sh` copies verbatim anyway.

```bash
cd /home/brian/dev/endurain-fork
for f in trail_matcher.py custom/api.py custom/pipeline.py; do
  $PVE "pct exec 116 -- tee /opt/endurain/app/$f > /dev/null" < overlay/backend/app/$f && echo "pushed $f"
done
```

Expected: three `pushed` lines.

- [ ] **Step 4: Stage the frontend and restart**

```bash
cd /home/brian/dev/endurain-fork/build/frontend
tar cf - --exclude=env.js . | $PVE "pct exec 116 -- tar xf - -C /opt/endurain/frontend"
$PVE "pct exec 116 -- sh -c 'chown -R endurain:endurain /opt/endurain/app /opt/endurain/frontend; systemctl restart endurain; sleep 8; systemctl is-active endurain'"
```

Expected: `active`

If it is not `active`, read the log before doing anything else:
`$PVE "pct exec 116 -- tail -30 /var/log/endurain/endurain.log"`

- [ ] **Step 5: Re-run the pipeline to populate ranges**

The version bump means every matched activity re-queries Overpass. Run it
directly rather than waiting for an import:

```bash
$PVE "pct exec 116 -- su endurain -s /bin/bash -c 'cd /opt/endurain/app && set -a && . /opt/endurain/.env && set +a && .venv/bin/python -m custom.cli process'"
```

Expected: `Processing all pending activities...` then `Done.`

- [ ] **Step 6: Confirm ranges landed in the database**

```bash
$PVE "pct exec 103 -- su postgres -c 'psql -d endurain -At -c \"select jsonb_array_length(trail_match_result::jsonb), (trail_match_result::jsonb -> 0 -> \\\"ranges\\\") is not null from activity_metadata where activity_id = 22;\"'"
```

Expected: a trail count and `t` (ranges present).

If any activity reports `trail_match_status = 'error'`, that is transient
Overpass rate limiting. Re-run Step 5; the sticky-error fix means errored rows
are retried rather than skipped.

- [ ] **Step 7: Verify the API response shape**

```bash
$PVE 'pct exec 116 -- sh -c "TOKEN=\$(curl -s -X POST http://localhost:8080/api/v1/auth/login -H \"X-Client-Type: mobile\" -d \"username=admin&password=Changeme-Endurain-1\" | sed -n \"s/.*\\\"access_token\\\":\\\"\\([^\\\"]*\\)\\\".*/\\1/p\"); curl -s http://localhost:8080/api/v1/custom/activities/22/trail-description -H \"Authorization: Bearer \$TOKEN\" -H \"X-Client-Type: mobile\" | head -c 400"'
```

Expected: JSON containing `"distance_m"` and a non-empty `"latlngs"` for the
first trail.

- [ ] **Step 8: Verify in the browser**

Load `https://fitness.homelab.dev/activity/22`, then check:

1. A `Trails (N)` tab appears beside `Laps (9)`.
2. The summary paragraph above the tabs is still present.
3. Clicking `Trails` shows Trail / Type / Distance / % columns.
4. Distances read in **miles** (the admin account is `units = imperial`), with
   2 decimal places — not `0 mi` and not whole numbers.
5. Hovering a trail row highlights orange sections on the map, and the row
   highlights.
6. For a multi-section trail, **no straight line** connects the disjoint
   sections across the map.
7. Hovering a Laps row still works exactly as before (regression check on the
   `highlightSegment` refactor).
8. No console errors.

- [ ] **Step 9: Fix anything the verification surfaced**

Fix in the same places Tasks 1-4 edited (`overlay/` for our files,
`build/source/` for upstream files), then repeat Steps 1-8 until all eight checks
pass. Do not commit and do not touch `patches/` — Task 6 handles both.

Report which checks passed and which needed fixes.

---

### Task 6: Formalize into patches and commit

Only start this task after every check in Task 5 Step 8 passes on the VM.

**Files:**
- Modify: `patches/0003-activity-map-highlight.patch`
- Modify: `patches/0004-activity-view.patch`
- Commit: everything from Tasks 1-5

**Interfaces:**
- Consumes: the verified working tree from Tasks 1-5.
- Produces: a patch series that reproduces the verified code from pristine
  upstream `v0.17.7`.

- [ ] **Step 1: Regenerate both patches from the verified tree**

`build/source` has a single `pristine` commit of untouched upstream, so its diff
is exactly the cumulative patch for each file.

```bash
cd /home/brian/dev/endurain-fork
git -C build/source diff -- frontend/app/src/components/Activities/ActivityMapComponent.vue \
  > patches/0003-activity-map-highlight.patch
git -C build/source diff -- frontend/app/src/views/ActivityView.vue \
  > patches/0004-activity-view.patch
wc -l patches/0003-activity-map-highlight.patch patches/0004-activity-view.patch
```

Expected: both files non-empty and larger than before (they gained the trails
changes).

- [ ] **Step 2: Verify the series applies cleanly to pristine upstream**

```bash
cd /tmp/opencode && rm -rf t6 && mkdir t6 \
  && git -C /home/brian/dev/endurain archive v0.17.7 | tar -C t6 -xf - \
  && cd t6 && git init -q && git add -A \
  && git -c user.email=b@x -c user.name=b commit -qm pristine \
  && for p in $(grep -v '^#' /home/brian/dev/endurain-fork/patches/series); do \
       git apply --whitespace=nowarn /home/brian/dev/endurain-fork/patches/$p \
         && echo "ok $p" || echo "FAIL $p"; done
```

Expected: five `ok` lines, no `FAIL`.

- [ ] **Step 3: Prove the patches reproduce the verified code**

This is the step that catches a patch which applies cleanly but does not match
what was actually tested on the VM.

```bash
cd /tmp/opencode/t6
cp -a /home/brian/dev/endurain-fork/overlay/backend/.  backend/
cp -a /home/brian/dev/endurain-fork/overlay/frontend/. frontend/
for f in frontend/app/src/components/Activities/ActivityMapComponent.vue \
         frontend/app/src/views/ActivityView.vue \
         backend/app/trail_matcher.py \
         backend/app/custom/api.py \
         backend/app/custom/pipeline.py; do
  if diff -q "$f" "/home/brian/dev/endurain-fork/build/source/$f" > /dev/null; then
    echo "match    $f"
  else
    echo "MISMATCH $f"
  fi
done
```

Expected: five `match` lines, no `MISMATCH`.

- [ ] **Step 4: Full clean rebuild from the patch series**

Now that the patches are the source of truth, prove an end-to-end build works.
This wipes and recreates `build/`.

```bash
cd /home/brian/dev/endurain-fork && ./build.sh 2>&1 | tail -8
```

Expected: ends with `==> [6/6] Build complete.`

- [ ] **Step 5: Re-run both test suites against the rebuilt venv**

```bash
cd /home/brian/dev/endurain-fork
build/app/.venv/bin/python tests/test_trail_ranges.py && python3 tests/test_trigger.py
```

Expected: `11 passed` then `5 passed`

- [ ] **Step 6: Commit in logical units**

The repo currently also carries uncommitted bugfix work that predates this
feature. Keep it in separate commits from the trails feature.

```bash
cd /home/brian/dev/endurain-fork
git status --short
```

Commit in this order:

```bash
# 1. Pipeline trigger bugfix (predates this feature)
git add overlay/backend/app/custom/trigger.py patches/0005-pipeline-import-hooks.patch \
        patches/series tests/test_trigger.py
git commit -m "fix(pipeline): run post-processing after every import path"

# 2. Laps table unit bugfix (independent)
git add overlay/frontend/app/src/components/Activities/ActivityLapsTableComponent.vue
git commit -m "fix(laps): use upstream formatters for distance, pace and speed"

# 3. Design docs
git add docs/plans/2026-07-29-trails-tab.md \
        docs/plans/2026-07-29-trails-tab-implementation.md
git commit -m "docs: add trails tab design and implementation plan"

# 4. The trails feature
git add overlay/backend/app/trail_matcher.py \
        overlay/backend/app/custom/api.py \
        overlay/backend/app/custom/pipeline.py \
        overlay/frontend/app/src/components/Activities/ActivityTrailsComponent.vue \
        overlay/frontend/app/src/components/Activities/ActivitySegmentsLapsComponent.vue \
        patches/0003-activity-map-highlight.patch \
        patches/0004-activity-view.patch \
        tests/test_trail_ranges.py
git commit -m "feat(trails): add hoverable Trails tab with map highlighting"
```

Note: `patches/series` and `pipeline.py` carry changes from both the bugfix work
and this feature. `series` goes with commit 1 (it adds 0005). `pipeline.py`
carries the Tier 2 sticky-error fix *and* the version bump — put it in commit 4
and note the combined change in the body, or split with `git add -p` if you want
them cleanly separated.

- [ ] **Step 7: Confirm a clean tree**

```bash
cd /home/brian/dev/endurain-fork && git status --short && git log --oneline -5
```

Expected: no output from `git status`, four new commits listed.

Do **not** push. The controller confirms with the human before pushing, since
pushing is what makes `make ansible-endurain` deploy this.

---

---

### Task 7: Make custom API auth consistent with upstream (two-tree mirror)

> **RUN THIS BEFORE TASK 6.** It is numbered 7 because it was added after the
> plan was written, but its changes must be committed together with the rest, so
> Task 6 (regenerate patches + commit) is the final task.

The custom router currently has NO auth dependency. Verified live: both
`/api/v1/custom/activities/22/trail-description` and `.../segments` return HTTP
200 unauthenticated, while upstream `/api/v1/activities/22` returns 401. Before
the trails work this leaked only trail names; it now leaks full GPS geometry.

Root cause of the gap: `customSegmentsService.js` used bare `fetch()`, and
`auth_security.get_sub_from_access_token` reads the access token from the
**Authorization header only** (cookies are refresh-token-only for web) and also
requires an `X-Client-Type` header. Bare `fetch()` therefore could not
authenticate, so the endpoint was left open to compensate for the client.

**Files:**
- Modify: `overlay/backend/app/custom/api.py`
- Modify: `overlay/backend/app/custom/__init__.py` (`register_api`)
- Modify: `overlay/frontend/app/src/services/customSegmentsService.js`
- Modify: `build/source/frontend/app/src/views/ActivityView.vue`

**Interfaces:**
- Produces authenticated `/api/v1/custom/activities/{id}/segments`, `/trail-description`, `/laps`
  and public `/api/v1/public/custom/activities/{id}/...` (prefixes built from `core_config.ROOT_PATH`). Response shapes unchanged.
- `customSegments` gains `getPublicActivitySegments`, `getPublicActivityTrailDescription`,
  `getPublicActivityLaps` alongside the existing three.

#### Upstream primitives to reuse — do not reinvent

| Need | Use |
|---|---|
| prefix constant | `core_config.ROOT_PATH` (`"/api/v1"`) |
| authenticated identity | `Annotated[int, Depends(auth_security.get_sub_from_access_token)]` |
| authenticated visibility | `activities_crud.get_activity_by_id_from_user_id_or_has_visibility(activity_id, user_id, db)` — owner OR visibility in (0,1) |
| public visibility | `activities_crud.get_activity_by_id_if_is_public(activity_id, db)` — enforces `public_shareable_links` AND `visibility == 0` |

Both helpers return an object carrying `user_id` and `hide_map` (typed
`bool | None`, so coerce with truthiness).

#### The third gate: `hide_map`

`activities/activity_streams/crud.py:44-48` computes
`user_is_owner = token_user_id != activity.user_id` and, for non-owners, strips
streams whose `stream_type == STREAM_TYPE_MAP`. Every `latlngs` field we return
— trails, segments AND laps — is derived from that same stream 7. So:

- authenticated tree: `hide = bool(activity.hide_map) and activity.user_id != token_user_id`
- public tree: `hide = bool(activity.hide_map)` (a public viewer is never the owner)

When `hide` is true, return `latlngs: []` (trails) / `latlngs: []` per row
(segments, laps). Everything else is still returned.

- [ ] **Step 1: Refactor `api.py` into shared payload builders plus two routers**

Extract the existing body of each of the three endpoints into a private helper
taking `(activity_id, db, hide_map: bool)` and returning the same structure it
returns today, with `latlngs` forced to `[]` when `hide_map` is true. Then define
six thin endpoints over them.

Sharing the builders is the point: it makes it impossible for the authenticated
and public trees to drift apart, which is how this class of gap appears.

```python
router = APIRouter(prefix=core_config.ROOT_PATH + "/custom", tags=["custom"])
public_router = APIRouter(
    prefix=core_config.ROOT_PATH + "/public/custom", tags=["custom-public"]
)
```

Authenticated endpoints resolve then 404:

```python
activity = activities_crud.get_activity_by_id_from_user_id_or_has_visibility(
    activity_id, token_user_id, db
)
if not activity:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
hide = bool(activity.hide_map) and activity.user_id != token_user_id
```

Public endpoints resolve then return the EMPTY payload (not 404 — match
upstream, which returns None/empty rather than leaking existence):

```python
activity = activities_crud.get_activity_by_id_if_is_public(activity_id, db)
if not activity:
    return []            # or {"description": None, "trails": []} for trail-description
hide = bool(activity.hide_map)
```

Delete the module docstring's claim that these endpoints "are public (no auth
required) to match upstream's public activity sharing pattern" — that claim is
what made the gap look deliberate.

- [ ] **Step 2: Register both routers**

In `overlay/backend/app/custom/__init__.py`, `register_api` must include the
public router as well as the authenticated one.

- [ ] **Step 3: Rewrite `customSegmentsService.js` in upstream's idiom**

Follow `frontend/app/src/services/activityStreams.js` exactly: relative URLs
(the wrapper prepends `API_URL`), authenticated and public methods on the same
exported object, `getX` / `getPublicX` naming.

```js
import { fetchGetRequest } from '@/utils/serviceUtils'
import { fetchPublicGetRequest } from '@/utils/servicePublicUtils'

export const customSegments = {
  async getActivitySegments(activityId) {
    return fetchGetRequest(`custom/activities/${activityId}/segments`)
  },
  async getPublicActivitySegments(activityId) {
    return fetchPublicGetRequest(`public/custom/activities/${activityId}/segments`)
  },
  // ...same pairing for trail-description and laps
}
```

Note the behaviour change: `attemptFetch` THROWS on a non-OK response, whereas
the old `fetchJson` returned `null`. The caller in `ActivityView.vue` already
wraps this in `try`/`catch` that logs a warning, and the refs are reset to
`[]`/`null` before `loadActivity()` runs, so a throw degrades to "no custom
data" rather than a broken page. Do not add a second layer of swallowing.

- [ ] **Step 4: Branch the caller on authentication**

In `build/source/frontend/app/src/views/ActivityView.vue`, the custom fetch block
currently calls the three methods unconditionally. Upstream\'s convention in this
same file is `if (authStore.isAuthenticated) { ... } else { ...Public... }` (see
its handling of streams, laps, workout steps and sets). Mirror it — select the
authenticated or public trio based on `authStore.isAuthenticated`, keeping the
existing `Promise.all`, the existing assignments, and the existing `try`/`catch`.

- [ ] **Step 5: Verify**

```bash
cd /home/brian/dev/endurain-fork
python3 -m py_compile overlay/backend/app/custom/api.py overlay/backend/app/custom/__init__.py && echo COMPILE_OK
build/app/.venv/bin/python tests/test_trail_ranges.py
python3 tests/test_trigger.py
```

Both suites must stay green (23 and 5).

Then deploy and prove the gate on the live server. Push `custom/api.py` and
`custom/__init__.py` to LXC 116, rebuild the frontend in place per Task 5 Steps
1-2 and 4, and confirm:

1. `curl -s -o /dev/null -w "%{http_code}" https://fitness.homelab.dev/api/v1/custom/activities/22/trail-description`
   → **401** (was 200).
2. Same for `.../segments` and `.../laps` → **401**.
3. The public tree with `public_shareable_links = false` returns an empty payload,
   NOT geometry: `curl -s https://fitness.homelab.dev/api/v1/public/custom/activities/22/trail-description`
4. An authenticated request still returns geometry. Log in with
   `X-Client-Type: mobile` (see Task 5) and confirm `latlngs` is non-empty.
5. In the browser, logged in, activity 22 still shows the Trails tab with 11 rows
   and working hover.

- [ ] **Step 6: Report, do not commit**

Task 6 regenerates patch 0004 from `build/source`. Do not touch `patches/`.

## Notes for the implementer

- `MAX_RUN_GAP_POINTS = 3` is a starting value chosen from reasoning, not
  measurement. If Step 6.6 shows a trail fragmenting into many tiny slivers,
  raise it; if two genuinely separate visits to a trail get joined by a long
  bogus line, lower it.
- The `%` column will not sum to 100%. That is expected and documented in the
  spec — do not "fix" it.
- Activity 22 (`Colorado Springs Mountain Biking`) is the best test case: 11
  matched trails, ranging from Ridge Trail at 51.9% of points down to Stratton
  Springs Trail at 3.2%, so it exercises both strong and weak matches.
