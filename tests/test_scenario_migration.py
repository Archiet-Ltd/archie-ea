"""Scenario-branch tables: the revision creates them from nothing, fences them,
and converges when it is run again or finds the tables already there."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

PATH = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261018_scenario_branches.py"
TABLES = ("scenario_branches", "scenario_changes")


def _load():
    spec = importlib.util.spec_from_file_location("scenario_branches_revision", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, fn):
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            fn()


@pytest.fixture
def owner(app):
    from app import db

    with app.app_context():
        url = db.engine.url
    engine = create_engine(url, poolclass=NullPool)
    yield engine
    engine.dispose()


def _policy_count(engine, table):
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = :t"), {"t": table}
        ).scalar_one()


def test_revision_chains_to_the_single_head():
    revision = _load()
    assert revision.revision == "20261018_scenario_branches"
    assert revision.down_revision


def test_upgrade_from_nothing_then_again_leaves_both_tables_indexed_and_fenced(owner):
    revision = _load()
    with owner.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS scenario_changes, scenario_branches CASCADE"))

    _run(owner, revision.upgrade)
    _run(owner, revision.upgrade)

    names = set(inspect(owner).get_table_names())
    assert set(TABLES) <= names
    for table in TABLES:
        columns = {c["name"] for c in inspect(owner).get_columns(table)}
        assert "organization_id" in columns
        indexed = {tuple(i["column_names"]) for i in inspect(owner).get_indexes(table)}
        assert ("organization_id",) in indexed
        assert _policy_count(owner, table) == 4
    with owner.connect() as connection:
        enabled = connection.execute(
            text("SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY(:n)"), {"n": list(TABLES)}
        ).all()
    assert all(flag for _, flag in enabled)


def test_upgrade_is_a_no_op_when_the_tables_were_already_created_from_the_models(owner):
    revision = _load()
    with owner.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS scenario_changes, scenario_branches CASCADE"))
    from app.models.scenario import ScenarioBranch, ScenarioChange

    ScenarioBranch.__table__.create(owner, checkfirst=True)
    ScenarioChange.__table__.create(owner, checkfirst=True)
    _run(owner, revision.upgrade)
    for table in TABLES:
        assert _policy_count(owner, table) == 4
