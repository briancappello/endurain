"""Patch psycopg to ensure text results are returned as str, not bytes.

psycopg 3.x with certain PostgreSQL 18 configurations returns text column
values as bytes instead of str, causing SQLAlchemy's regex-based parsers to
fail throughout the codebase (version detection, foreign key introspection,
constraint parsing, etc.).

The root fix is to register a text loader that always decodes bytes to str
on every new connection via a SQLAlchemy event listener.

Remove this patch when psycopg ships a fix upstream.
"""
from sqlalchemy import event, Engine
from psycopg.types import TypeInfo
from psycopg.adapt import Loader


class TextBytesLoader(Loader):
    """Loader that ensures text OIDs always return str."""
    def load(self, data):
        if isinstance(data, bytes):
            return data.decode("utf-8")
        if isinstance(data, memoryview):
            return bytes(data).decode("utf-8")
        return str(data)


# OIDs for text types in PostgreSQL
_TEXT_OIDS = (
    25,    # TEXT
    1043,  # VARCHAR
    18,    # CHAR
    19,    # NAME
    1042,  # BPCHAR
)


@event.listens_for(Engine, "connect")
def _register_text_loaders(dbapi_connection, connection_record):
    """Register text loaders on every new psycopg connection."""
    try:
        for oid in _TEXT_OIDS:
            dbapi_connection.adapters.register_loader(oid, TextBytesLoader)
    except Exception:
        pass  # Silently skip if the connection doesn't support this
