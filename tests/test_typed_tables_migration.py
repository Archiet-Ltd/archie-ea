"""The six-typed-tables organisation migration: backfill order, FK dedupe, downgrade.

Covers migrations/versions/20261018_typed_tables_org_id.py (and its companion
migrations/versions/20261018_typed_tables_rls.py for the downgrade-with-RLS
case) directly, by loading each revision module and running its upgrade()/
downgrade() through Alembic's MigrationContext/Operations against a real
connection -- the same mechanism tests/test_api_token_migration.py uses.

Deliberately does NOT use the app/_schema fixtures: db.create_all() builds
every table from today's models, and this PR's own models already carry
TenantMixin on these six tables, so create_all() would hand the migration a
database where organization_id already exists (NOT NULL) before the migration
under test ever runs -- the "test real deploy order, not create_all" trap
(see tests/test_schema_migrations.py's own scratch_databases fixture, which
avoids the same trap for the baseline/relax/widen chain by running the real
deploy commands against a fresh database rather than create_all()). Each test
here instead builds only the real pre-migration shape of the six tables and
their parents directly from the verified current model columns -- no
organization_id on them at all, matching exactly what a database looks like
the moment before this migration runs in a real deploy.

Each test gets its own throwaway database (create/drop per test), not a
shared session-scoped one: the real downgrade() this migration runs mutates
schema globally (drops organization_id from six tables), so sharing a
database or engine across tests makes every test after the first
order-dependent on what the previous one left behind.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parents[1]
ORG_ID_MIGRATION = REPO_ROOT / "migrations" / "versions" / "20261018_typed_tables_org_id.py"
RLS_MIGRATION = REPO_ROOT / "migrations" / "versions" / "20261018_typed_tables_rls.py"

_DEFAULT_URL = "postgresql://postgres:postgres@127.0.0.1:5432/archie_test"


def _base_parts():
    return urlsplit(os.environ.get("TEST_DATABASE_URL") or _DEFAULT_URL)


# The real pre-migration shape of the six tables and their parents, built
# directly from the current model columns (verified against app/models/
# models.py's ConstraintElement, application_layer.py's ApplicationService/
# ApplicationInteraction/ApplicationCollaboration, implementation_migration.py's
# Deliverable, strategy_layer.py's StrategyResource, archimate_core.py's
# ArchitectureModel) -- no organization_id anywhere, exactly as these tables
# stood immediately before this migration.
_SCHEMA = """
CREATE TABLE organizations (id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL, slug VARCHAR(255) NOT NULL UNIQUE);

CREATE TABLE archimate_elements (
    id SERIAL PRIMARY KEY, name VARCHAR(500) NOT NULL, organization_id INTEGER NOT NULL REFERENCES organizations(id)
);

CREATE TABLE architecture_models (
    id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL, organization_id INTEGER NOT NULL REFERENCES organizations(id)
);

CREATE TABLE business_actors (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL, organization_id INTEGER NOT NULL REFERENCES organizations(id)
);

CREATE TABLE application_components (
    id SERIAL PRIMARY KEY, name VARCHAR(256) NOT NULL, organization_id INTEGER NOT NULL REFERENCES organizations(id)
);

CREATE TABLE unified_work_packages (
    id BIGSERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL, organization_id INTEGER NOT NULL REFERENCES organizations(id)
);

CREATE TABLE work_packages (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL, organization_id INTEGER NOT NULL REFERENCES organizations(id)
);

CREATE TABLE constraints (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL, description TEXT NOT NULL,
    archimate_element_id INTEGER REFERENCES archimate_elements(id),
    architecture_id INTEGER REFERENCES architecture_models(id),
    goal_id INTEGER REFERENCES archimate_elements(id)
);

CREATE TABLE strategy_resources (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
    archimate_element_id INTEGER REFERENCES archimate_elements(id),
    owning_organization_id INTEGER REFERENCES business_actors(id)
);

CREATE TABLE application_services (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
    archimate_element_id INTEGER REFERENCES archimate_elements(id),
    application_component_id INTEGER REFERENCES application_components(id)
);

CREATE TABLE application_interactions (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
    archimate_element_id INTEGER REFERENCES archimate_elements(id),
    application_component_id INTEGER REFERENCES application_components(id),
    source_component_id INTEGER REFERENCES application_components(id),
    target_component_id INTEGER REFERENCES application_components(id)
);

CREATE TABLE application_collaborations (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
    archimate_element_id INTEGER REFERENCES archimate_elements(id),
    application_component_id INTEGER REFERENCES application_components(id)
);

CREATE TABLE deliverables (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
    archimate_element_id INTEGER REFERENCES archimate_elements(id),
    unified_work_package_id BIGINT REFERENCES unified_work_packages(id),
    work_package_id INTEGER REFERENCES work_packages(id),
    application_component_id INTEGER REFERENCES application_components(id),
    architecture_id INTEGER REFERENCES architecture_models(id)
);
"""


@pytest.fixture
def pre_migration_db():
    """A fresh, isolated database in the real pre-migration shape. Dropped after."""
    parts = _base_parts()
    name = f"archie_typed_tables_{uuid.uuid4().hex[:10]}"
    admin_url = urlunsplit(parts._replace(scheme="postgresql+psycopg2", path="/postgres"))
    scratch_url = urlunsplit(parts._replace(scheme="postgresql+psycopg2", path=f"/{name}"))
    admin = create_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))  # nosec B608 - generated name
    engine = create_engine(scratch_url, poolclass=NullPool)
    with engine.begin() as conn:
        for stmt in _SCHEMA.strip().split(";\n\n"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))  # nosec B608 - generated name
        admin.dispose()


def _load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, module, direction="upgrade"):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(module, direction)()


def _run_upgrade(engine, times=1):
    module = _load(ORG_ID_MIGRATION)
    for _ in range(times):
        _run(engine, module, "upgrade")


def _run_downgrade(engine):
    _run(engine, _load(ORG_ID_MIGRATION), "downgrade")


def _scalar(engine, sql, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def _insert(engine, sql, **params):
    with engine.begin() as connection:
        return connection.execute(text(sql), params).scalar()


def _exec(engine, sql, **params):
    with engine.begin() as connection:
        connection.execute(text(sql), params)


def _make_org(engine, tag):
    return _insert(
        engine, "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id",
        n=f"Typed {tag}", s=f"typed-{tag}-{uuid.uuid4().hex[:6]}",
    )


def test_constraint_falls_back_through_sources_in_priority_order(pre_migration_db):
    """constraints: archimate_element_id, then architecture_id, then goal_id. A
    row with only the third source set must still backfill."""
    engine = pre_migration_db
    org_a = _make_org(engine, "a")
    goal_element = _insert(
        engine, "INSERT INTO archimate_elements (name, organization_id) VALUES ('goal', :o) RETURNING id", o=org_a
    )
    constraint_id = _insert(
        engine, "INSERT INTO constraints (name, description, goal_id) VALUES ('c', 'd', :g) RETURNING id", g=goal_element
    )

    _run_upgrade(engine)

    assert _scalar(engine, "SELECT organization_id FROM constraints WHERE id = :i", i=constraint_id) == org_a, (
        "a constraint linked only via goal_id did not backfill from the goal element's organisation"
    )


def test_constraint_prefers_earlier_source_over_later_one(pre_migration_db):
    """A constraint with both archimate_element_id (org A) and architecture_id
    (org B) set must take A, the first source in SOURCES, not B."""
    engine = pre_migration_db
    org_a = _make_org(engine, "a")
    org_b = _make_org(engine, "b")
    element = _insert(
        engine, "INSERT INTO archimate_elements (name, organization_id) VALUES ('e', :o) RETURNING id", o=org_a
    )
    model = _insert(
        engine, "INSERT INTO architecture_models (name, organization_id) VALUES ('m', :o) RETURNING id", o=org_b
    )
    constraint_id = _insert(
        engine,
        "INSERT INTO constraints (name, description, archimate_element_id, architecture_id) "
        "VALUES ('c', 'd', :e, :m) RETURNING id",
        e=element, m=model,
    )

    _run_upgrade(engine)

    assert _scalar(engine, "SELECT organization_id FROM constraints WHERE id = :i", i=constraint_id) == org_a, (
        "backfill took the second source's organisation instead of the first"
    )


def test_application_collaboration_backfills_from_application_component(pre_migration_db):
    """The HIGH defect found in the first review round: a collaboration linked
    only through application_component_id (no archimate_element_id) must still
    backfill."""
    engine = pre_migration_db
    org_a = _make_org(engine, "a")
    app_component = _insert(
        engine, "INSERT INTO application_components (name, organization_id) VALUES ('ac', :o) RETURNING id", o=org_a
    )
    collab_id = _insert(
        engine,
        "INSERT INTO application_collaborations (name, application_component_id) VALUES ('collab', :c) RETURNING id",
        c=app_component,
    )

    _run_upgrade(engine)

    assert _scalar(engine, "SELECT organization_id FROM application_collaborations WHERE id = :i", i=collab_id) == org_a


def test_application_interaction_backfills_from_source_and_target_component(pre_migration_db):
    """The MED defect found in the second review round: source_component_id
    and target_component_id (both -> application_components) were not used as
    backfill sources at all, same shape as the collaboration fix above."""
    engine = pre_migration_db
    org_a = _make_org(engine, "a")
    org_b = _make_org(engine, "b")
    source_comp = _insert(
        engine, "INSERT INTO application_components (name, organization_id) VALUES ('src', :o) RETURNING id", o=org_a
    )
    target_comp = _insert(
        engine, "INSERT INTO application_components (name, organization_id) VALUES ('tgt', :o) RETURNING id", o=org_b
    )
    via_source = _insert(
        engine,
        "INSERT INTO application_interactions (name, source_component_id) VALUES ('via-source', :c) RETURNING id",
        c=source_comp,
    )
    via_target = _insert(
        engine,
        "INSERT INTO application_interactions (name, target_component_id) VALUES ('via-target', :c) RETURNING id",
        c=target_comp,
    )

    _run_upgrade(engine)

    assert _scalar(engine, "SELECT organization_id FROM application_interactions WHERE id = :i", i=via_source) == org_a, (
        "an interaction linked only via source_component_id did not backfill"
    )
    assert _scalar(engine, "SELECT organization_id FROM application_interactions WHERE id = :i", i=via_target) == org_b, (
        "an interaction linked only via target_component_id did not backfill"
    )


def test_strategy_resource_backfills_from_owning_organization_via_business_actor(pre_migration_db):
    """owning_organization_id points to business_actors, not organizations
    directly (despite the name) -- BusinessActor is TenantMixin, so the join
    still resolves the real tenant organisation. A row linked only through it
    (no archimate_element_id) must still backfill."""
    engine = pre_migration_db
    org_a = _make_org(engine, "a")
    actor = _insert(
        engine, "INSERT INTO business_actors (name, organization_id) VALUES ('dept', :o) RETURNING id", o=org_a
    )
    resource_id = _insert(
        engine,
        "INSERT INTO strategy_resources (name, owning_organization_id) VALUES ('r', :a) RETURNING id", a=actor,
    )

    _run_upgrade(engine)

    assert _scalar(engine, "SELECT organization_id FROM strategy_resources WHERE id = :i", i=resource_id) == org_a


def test_backfill_never_overwrites_an_existing_organization(pre_migration_db):
    """A row that already carries an organisation (written some other way
    before this migration runs -- it adds the column nullable first, then
    backfills) must not be reassigned, even when a parent naming a different
    organisation is also present."""
    engine = pre_migration_db
    org_a = _make_org(engine, "a")
    org_b = _make_org(engine, "b")
    element = _insert(
        engine, "INSERT INTO archimate_elements (name, organization_id) VALUES ('e', :o) RETURNING id", o=org_b
    )
    resource_id = _insert(
        engine,
        "INSERT INTO strategy_resources (name, archimate_element_id) VALUES ('r', :e) RETURNING id", e=element,
    )
    # The real sequence this simulates: the column is added (nullable) by this
    # same migration's own upgrade(), and something sets it before the
    # backfill UPDATE reaches this row -- the WHERE organization_id IS NULL
    # guard is what this test actually exercises, so set it directly between
    # column-add and backfill isn't reachable from outside the migration; the
    # fixture instead pre-adds the column exactly as upgrade() would and
    # presets a value, proving the guard, not the column-add step.
    _exec(engine, "ALTER TABLE strategy_resources ADD COLUMN organization_id INTEGER")
    _exec(engine, "UPDATE strategy_resources SET organization_id = :o WHERE id = :i", o=org_a, i=resource_id)

    _run_upgrade(engine)

    assert _scalar(engine, "SELECT organization_id FROM strategy_resources WHERE id = :i", i=resource_id) == org_a, (
        "backfill overwrote a row that already had an organisation"
    )


def test_upgrade_does_not_duplicate_an_auto_named_foreign_key(pre_migration_db):
    """Simulates ensure_baseline_schema having already built application_services
    from today's models (TenantMixin already present) before this migration's
    upgrade() ever runs: the column and an auto-named foreign key exist first.
    The real bug this guards: the old name-only check (fk_application_services_
    organization_id) never matched the auto-generated name and created a
    second, duplicate foreign key on the same column."""
    engine = pre_migration_db
    _exec(engine, "ALTER TABLE application_services ADD COLUMN organization_id INTEGER")
    _exec(
        engine,
        "ALTER TABLE application_services ADD CONSTRAINT application_services_organization_id_fkey "
        "FOREIGN KEY (organization_id) REFERENCES organizations(id)",
    )

    _run_upgrade(engine)

    fk_count = _scalar(
        engine,
        "SELECT count(*) FROM pg_constraint con "
        "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey) "
        "WHERE con.contype = 'f' AND con.conrelid = to_regclass('public.application_services') "
        "AND att.attname = 'organization_id'",
    )
    assert fk_count == 1, f"expected exactly one foreign key on organization_id, found {fk_count}"


def test_migration_runs_twice_without_error(pre_migration_db):
    _run_upgrade(pre_migration_db, times=2)  # idempotent: the second run changes nothing


def test_downgrade_drops_whatever_foreign_key_actually_exists(pre_migration_db):
    """The Gemini-flagged gap: downgrade() used to look only for
    fk_{table}_organization_id by name. Renaming the constraint after a normal
    upgrade() simulates the same auto-named-FK scenario for the rollback path."""
    engine = pre_migration_db
    _run_upgrade(engine)
    _exec(
        engine,
        "ALTER TABLE strategy_resources RENAME CONSTRAINT fk_strategy_resources_organization_id "
        "TO strategy_resources_organization_id_fkey_renamed",
    )

    _run_downgrade(engine)

    assert not _scalar(
        engine,
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'strategy_resources' AND column_name = 'organization_id')",
    ), "downgrade left the organization_id column in place"
    remaining_fk = _scalar(
        engine,
        "SELECT count(*) FROM pg_constraint con "
        "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey) "
        "WHERE con.contype = 'f' AND con.conrelid = to_regclass('public.strategy_resources') "
        "AND att.attname = 'organization_id'",
    )
    assert remaining_fk == 0, "downgrade left a renamed foreign key in place because it only looked for its own expected name"


def test_downgrade_with_row_level_security_already_applied(pre_migration_db):
    """The HIGH defect found in the third review round: on a real deploy, this
    migration's own companion (20261018_typed_tables_rls.py) runs right after
    it and adds row-level security policies that reference organization_id.
    A rollback that walks back through both revisions in the real chain order
    (rls's downgrade() first, then this one's) must still work -- this proves
    the column drop does not fail against policies that genuinely depend on
    it, rather than only ever being exercised with no RLS present at all."""
    engine = pre_migration_db
    org_module = _load(ORG_ID_MIGRATION)
    rls_module = _load(RLS_MIGRATION)

    _run(engine, org_module, "upgrade")
    _run(engine, rls_module, "upgrade")

    policy_count = _scalar(
        engine,
        "SELECT count(*) FROM pg_policy pol JOIN pg_class c ON c.oid = pol.polrelid "
        "WHERE c.relname = 'constraints'",
    )
    assert policy_count > 0, "setup didn't actually create row-level security policies to test against"

    # Real downgrade order: the later revision (rls) unwinds first.
    _run(engine, rls_module, "downgrade")
    _run(engine, org_module, "downgrade")

    assert not _scalar(
        engine,
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'constraints' AND column_name = 'organization_id')",
    ), "downgrade left organization_id in place once row-level security was also unwound first"
