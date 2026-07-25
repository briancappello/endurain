"""Custom database models (1:1 extension of upstream Endurain tables)."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID


from core.database import Base

# Import ALL upstream models so SQLAlchemy can resolve relationships.
# This mirrors what upstream's alembic/env.py does.
import auth.identity_providers.models  # noqa: F401
import auth.mfa_backup_codes.models  # noqa: F401
import auth.oauth_state.models  # noqa: F401
import auth.idp_link_tokens.models  # noqa: F401
import activities.activity.models  # noqa: F401
import activities.activity_exercise_titles.models  # noqa: F401
import activities.activity_laps.models  # noqa: F401
import activities.activity_media.models  # noqa: F401
import activities.activity_sets.models  # noqa: F401
import activities.activity_streams.models  # noqa: F401
import activities.activity_workout_steps.models  # noqa: F401
import followers.models  # noqa: F401
import gears.gear.models  # noqa: F401
import gears.gear_components.models  # noqa: F401
import health.health_sleep.models  # noqa: F401
import health.health_steps.models  # noqa: F401
import health.health_targets.models  # noqa: F401
import health.health_weight.models  # noqa: F401
import migrations.models  # noqa: F401
import notifications.models  # noqa: F401
import password_reset_tokens.models  # noqa: F401
import sign_up_tokens.models  # noqa: F401
import server_settings.models  # noqa: F401
import users.users_sessions.models  # noqa: F401
import users.users_sessions.rotated_refresh_tokens.models  # noqa: F401
import users.users.models  # noqa: F401
import users.users_goals.models  # noqa: F401
import users.users_default_gear.models  # noqa: F401
import users.users_identity_providers.models  # noqa: F401
import users.users_integrations.models  # noqa: F401
import users.users_privacy_settings.models  # noqa: F401


class ActivityMetadata(Base):
    """Extension table for activity post-processing state.

    One-to-one with the upstream activities table. Cascade-deletes when
    the parent activity is removed.
    """

    __tablename__ = "activity_metadata"

    id = Column(Integer, primary_key=True)
    activity_id = Column(
        Integer,
        ForeignKey("activities.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Merge tracking
    merge_group = Column(
        UUID, nullable=True, index=True
    )  # shared UUID for merged activities
    merge_role = Column(String(20), nullable=True)  # 'primary', 'secondary'
    source = Column(String(20), nullable=True)  # 'garmin', 'strava', 'upload'

    # Segment extraction
    segments_extracted = Column(Boolean, default=False, nullable=False)

    # Trail matching
    trail_match_status = Column(
        String(20), default="pending", nullable=False
    )  # pending, matched, no_trails, error
    trail_match_result = Column(JSONB)  # full match output from trail_matcher
    trail_match_at = Column(DateTime)

    # Pipeline versioning -- bump CURRENT_PIPELINE_VERSION in pipeline.py
    # to re-process all activities
    pipeline_version = Column(Integer, default=0, nullable=False)

    # Timestamps
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Segment(Base):
    """A Strava segment (a specific section of road/trail)."""

    __tablename__ = "segments"

    id = Column(Integer, primary_key=True)
    strava_id = Column(BigInteger, unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    activity_type = Column(String(20))  # 'Ride', 'Run'
    distance = Column(Float)  # meters
    average_grade = Column(Float)  # percent
    maximum_grade = Column(Float)  # percent
    elevation_high = Column(Float)  # meters
    elevation_low = Column(Float)  # meters
    total_elevation_gain = Column(Float)  # meters
    climb_category = Column(Integer)  # 0-5 (0=uncategorized, 5=HC)
    city = Column(String(255))
    state = Column(String(255))
    country = Column(String(255))
    start_lat = Column(Float)
    start_lon = Column(Float)
    end_lat = Column(Float)
    end_lon = Column(Float)
    polyline = Column(Text)  # encoded polyline from DetailedSegment
    hazardous = Column(Boolean, default=False)
    effort_count = Column(Integer)  # global effort count from Strava
    athlete_count = Column(Integer)  # global unique athletes from Strava
    star_count = Column(Integer)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), nullable=False)


class SegmentEffort(Base):
    """A single effort on a segment (one pass through it during an activity)."""

    __tablename__ = "segment_efforts"

    id = Column(Integer, primary_key=True)
    strava_effort_id = Column(BigInteger, unique=True, nullable=False)
    segment_id = Column(
        Integer,
        ForeignKey("segments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_id = Column(
        Integer,
        ForeignKey("activities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(Integer, nullable=False, index=True)
    elapsed_time = Column(Integer, nullable=False)  # seconds
    moving_time = Column(Integer)  # seconds
    distance = Column(Float)  # meters
    start_date = Column(DateTime, nullable=False)
    start_index = Column(Integer)
    end_index = Column(Integer)
    average_heartrate = Column(Float)
    max_heartrate = Column(Float)
    average_watts = Column(Float)
    average_cadence = Column(Float)
    device_watts = Column(Boolean)
    pr_rank = Column(Integer)  # 1=PR, 2=2nd, 3=3rd, null=not top 3
    kom_rank = Column(Integer)  # 1-10 if global top 10, null otherwise
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
