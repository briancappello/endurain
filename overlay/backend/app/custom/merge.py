"""Fuzzy duplicate detection and merge logic.

Identifies activities from different sources (Garmin, Strava) that represent
the same physical activity, and merges them with Garmin as the primary source.

Matching criteria (all must be true):
  - Same user_id
  - start_time within MATCH_WINDOW_SECONDS of each other
  - distance within MATCH_DISTANCE_TOLERANCE of each other (if both have distance)

Merge actions:
  - Garmin activity becomes primary (visible), Strava becomes secondary (hidden)
  - Copy strava_activity_id onto the Garmin activity
  - Both activities share a merge_group UUID
"""

import uuid
from datetime import timedelta

from sqlalchemy import text

import core.logger as core_logger
from core.database import SessionLocal

# Matching thresholds
MATCH_WINDOW_SECONDS = 60
MATCH_DISTANCE_TOLERANCE = 0.10  # 10%


def run_merge(db=None):
    """Find and merge duplicate activities across sources.

    Scans for unmerged activities and attempts to match them with activities
    from other sources. Garmin is always primary.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        _detect_and_merge(db)
    finally:
        if close_db:
            db.close()


def _detect_and_merge(db):
    """Find duplicate pairs and merge them."""

    # Find all candidate pairs: activities from different sources with close
    # start times that haven't been merged yet.
    #
    # We look for activities where:
    #   - One has a garminconnect_activity_id and the other has a strava_activity_id
    #   - They belong to the same user
    #   - Their start times are within MATCH_WINDOW_SECONDS
    #   - Neither has been assigned a merge_group yet
    pairs = db.execute(
        text("""
        SELECT
            g.id AS garmin_id,
            s.id AS strava_id,
            g.start_time AS g_start,
            s.start_time AS s_start,
            g.distance AS g_distance,
            s.distance AS s_distance,
            g.strava_activity_id AS g_strava_id,
            s.strava_activity_id AS s_strava_id
        FROM activities g
        JOIN activities s
            ON g.user_id = s.user_id
            AND g.id != s.id
            AND ABS(EXTRACT(EPOCH FROM (g.start_time - s.start_time))) <= :window
        LEFT JOIN activity_metadata gm ON gm.activity_id = g.id
        LEFT JOIN activity_metadata sm ON sm.activity_id = s.id
        WHERE g.garminconnect_activity_id IS NOT NULL
          AND s.strava_activity_id IS NOT NULL
          AND (gm.merge_group IS NULL OR gm.id IS NULL)
          AND (sm.merge_group IS NULL OR sm.id IS NULL)
        ORDER BY g.start_time DESC
    """),
        {"window": MATCH_WINDOW_SECONDS},
    ).fetchall()

    if not pairs:
        core_logger.print_to_log("Merge: no unmerged duplicate pairs found")
        return

    core_logger.print_to_log(f"Merge: found {len(pairs)} candidate pair(s)")

    for row in pairs:
        garmin_id = row[0]
        strava_id = row[1]
        g_distance = row[4]
        s_distance = row[5]
        g_existing_strava_id = row[6]
        s_strava_activity_id = row[7]

        # Distance check (if both have distance data)
        if g_distance and s_distance and g_distance > 0 and s_distance > 0:
            ratio = abs(g_distance - s_distance) / max(g_distance, s_distance)
            if ratio > MATCH_DISTANCE_TOLERANCE:
                core_logger.print_to_log(
                    f"Merge: skipping pair garmin={garmin_id} strava={strava_id} "
                    f"-- distance mismatch ({g_distance:.0f} vs {s_distance:.0f}, {ratio:.1%})"
                )
                continue

        group_id = str(uuid.uuid4())
        core_logger.print_to_log(
            f"Merge: merging garmin={garmin_id} (primary) + strava={strava_id} (secondary) "
            f"-> group={group_id[:8]}"
        )

        # Ensure both have metadata rows
        _ensure_metadata(db, garmin_id, "garmin")
        _ensure_metadata(db, garmin_id, "garmin")

        # Mark the Garmin activity as primary
        db.execute(
            text("""
            UPDATE activity_metadata
            SET merge_group = :group_id, merge_role = 'primary', source = 'garmin', updated_at = NOW()
            WHERE activity_id = :aid
        """),
            {"group_id": group_id, "aid": garmin_id},
        )

        # Copy strava_activity_id onto the Garmin activity, then delete the
        # Strava row entirely -- it has no unique data worth keeping.
        if not g_existing_strava_id and s_strava_activity_id:
            # Delete Strava row first (clears the unique constraint on strava_activity_id)
            db.execute(
                text("DELETE FROM activities WHERE id = :sid"),
                {"sid": strava_id},
            )
            db.execute(
                text(
                    "UPDATE activities SET strava_activity_id = :strava_id WHERE id = :aid"
                ),
                {"strava_id": s_strava_activity_id, "aid": garmin_id},
            )
        else:
            # strava_activity_id already on primary, just delete the duplicate
            db.execute(
                text("DELETE FROM activities WHERE id = :sid"),
                {"sid": strava_id},
            )

        # Ensure the Garmin activity is visible
        db.execute(
            text("UPDATE activities SET is_hidden = false WHERE id = :aid"),
            {"aid": garmin_id},
        )

        db.commit()


def _ensure_metadata(db, activity_id: int, source: str):
    """Create an activity_metadata row if one doesn't exist yet."""
    existing = db.execute(
        text("SELECT id FROM activity_metadata WHERE activity_id = :aid"),
        {"aid": activity_id},
    ).fetchone()

    if not existing:
        db.execute(
            text("""
            INSERT INTO activity_metadata (activity_id, trail_match_status, pipeline_version, source, created_at, updated_at)
            VALUES (:aid, 'pending', 0, :source, NOW(), NOW())
        """),
            {"aid": activity_id, "source": source},
        )
    elif source:
        # Update source if not already set
        db.execute(
            text("""
            UPDATE activity_metadata SET source = :source, updated_at = NOW()
            WHERE activity_id = :aid AND source IS NULL
        """),
            {"aid": activity_id, "source": source},
        )
