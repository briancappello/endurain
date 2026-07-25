"""Create segments and segment_efforts tables, add segments_extracted to activity_metadata

Revision ID: 003
Revises: 002
Create Date: 2026-04-20
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Segments table (one row per unique Strava segment)
    op.create_table(
        "segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strava_id", sa.BigInteger(), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("activity_type", sa.String(20), nullable=True),
        sa.Column("distance", sa.Float(), nullable=True),
        sa.Column("average_grade", sa.Float(), nullable=True),
        sa.Column("maximum_grade", sa.Float(), nullable=True),
        sa.Column("elevation_high", sa.Float(), nullable=True),
        sa.Column("elevation_low", sa.Float(), nullable=True),
        sa.Column("total_elevation_gain", sa.Float(), nullable=True),
        sa.Column("climb_category", sa.Integer(), nullable=True),
        sa.Column("city", sa.String(255), nullable=True),
        sa.Column("state", sa.String(255), nullable=True),
        sa.Column("country", sa.String(255), nullable=True),
        sa.Column("start_lat", sa.Float(), nullable=True),
        sa.Column("start_lon", sa.Float(), nullable=True),
        sa.Column("end_lat", sa.Float(), nullable=True),
        sa.Column("end_lon", sa.Float(), nullable=True),
        sa.Column("polyline", sa.Text(), nullable=True),
        sa.Column("hazardous", sa.Boolean(), server_default="false"),
        sa.Column("effort_count", sa.Integer(), nullable=True),
        sa.Column("athlete_count", sa.Integer(), nullable=True),
        sa.Column("star_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )

    # Segment efforts table (one row per effort = each time you ride/run a segment)
    op.create_table(
        "segment_efforts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strava_effort_id", sa.BigInteger(), unique=True, nullable=False),
        sa.Column(
            "segment_id",
            sa.Integer(),
            sa.ForeignKey("segments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activity_id",
            sa.Integer(),
            sa.ForeignKey("activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("elapsed_time", sa.Integer(), nullable=False),
        sa.Column("moving_time", sa.Integer(), nullable=True),
        sa.Column("distance", sa.Float(), nullable=True),
        sa.Column("start_date", sa.DateTime(), nullable=False),
        sa.Column("start_index", sa.Integer(), nullable=True),
        sa.Column("end_index", sa.Integer(), nullable=True),
        sa.Column("average_heartrate", sa.Float(), nullable=True),
        sa.Column("max_heartrate", sa.Float(), nullable=True),
        sa.Column("average_watts", sa.Float(), nullable=True),
        sa.Column("average_cadence", sa.Float(), nullable=True),
        sa.Column("device_watts", sa.Boolean(), nullable=True),
        sa.Column("pr_rank", sa.Integer(), nullable=True),
        sa.Column("kom_rank", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_segment_efforts_segment_id", "segment_efforts", ["segment_id"])
    op.create_index(
        "ix_segment_efforts_activity_id", "segment_efforts", ["activity_id"]
    )
    op.create_index("ix_segment_efforts_user_id", "segment_efforts", ["user_id"])

    # Add segments_extracted flag to activity_metadata
    op.add_column(
        "activity_metadata",
        sa.Column(
            "segments_extracted", sa.Boolean(), server_default="false", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("activity_metadata", "segments_extracted")
    op.drop_index("ix_segment_efforts_user_id")
    op.drop_index("ix_segment_efforts_activity_id")
    op.drop_index("ix_segment_efforts_segment_id")
    op.drop_table("segment_efforts")
    op.drop_table("segments")
