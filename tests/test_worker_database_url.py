"""Each parallel test worker runs on its own database.

``worker_database`` in tests/conftest.py clones a database per xdist worker and
points ``TEST_DATABASE_URL`` at it before the app boots. The testing config must
read that variable when the app is created, not when ``config.py`` is first
imported: test modules import the app package at collection time, long before
the fixture runs, so a value frozen at import sends every worker to the one
shared database. There, the row-level-security fixture's policy changes
deadlock against another worker's reads of the same tables.
"""
import os

from flask import Flask
from sqlalchemy.engine import make_url


def test_the_testing_config_reads_the_database_url_when_the_app_is_created(monkeypatch):
    from config import config

    url = make_url(os.environ["TEST_DATABASE_URL"]).set(database="archie_worker_url_probe")
    rendered = url.render_as_string(hide_password=False)
    monkeypatch.setenv("TEST_DATABASE_URL", rendered)

    probe = Flask(__name__)
    probe.config.from_object(config["testing"])
    config["testing"].init_app(probe)

    assert probe.config["SQLALCHEMY_DATABASE_URI"] == rendered


def test_a_parallel_worker_is_connected_to_its_own_database(app, request):
    from app import db

    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
    with app.app_context():
        database = db.engine.url.database
    assert database == make_url(os.environ["TEST_DATABASE_URL"]).database
    if worker_id != "master":
        assert f"_{worker_id}_" in database


def test_a_worker_copy_waits_while_another_session_is_connected_to_the_source():
    """A serial run or a shell left on the source database must not make every
    parallel worker error at setup: the copy waits for it, then succeeds."""
    import threading
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    from tests.conftest import _copy_when_source_is_free

    base = make_url(os.environ["TEST_DATABASE_URL"])
    tag = uuid.uuid4().hex[:8]
    source, copy = f"worker_copy_source_{tag}", f"worker_copy_target_{tag}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{source}"'))
        holder = create_engine(base.set(database=source), poolclass=NullPool).connect()
        threading.Timer(4, holder.close).start()
        with admin.connect() as connection:
            _copy_when_source_is_free(connection, f'"{copy}"', f'"{source}"', source, wait_seconds=30)
            assert connection.execute(
                text("SELECT count(*) FROM pg_database WHERE datname = :d"), {"d": copy}
            ).scalar_one() == 1
    finally:
        with admin.connect() as connection:
            for name in (copy, source):
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
