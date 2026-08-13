"""Shared pytest fixtures for real-database backend tests.

Design (per project decision):
  * A dedicated, isolated database ``endurain_test`` -- never the dev DB.
  * Migrations (upstream alembic + custom fork alembic) run ONCE per session,
    exactly as the app does at startup (main.py: command.upgrade then
    custom.run_migrations). This registers the full SQLAlchemy model graph and
    mirrors production schema, including Task 1's nullable hide_* columns.
  * Each test gets a clean slate via a connection-level transaction that is
    ROLLED BACK at test end -- no cross-test bleed, no per-test re-migration.

Env comes from local/.env (the same file run.sh loads). DB_DATABASE is forced
to the test DB so a misconfigured run can never touch dev data.

Run:  build/app/.venv/bin/python -m pytest tests/ -v
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "build" / "app"
ENV_FILE = ROOT / "local" / ".env"

TEST_DB = "endurain_test"


def _load_env():
    """Load local/.env into os.environ, then pin the test DB + dirs."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())
    # Force the isolated test database -- never the dev DB.
    os.environ["DB_DATABASE"] = TEST_DB
    os.environ.setdefault("BACKEND_DIR", str(APP))
    os.environ.setdefault("FRONTEND_DIR", str(APP))
    os.environ.setdefault("DATA_DIR", "/tmp/opencode/endtest-data")
    os.environ.setdefault("LOGS_DIR", "/tmp/opencode/endtest-logs")
    Path(os.environ["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(os.environ["LOGS_DIR"]).mkdir(parents=True, exist_ok=True)


def _ensure_test_database():
    """(Re)create endurain_test so every session starts from an empty schema.

    Connects to the maintenance ``postgres`` DB as the app role (which has
    CREATEDB) and drops+recreates the test DB. template0 dodges a local
    template1 collation-version mismatch.
    """
    import psycopg

    dsn = (
        f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT', '5432')} "
        f"user={os.environ['DB_USER']} password={os.environ['DB_PASSWORD']} "
        f"dbname=postgres"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (TEST_DB,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
            cur.execute(f'CREATE DATABASE "{TEST_DB}" TEMPLATE template0')


@pytest.fixture(scope="session")
def _migrated_db():
    """Session-scoped: create the test DB and run both migration chains once.

    Returns the app's SQLAlchemy engine (bound to endurain_test), with the full
    model graph imported/configured as a side effect of running the migrations.
    """
    _load_env()
    sys.path.insert(0, str(APP))
    _ensure_test_database()

    # cwd must be build/app so alembic.ini + custom/alembic.ini resolve.
    prev_cwd = os.getcwd()
    os.chdir(APP)
    try:
        from alembic.config import Config
        from alembic import command

        cfg = Config("alembic.ini")
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")  # upstream schema (imports all models)

        import custom

        custom.run_migrations()  # custom fork migrations: hide_* -> nullable
    finally:
        os.chdir(prev_cwd)

    from sqlalchemy.orm import configure_mappers

    configure_mappers()

    from core.database import engine

    return engine


@pytest.fixture()
def db(_migrated_db):
    """Function-scoped session with per-test rollback isolation.

    Opens a connection, begins an outer transaction, binds a Session to it, and
    rolls the whole thing back at teardown -- so each test sees a clean slate
    regardless of what it inserted.
    """
    from sqlalchemy.orm import Session

    connection = _migrated_db.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()
