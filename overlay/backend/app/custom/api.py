"""Custom API endpoints for segments and trail data.

Two routers mirror upstream's own split:
- ``router`` at ``{ROOT_PATH}/custom`` requires an access token and enforces the
  same visibility rules as upstream's activity endpoints.
- ``public_router`` at ``{ROOT_PATH}/public/custom`` is anonymous but only ever
  resolves activities that upstream itself considers publicly shareable.

Both trees call the same private payload builders, so they cannot drift apart.
"""

import json
from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, HTTPException, Security, status
from sqlalchemy import text
from sqlalchemy.orm import Session

import activities.activity.crud as activities_crud
import activities.activity.dependencies as activities_dependencies

import auth.security as auth_security

import core.config as core_config
import core.database as core_database

router = APIRouter(prefix=core_config.ROOT_PATH + "/custom", tags=["custom"])
public_router = APIRouter(
    prefix=core_config.ROOT_PATH + "/public/custom", tags=["custom-public"]
)


@dataclass(frozen=True)
class _Privacy:
    """Per-activity privacy flags, already gated for the current caller.

    Upstream enforces these on its own stream/lap endpoints
    (activities/activity_streams/crud.py). Our custom Laps and Segments payloads
    embed the same per-effort metrics, so they MUST strip the same fields or a
    hidden metric leaks to anyone who can see the activity. The owner sees
    everything, so every flag here is already ANDed with "caller is not owner"
    at construction -- payload builders just read booleans, no ownership logic.

    hide_map / hide_laps gate GEOMETRY and the whole laps list; the rest gate
    individual metric columns.
    """

    hide_map: bool = False
    hide_laps: bool = False
    hide_hr: bool = False
    hide_power: bool = False
    hide_cadence: bool = False
    hide_elevation: bool = False
    hide_speed: bool = False
    hide_pace: bool = False


def _privacy_for(activity, is_owner: bool) -> _Privacy:
    """Build a _Privacy from a resolved activity for a given caller.

    Owners see their own hidden data, matching upstream: every flag collapses to
    False when is_owner. A public (anonymous) caller is never the owner.
    """
    if is_owner:
        return _Privacy()
    return _Privacy(
        hide_map=bool(activity.hide_map),
        hide_laps=bool(activity.hide_laps),
        hide_hr=bool(activity.hide_hr),
        hide_power=bool(activity.hide_power),
        hide_cadence=bool(activity.hide_cadence),
        hide_elevation=bool(activity.hide_elevation),
        hide_speed=bool(activity.hide_speed),
        hide_pace=bool(activity.hide_pace),
    )


def _get_waypoints(activity_id: int, db: Session):
    """Fetch the activity's GPS stream (stream_type 7) once, decoded."""
    stream_row = db.execute(
        text(
            "SELECT stream_waypoints FROM activities_streams "
            "WHERE activity_id = :aid AND stream_type = 7"
        ),
        {"aid": activity_id},
    ).fetchone()

    if not stream_row or not stream_row[0]:
        return None

    wp_data = stream_row[0]
    return json.loads(wp_data) if isinstance(wp_data, str) else wp_data


def _segments_payload(activity_id: int, db: Session, privacy: _Privacy):
    """Return segment efforts for an activity, with segment metadata.

    Each effort includes a latlng array (sliced from the activity's GPS
    stream using start_index/end_index) for map rendering, unless hide_map.
    Per-effort HR/power/cadence metrics are nulled per the matching hide_*
    flag, so a hidden metric never leaks through the segments view.
    """
    hide_map = privacy.hide_map
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

    # Fetch GPS stream once for slicing. Skipped entirely when hidden.
    # Any future NON-GEOMETRIC field must be computed OUTSIDE the `if waypoints`
    # guards below, or it would silently vanish whenever hide_map is set.
    waypoints = None if hide_map else _get_waypoints(activity_id, db)

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
                "average_heartrate": None if privacy.hide_hr else r[7],
                "max_heartrate": None if privacy.hide_hr else r[8],
                "average_watts": None if privacy.hide_power else r[9],
                "average_cadence": None if privacy.hide_cadence else r[10],
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


def _trail_description_payload(activity_id: int, db: Session, hide_map: bool):
    """Return trail match data for an activity.

    Each trail includes a latlngs array of SECTIONS (each a list of [lat, lon]
    pairs) sliced from the activity's GPS stream, so the map can highlight the
    parts of the track that ran on that trail. A trail is traversed in disjoint
    stretches, so sections must stay separate -- flattening them would draw
    straight lines across the map between them. Empty when hide_map.
    """
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

    # Fetch the GPS stream once for slicing. Skipped entirely when hidden.
    # Any future NON-GEOMETRIC field must be computed OUTSIDE the `if waypoints`
    # guard below, or it would silently vanish whenever hide_map is set.
    waypoints = None if hide_map else _get_waypoints(activity_id, db)

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


def _laps_payload(activity_id: int, db: Session, privacy: _Privacy):
    """Return laps for an activity, with GPS track slices for map rendering.

    Empty when hide_laps, matching activities/activity_laps/crud.py. Returning
    [] rather than a 404 keeps a hidden activity indistinguishable from a
    non-public one and from one that simply has no laps. Per-lap HR/power/
    cadence/speed/pace/ascent are nulled per the matching hide_* flag.
    """
    if privacy.hide_laps:
        return []

    hide_map = privacy.hide_map

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

    # Fetch GPS stream once for slicing. Skipped entirely when hidden.
    # Any future NON-GEOMETRIC field must be computed OUTSIDE the `if waypoints`
    # guards below, or it would silently vanish whenever hide_map is set.
    waypoints = None if hide_map else _get_waypoints(activity_id, db)

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
                "total_ascent": None if privacy.hide_elevation else r[8],
                "avg_heart_rate": None if privacy.hide_hr else r[9],
                "max_heart_rate": None if privacy.hide_hr else r[10],
                "avg_cadence": None if privacy.hide_cadence else r[11],
                "avg_power": None if privacy.hide_power else r[12],
                "intensity": r[13],
                "enhanced_avg_speed": (
                    None
                    if privacy.hide_speed or r[14] is None
                    else float(r[14])
                ),
                "enhanced_avg_pace": (
                    None
                    if privacy.hide_pace or r[15] is None
                    else float(r[15])
                ),
                "latlngs": latlngs,
            }
        )

    return results


def _resolve_for_token_user(
    activity_id: int, token_user_id: int, db: Session
) -> _Privacy:
    """Resolve an activity for an authenticated caller, or 404.

    Returns a _Privacy already gated for this caller (owners see everything),
    resolved in a single query so the flags cannot drift. Matches
    activities/activity_streams/crud.py and activities/activity_laps/crud.py:
    only the owner sees their own hidden data.
    """
    activity = activities_crud.get_activity_by_id_from_user_id_or_has_visibility(
        activity_id, token_user_id, db
    )
    if not activity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found"
        )
    return _privacy_for(activity, is_owner=activity.user_id == token_user_id)


# Authenticated tree


@router.get("/activities/{activity_id}/segments")
def get_activity_segments(
    activity_id: int,
    validate_id: Annotated[
        Callable, Depends(activities_dependencies.validate_activity_id)
    ],
    _check_scopes: Annotated[
        Callable, Security(auth_security.check_scopes, scopes=["activities:read"])
    ],
    token_user_id: Annotated[int, Depends(auth_security.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
):
    privacy = _resolve_for_token_user(activity_id, token_user_id, db)
    return _segments_payload(activity_id, db, privacy)


@router.get("/activities/{activity_id}/trail-description")
def get_activity_trail_description(
    activity_id: int,
    validate_id: Annotated[
        Callable, Depends(activities_dependencies.validate_activity_id)
    ],
    _check_scopes: Annotated[
        Callable, Security(auth_security.check_scopes, scopes=["activities:read"])
    ],
    token_user_id: Annotated[int, Depends(auth_security.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
):
    privacy = _resolve_for_token_user(activity_id, token_user_id, db)
    return _trail_description_payload(activity_id, db, privacy.hide_map)


@router.get("/activities/{activity_id}/laps")
def get_activity_laps(
    activity_id: int,
    validate_id: Annotated[
        Callable, Depends(activities_dependencies.validate_activity_id)
    ],
    _check_scopes: Annotated[
        Callable, Security(auth_security.check_scopes, scopes=["activities:read"])
    ],
    token_user_id: Annotated[int, Depends(auth_security.get_sub_from_access_token)],
    db: Annotated[Session, Depends(core_database.get_db)],
):
    privacy = _resolve_for_token_user(activity_id, token_user_id, db)
    return _laps_payload(activity_id, db, privacy)


# Public tree. A public viewer is never the owner, so hide_map and hide_laps
# always apply.
# Unresolvable activities get the empty payload rather than a 404, matching
# upstream's public routers, which return None instead of leaking existence.


@public_router.get("/activities/{activity_id}/segments")
def get_public_activity_segments(
    activity_id: int,
    validate_id: Annotated[
        Callable, Depends(activities_dependencies.validate_activity_id)
    ],
    db: Annotated[Session, Depends(core_database.get_db)],
):
    activity = activities_crud.get_activity_by_id_if_is_public(activity_id, db)
    if not activity:
        return []
    return _segments_payload(activity_id, db, _privacy_for(activity, is_owner=False))


@public_router.get("/activities/{activity_id}/trail-description")
def get_public_activity_trail_description(
    activity_id: int,
    validate_id: Annotated[
        Callable, Depends(activities_dependencies.validate_activity_id)
    ],
    db: Annotated[Session, Depends(core_database.get_db)],
):
    activity = activities_crud.get_activity_by_id_if_is_public(activity_id, db)
    if not activity:
        return {"description": None, "trails": []}
    # trail-description carries no personal metrics, only hide_map geometry.
    return _trail_description_payload(activity_id, db, bool(activity.hide_map))


@public_router.get("/activities/{activity_id}/laps")
def get_public_activity_laps(
    activity_id: int,
    validate_id: Annotated[
        Callable, Depends(activities_dependencies.validate_activity_id)
    ],
    db: Annotated[Session, Depends(core_database.get_db)],
):
    activity = activities_crud.get_activity_by_id_if_is_public(activity_id, db)
    if not activity:
        return []
    return _laps_payload(activity_id, db, _privacy_for(activity, is_owner=False))
