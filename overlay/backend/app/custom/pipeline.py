"""Post-processing pipeline for activities.

Pipeline steps (in order):
  1. Merge: detect duplicate activities across sources, assign Garmin as primary
  2. Segment extraction: fetch segment efforts from Strava API
  3. Trail matching: match GPS tracks against OSM trails via Overpass

Scans for activities that need processing and runs all steps.
Each activity gets an activity_metadata row tracking what's been done.
"""

import json
import os
from datetime import datetime, timezone

from sqlalchemy import text

import core.logger as core_logger
from core.database import SessionLocal
from custom.models import ActivityMetadata
from custom.merge import run_merge
from custom.segments import extract_segments_for_activity
from trail_matcher import match_trails, generate_trail_name

# Bump this to re-process all activities on next pipeline run.
CURRENT_PIPELINE_VERSION = 3

# GPS stream type in Endurain
STREAM_TYPE_LATLON = 7


def process_pending(activity_id: int | None = None):
    """Run the post-processing pipeline on pending activities.

    Args:
        activity_id: If set, process only this activity. Otherwise process
                     all activities that need it.
    """
    db = SessionLocal()
    try:
        # Step 0: Run merge detection across all activities (always runs
        # globally, even when processing a single activity, because merge
        # candidates could be from any import batch)
        run_merge(db)

        if activity_id:
            _ensure_metadata(db, activity_id)
            _process_one(db, activity_id)
        else:
            _process_all(db)
    finally:
        db.close()


def _process_all(db):
    """Find and process all activities that need post-processing."""
    # Find activities with GPS data that either:
    # 1. Have no activity_metadata row yet, or
    # 2. Have a pipeline_version < CURRENT_PIPELINE_VERSION
    rows = db.execute(
        text("""
        SELECT a.id
        FROM activities a
        JOIN activities_streams s ON s.activity_id = a.id AND s.stream_type = :stream_type
        LEFT JOIN activity_metadata m ON m.activity_id = a.id
        WHERE m.id IS NULL
           OR m.pipeline_version < :pipeline_version
           OR m.segments_extracted = false
        ORDER BY a.start_time DESC
    """),
        {
            "stream_type": STREAM_TYPE_LATLON,
            "pipeline_version": CURRENT_PIPELINE_VERSION,
        },
    ).fetchall()

    if not rows:
        core_logger.print_to_log("Pipeline: no activities need processing")
        return

    core_logger.print_to_log(f"Pipeline: {len(rows)} activities to process")

    for (aid,) in rows:
        _ensure_metadata(db, aid)
        _process_one(db, aid)


def _ensure_metadata(db, activity_id: int):
    """Create an activity_metadata row if one doesn't exist yet."""
    existing = db.query(ActivityMetadata).filter_by(activity_id=activity_id).first()
    if not existing:
        meta = ActivityMetadata(
            activity_id=activity_id,
            trail_match_status="pending",
            pipeline_version=0,
        )
        db.add(meta)
        db.commit()


def _process_one(db, activity_id: int):
    """Run all pipeline steps on a single activity."""
    meta = db.query(ActivityMetadata).filter_by(activity_id=activity_id).first()
    if not meta:
        return

    # Skip if fully processed (correct version AND segments extracted)
    if meta.pipeline_version >= CURRENT_PIPELINE_VERSION and meta.segments_extracted:
        return

    core_logger.print_to_log(f"Pipeline: processing activity {activity_id}")

    # Step 1: Segment extraction (from Strava API)
    if not meta.segments_extracted:
        _run_segment_extraction(db, activity_id, meta)

    # Step 2: Trail matching
    _run_trail_matching(db, activity_id, meta)

    # Mark as processed at current version
    meta.pipeline_version = CURRENT_PIPELINE_VERSION
    meta.updated_at = datetime.now(timezone.utc)
    db.commit()


def _run_segment_extraction(db, activity_id: int, meta: ActivityMetadata):
    """Extract segment efforts from Strava API for an activity."""
    # Get user_id from the activity
    row = db.execute(
        text("SELECT user_id, strava_activity_id FROM activities WHERE id = :aid"),
        {"aid": activity_id},
    ).fetchone()

    if not row or not row[1]:
        # No strava_activity_id -- nothing to extract
        meta.segments_extracted = True
        return

    user_id = row[0]

    try:
        result = extract_segments_for_activity(db, activity_id, user_id)
        meta.segments_extracted = True
        if result < 0:
            core_logger.print_to_log(
                f"Pipeline: segment extraction had errors for activity {activity_id}",
                "warning",
            )
    except Exception as e:
        core_logger.print_to_log(
            f"Pipeline: segment extraction failed for activity {activity_id}: {e}",
            "warning",
        )
        # Don't mark as extracted so it can be retried
        meta.segments_extracted = False


def _run_trail_matching(db, activity_id: int, meta: ActivityMetadata):
    """Run trail matching for a single activity."""
    overpass_url = os.getenv("OVERPASS_API_URL")

    # Fetch GPS waypoints
    result = db.execute(
        text(
            "SELECT stream_waypoints FROM activities_streams "
            "WHERE activity_id = :aid AND stream_type = :st"
        ),
        {"aid": activity_id, "st": STREAM_TYPE_LATLON},
    ).fetchone()

    if not result or not result[0]:
        meta.trail_match_status = "no_trails"
        meta.trail_match_at = datetime.now(timezone.utc)
        return

    waypoints = json.loads(result[0]) if isinstance(result[0], str) else result[0]

    try:
        matches = match_trails(waypoints, overpass_url=overpass_url)
    except Exception as e:
        core_logger.print_to_log(
            f"Pipeline: trail matching failed for activity {activity_id}: {e}",
            "warning",
        )
        meta.trail_match_status = "error"
        meta.trail_match_result = {"error": str(e)}
        meta.trail_match_at = datetime.now(timezone.utc)
        return

    meta.trail_match_at = datetime.now(timezone.utc)

    if not matches:
        meta.trail_match_status = "no_trails"
        meta.trail_match_result = None
        return

    meta.trail_match_status = "matched"
    meta.trail_match_result = matches

    # Fetch current activity name and type
    activity = db.execute(
        text("SELECT name, activity_type FROM activities WHERE id = :aid"),
        {"aid": activity_id},
    ).fetchone()

    if not activity:
        return

    current_name, activity_type = activity

    # Only update the name if it's still a generic default
    generic_names = {"Workout", ""}
    if current_name in generic_names or current_name.endswith(" workout"):
        new_name = generate_trail_name(matches, activity_type=activity_type)
        if new_name:
            db.execute(
                text("UPDATE activities SET name = :name WHERE id = :aid"),
                {"name": new_name, "aid": activity_id},
            )
            core_logger.print_to_log(
                f"Pipeline: activity {activity_id} renamed to '{new_name}'"
            )
