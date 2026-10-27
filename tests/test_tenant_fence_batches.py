"""Every tenant-fence batch migration backfills from the parent, reports what it cannot reach,
and fences each table (T-sec03 slice A).

A batch is any revision in ``migrations/versions`` that defines ``BACKFILL``. Each is run against a
throw-away database holding the legacy shape of its tables (an id and the parent columns, no
organization_id): once with rows (resolved, second-source and orphan rows), once empty, then twice
to prove it is repeatable. A new batch is picked up without a code change here.
"""

from __future__ import annotations

import importlib.util
import os
import re
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[1]
VERSIONS = REPO / "migrations" / "versions"


def _batches():
    found = []
    for path in sorted(VERSIONS.glob("*.py")):
        if re.search(r"^BACKFILL\s*=", path.read_text(encoding="utf-8"), re.M):
            found.append(path)
    return found


def _load(path):
    spec = importlib.util.spec_from_file_location(f"fence_batch_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy_db(request):
    admin_url = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_url:
        pytest.skip("DATABASE_ADMIN_URL not set")
    module = _load(request.param)
    name = f"mig_fence_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    engine = sa.create_engine(sa.engine.make_url(admin_url).set(database=name))
    tables = {t: [col for _, col in sources] for t, sources in module.BACKFILL}
    in_batch = set(tables)
    external = {p for _, sources in module.BACKFILL for p, _ in sources if p not in in_batch}
    with engine.begin() as c:
        c.execute(text("CREATE TABLE organizations (id serial primary key)"))
        for parent in sorted(external):
            c.execute(text(f"CREATE TABLE {parent} (id serial primary key, organization_id integer)"))
        for table, cols in tables.items():
            defs = "".join(f", {col} integer" for col in dict.fromkeys(cols))
            c.execute(text(f"CREATE TABLE {table} (id serial primary key{defs})"))
    yield module, engine
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _upgrade(module, engine):
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            module.upgrade()


def _seed(module, engine):
    """org 1 and 2; external parents 1 -> org 1, 2 -> org 2; per table: resolved 1, resolved 2,
    orphan, and (when it has a second source) a row reachable only through that source."""
    expected = {}
    in_batch = {t for t, _ in module.BACKFILL}
    with engine.begin() as c:
        c.execute(text("INSERT INTO organizations DEFAULT VALUES"))
        c.execute(text("INSERT INTO organizations DEFAULT VALUES"))
        for _, sources in module.BACKFILL:
            for parent, _ in sources:
                if parent not in in_batch and not c.execute(text(f"SELECT count(*) FROM {parent}")).scalar():
                    c.execute(text(f"INSERT INTO {parent} (organization_id) VALUES (1), (2)"))
        for table, sources in module.BACKFILL:
            first = sources[0][1]
            c.execute(text(f"INSERT INTO {table} ({first}) VALUES (1), (2), (NULL)"))
            rows = [1, 2, None]
            if len(sources) > 1:
                second = sources[1][1]
                c.execute(text(f"INSERT INTO {table} ({second}) VALUES (1)"))
                rows.append(1)
            expected[table] = rows
    return expected


@pytest.mark.parametrize("legacy_db", _batches(), ids=lambda p: p.stem, indirect=True)
def test_backfills_from_the_parent_leaves_orphans_unowned_and_fences(legacy_db):
    module, engine = legacy_db
    expected = _seed(module, engine)
    _upgrade(module, engine)
    with engine.connect() as c:
        for table, want in expected.items():
            got = [r[0] for r in c.execute(text(f"SELECT organization_id FROM {table} ORDER BY id"))]
            assert got == want, f"{table}: {got} != {want}"
            nullable = c.execute(text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name = :t "
                "AND column_name = 'organization_id'"), {"t": table}).scalar()
            assert nullable == "YES", f"{table}: an orphan row is left NULL, so the column stays nullable"
            rls = c.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table}).scalar()
            policies = c.execute(text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}).scalar()
            assert rls is True and policies == 4, table
    _upgrade(module, engine)  # a second run changes nothing and does not raise
    with engine.connect() as c:
        for table, want in expected.items():
            got = [r[0] for r in c.execute(text(f"SELECT organization_id FROM {table} ORDER BY id"))]
            assert got == want


@pytest.mark.parametrize("legacy_db", _batches(), ids=lambda p: p.stem, indirect=True)
def test_empty_tables_end_not_null_and_fenced(legacy_db):
    module, engine = legacy_db
    _upgrade(module, engine)
    with engine.connect() as c:
        for table, _ in module.BACKFILL:
            assert c.execute(text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name = :t "
                "AND column_name = 'organization_id'"), {"t": table}).scalar() == "NO", table
            assert c.execute(text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}).scalar() == 4


@pytest.mark.parametrize("legacy_db", _batches(), ids=lambda p: p.stem, indirect=True)
def test_the_unowned_row_is_hidden_from_every_organisation(legacy_db):
    """A NULL-organisation row matches no tenant: the policy compares organization_id to the
    session organisation, and NULL equals nothing."""
    module, engine = legacy_db
    _seed(module, engine)
    _upgrade(module, engine)
    table = module.BACKFILL[0][0]
    with engine.connect() as c:
        policy = c.execute(text(
            "SELECT qual FROM pg_policies WHERE tablename = :t AND cmd = 'SELECT'"), {"t": table}).scalar()
    assert "organization_id = " in policy and "IS NULL" not in policy


def test_every_batch_lists_parents_before_children():
    for path in _batches():
        module = _load(path)
        seen = set()
        names = {t for t, _ in module.BACKFILL}
        for table, sources in module.BACKFILL:
            for parent, _ in sources:
                assert parent not in names or parent in seen or parent == table, (
                    f"{path.stem}: {table} is backfilled from {parent}, which is fenced after it"
                )
            seen.add(table)


@pytest.mark.parametrize("path", _batches(), ids=lambda p: p.stem)
def test_a_database_built_from_the_models_is_fenced_and_a_rerun_is_a_no_op(app, _schema, path):
    """The fresh-database half: create_all already gave every model table its NOT NULL column,
    so the revision only has to fence (and, for tables already carrying the mixin, verify)."""
    from app import db

    module = _load(path)
    with app.app_context():
        engine = db.engine
        for _ in range(2):
            with engine.begin() as conn:
                ctx = MigrationContext.configure(conn)
                with Operations.context(ctx):
                    module.upgrade()
        with engine.connect() as c:
            for table in module.TENANT_TABLES:
                present = c.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar()
                assert present, f"{table} is not in the model schema"
                assert c.execute(text(
                    "SELECT is_nullable FROM information_schema.columns WHERE table_name = :t "
                    "AND column_name = 'organization_id'"), {"t": table}).scalar() == "NO", table
                assert c.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname = :t"), {"t": table}).scalar(), table
                assert c.execute(text(
                    "SELECT count(*) FROM pg_policies WHERE tablename = :t AND policyname LIKE 'tenant_%'"),
                    {"t": table}).scalar() == 4, table
            for table in module.HYBRID_TABLES:
                assert c.execute(text(
                    "SELECT count(*) FROM pg_policies WHERE tablename = :t AND policyname LIKE 'hybrid_%'"),
                    {"t": table}).scalar() == 4, table


def test_every_model_in_a_batch_inherits_the_tenant_mixin(app, _schema):
    from app import db
    from app.models.mixins.core import TenantMixin

    tables = {t for path in _batches() for t in _load(path).TENANT_TABLES}
    with app.app_context():
        by_table = {}
        for mapper in db.Model.registry.mappers:
            cls = mapper.class_
            table = getattr(cls, "__tablename__", None)
            if isinstance(table, str):
                by_table.setdefault(table, []).append(cls)
        junctions = {t for t in tables if not by_table.get(t)}
        bare = sorted(t for t in junctions
                      if "organization_id" not in db.metadata.tables[t].c
                      or db.metadata.tables[t].c.organization_id.default is None)
    # A junction table (db.Table, no class) carries organization_id with a default taken from its parent.
    assert not bare, f"junction table without an organization_id default: {bare}"
    missing = sorted(t for t in tables - junctions if not any(issubclass(c, TenantMixin) for c in by_table.get(t, [])))
    assert not missing, f"model has no TenantMixin: {missing}"
