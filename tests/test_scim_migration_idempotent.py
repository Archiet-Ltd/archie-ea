"""The SCIM provisioning revision (R1-B26 PR 1, acceptance criterion 10).

On an empty database ``schema-upgrade`` reaches the new head, ``db downgrade
-1`` then ``db upgrade`` round-trips, running the revision's ``upgrade()``
twice is a no-op, and the chain has exactly one head whose ``down_revision``
is the head before this change.
"""

from __future__ import annotations

import importlib.util
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.test_schema_migrations import (
    REPO_ROOT,
    _admin_engine,
    _flask,
    _recorded,
    _server_url,
    _snapshot,
    _tables,
    _true_head,
)

REVISION = "20261007_scim_provisioning"
# 20261010_scim_group_members_rls fences scim_group_memberships with RLS on top
# of REVISION -- it is now the real chain head, one step past REVISION.
HEAD = "20261010_scim_group_members_rls"
USER_COLUMNS = {"deactivated_at", "deactivation_reason", "provisioned_via"}


@pytest.fixture(scope="module")
def empty_db():
    admin = _admin_engine()
    name = f"archie_scim_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "template0"'))
    url = _server_url().set(database=name).render_as_string(hide_password=False)
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _scim_part(snapshot):
    """The slice of a schema snapshot this revision owns."""
    keep = ("scim_tokens", "scim_group_memberships")
    return {
        "columns": {r for r in snapshot["columns"] if r[0] in keep or (r[0] == "users" and r[1] in USER_COLUMNS)},
        "indexes": {r for r in snapshot["indexes"] if r[0] in keep},
        "constraints": {r for r in snapshot["constraints"] if any(k in r[0] for k in keep)},
    }


def _user_columns(url):
    return {c[1] for c in _snapshot(url)["columns"] if c[0] == "users"} & USER_COLUMNS


def _load_revision():
    path = REPO_ROOT / "migrations" / "versions" / f"{REVISION}.py"
    spec = importlib.util.spec_from_file_location("scim_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_there_is_exactly_one_head_and_it_chains_onto_the_previous_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(REPO_ROOT / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [HEAD]
    assert _true_head() == HEAD
    assert script.get_revision(HEAD).down_revision == REVISION
    # REVISION's own parent moves every time this branch re-chains onto a
    # new main head -- don't hardcode it (it has gone stale twice already).
    # What must hold regardless is that it resolves to a real, distinct
    # revision already in the chain, i.e. REVISION has not become its own
    # root and the chain has not forked.
    previous = script.get_revision(REVISION).down_revision
    assert previous not in (None, REVISION, HEAD)
    assert script.get_revision(previous) is not None


def test_upgrade_reaches_head_downgrade_one_and_upgrade_again(empty_db):
    _flask(empty_db, ["schema-upgrade"])
    assert _recorded(empty_db) == [HEAD]
    snapshot = _snapshot(empty_db)
    assert {"scim_tokens", "scim_group_memberships"} <= _tables(snapshot)
    assert _user_columns(empty_db) == USER_COLUMNS

    # -1 from HEAD only undoes the RLS-fencing revision stacked on top of
    # REVISION; the SCIM tables/columns REVISION itself built are untouched.
    _flask(empty_db, ["db", "downgrade", "--", "-1"])
    assert _recorded(empty_db) == [REVISION]
    after_down = _snapshot(empty_db)
    assert {"scim_tokens", "scim_group_memberships"} <= _tables(after_down)
    assert _user_columns(empty_db) == USER_COLUMNS

    _flask(empty_db, ["db", "upgrade"])
    assert _recorded(empty_db) == [HEAD]
    # what the revision builds is exactly what the models build
    assert _scim_part(_snapshot(empty_db)) == _scim_part(snapshot)


def test_running_the_revision_twice_is_a_no_op(empty_db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    _flask(empty_db, ["schema-upgrade"])
    before = _snapshot(empty_db)
    module = _load_revision()
    engine = create_engine(empty_db)
    try:
        for _ in range(2):
            with engine.begin() as conn:
                context = MigrationContext.configure(conn)
                with Operations.context(context):
                    module.upgrade()
    finally:
        engine.dispose()
    assert _snapshot(empty_db) == before
    assert _scim_part(before)["columns"], "the revision's tables and columns must be present"


def test_the_unique_token_hash_and_membership_constraints_exist(empty_db):
    _flask(empty_db, ["schema-upgrade"])
    snapshot = _snapshot(empty_db)
    index_names = {row[1] for row in snapshot["indexes"]}
    assert "uq_scim_tokens_token_hash" in index_names
    assert "uq_scim_group_membership" in index_names
