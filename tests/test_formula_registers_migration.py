"""formula_registers table creation (migrations/versions/20261006_formula_registers.py),
made idempotent against a table that already exists.

``formula_registers`` is already a mapped model
(``app/models/formula_register.py``), so anything that builds a schema from
the current models -- the baseline revision's own ``ensure_baseline_schema``
(``migrations/versions/20260926_baseline.py``, which runs ``create_all()``
against current models on an empty database) and ``flask init-db`` alike --
can create this table before this revision's ``upgrade()`` ever runs. A bare
``op.create_table()`` then fails with ``DuplicateTable: relation
"formula_registers" already exists"``, which is exactly what happened on CI
(main's "Database gates / schema drift" run): a scratch test database built
by stepping through the full migration chain hits the baseline step first,
which already creates formula_registers from the current model, and this
revision's own, later ``upgrade()`` then collided with it.

Each scenario below first drops ``formula_registers`` after reaching the
revision immediately before this one, so the "table absent" starting point
is asserted and constructed explicitly rather than assumed -- the baseline's
own create_all() already means a plain "step the chain up to the prior
revision" does NOT by itself leave the table absent, so the tests do not
rely on happening to still have it missing.

Three things are proved here:
  1. a database where the table is genuinely absent: upgrade() creates it
     correctly, with both indexes present;
  2. upgrade() run a SECOND time immediately after (a retried/re-run deploy)
     is a clean no-op, not a duplicate-object error;
  3. a database where formula_registers was already created some OTHER way
     first -- via ``db.metadata.create_all()`` against the current model,
     reproducing what a reconciled/fresh database's schema-build step does
     ahead of ``schema-upgrade`` -- is also a clean no-op, not
     ``DuplicateTable``. This is the exact CI failure mode, not just the
     simpler "ran twice" case above.
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

REPO_ROOT = Path(__file__).resolve().parents[1]

# The revision immediately before, and the revision that is, this table's
# creation.
PRE_REVISION = "20261004_acr_escalated_at"
REVISION = "20261006_formula_registers"


def _admin_engine():
    url = make_url(
        os.environ.get("TEST_DATABASE_URL")
        or "postgresql://postgres:postgres@127.0.0.1:5432/archie_test"
    )
    return create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")


def _scratch_database_url(label):
    name = f"archie_formulareg_{label}_{uuid.uuid4().hex[:8]}"
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
    url = _scratch_database_url("mig")
    yield url
    _drop_scratch_database(url)


def _table_and_indexes(engine):
    with engine.connect() as conn:
        table_exists = conn.execute(
            text("SELECT to_regclass('formula_registers')")
        ).scalar()
        index_names = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'formula_registers'"
                )
            )
        }
    return table_exists, index_names


def _drop_formula_registers_table(engine):
    """Force the "table absent" starting point this revision's own
    down_revision conceptually represents.

    The baseline revision's own ``ensure_baseline_schema`` already runs
    ``create_all()`` against the CURRENT models on an empty database --
    which includes ``formula_registers``, since it is already a mapped
    model -- so a plain "step the chain up to PRE_REVISION" does not, by
    itself, leave the table absent (confirmed: it is already present
    immediately after stepping to PRE_REVISION on a freshly created scratch
    database). Dropping it explicitly here makes the "absent" precondition
    asserted and constructed, not assumed.
    """
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS formula_registers CASCADE"))


def test_migration_creates_table_and_indexes_on_a_fresh_database(scratch_db):
    """Scenario 1: a fresh database state (table doesn't exist yet) -- the
    table gets created correctly, with both indexes present."""
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    engine = create_engine(scratch_db)
    try:
        _drop_formula_registers_table(engine)
        table_exists_before, _ = _table_and_indexes(engine)
        assert table_exists_before is None

        proc = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        table_exists_after, index_names = _table_and_indexes(engine)
    finally:
        engine.dispose()

    assert table_exists_after is not None
    assert "ix_formula_registers_org_key_active" in index_names
    assert "ix_formula_registers_formula_key" in index_names


def test_migration_is_idempotent_on_a_second_run(scratch_db):
    """Scenario 2: upgrade() run a SECOND time immediately after (simulating
    a re-run / already-migrated database) must be a clean no-op, no
    exception.

    Builds on scenario 1's flow (table absent, then created by this
    revision's own upgrade()), then rewinds only the Alembic bookkeeping
    (the alembic_version row) back to the prior revision -- the schema
    itself is left exactly as the first upgrade left it -- so the second
    ``schema-upgrade`` call genuinely re-enters this revision's upgrade()
    against an already-upgraded schema. A plain second
    ``schema-upgrade --to REVISION`` would not do this: Alembic sees the
    database already recorded at that revision and skips it entirely,
    proving nothing about the guard. Matches
    ``test_business_function_capability_fk.py``'s own convention for the
    same kind of idempotency proof.
    """
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    engine = create_engine(scratch_db)
    try:
        _drop_formula_registers_table(engine)

        first = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert first.returncode == 0, first.stdout + first.stderr

        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE alembic_version SET version_num = :pre"
            ), {"pre": PRE_REVISION})

        second = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert second.returncode == 0, second.stdout + second.stderr

        table_exists, index_names = _table_and_indexes(engine)
    finally:
        engine.dispose()

    assert table_exists is not None
    assert "ix_formula_registers_org_key_active" in index_names
    assert "ix_formula_registers_formula_key" in index_names


def test_migration_is_idempotent_when_table_already_created_by_model_metadata(
    app, scratch_db,
):
    """Scenario 3: the exact CI failure mode. formula_registers was already
    created some OTHER way first -- via ``db.metadata.create_all()`` against
    the current model, simulating what a reconciled/fresh database's
    schema-build step does ahead of ``schema-upgrade`` -- and upgrade() must
    also be a clean no-op, not a ``DuplicateTable`` error.
    """
    proc = _run_flask(scratch_db, "schema-upgrade", "--to", PRE_REVISION)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    engine = create_engine(scratch_db)
    try:
        _drop_formula_registers_table(engine)
        table_exists_before, _ = _table_and_indexes(engine)
        assert table_exists_before is None

        # The ORM-driven path: db.metadata.create_all() against the CURRENT
        # models, bound at this scratch database rather than the app's
        # configured one, so this proves the guard without depending on
        # process-level DATABASE_URL plumbing. This is the same call
        # ``flask init-db`` makes (Flask-SQLAlchemy's ``db.create_all()``)
        # and the same mechanism the baseline revision's own
        # ``ensure_baseline_schema`` uses.
        with app.app_context():
            from app import db

            db.metadata.create_all(bind=engine, checkfirst=True)

        table_exists_after_create_all, index_names_after_create_all = (
            _table_and_indexes(engine)
        )
        assert table_exists_after_create_all is not None, (
            "db.metadata.create_all() should have created formula_registers "
            "from the model, same as a reconciled/fresh database would"
        )
        assert "ix_formula_registers_org_key_active" in index_names_after_create_all
        assert "ix_formula_registers_formula_key" in index_names_after_create_all

        proc = _run_flask(scratch_db, "schema-upgrade", "--to", REVISION)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        table_exists_after_migration, index_names_after_migration = (
            _table_and_indexes(engine)
        )
    finally:
        engine.dispose()

    assert table_exists_after_migration is not None
    assert "ix_formula_registers_org_key_active" in index_names_after_migration
    assert "ix_formula_registers_formula_key" in index_names_after_migration
