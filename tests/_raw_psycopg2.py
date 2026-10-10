"""A raw psycopg2 connection for tests that exercise database triggers directly.

Those tests compose SQL with ``psycopg2.sql`` and assert on ``psycopg2.Error``.
``db.engine.raw_connection()`` hands back whichever driver SQLAlchemy picked for
the URL, and from SQLAlchemy 2.1 a plain ``postgresql://`` URL selects psycopg 3,
so the same database refusal surfaced as ``psycopg.errors.CheckViolation`` and
the assertion failed although the trigger worked. Connecting through psycopg2
explicitly keeps these tests independent of that default.
"""

from __future__ import annotations

import psycopg2


def raw_psycopg2_connection(engine):
    """A new psycopg2 connection to the database ``engine`` points at."""
    url = engine.url
    kwargs = url.translate_connect_args(username="user", database="dbname")
    kwargs.update(dict(url.query))
    return psycopg2.connect(**kwargs)
