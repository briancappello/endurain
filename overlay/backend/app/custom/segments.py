"""Strava segment extraction and storage.

Fetches segment efforts from Strava's API for activities that have a
strava_activity_id, and stores the segments and efforts in our custom tables.

For each unique segment, also fetches the DetailedSegment (with polyline)
via a separate API call (one-time cost per segment).
"""

from datetime import datetime, timezone

from sqlalchemy import text

import core.logger as core_logger
import users.users_integrations.crud as user_integrations_crud
import strava.utils as strava_utils

from custom.models import Segment, SegmentEffort


def extract_segments_for_activity(db, activity_id: int, user_id: int):
    """Fetch and store segment efforts from Strava for a given activity.

    Args:
        db: SQLAlchemy session.
        activity_id: The local activity ID (Garmin primary).
        user_id: The Endurain user ID.

    Returns:
        Number of segment efforts stored, or -1 on error.
    """
    # Get the strava_activity_id from the activity
    row = db.execute(
        text("SELECT strava_activity_id FROM activities WHERE id = :aid"),
        {"aid": activity_id},
    ).fetchone()

    if not row or not row[0]:
        core_logger.print_to_log(
            f"Segments: activity {activity_id} has no strava_activity_id, skipping"
        )
        return 0

    strava_activity_id = row[0]

    # Get Strava client
    try:
        strava_client = _get_strava_client(db, user_id)
    except Exception as e:
        core_logger.print_to_log(
            f"Segments: failed to get Strava client for user {user_id}: {e}",
            "warning",
        )
        return -1

    if not strava_client:
        core_logger.print_to_log(
            f"Segments: no Strava integration for user {user_id}, skipping"
        )
        return 0

    # Fetch detailed activity with all segment efforts
    try:
        detailed = strava_client.get_activity(
            strava_activity_id, include_all_efforts=True
        )
    except Exception as e:
        core_logger.print_to_log(
            f"Segments: failed to fetch Strava activity {strava_activity_id}: {e}",
            "warning",
        )
        return -1

    if not detailed.segment_efforts:
        core_logger.print_to_log(
            f"Segments: activity {activity_id} has no segment efforts"
        )
        return 0

    efforts_stored = 0

    for effort in detailed.segment_efforts:
        if not effort.segment or not effort.segment.id:
            continue

        # Upsert the segment
        segment = _upsert_segment(db, effort.segment, strava_client)
        if not segment:
            continue

        # Insert the effort (skip if already exists)
        if _insert_effort(db, effort, segment.id, activity_id, user_id):
            efforts_stored += 1

    db.commit()

    core_logger.print_to_log(
        f"Segments: stored {efforts_stored} efforts for activity {activity_id} "
        f"(strava {strava_activity_id})"
    )
    return efforts_stored


def _get_strava_client(db, user_id: int):
    """Get an authenticated stravalib Client for a user."""
    user_integrations = user_integrations_crud.get_user_integrations_by_user_id(
        user_id, db
    )
    if not user_integrations or not user_integrations.strava_token:
        return None

    # Refresh token if needed
    strava_utils.refresh_user_strava_token(user_id, db)

    # Re-fetch after potential refresh
    user_integrations = user_integrations_crud.get_user_integrations_by_user_id(
        user_id, db
    )
    return strava_utils.create_strava_client(user_integrations)


def _upsert_segment(db, strava_segment, strava_client) -> Segment | None:
    """Insert or update a segment. Fetches DetailedSegment for polyline if needed.

    Returns the Segment ORM object, or None on failure.
    """
    existing = db.query(Segment).filter_by(strava_id=strava_segment.id).first()

    if existing:
        # Update basic fields that might change
        existing.name = strava_segment.name or existing.name
        if strava_segment.effort_count:
            existing.effort_count = strava_segment.effort_count
        if strava_segment.athlete_count:
            existing.athlete_count = strava_segment.athlete_count
        existing.updated_at = datetime.now(timezone.utc)
        return existing

    # New segment -- extract fields from the SummarySegment.
    # Use getattr() for fields that may not exist on SummarySegment
    # (only present on DetailedSegment).
    segment = Segment(
        strava_id=strava_segment.id,
        name=strava_segment.name or "Unknown Segment",
        activity_type=str(strava_segment.activity_type)
        if strava_segment.activity_type
        else None,
        distance=float(strava_segment.distance) if strava_segment.distance else None,
        average_grade=strava_segment.average_grade,
        maximum_grade=strava_segment.maximum_grade,
        elevation_high=float(strava_segment.elevation_high)
        if strava_segment.elevation_high
        else None,
        elevation_low=float(strava_segment.elevation_low)
        if strava_segment.elevation_low
        else None,
        climb_category=strava_segment.climb_category,
        city=getattr(strava_segment, "city", None),
        state=getattr(strava_segment, "state", None),
        country=getattr(strava_segment, "country", None),
        hazardous=getattr(strava_segment, "hazardous", False) or False,
    )

    # Extract lat/lon (safely)
    start_ll = getattr(strava_segment, "start_latlng", None)
    end_ll = getattr(strava_segment, "end_latlng", None)
    if start_ll:
        segment.start_lat = start_ll.lat
        segment.start_lon = start_ll.lon
    if end_ll:
        segment.end_lat = end_ll.lat
        segment.end_lon = end_ll.lon

    # Fetch DetailedSegment for polyline and extra fields
    try:
        detailed = strava_client.get_segment(strava_segment.id)
        if detailed:
            if detailed.map and detailed.map.polyline:
                segment.polyline = detailed.map.polyline
            if detailed.total_elevation_gain:
                segment.total_elevation_gain = float(detailed.total_elevation_gain)
            if detailed.effort_count:
                segment.effort_count = detailed.effort_count
            if detailed.athlete_count:
                segment.athlete_count = detailed.athlete_count
            if detailed.star_count:
                segment.star_count = detailed.star_count
    except Exception as e:
        core_logger.print_to_log(
            f"Segments: failed to fetch detailed segment {strava_segment.id}: {e}",
            "warning",
        )
        # Continue without polyline -- we still have the basic segment data

    db.add(segment)
    db.flush()  # get the auto-generated id
    return segment


def _insert_effort(
    db, strava_effort, segment_id: int, activity_id: int, user_id: int
) -> bool:
    """Insert a segment effort if it doesn't already exist.

    Returns True if inserted, False if skipped (duplicate).
    """
    existing = (
        db.query(SegmentEffort).filter_by(strava_effort_id=strava_effort.id).first()
    )
    if existing:
        return False

    effort = SegmentEffort(
        strava_effort_id=strava_effort.id,
        segment_id=segment_id,
        activity_id=activity_id,
        user_id=user_id,
        elapsed_time=strava_effort.elapsed_time,
        moving_time=getattr(strava_effort, "moving_time", None),
        distance=float(strava_effort.distance) if strava_effort.distance else None,
        start_date=strava_effort.start_date,
        start_index=getattr(strava_effort, "start_index", None),
        end_index=getattr(strava_effort, "end_index", None),
        average_heartrate=getattr(strava_effort, "average_heartrate", None),
        max_heartrate=getattr(strava_effort, "max_heartrate", None),
        average_watts=getattr(strava_effort, "average_watts", None),
        average_cadence=getattr(strava_effort, "average_cadence", None),
        device_watts=getattr(strava_effort, "device_watts", None),
        pr_rank=getattr(strava_effort, "pr_rank", None),
        kom_rank=getattr(strava_effort, "kom_rank", None),
    )

    db.add(effort)
    return True
