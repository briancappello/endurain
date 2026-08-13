"""Match GPS tracks against named OSM trails via Overpass API + Shapely.

Given an activity's GPS waypoints, queries Overpass for named trails/paths
in the bounding box, then uses Shapely to determine which trails the GPS
track follows (within a configurable distance threshold).

Usage as a module:
    from trail_matcher import match_trails_for_activity

Usage as a CLI tool:
    python trail_matcher.py <activity_id> [--update] [--overpass-url URL]
"""

import json
import math
import os
import sys
import time
from collections import defaultdict

import requests
from shapely.geometry import Point, LineString
from shapely.ops import transform as shapely_transform
import pyproj

import core.logger as core_logger

# --- Configuration ---

# Default Overpass API URL (public). Override with OVERPASS_API_URL env var
# or the overpass_url parameter.
DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# GPS point must be within this distance (meters) of a trail to count as "on" it
DISTANCE_THRESHOLD_M = 50

# Minimum fraction of GPS points near a trail to consider it a match
MIN_MATCH_FRACTION = 0.03

# Maximum GPS points to use (downsample if more). 500 is plenty for matching.
MAX_GPS_POINTS = 500

# Merge near-point runs separated by at most this many decimated points.
# A real GPS track briefly wanders past DISTANCE_THRESHOLD_M (tree cover,
# switchbacks, a parallel spur); without this a single trail shatters into
# dozens of one-point slivers. At a typical decimation step of 2 and ~1s
# sampling this is roughly a 6-second excursion. Tune against real tracks.
MAX_RUN_GAP_POINTS = 3

# OSM highway types to consider as trails/paths
TRAIL_HIGHWAY_TYPES = (
    "path",
    "track",
    "footway",
    "cycleway",
    "bridleway",
    "pedestrian",
    "steps",
)

# Bounding box buffer in degrees (~500m at mid-latitudes)
BBOX_BUFFER_DEG = 0.005

USER_AGENT = "Endurain/1.0 (trail-matcher)"

# --- Overpass request pacing ---
#
# These three values are real-world timing knobs, not logic: they are starting
# points tuned for the PUBLIC overpass-api.de endpoint, which rate-limits hard.
# A bulk re-match fires one query per activity back to back and roughly half
# used to come back 429/504. A self-hosted Overpass instance makes all of this
# mostly unnecessary -- there you can drop the interval to 0 and the attempts
# to 1. Tune against the endpoint you actually point at.

# Minimum wall-clock gap between two Overpass requests from this process.
OVERPASS_MIN_INTERVAL_S = 2.0

# Total tries per query, including the first, before giving up.
OVERPASS_MAX_ATTEMPTS = 3

# First backoff sleep; doubles each retry (3s, 6s, ...).
OVERPASS_BACKOFF_BASE_S = 3.0

# Monotonic time of the last Overpass request in THIS process. Module-level on
# purpose: consecutive match_trails() calls in one bulk run must be spaced out,
# and each call has no idea how many ran before it.
# ponytail: per-process only. A second worker process gets its own budget; move
# this to a shared store only if concurrent workers ever start tripping 429s.
_last_request_at = float("-inf")


class TrailMatchUnavailable(Exception):
    """The Overpass query could not be completed (rate limit, timeout, network).

    Distinct from a successful query that found no trails, which is an empty
    result. Callers must treat this as retryable and must NOT record it as
    "no trails found".
    """


def _overpass_post(overpass_url: str, query: str) -> dict:
    """POST a query to Overpass, throttled and retried, and return parsed JSON.

    Raises:
        TrailMatchUnavailable: every attempt failed. Never returns an empty
            result to signal failure -- an empty result means "no trails".
    """
    global _last_request_at

    last_error: Exception | None = None
    for attempt in range(1, OVERPASS_MAX_ATTEMPTS + 1):
        wait = OVERPASS_MIN_INTERVAL_S - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()

        try:
            resp = requests.post(
                overpass_url,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            core_logger.print_to_log_and_console(
                f"Trail matcher: Overpass query failed "
                f"(attempt {attempt}/{OVERPASS_MAX_ATTEMPTS}): {e}",
                "warning",
            )
            if attempt < OVERPASS_MAX_ATTEMPTS:
                time.sleep(OVERPASS_BACKOFF_BASE_S * 2 ** (attempt - 1))

    raise TrailMatchUnavailable(str(last_error)) from last_error


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
            Every position must be a valid index into coord_indices; an
            out-of-range position raises IndexError.
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


def _index_and_decimate(
    waypoints: list[dict], max_points: int = MAX_GPS_POINTS
) -> tuple[list[int], list[tuple[float, float]]]:
    """Filter waypoints with usable coordinates, then decimate.

    Returns (coord_indices, coords) where coord_indices[i] is the ORIGINAL
    waypoints index of coords[i], carried through both the filter and the
    decimation. Never reconstruct these as i * step: waypoints missing lat/lon
    are dropped here, so that arithmetic is silently wrong for any track with a
    gap in its GPS stream.

    Guarding on `is not None` (rather than key presence) also keeps an explicit
    null lat/lon from reaching Point() and failing the whole match.

    Fewer than 2 usable waypoints yields ([], []) -- callers cannot match a
    track with under two points.

    coords are (lon, lat) ordered, matching Shapely's x/y convention.
    """
    indexed = [
        (i, w["lon"], w["lat"])
        for i, w in enumerate(waypoints)
        if w.get("lat") is not None and w.get("lon") is not None
    ]
    if len(indexed) < 2:
        return [], []

    step = max(1, len(indexed) // max_points)
    indexed = indexed[::step]
    coord_indices = [i for i, _lon, _lat in indexed]
    coords = [(lon, lat) for _i, lon, lat in indexed]
    return coord_indices, coords


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


def match_trails(
    waypoints: list[dict],
    overpass_url: str | None = None,
    distance_threshold_m: float = DISTANCE_THRESHOLD_M,
    min_match_fraction: float = MIN_MATCH_FRACTION,
) -> list[dict]:
    """Match GPS waypoints against named OSM trails.

    Args:
        waypoints: List of dicts with "lat" and "lon" keys.
        overpass_url: Overpass API endpoint. Defaults to public API or env var.
        distance_threshold_m: Max distance in meters to consider "on trail".
        min_match_fraction: Minimum fraction of points near trail to match.

    Returns:
        List of matched trail dicts sorted by match strength:
        [{"name": str, "highway": str, "points_near": int,
          "points_total": int, "fraction": float, "avg_distance_m": float,
          "ranges": list[list[int]], "distance_m": float}, ...]

        "ranges" are inclusive [start, end] index pairs into the ORIGINAL
        waypoints list, grouped into contiguous runs. "distance_m" is the
        distance actually travelled on that trail.

        An empty list means the query succeeded and there is genuinely nothing
        to match: too few usable waypoints, or no named ways nearby. It never
        means the query failed.

    Raises:
        TrailMatchUnavailable: the Overpass query could not be completed after
            OVERPASS_MAX_ATTEMPTS tries (rate limit, timeout, network). Callers
            must retry later and must NOT record this as "no trails found".
    """
    if not waypoints or len(waypoints) < 2:
        return []

    overpass_url = overpass_url or os.getenv("OVERPASS_API_URL", DEFAULT_OVERPASS_URL)

    # --- Extract and downsample GPS points ---
    coord_indices, coords = _index_and_decimate(waypoints)
    if len(coords) < 2:
        return []

    # --- Compute bounding box ---
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    bbox = {
        "s": min(lats) - BBOX_BUFFER_DEG,
        "w": min(lons) - BBOX_BUFFER_DEG,
        "n": max(lats) + BBOX_BUFFER_DEG,
        "e": max(lons) + BBOX_BUFFER_DEG,
    }

    # --- Query Overpass for named trails ---
    highway_filter = "|".join(TRAIL_HIGHWAY_TYPES)
    query = (
        f"[out:json][timeout:25];"
        f"("
        f'  way["highway"~"^({highway_filter})$"]["name"]'
        f"     ({bbox['s']},{bbox['w']},{bbox['n']},{bbox['e']});"
        f");"
        f"out body;"
        f">;"
        f"out skel qt;"
    )

    # Raises TrailMatchUnavailable on failure. Deliberately NOT caught here:
    # returning [] would be indistinguishable from "no trails nearby", and
    # callers persist that as a terminal result.
    data = _overpass_post(overpass_url, query)

    # --- Parse Overpass response ---
    nodes = {}
    for el in data.get("elements", []):
        if el["type"] == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])

    trails = []
    for el in data.get("elements", []):
        if el["type"] == "way" and "tags" in el and "name" in el.get("tags", {}):
            trail_coords = [nodes[nid] for nid in el.get("nodes", []) if nid in nodes]
            if len(trail_coords) >= 2:
                trails.append(
                    {
                        "name": el["tags"]["name"],
                        "highway": el["tags"].get("highway", ""),
                        "osm_id": el["id"],
                        "geometry_wgs84": LineString(trail_coords),
                    }
                )

    if not trails:
        return []

    # --- Project to UTM for meter-based distances ---
    center_lat = (bbox["s"] + bbox["n"]) / 2
    center_lon = (bbox["w"] + bbox["e"]) / 2
    utm_zone = int((center_lon + 180) / 6) + 1
    hemisphere = "north" if center_lat >= 0 else "south"
    utm_crs = f"+proj=utm +zone={utm_zone} +{hemisphere} +datum=WGS84"

    transformer = pyproj.Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)

    def project(geom):
        return shapely_transform(transformer.transform, geom)

    gps_points_utm = [project(Point(lon, lat)) for lon, lat in coords]
    for trail in trails:
        trail["geometry_utm"] = project(trail["geometry_wgs84"])

    # --- Match: aggregate by trail name ---
    # Multiple OSM ways often share a name (trail split into segments).
    # We need to avoid double-counting GPS points near multiple segments
    # of the same trail.

    name_trails = defaultdict(list)
    for trail in trails:
        name_trails[trail["name"]].append(trail)

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

    results.sort(key=lambda r: r["points_near"], reverse=True)
    return results


def generate_trail_name(
    matches: list[dict],
    activity_type: int | None = None,
    max_trails: int = 3,
) -> str | None:
    """Generate an activity name from matched trails.

    Args:
        matches: Output of match_trails().
        activity_type: Endurain activity type int (4=Ride, 12=Hike, etc.).
        max_trails: Maximum number of trail names to include.

    Returns:
        Generated name string, or None if no matches.
    """
    if not matches:
        return None

    # Activity type labels
    type_labels = {
        1: "Run",
        2: "Trail Run",
        4: "Ride",
        5: "Gravel Ride",
        6: "Mountain Bike Ride",
        7: "E-Bike Ride",
        9: "Walk",
        10: "Hike",
        12: "Hike",
    }
    verb = type_labels.get(activity_type, "Activity")

    trail_names = [m["name"] for m in matches[:max_trails]]

    if len(trail_names) == 1:
        return f"{verb} on {trail_names[0]}"
    elif len(trail_names) == 2:
        return f"{verb} on {trail_names[0]} and {trail_names[1]}"
    else:
        return f"{verb} on {', '.join(trail_names[:-1])}, and {trail_names[-1]}"


def match_trails_for_activity(
    activity_id: int,
    db_session=None,
    overpass_url: str | None = None,
) -> list[dict]:
    """Match trails for an activity by its database ID.

    Fetches the GPS stream (stream_type=7) from the database and runs
    trail matching against it.

    Args:
        activity_id: The activity ID in the database.
        db_session: SQLAlchemy session. If None, creates one.
        overpass_url: Override Overpass API URL.

    Returns:
        List of matched trail dicts (same as match_trails output).
    """
    from core.database import SessionLocal

    close_session = False
    if db_session is None:
        db_session = SessionLocal()
        close_session = True

    try:
        # Fetch GPS stream (stream_type=7)
        result = db_session.execute(
            __import__("sqlalchemy").text(
                "SELECT stream_waypoints FROM activities_streams "
                "WHERE activity_id = :aid AND stream_type = 7"
            ),
            {"aid": activity_id},
        ).fetchone()

        if not result or not result[0]:
            core_logger.print_to_log_and_console(
                f"Trail matcher: No GPS stream for activity {activity_id}", "info"
            )
            return []

        waypoints = json.loads(result[0]) if isinstance(result[0], str) else result[0]
        return match_trails(waypoints, overpass_url=overpass_url)

    finally:
        if close_session:
            db_session.close()


# --- CLI ---

if __name__ == "__main__":
    import argparse

    # Load PG18 compat patch before any database access
    import pg18_compat  # noqa: F401

    parser = argparse.ArgumentParser(
        description="Match activity GPS tracks to OSM trails"
    )
    parser.add_argument("activity_id", type=int, help="Activity ID to match")
    parser.add_argument(
        "--update", action="store_true", help="Update the activity name in the database"
    )
    parser.add_argument("--overpass-url", default=None, help="Overpass API URL")
    args = parser.parse_args()

    # Need to run from the app directory for imports to work
    matches = match_trails_for_activity(
        args.activity_id, overpass_url=args.overpass_url
    )

    if not matches:
        print(f"No trail matches found for activity {args.activity_id}")
        sys.exit(0)

    print(f"\nTrail matches for activity {args.activity_id}:")
    print(
        f"{'Trail Name':40s} {'Type':10s} {'Points':>8s} {'Fraction':>10s} {'Avg Dist':>10s}"
    )
    print("-" * 82)
    for m in matches:
        print(
            f"{m['name']:40s} {m['highway']:10s} "
            f"{m['points_near']:>5d}/{m['points_total']:<5d} "
            f"{m['fraction']:>8.1%} "
            f"{m['avg_distance_m']:>8.1f}m"
        )

    # Fetch activity type for name generation
    from core.database import SessionLocal
    import sqlalchemy

    db = SessionLocal()
    row = db.execute(
        sqlalchemy.text("SELECT activity_type, name FROM activities WHERE id = :aid"),
        {"aid": args.activity_id},
    ).fetchone()
    db.close()

    if row:
        suggested_name = generate_trail_name(matches, activity_type=row[0])
        print(f"\nCurrent name:   {row[1]}")
        print(f"Suggested name: {suggested_name}")

        if args.update and suggested_name:
            db = SessionLocal()
            db.execute(
                sqlalchemy.text("UPDATE activities SET name = :name WHERE id = :aid"),
                {"name": suggested_name, "aid": args.activity_id},
            )
            db.commit()
            db.close()
            print(f"Updated activity {args.activity_id} name to: {suggested_name}")
