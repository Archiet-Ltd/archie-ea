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
