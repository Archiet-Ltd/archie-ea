"""A DBAPI cursor class that records the statements it runs, for the tenant-context tests.

Recording at the driver means a statement is seen whichever cursor ran it,
including the side cursor the engine hook uses (SQLAlchemy's own events never see
that one). Works with either PostgreSQL driver SQLAlchemy may select.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url


def recording_cursor_class(url: str, sink: list):
    """A cursor class for ``connect_args={"cursor_factory": ...}`` that appends
    each executed statement's text to ``sink``."""
    driver = make_url(url).get_dialect().driver

    if driver == "psycopg2":
        import psycopg2.extensions as base_module

        base = base_module.cursor
    else:
        import psycopg

        base = psycopg.Cursor

    class RecordingCursor(base):
        def execute(self, query, *args, **kwargs):
            sink.append(query.decode() if isinstance(query, bytes) else str(query))
            return super().execute(query, *args, **kwargs)

    return RecordingCursor
