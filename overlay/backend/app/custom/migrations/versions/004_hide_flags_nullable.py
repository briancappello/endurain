"""Make per-activity hide_* columns nullable (NULL = inherit global)

Revision ID: 004
Revises: 003
Create Date: 2026-08-13

Drops the NOT NULL constraint on the 12 activities.hide_* columns so a NULL
value can mean "inherit the owner's global users_privacy_settings default".
Existing rows keep their current boolean, which now acts as an explicit
per-activity override.

ponytail: this custom-tree migration ALTERs an upstream table (activities). It
has a soft dependency on upstream's schema -- if a future upstream version drops
or renames a hide_* column, this migration and the model hybrids must be
refreshed against it.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HIDE_COLUMNS = (
    "hide_start_time",
    "hide_location",
    "hide_map",
    "hide_hr",
    "hide_power",
    "hide_cadence",
    "hide_elevation",
    "hide_speed",
    "hide_pace",
    "hide_laps",
    "hide_workout_sets_steps",
    "hide_gear",
)


def upgrade() -> None:
    for col in HIDE_COLUMNS:
        op.alter_column("activities", col, existing_type=sa.Boolean(), nullable=True)


def downgrade() -> None:
    # Backfill any NULLs to False before restoring NOT NULL, or the constraint
    # would fail on inherited rows.
    for col in HIDE_COLUMNS:
        op.execute(f"UPDATE activities SET {col} = false WHERE {col} IS NULL")
        op.alter_column("activities", col, existing_type=sa.Boolean(), nullable=False)
