"""Custom fork layer for Endurain.

Provides:
- Separate Alembic migrations (tracked in custom_alembic_version table)
- activity_metadata table for post-processing state
- Custom API endpoints at /api/v1/custom/
- CLI for sync and post-processing pipeline
"""

import os
from pathlib import Path

from alembic.config import Config
from alembic import command

import core.logger as core_logger

# Import models so SQLAlchemy knows about them
import custom.models  # noqa: F401

CUSTOM_DIR = Path(__file__).parent


def run_migrations():
    """Run custom Alembic migrations against the shared database.

    Uses a separate version table (custom_alembic_version) so our migration
    state doesn't interfere with upstream Endurain's alembic_version table.
    """
    alembic_ini = CUSTOM_DIR / "alembic.ini"
    if not alembic_ini.exists():
        core_logger.print_to_log(
            f"Custom alembic.ini not found at {alembic_ini}, skipping custom migrations",
            "warning",
        )
        return

    cfg = Config(str(alembic_ini))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    core_logger.print_to_log("Custom migrations applied successfully")


def register_api(app):
    """Register custom API routes with the FastAPI app."""
    from custom.api import router

    app.include_router(router)
    core_logger.print_to_log("Custom API routes registered")
