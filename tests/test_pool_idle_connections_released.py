"""Pool-of-one hygiene: a module's idle pooled connections are released.

Each engine here is a one-connection QueuePool, as on the pool-of-one run. Three
engines stand for three test modules that each built an app; without disposal each
leaves an idle backend connection on the server, and the server runs out of
connections after about a hundred modules.
"""

from __future__ import annotations

import os
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from tests import _pool_hygiene


def _url():
    return os.environ["TEST_DATABASE_URL"]


def _backends(admin, database):
    return admin.execute(
        text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"
        ),
        {"d": database},
    ).scalar_one()


def test_idle_connections_do_not_outlive_dispose():
    _pool_hygiene.track_engines()
    engines = [
        create_engine(_url(), poolclass=QueuePool, pool_size=1, max_overflow=0)
        for _ in range(3)
    ]
    admin = create_engine(_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as watcher:
            database = watcher.execute(text("SELECT current_database()")).scalar_one()
            before = _backends(watcher, database)
            for engine in engines:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
            held = _backends(watcher, database)
            assert held - before == 3, "each pooled engine keeps one idle connection"

            _pool_hygiene.dispose_tracked_engines()

            # The server reaps a closed backend a moment after the client closes it.
            deadline = time.monotonic() + 3
            after = _backends(watcher, database)
            while after != before and time.monotonic() < deadline:
                time.sleep(0.1)
                after = _backends(watcher, database)
            assert after == before, (
                f"{after - before} idle pooled connection(s) outlived the dispose"
            )
    finally:
        for engine in engines:
            engine.dispose()
        admin.dispose()


def test_dispose_leaves_a_checked_out_connection_usable():
    _pool_hygiene.track_engines()
    engine = create_engine(_url(), poolclass=QueuePool, pool_size=1, max_overflow=0)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            _pool_hygiene.dispose_tracked_engines()
            assert connection.execute(text("SELECT 2")).scalar_one() == 2
    finally:
        engine.dispose()
