"""One element per compliance control per organisation, on a deployed database.

``deploy-schema.sh`` runs ``init-db``, ``schema-upgrade``, then ``reconcile-schema``. The
revision ``20261021_control_element_unique`` merges duplicate control elements and adds the unique
index. These tests build the state a real deploy finds, and do not rely on ``create_all`` for the index:

- a hand-built scratch database holding duplicates, to read what the merge did;
- a database built by ``init-db`` and brought to the previous head by ``schema-upgrade``, its
  index removed, then ``schema-upgrade`` run, to see the index come from the revision alone.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

# The chain, deploy and scratch-database helpers of the schema migration tests.
from test_schema_migrations import (  # noqa: F401  (fixtures are used by name)
    _flask,
    _recorded,
    _true_head,
    deployed_db,
    deployed_template,
    scratch_databases,
)

REPO = Path(__file__).resolve().parents[1]
REVISION_FILE = REPO / "migrations" / "versions" / "20261021_control_element_unique.py"
REVISION = "20261021_control_element_unique"
PREVIOUS_HEAD = "20261018_tree_models_rls"
INDEX = "uq_archimate_elements_org_control"


def _load():
    spec = importlib.util.spec_from_file_location("control_element_unique_under_test", REVISION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, step):
    module = _load()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, step)()


def _index_present(engine):
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() AND indexname = :n"), {"n": INDEX}
        ).first() is not None


def test_the_revision_sits_on_the_previous_head():
    module = _load()
    assert module.revision == REVISION
    assert module.down_revision == PREVIOUS_HEAD
    assert len(module.revision) <= 32, "alembic_version holds 32 characters"


@pytest.fixture()
def scratch():
    base = os.environ["TEST_DATABASE_URL"]
    parts = urlsplit(base)
    name = f"ctl_uniq_{uuid.uuid4().hex[:10]}"
    admin = create_engine(
        urlunsplit(parts._replace(scheme="postgresql+psycopg2", path="/postgres")),
        poolclass=NullPool, isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))  # nosec B608 - generated name
    engine = create_engine(
        urlunsplit(parts._replace(scheme="postgresql+psycopg2", path=f"/{name}")), poolclass=NullPool
    )
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE archimate_elements (id serial PRIMARY KEY, organization_id integer, "
            "name text, type text, custom_properties json)"
        ))
        connection.execute(text(
            "CREATE TABLE archimate_relationships (id serial PRIMARY KEY, organization_id integer, "
            "type text, source_id integer, target_id integer, architecture_id integer)"
        ))
        rows = [
            # organisation 1, control 7: three elements (ids 1, 2, 3); organisation 2, control 7: one (4)
            (1, "Requirement", '{"compliance_control_id": 7}'),
            (1, "Requirement", '{"compliance_control_id": 7}'),
            (1, "Constraint", '{"compliance_control_id": 7}'),
            (2, "Requirement", '{"compliance_control_id": 7}'),
            (1, "Requirement", '{"compliance_control_id": 8}'),  # 5: a different control, one element
            (1, "ApplicationComponent", "{}"),  # 6: an ordinary element
            (1, "Node", None),  # 7: an element with no properties
        ]
        for org, kind, props in rows:
            connection.execute(
                text("INSERT INTO archimate_elements (organization_id, name, type, custom_properties) "
                     "VALUES (:o, 'x', :t, CAST(:p AS json))"),
                {"o": org, "t": kind, "p": props},
            )
        # element 6 realises the control through two different duplicates, element 7 through the third
        for source, target in ((6, 2), (6, 3), (7, 3), (6, 5)):
            connection.execute(
                text("INSERT INTO archimate_relationships (organization_id, type, source_id, target_id) "
                     "VALUES (1, 'realization', :s, :t)"),
                {"s": source, "t": target},
            )
        # a link between two duplicates would become a loop on the kept element
        connection.execute(text(
            "INSERT INTO archimate_relationships (organization_id, type, source_id, target_id) "
            "VALUES (1, 'association', 2, 3)"
        ))
    yield engine
    engine.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))  # nosec B608


def test_the_merge_keeps_one_element_per_control_and_points_the_links_at_it(scratch):
    _run(scratch, "upgrade")

    with scratch.connect() as connection:
        holders = connection.execute(text(
            "SELECT organization_id, id FROM archimate_elements "
            "WHERE custom_properties ->> 'compliance_control_id' = '7' ORDER BY id"
        )).all()
        assert holders == [(1, 1), (2, 4)]
        merged = connection.execute(text(
            "SELECT id, custom_properties ->> 'merged_into_element_id' FROM archimate_elements "
            "WHERE custom_properties ->> 'merged_into_element_id' IS NOT NULL ORDER BY id"
        )).all()
        assert merged == [(2, "1"), (3, "1")], "the duplicates stay on record and name the element kept"
        assert connection.execute(text("SELECT count(*) FROM archimate_elements")).scalar() == 7, "no element deleted"
        links = connection.execute(text(
            "SELECT source_id, target_id, type FROM archimate_relationships ORDER BY id"
        )).all()
        # 6->2 and 6->3 became one 6->1; 7->3 became 7->1; 6->5 untouched; the 2->3 association
        # would be a loop on element 1 and is gone.
        assert sorted(links) == [(6, 1, "realization"), (6, 5, "realization"), (7, 1, "realization")]


def test_the_index_is_created_and_refuses_a_second_element(scratch):
    _run(scratch, "upgrade")
    assert _index_present(scratch)
    with pytest.raises(IntegrityError):
        with scratch.begin() as connection:
            connection.execute(text(
                "INSERT INTO archimate_elements (organization_id, name, type, custom_properties) "
                "VALUES (1, 'again', 'Requirement', CAST('{\"compliance_control_id\": 8}' AS json))"
            ))
    with scratch.begin() as connection:  # another organisation, and any number of ordinary elements, are fine
        connection.execute(text(
            "INSERT INTO archimate_elements (organization_id, name, type, custom_properties) "
            "VALUES (3, 'other org', 'Requirement', CAST('{\"compliance_control_id\": 8}' AS json)), "
            "(1, 'plain', 'Node', NULL), (1, 'plain 2', 'Node', NULL)"
        ))


def test_a_second_run_changes_nothing_and_the_downgrade_drops_only_the_index(scratch):
    _run(scratch, "upgrade")
    query_elements = "SELECT id, custom_properties::text FROM archimate_elements ORDER BY id"
    with scratch.connect() as connection:
        before = connection.execute(text(query_elements)).all()
        before_links = connection.execute(text("SELECT id FROM archimate_relationships ORDER BY id")).all()
    _run(scratch, "upgrade")
    with scratch.connect() as connection:
        assert connection.execute(text(query_elements)).all() == before
        assert connection.execute(text("SELECT id FROM archimate_relationships ORDER BY id")).all() == before_links
    _run(scratch, "downgrade")
    assert not _index_present(scratch)
    with scratch.connect() as connection:
        assert connection.execute(text(query_elements)).all() == before


def test_a_real_deploy_gets_the_index_from_the_revision_not_from_create_all(deployed_db):
    """init-db builds the tables (the model carries the index too); take the index away, bring
    the chain to the previous head, then schema-upgrade must put it back and record the revision."""
    url = deployed_db
    _flask(url, ["schema-upgrade", "--to", PREVIOUS_HEAD])
    assert _recorded(url) == [PREVIOUS_HEAD]
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))  # nosec B608 - literal name
        assert not _index_present(engine)

        _flask(url, ["schema-upgrade"])

        assert _recorded(url) == [_true_head()]
        assert _index_present(engine)
    finally:
        engine.dispose()
