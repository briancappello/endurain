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


def _purge_stub_pollution():
    """Undo import-time sys.path/sys.modules pollution from standalone tests.

    The legacy standalone test files (test_trail_ranges, test_trigger,
    test_api_privacy_gates) run at COLLECTION time -- before any fixture -- and,
    to test modules in isolation, they:
      * ``sys.path.insert(0, overlay/backend/app)`` (a different app root), and
      * stub top-level app packages (``core``, ``auth``, ``custom``,
        ``activities`` ...) into ``sys.modules`` as bare ``types.ModuleType``
        objects with NO ``__path__``.

    A bare stub for e.g. ``auth`` means ``auth`` is no longer a package, so
    alembic's env.py ``import auth.identity_providers.models`` dies with
    "'auth' is not a package". This purge makes ``build/app`` the winning root
    and drops every stub that shadows a real package under it, so the migration
    imports resolve against the real code. Isolation of those tests is
    unaffected: they already imported and asserted at collection time.
    """
    app_str = str(APP)

    # 1. build/app must win over any other app root (e.g. overlay/backend/app).
    sys.path[:] = [p for p in sys.path if p and p != app_str]
    sys.path.insert(0, app_str)

    # 2. Drop any sys.modules entry whose top-level name is a real package/module
    #    under build/app but is currently NOT backed by a file there (i.e. a stub
    #    or an overlay import). alembic will re-import the real ones.
    #
    #    Exception: keep stubs a still-pending standalone test consumes lazily at
    #    RUN time. test_trigger.py binds ``custom.trigger`` at collection but its
    #    ``schedule_pipeline`` does ``from custom.pipeline import process_pending``
    #    inside the timer, re-reading ``sys.modules`` when the test runs (after
    #    this fixture). Purging ``custom.pipeline`` would make that lazy import
    #    resolve to the real pipeline and the stub's run-counter never fires. No
    #    pytest-style test imports ``custom.pipeline``/``custom.trigger``, so
    #    preserving those stub bindings is safe. Restore them after the migration.
    preserve = {}
    for name in list(sys.modules):
        top = name.split(".")[0]
        if not ((APP / top).is_dir() or (APP / f"{top}.py").exists()):
            continue  # not an app package -- leave stdlib/3rd-party alone
        mod = sys.modules.get(name)
        f = getattr(mod, "__file__", None)
        if f is None or app_str not in str(Path(f).resolve()):
            if name in ("custom.pipeline", "custom.trigger"):
                preserve[name] = mod
            del sys.modules[name]
    return preserve


@pytest.fixture(scope="session")
def _migrated_db():
    """Session-scoped: create the test DB and run both migration chains once.

    Returns the app's SQLAlchemy engine (bound to endurain_test), with the full
    model graph imported/configured as a side effect of running the migrations.
    """
    _load_env()
    sys.path.insert(0, str(APP))
    preserved_stubs = _purge_stub_pollution()
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
        # Give the standalone tests back the stub bindings they consume lazily.
        sys.modules.update(preserved_stubs)

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
