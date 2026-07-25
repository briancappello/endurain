"""Custom API endpoints for segments and trail data.

Registered as a FastAPI sub-application mounted at /api/v1/custom/.
These endpoints are public (no auth required) to match upstream's
public activity sharing pattern.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text

from core.database import SessionLocal

router = APIRouter(prefix="/api/v1/custom", tags=["custom"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/activities/{activity_id}/segments")
def get_activity_segments(activity_id: int, db=Depends(get_db)):
    """Return segment efforts for an activity, with segment metadata.

    Each effort includes a latlng array (sliced from the activity's GPS
    stream using start_index/end_index) for map rendering.
    """
    import json

    rows = db.execute(
        text("""
        SELECT
            se.id,
            s.name,
            se.elapsed_time,
            se.moving_time,
            se.distance,
            se.start_index,
            se.end_index,
            se.average_heartrate,
            se.max_heartrate,
            se.average_watts,
            se.average_cadence,
            se.pr_rank,
            se.kom_rank,
            s.distance AS segment_distance,
            s.average_grade,
            s.maximum_grade,
            s.climb_category,
            s.strava_id AS segment_strava_id
        FROM segment_efforts se
        JOIN segments s ON s.id = se.segment_id
        WHERE se.activity_id = :aid
        ORDER BY se.start_index ASC NULLS LAST
    """),
        {"aid": activity_id},
    ).fetchall()

    if not rows:
        return []

    # Fetch GPS stream once for slicing
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

    results = []
    for r in rows:
        start_idx = r[5]
        end_idx = r[6]

        # Slice GPS track for this segment
        latlngs = []
        if waypoints and start_idx is not None and end_idx is not None:
            segment_slice = waypoints[start_idx : end_idx + 1]
            latlngs = [
                [wp["lat"], wp["lon"]]
                for wp in segment_slice
                if wp.get("lat") and wp.get("lon")
            ]

        results.append(
            {
                "id": r[0],
                "name": r[1],
                "elapsed_time": r[2],
                "moving_time": r[3],
                "distance": r[4],
                "start_index": start_idx,
                "end_index": end_idx,
                "average_heartrate": r[7],
                "max_heartrate": r[8],
                "average_watts": r[9],
                "average_cadence": r[10],
                "pr_rank": r[11],
                "kom_rank": r[12],
                "segment_distance": r[13],
                "average_grade": r[14],
                "maximum_grade": r[15],
                "climb_category": r[16],
                "segment_strava_id": r[17],
                "latlngs": latlngs,
            }
        )

    return results


@router.get("/activities/{activity_id}/trail-description")
def get_activity_trail_description(activity_id: int, db=Depends(get_db)):
    """Return trail match data for an activity."""
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

    return {
        "description": desc,
        "trails": [
            {
                "name": t["name"],
                "fraction": t["fraction"],
                "points_near": t["points_near"],
                "avg_distance_m": t["avg_distance_m"],
            }
            for t in trails
        ],
    }


@router.get("/activities/{activity_id}/laps")
def get_activity_laps(activity_id: int, db=Depends(get_db)):
    """Return laps for an activity, with GPS track slices for map rendering."""
    import json

    rows = db.execute(
        text("""
        SELECT
            id,
            start_position_lat,
            start_position_long,
            end_position_lat,
            end_position_long,
            total_elapsed_time,
            total_timer_time,
            total_distance,
            total_ascent,
            avg_heart_rate,
            max_heart_rate,
            avg_cadence,
            avg_power,
            intensity,
            enhanced_avg_speed,
            enhanced_avg_pace
        FROM activity_laps
        WHERE activity_id = :aid
        ORDER BY start_time ASC
        """),
        {"aid": activity_id},
    ).fetchall()

    if not rows:
        return []

    # Fetch GPS stream once for slicing
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

    def nearest_idx(target_lat, target_lon):
        if not waypoints:
            return None
        best = 0
        best_dist = float("inf")
        for i, wp in enumerate(waypoints):
            lat = wp.get("lat") or wp.get("latitude")
            lon = wp.get("lon") or wp.get("longitude")
            if lat is None or lon is None:
                continue
            dlat = lat - target_lat
            dlon = lon - target_lon
            dist = dlat * dlat + dlon * dlon
            if dist < best_dist:
                best_dist = dist
                best = i
        return best

    results = []
    for r in rows:
        start_lat = r[1]
        start_lon = r[2]
        end_lat = r[3]
        end_lon = r[4]

        start_idx = None
        end_idx = None
        latlngs = []

        if waypoints and start_lat is not None and start_lon is not None:
            start_idx = nearest_idx(float(start_lat), float(start_lon))
        if waypoints and end_lat is not None and end_lon is not None:
            end_idx = nearest_idx(float(end_lat), float(end_lon))

        if start_idx is not None and end_idx is not None:
            si = min(start_idx, end_idx)
            ei = max(start_idx, end_idx)
            segment_slice = waypoints[si : ei + 1]
            latlngs = [
                [wp["lat"], wp["lon"]]
                for wp in segment_slice
                if wp.get("lat") and wp.get("lon")
            ]

        results.append(
            {
                "id": r[0],
                "total_elapsed_time": float(r[5]) if r[5] is not None else None,
                "total_timer_time": float(r[6]) if r[6] is not None else None,
                "total_distance": float(r[7]) if r[7] is not None else None,
                "total_ascent": r[8],
                "avg_heart_rate": r[9],
                "max_heart_rate": r[10],
                "avg_cadence": r[11],
                "avg_power": r[12],
                "intensity": r[13],
                "enhanced_avg_speed": float(r[14]) if r[14] is not None else None,
                "enhanced_avg_pace": float(r[15]) if r[15] is not None else None,
                "latlngs": latlngs,
            }
        )

    return results
