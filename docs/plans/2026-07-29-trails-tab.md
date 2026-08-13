# Trails tab with map hover highlighting

Date: 2026-07-29
Status: approved, not yet implemented

## Problem

Matched OSM trails are currently surfaced only as a static sentence above the
tab strip ("Trails: Ridge Trail, Chamberlain, Ladders, Upper Chutes, and
Ridgeway Trail"). There is no way to see how much of an activity was spent on a
given trail, and no way to see *where* on the map a trail was.

The `Segments` and `Laps` tabs already support hover-to-highlight. Trails should
get the same treatment as a third tab.

### Why this is not a small change

`trail_matcher.match_trails()` computes trail geometry (`geometry_wgs84`,
`geometry_utm`) and per-GPS-point distances internally, then discards all of it.
The dicts it returns — and therefore everything persisted in
`activity_metadata.trail_match_result` — carry only:

```
name, highway, points_near, points_total, fraction, avg_distance_m
```

There is nothing to draw on a map. The feature is blocked on persisting new
data, which is most of the work.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| What hover highlights | The parts of **your track** that were on that trail | Reuses the `latlngs` contract Segments/Laps already use, so existing hover machinery works unchanged. Tiny payload (index ranges). Consistent with the other two tabs. |
| Static summary paragraph | **Keep it**, alongside the new tab | Skim vs. explore are different jobs. The paragraph already works and is already wired to the same endpoint. |
| Table columns | Trail, Type, Distance, % of activity | Distance is the honest, comparable metric. |
| % of activity | Derived from **distance**, not stored `fraction` | `fraction` counts GPS *points*, so it inflates anywhere you stopped and rested — points pile up while stationary. |

Percentage is computed frontend-side as `trail.distance_m / activity.distance`,
both in metres (`activities.distance` is metres — 13713 for activity 22). Guard
against a null or zero `activity.distance` by rendering `—`.

Percentages will not sum to 100%. Trails overlap where they run concurrently or
where OSM has parallel ways, and stretches on no named trail count toward
neither. This is expected, not a bug to correct.

## Data model

Two new keys per entry in the existing `trail_match_result` JSONB. No schema
migration: the column is already JSONB.

```json
{
  "name": "Ridge Trail",
  "highway": "path",
  "points_near": 287,
  "points_total": 553,
  "fraction": 0.519,
  "avg_distance_m": 3.4,

  "ranges": [[120, 480], [1300, 1520]],
  "distance_m": 4120.5
}
```

- `ranges` — list of `[start, end]` **inclusive** index pairs into the
  *original* `activities_streams.stream_waypoints` array (stream_type 7). Chosen
  so the API can slice them exactly the way the existing `segments` and `laps`
  endpoints already slice their own ranges.
- `distance_m` — metres actually covered on that trail.

### Why ranges and not a flat index list

A trail is traversed in disjoint sections. Handing a flat list of near-points to
a single polyline draws bogus straight lines across the map between sections.
Ranges are a correctness requirement, not a compression trick.

## Backend

### `trail_matcher.py`

1. **Preserve original indices.** Build a `coord_indices` list alongside
   `coords`, and decimate both with the same `[::step]` slice.

   Do **not** derive original indices as `decimated_i * step`. `coords` is built
   by filtering waypoints that have both `lat` and `lon`, so that arithmetic is
   silently wrong for any activity with a gap in its GPS stream. Carrying the
   indices through by construction makes the mapping exact.

2. **Collect near-point indices.** `enumerate` the existing match loop
   (currently `trail_matcher.py:182`) and record the index of each point within
   `distance_threshold_m` of the trail. `points_near`, `total_distance` and
   `avg_distance_m` keep their current meaning.

3. **Group into runs.** Merge consecutive near indices into runs, joining across
   gaps of at most `MAX_RUN_GAP_POINTS` decimated points.

   A real GPS track briefly wanders past the 50 m threshold — tree cover, switch-
   backs, a parallel spur. Without gap merging a single trail shatters into
   dozens of one-point slivers. This is a physical-world tuning threshold, so it
   is a named module constant, not an inline literal.

   `MAX_RUN_GAP_POINTS = 3` to start: gaps of up to 3 *decimated* points merge.
   At a typical decimation step of 2 and ~1 s sampling that is roughly a 6-second
   excursion. Expect to tune this against real tracks.

4. **Map runs to original ranges** via `coord_indices`, and compute `distance_m`
   by haversine over the real waypoints covered by those ranges.

### `custom/api.py`

Extend the existing `GET /api/v1/custom/activities/{id}/trail-description`. No
new endpoint.

Per trail, add:
- `latlngs` — array of sections, each an array of `[lat, lon]` pairs, sliced
  from the GPS stream by `ranges`. Drop any section left with fewer than 2
  points, so the frontend never receives a degenerate one-point section.
- `distance_m` — passed through.

`description` is unchanged.

**Backward compatibility:** rows matched before this change have no `ranges`.
Return `latlngs: []` for them so the row still renders (name, type) and simply
does not highlight. No crash, no empty tab.

### `custom/pipeline.py`

Bump `CURRENT_PIPELINE_VERSION` 3 → 4 so existing rows re-match and pick up the
new keys.

Consequence: 13 GPS activities re-query public Overpass on the next pipeline
run. This is safe to retry now that transient Overpass failures no longer stick
(`_run_trail_matching` returns a bool and `pipeline_version` is only advanced on
success).

## Frontend

### New `ActivityTrailsComponent.vue`

Mirrors `ActivitySegmentsComponent.vue`: same table shape, same
`custom-segment-hover` row class, emits `trailHover` / `trailLeave`.

Columns: Trail, Type, Distance, %.

Distance must honour the user's metric/imperial setting. Do not hand-roll unit
conversion — that is exactly the bug just fixed in the laps table.

Use `formatDistance(t, activity, units, { total_distance: trail.distance_m })`,
passing a lap-shaped adapter object. `formatDistance` routes through
`metersToKm`/`metersToMiles`, which keep 2 decimal places.

Do **not** use `formatDistanceRaw`: it formats with
`maximumFractionDigits: 0` unconditionally — the `round` parameter does not
control it — so a 4,120 m trail renders as "4 km" and an 800 m trail renders as
"0 km".

Trails have no database id, so **the trail name is the hover key**.

### `ActivitySegmentsLapsComponent.vue`

Add a third tab and a `hoveredTrailId` prop (holding a name).

Tab precedence stays `segments > laps`; trails are appended last so no existing
default-tab behaviour changes.

### `ActivityView.vue` (patch 0004)

Wire `@trailHover` → `activityMapRef?.highlightSegment(trail.latlngs)` and
`@trailLeave` → `clearSegmentHighlight()`, matching the existing segment and lap
handlers.

### `ActivityMapComponent.vue` (patch 0003)

`highlightSegment` currently takes a flat `latlngs` array and draws one
`L.polyline` plus directional arrowheads.

Change it to normalise its input to a list of sections. Leaflet's `L.polyline`
renders nested coordinate arrays as a multi-polyline natively, so layer
management and `clearSegmentHighlight()` need no changes. `segmentArrowheads` is
already an array, so the existing arrowhead computation runs per section in a
loop.

Existing flat-array callers (segments, laps) must keep working unchanged.

## Testing

`tests/test_trail_ranges.py` — pure functions, no network, no database, run with
`python3 tests/test_trail_ranges.py`:

- consecutive near-points collapse into one run
- a gap larger than `MAX_RUN_GAP_POINTS` splits into two runs
- a gap at or under the tolerance merges
- index mapping is exact under decimation (`step > 1`)
- index mapping is exact when waypoints are missing lat/lon (the case that
  breaks `i * step`)
- a single isolated near-point yields a degenerate run and does not crash
- no near-points yields no ranges

Then verify live on activity 22: hover each trail row, confirm the highlighted
sections track the trail and that multi-section trails draw no connecting lines
across the map.

## Out of scope

- Showing the full OSM trail geometry, including parts not travelled.
- Making the names in the summary paragraph individually hoverable.
- Any change to how trails are matched (thresholds, highway types, Overpass
  query).
