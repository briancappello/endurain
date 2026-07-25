"""Add merge tracking columns to activity_metadata

Revision ID: 002
Revises: 001
Create Date: 2026-04-19
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "activity_metadata", sa.Column("merge_group", sa.UUID(), nullable=True)
    )
    op.add_column(
        "activity_metadata", sa.Column("merge_role", sa.String(20), nullable=True)
    )
    op.add_column(
        "activity_metadata", sa.Column("source", sa.String(20), nullable=True)
    )
    op.create_index(
        "ix_activity_metadata_merge_group", "activity_metadata", ["merge_group"]
    )


def downgrade() -> None:
    op.drop_index("ix_activity_metadata_merge_group")
    op.drop_column("activity_metadata", "source")
    op.drop_column("activity_metadata", "merge_role")
    op.drop_column("activity_metadata", "merge_group")
