"""Alembic environment for custom migrations.

Uses Endurain's existing database engine but tracks migration state in
a separate table (custom_alembic_version) to avoid conflicts with
upstream's alembic_version table.
"""

from logging.config import fileConfig

from alembic import context

# Import PG18 compat before any database access
import pg18_compat  # noqa: F401

# Reuse Endurain's existing engine (reads DB config from env vars)
from core.database import engine

# Import our models so metadata includes our tables
from custom.models import Base

config = context.config

if config.attributes.get("configure_logger", True):
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Key: use a separate version table
VERSION_TABLE = "custom_alembic_version"


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
