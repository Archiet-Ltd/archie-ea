"""The six-typed-tables organisation migration: backfill order, FK dedupe, downgrade.

Covers migrations/versions/20261018_typed_tables_org_id.py directly, the same
load-the-revision-module-and-run-it-against-a-real-connection pattern already
used by tests/test_api_token_migration.py. These tests exist because the real
defects here (a missing backfill source, a duplicate foreign key on a
from-scratch database, a downgrade that only knew its own expected constraint
name) were all found by running the migration against real data, not by
reading the code.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = REPO_ROOT / "migrations" / "versions" / "20261018_typed_tables_org_id.py"


def _load_revision():
    spec = importlib.util.spec_from_file_location("migration_20261018_typed_tables_org_id", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(app, times=1):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app import db

    module = _load_revision()
    for _ in range(times):
        with app.app_context():
            with db.engine.begin() as connection:
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    module.upgrade()


def _run_downgrade(app):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app import db

    module = _load_revision()
    with app.app_context():
        with db.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module.downgrade()


def _scalar(app, sql, **params):
    from app import db

    with app.app_context():
        with db.engine.connect() as connection:
            return connection.execute(text(sql), params).scalar()


def _insert(app, sql, **params):
    from app import db

    with app.app_context():
        with db.engine.begin() as connection:
            return connection.execute(text(sql), params).scalar()


def _exec(app, sql, **params):
    from app import db

    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text(sql), params)


def _make_org(app, tag):
    return _insert(
        app,
        "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id",
        n=f"Typed {tag}", s=f"typed-{tag}",
    )


def _make_archimate_element(app, org_id, tag):
    return _insert(
        app,
        "INSERT INTO archimate_elements (name, organization_id) VALUES (:n, :o) RETURNING id",
        n=f"Element {tag}", o=org_id,
    )


def _make_architecture_model(app, org_id, tag):
    return _insert(
        app,
        "INSERT INTO architecture_models (name, organization_id) VALUES (:n, :o) RETURNING id",
        n=f"Model {tag}", o=org_id,
    )


def _make_application_component(app, org_id, tag):
    return _insert(
        app,
        "INSERT INTO application_components (name, organization_id) VALUES (:n, :o) RETURNING id",
        n=f"App {tag}", o=org_id,
    )


@pytest.fixture
def two_orgs(app, _schema):
    suffix = uuid.uuid4().hex[:8]
    org_a = _make_org(app, f"a-{suffix}")
    org_b = _make_org(app, f"b-{suffix}")

    def cleanup():
        _exec(app, "DELETE FROM organizations WHERE id = ANY(:ids)", ids=[org_a, org_b])

    try:
        yield {"a": org_a, "b": org_b, "suffix": suffix}
    finally:
        cleanup()


def test_constraint_falls_back_through_sources_in_priority_order(app, two_orgs):
    """constraints: archimate_element_id, then architecture_id, then goal_id (the
    migration's own SOURCES order). A row with only the third source set must
    still backfill -- proves the fallback actually reaches past the first two."""
    org_a = two_orgs["a"]
    suffix = two_orgs["suffix"]
    goal_element = _make_archimate_element(app, org_a, f"goal-{suffix}")
    constraint_id = _insert(
        app,
        "INSERT INTO constraints (name, description, goal_id) VALUES (:n, :d, :g) RETURNING id",
        n=f"Constraint {suffix}", d="d", g=goal_element,
    )
    try:
        _run_upgrade(app)
        assert _scalar(
            app, "SELECT organization_id FROM constraints WHERE id = :i", i=constraint_id
        ) == org_a, "a constraint linked only via goal_id did not backfill from the goal element's organisation"
    finally:
        _run_downgrade(app)
        _exec(app, "DELETE FROM constraints WHERE id = :i", i=constraint_id)
        _exec(app, "DELETE FROM archimate_elements WHERE id = :i", i=goal_element)


def test_constraint_prefers_earlier_source_over_later_one(app, two_orgs):
    """A constraint with both archimate_element_id (org A) and architecture_id
    (org B) set must take A, the first source in SOURCES, not B."""
    org_a, org_b = two_orgs["a"], two_orgs["b"]
    suffix = two_orgs["suffix"]
    element = _make_archimate_element(app, org_a, f"elem-{suffix}")
    model = _make_architecture_model(app, org_b, f"model-{suffix}")
    constraint_id = _insert(
        app,
        "INSERT INTO constraints (name, description, archimate_element_id, architecture_id) "
        "VALUES (:n, :d, :e, :m) RETURNING id",
        n=f"Constraint priority {suffix}", d="d", e=element, m=model,
    )
    try:
        _run_upgrade(app)
        assert _scalar(
            app, "SELECT organization_id FROM constraints WHERE id = :i", i=constraint_id
        ) == org_a, "backfill took the second source's organisation instead of the first"
    finally:
        _run_downgrade(app)
        _exec(app, "DELETE FROM constraints WHERE id = :i", i=constraint_id)
        _exec(app, "DELETE FROM archimate_elements WHERE id = :i", i=element)
        _exec(app, "DELETE FROM architecture_models WHERE id = :i", i=model)


def test_application_collaboration_backfills_from_application_component(app, two_orgs):
    """The HIGH defect this migration was missing: a collaboration linked only
    through application_component_id (no archimate_element_id) must still
    backfill -- previously this source was absent and the row stayed NULL,
    invisible under RLS to the application's own cascade delete."""
    org_a = two_orgs["a"]
    suffix = two_orgs["suffix"]
    app_component = _make_application_component(app, org_a, f"collab-{suffix}")
    collab_id = _insert(
        app,
        "INSERT INTO application_collaborations (name, application_component_id) VALUES (:n, :c) RETURNING id",
        n=f"Collab {suffix}", c=app_component,
    )
    try:
        _run_upgrade(app)
        assert _scalar(
            app, "SELECT organization_id FROM application_collaborations WHERE id = :i", i=collab_id
        ) == org_a, "a collaboration linked only via application_component_id did not backfill"
    finally:
        _run_downgrade(app)
        _exec(app, "DELETE FROM application_collaborations WHERE id = :i", i=collab_id)
        _exec(app, "DELETE FROM application_components WHERE id = :i", i=app_component)


def test_backfill_never_overwrites_an_existing_organization(app, two_orgs):
    """A row that already carries an organisation (set some other way, e.g. an
    app-level write before this migration ever ran) must not be reassigned,
    even when a parent naming a different organisation is also present."""
    org_a, org_b = two_orgs["a"], two_orgs["b"]
    suffix = two_orgs["suffix"]
    element = _make_archimate_element(app, org_b, f"elem-preset-{suffix}")
    resource_id = _insert(
        app,
        "INSERT INTO strategy_resources (name, archimate_element_id, organization_id) "
        "VALUES (:n, :e, :o) RETURNING id",
        n=f"Resource preset {suffix}", e=element, o=org_a,
    )
    try:
        _run_upgrade(app)
        assert _scalar(
            app, "SELECT organization_id FROM strategy_resources WHERE id = :i", i=resource_id
        ) == org_a, "backfill overwrote a row that already had an organisation"
    finally:
        _run_downgrade(app)
        _exec(app, "DELETE FROM strategy_resources WHERE id = :i", i=resource_id)
        _exec(app, "DELETE FROM archimate_elements WHERE id = :i", i=element)


def test_upgrade_does_not_duplicate_an_auto_named_foreign_key(app):
    """Simulates ensure_baseline_schema having already built strategy_resources
    from today's models (TenantMixin already present): the column and an
    auto-named foreign key exist before this migration's upgrade() runs. The
    real bug this guards: the old name-only check (fk_strategy_resources_
    organization_id) never matched the auto-generated name and created a
    second, duplicate foreign key on the same column."""
    from app import db

    with db.engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE strategy_resources ADD COLUMN organization_id INTEGER"
        ))
        connection.execute(text(
            "ALTER TABLE strategy_resources ADD CONSTRAINT strategy_resources_organization_id_fkey "
            "FOREIGN KEY (organization_id) REFERENCES organizations(id)"
        ))
    try:
        _run_upgrade(app)
        fk_count = _scalar(
            app,
            "SELECT count(*) FROM pg_constraint con "
            "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey) "
            "WHERE con.contype = 'f' AND con.conrelid = to_regclass('public.strategy_resources') "
            "AND att.attname = 'organization_id'",
        )
        assert fk_count == 1, f"expected exactly one foreign key on organization_id, found {fk_count}"
    finally:
        _run_downgrade(app)


def test_downgrade_drops_whatever_foreign_key_actually_exists(app):
    """The Gemini-flagged gap: downgrade() used to look only for
    fk_{table}_organization_id by name. Renaming the constraint after a normal
    upgrade() simulates the same auto-named-FK scenario for the rollback path
    -- downgrade must still remove it (and the column), not leave it behind."""
    _run_upgrade(app)
    from app import db

    with db.engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE strategy_resources RENAME CONSTRAINT fk_strategy_resources_organization_id "
            "TO strategy_resources_organization_id_fkey"
        ))

    _run_downgrade(app)

    assert not _scalar(
        app,
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'strategy_resources' "
        "AND column_name = 'organization_id')",
    ), "downgrade left the organization_id column in place"
    remaining_fk = _scalar(
        app,
        "SELECT count(*) FROM pg_constraint con "
        "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey) "
        "WHERE con.contype = 'f' AND con.conrelid = to_regclass('public.strategy_resources') "
        "AND att.attname = 'organization_id'",
    )
    assert remaining_fk == 0, "downgrade left a renamed foreign key in place because it only looked for its own expected name"


def test_migration_runs_twice_without_error(app):
    _run_upgrade(app, times=2)  # idempotent: the second run changes nothing
    _run_downgrade(app)
