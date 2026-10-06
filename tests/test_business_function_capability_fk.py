"""BusinessFunction.capability_id's FK, repointed from business_capability.id
onto unified_capabilities.id.

ensure_function() in
app/modules/applications/services/application_capability_catalog.py builds
every BusinessFunction it creates from a UnifiedCapability instance (via
walk() -> get_or_create_capability()), never a BusinessCapability, and is
the only code path that ever writes this column. unified_capabilities is
this codebase's single source of truth for capability modeling; the old FK
let that write succeed only when a UnifiedCapability.id happened to collide
with a business_capability.id (the two tables have independent id
sequences) and raised a ForeignKeyViolation the rest of the time.

Five things are proved here:
  1. the declared FK metadata really targets unified_capabilities now;
  2. a UnifiedCapability.id -- the only shape ensure_function() ever writes
     -- is accepted;
  3. a capability_id that only exists in the legacy business_capability
     table is now correctly rejected, proving the constraint target really
     changed rather than the column merely accepting any integer;
  4. the migration does NOT rewrite a pre-existing row's capability_id --
     it is already a unified_capabilities id (the only shape
     ensure_function() ever wrote) and must come out of the migration
     byte-for-byte identical, not remapped through the legacy provenance
     lookup (an earlier version of this migration did exactly that, and it
     was a data-corruption bug: see the migration's own docstring);
  5. a row whose capability_id is NOT a valid unified_capabilities id (a
     genuinely orphaned/legacy-only value) makes the migration fail loudly,
     naming the row, rather than silently leaving it or guessing;
  6. running the migration twice is a clean no-op.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.models.business_capabilities import BusinessCapability, BusinessFunction
from app.models.unified_capability import UnifiedCapability

REPO_ROOT = Path(__file__).resolve().parents[1]

# The revision immediately before, and the revision that is, this repoint.
PRE_REVISION = "20261004_arb_review_source_cols"
REVISION = "20261005_bf_capability_fk"


# =========================================================================
# Model-level: ORM behaviour against the schema tests already build via
# db.create_all() (which reflects these models as they stand now).
# =========================================================================


def _unified_capability(db_session, org, name=None):
    cap = UnifiedCapability(
        name=name or f"Capability {uuid.uuid4().hex[:8]}",
        code=f"CAP-{uuid.uuid4().hex[:8]}",
        level=1,
        scope="tenant",
        organization_id=org.id,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def test_fk_metadata_targets_unified_capabilities():
    """The declared FK must point at unified_capabilities, not
    business_capability -- a static check that the model change itself
    landed, independent of any database round-trip."""
    col = BusinessFunction.__table__.c.capability_id
    targets = {fk.column.table.name for fk in col.foreign_keys}
    assert targets == {"unified_capabilities"}, (
        "BusinessFunction.capability_id must FK unified_capabilities -- the "
        "single source of truth ensure_function() actually writes ids from, "
        f"found {targets!r} instead"
    )


def test_business_function_accepts_a_unified_capability_id(db_session, make_org):
    """The bug ensure_function() hit: a UnifiedCapability.id must be a valid
    capability_id without raising ForeignKeyViolation."""
    org = make_org("bf-fk-ok")
    cap = _unified_capability(db_session, org)

    function = BusinessFunction(
        capability_id=cap.id,
        name=f"Function {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    db_session.add(function)
    db_session.flush()  # would raise ForeignKeyViolation under the old FK

    assert function.id is not None
    assert function.capability_id == cap.id
    assert function.capability is cap


def test_business_function_rejects_a_legacy_only_capability_id(db_session, make_org):
    """A capability_id that exists in the legacy business_capability table
    but not in unified_capabilities must now be refused -- proving the
    constraint target really changed, not just that the column still
    accepts any integer.

    The legacy id is pinned well outside both tables' real sequence ranges
    so this assertion is never a coincidence of two independent
    auto-increment counters landing on the same small number.
    """
    org = make_org("bf-fk-reject")
    legacy_id = 999_000_001
    legacy = BusinessCapability(
        id=legacy_id,
        name=f"Legacy Capability {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        level=1,
    )
    db_session.add(legacy)
    db_session.flush()

    # This capability is NOT itself projected into unified_capabilities
    # under this same id (projection assigns its own, unrelated primary
    # key) -- confirm that, or the test below would pass for the wrong
    # reason.
    assert UnifiedCapability.query.filter_by(id=legacy_id).first() is None

    function = BusinessFunction(
        capability_id=legacy_id,
        name=f"Function {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    db_session.add(function)
    with pytest.raises(IntegrityError):
        db_session.flush()


# =========================================================================
# Migration-level: the schema walk in
# migrations/versions/20261005_business_function_capability_fk.py, exercised
# against a real schema built up to, then through, that revision -- a
# db.create_all()-based fixture cannot exercise this, since it always
# builds the *current* (already-repointed) model shape.
# =========================================================================


def _admin_engine():
    url = make_url(
        os.environ.get("TEST_DATABASE_URL")
        or "postgresql://postgres:postgres@127.0.0.1:5432/archie_test"
    )
    return create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")


def _scratch_database_url(label):
    name = f"archie_bfcapfk_{label}_{uuid.uuid4().hex[:8]}"
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    base = make_url(
        os.environ.get("TEST_DATABASE_URL")
        or "postgresql://postgres:postgres@127.0.0.1:5432/archie_test"
    )
    return base.set(database=name).render_as_string(hide_password=False)


def _drop_scratch_database(url):
    name = make_url(url).database
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


def _run_flask(url, *args):
    env = dict(os.environ)
    env.update(DATABASE_URL=url, TEST_DATABASE_URL=url, DEV_DATABASE_URL=url)
    env.setdefault("FLASK_CONFIG", "testing")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [sys.executable, "-m", "flask", "--app", "manage", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, timeout=1800,
        encoding="utf-8", errors="replace",
    )


@pytest.fixture
def scratch_db():
    url = _scratch_database_url("fkmig")
    yield url
    _drop_scratch_database(url)


def _revert_fk_to_business_capability(url):
    """Put business_function.capability_id's FK back the way every database
    deployed before this migration actually has it.

    ``schema-upgrade --to <PRE_REVISION>`` is not enough on its own: the
    baseline revision (20260926_baseline) builds a brand-new database with
    ``db.metadata.create_all()`` against the *current* models -- which
    already declare the repointed FK, since that is this PR's whole model
    change. A fresh scratch database therefore gets the new FK for free,
    regardless of which revision you stop short at. A database that
    actually existed before this PR shipped has the OLD constraint, so
    these tests must put it there by hand before seeding legacy-shaped data
    and running the migration under test.
    """
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE business_function "
                "DROP CONSTRAINT IF EXISTS business_function_capability_id_fkey"
            ))
            conn.execute(text(
                "ALTER TABLE business_function ADD CONSTRAINT "
                "business_function_capability_id_fkey "
                "FOREIGN KEY (capability_id) REFERENCES business_capability(id)"
            ))
    finally:
        engine.dispose()


def test_migration_keeps_a_real_ensure_function_row_unchanged(scratch_db):
    """Seed a business_function row exactly the way ensure_function() always
    has (capability_id set to a real UnifiedCapability's id -- never a
    BusinessCapability's), run the migration, and assert capability_id comes
    out byte-for-byte identical, not remapped to anything else.

    This is the regression test for the corruption bug: an earlier version
    of this migration ran every row's capability_id through the legacy
    (source_table='business_capability', source_id) provenance lookup as if
    it were an unresolved legacy id, which could silently rewrite an
    already-correct unified_capabilities id to a different, unrelated one if
    any unified_capabilities row happened to have a matching source_id.
    """
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _revert_fk_to_business_capability(scratch_db)

    engine = create_engine(scratch_db)
    try:
        with engine.begin() as conn:
            org_id = conn.execute(text(
                "INSERT INTO organizations (name, slug) "
                "VALUES ('Fixture Org', :slug) RETURNING id"
            ), {"slug": f"fixture-org-{uuid.uuid4().hex[:8]}"}).scalar()
            unified_id = conn.execute(text(
                "INSERT INTO unified_capabilities "
                "(name, level, organization_id) "
                "VALUES ('Real Cap', 1, :org) RETURNING id"
            ), {"org": org_id}).scalar()
            # A row that also happens to have a unified_capabilities row
            # whose source_id matches this same numeric string -- the exact
            # shape that tricked the old, corrupting migration into
            # rewriting an already-correct value. This row proves the fix
            # leaves it alone regardless.
            decoy_unified_id = conn.execute(text(
                "INSERT INTO unified_capabilities "
                "(name, level, organization_id, source_table, source_id) "
                "VALUES ('Decoy Projected Cap', 1, :org, 'business_capability', :source_id) "
                "RETURNING id"
            ), {"org": org_id, "source_id": str(unified_id)}).scalar()
            function_id = conn.execute(text(
                "INSERT INTO business_function (name, capability_id, organization_id) "
                "VALUES ('Real Function', :cap, :org) RETURNING id"
            ), {"cap": unified_id, "org": org_id}).scalar()

        proc = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        with engine.connect() as conn:
            capability_id_after = conn.execute(text(
                "SELECT capability_id FROM business_function WHERE id = :id"
            ), {"id": function_id}).scalar()
            fk_target = conn.execute(text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conname = 'business_function_capability_id_fkey'"
            )).scalar()
    finally:
        engine.dispose()

    assert capability_id_after == unified_id, (
        f"expected capability_id to stay {unified_id} (unchanged), got "
        f"{capability_id_after} -- the migration remapped an already-correct "
        f"unified_capabilities id, likely via the decoy row {decoy_unified_id}"
    )
    assert fk_target == "unified_capabilities"


def test_migration_fails_loudly_on_a_row_with_no_valid_unified_capability(scratch_db):
    """A business_function row whose capability_id is not a valid
    unified_capabilities.id at all (a genuinely orphaned/legacy-only value)
    must fail this migration loudly, not silently drop, null, or rewrite the
    row."""
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _revert_fk_to_business_capability(scratch_db)

    engine = create_engine(scratch_db)
    try:
        with engine.begin() as conn:
            org_id = conn.execute(text(
                "INSERT INTO organizations (name, slug) "
                "VALUES ('Fixture Org', :slug) RETURNING id"
            ), {"slug": f"fixture-org-{uuid.uuid4().hex[:8]}"}).scalar()
            orphan_legacy_id = conn.execute(text(
                "INSERT INTO business_capability (name, organization_id, level) "
                "VALUES ('Orphan Legacy Cap', :org, 1) RETURNING id"
            ), {"org": org_id}).scalar()
            # Deliberately no unified_capabilities row with this id at all.
            conn.execute(text(
                "INSERT INTO business_function (name, capability_id, organization_id) "
                "VALUES ('Orphan Function', :cap, :org)"
            ), {"cap": orphan_legacy_id, "org": org_id})

        proc = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
    finally:
        engine.dispose()

    assert proc.returncode != 0
    assert "not a valid unified_capabilities.id" in (proc.stdout + proc.stderr)


def test_migration_is_idempotent_on_a_second_run(scratch_db):
    """A second invocation of this revision's upgrade() against a database
    already in the post-upgrade state (FK already targeting
    unified_capabilities) must be a clean no-op via the existing
    current-FK-target check at the top of upgrade() -- not an error from
    re-adding a constraint that already exists, and not a second,
    unnecessary validation pass that could behave differently.

    A plain second ``schema-upgrade --to <REVISION>`` would not actually
    re-invoke this revision's upgrade() at all (Alembic sees the database is
    already recorded at that revision and does nothing), so it would not
    prove anything about the function's own idempotency check. This test
    rewinds only the Alembic bookkeeping (the alembic_version row) back to
    the prior revision -- the schema itself is left exactly as the first
    upgrade left it -- so the second ``schema-upgrade`` call genuinely
    re-runs this revision's upgrade() against an already-upgraded schema,
    the scenario the current-FK-target check exists to handle (e.g. a
    retried deploy that crashed after the DDL committed but before Alembic
    recorded the new revision).
    """
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _revert_fk_to_business_capability(scratch_db)

    engine = create_engine(scratch_db)
    try:
        with engine.begin() as conn:
            org_id = conn.execute(text(
                "INSERT INTO organizations (name, slug) "
                "VALUES ('Fixture Org', :slug) RETURNING id"
            ), {"slug": f"fixture-org-{uuid.uuid4().hex[:8]}"}).scalar()
            unified_id = conn.execute(text(
                "INSERT INTO unified_capabilities "
                "(name, level, organization_id) "
                "VALUES ('Real Cap', 1, :org) RETURNING id"
            ), {"org": org_id}).scalar()
            function_id = conn.execute(text(
                "INSERT INTO business_function (name, capability_id, organization_id) "
                "VALUES ('Real Function', :cap, :org) RETURNING id"
            ), {"cap": unified_id, "org": org_id}).scalar()

        first = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert first.returncode == 0, first.stdout + first.stderr

        # Rewind only the bookkeeping, not the schema, so the second call
        # genuinely re-enters this revision's upgrade() against a database
        # already in the post-upgrade state.
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE alembic_version SET version_num = :pre"
            ), {"pre": PRE_REVISION})

        second = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert second.returncode == 0, second.stdout + second.stderr

        with engine.connect() as conn:
            capability_id_after = conn.execute(text(
                "SELECT capability_id FROM business_function WHERE id = :id"
            ), {"id": function_id}).scalar()
            fk_target = conn.execute(text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conname = 'business_function_capability_id_fkey'"
            )).scalar()
    finally:
        engine.dispose()

    assert capability_id_after == unified_id
    assert fk_target == "unified_capabilities"
