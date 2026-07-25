"""Create activity_metadata table

Revision ID: 001
Revises: None
Create Date: 2026-04-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op


revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_metadata",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "activity_id",
            sa.Integer(),
            sa.ForeignKey("activities.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        # Trail matching
        sa.Column(
            "trail_match_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("trail_match_result", JSONB(), nullable=True),
        sa.Column("trail_match_at", sa.DateTime(), nullable=True),
        # Pipeline versioning
        sa.Column("pipeline_version", sa.Integer(), nullable=False, server_default="0"),
        # Timestamps
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_activity_metadata_activity_id", "activity_metadata", ["activity_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_activity_metadata_activity_id")
    op.drop_table("activity_metadata")
