"""End-of-support dates on the technology element, and the window query.

Migration 20261019_support_dates adds four nullable columns to
archimate_elements -- support_end_date, extended_support_end_date,
support_source, support_verified_on -- and copies any Device or
SystemSoftware eos_date into its element's support_end_date.

app.services.eol_window.technologies_reaching_end_of_support(months) returns
the current organisation's technology-layer elements whose support ends
within the window, soonest first, with id, name, type, support_end_date,
extended_support_end_date and support_source on each row.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.services.eol_window import technologies_reaching_end_of_support

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = REPO_ROOT / "migrations" / "versions" / "20261019_support_dates.py"

_PRE_SCHEMA = """
CREATE TABLE organizations (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL, slug VARCHAR(255) NOT NULL UNIQUE
);

CREATE TABLE archimate_elements (
    id SERIAL PRIMARY KEY, name VARCHAR(500) NOT NULL, type VARCHAR(50),
    layer VARCHAR(30), organization_id INTEGER NOT NULL REFERENCES organizations(id)
);

CREATE TABLE technology_devices (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
    archimate_element_id INTEGER REFERENCES archimate_elements(id),
    eos_date DATE, organization_id INTEGER NOT NULL REFERENCES organizations(id)
);

CREATE TABLE technology_system_software (
    id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL,
    archimate_element_id INTEGER REFERENCES archimate_elements(id),
    eos_date DATE, organization_id INTEGER NOT NULL REFERENCES organizations(id)
);
"""


@pytest.fixture
def pre_migration_db():
    """A fresh, isolated database in the pre-migration shape (no support
    columns on archimate_elements yet). Dropped after the test."""
    parts = urlsplit(os.environ.get("TEST_DATABASE_URL"))
    name = f"archie_eos_dates_{uuid.uuid4().hex[:8]}"
    admin_url = urlunsplit(parts._replace(scheme="postgresql+psycopg2", path="/postgres"))
    scratch_url = urlunsplit(parts._replace(scheme="postgresql+psycopg2", path=f"/{name}"))
    admin = create_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))  # nosec B608 - generated name
    engine = create_engine(scratch_url, poolclass=NullPool)
    with engine.begin() as conn:
        for stmt in _PRE_SCHEMA.strip().split(";\n\n"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))  # nosec B608
        admin.dispose()


def _load_migration():
    spec = importlib.util.spec_from_file_location("support_dates_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(engine, module, direction="upgrade"):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(module, direction)()


def _columns(engine, table):
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            ).fetchall()
        }


def _scalar(engine, sql, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def _insert(engine, sql, **params):
    with engine.begin() as connection:
        return connection.execute(text(sql), params).scalar()


def _make_org(engine, tag):
    return _insert(
        engine,
        "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id",
        n=f"EOS {tag}", s=f"eos-{tag}-{uuid.uuid4().hex[:6]}",
    )


def _make_element(engine, org, type_):
    return _insert(
        engine,
        "INSERT INTO archimate_elements (name, type, layer, organization_id) "
        "VALUES (:n, :t, 'Technology', :o) RETURNING id",
        n=f"Element {uuid.uuid4().hex[:6]}", t=type_, o=org,
    )


def test_migration_adds_the_support_columns(pre_migration_db):
    engine = pre_migration_db
    before = _columns(engine, "archimate_elements")
    assert "support_end_date" not in before

    module = _load_migration()
    _run_migration(engine, module)

    after = _columns(engine, "archimate_elements")
    assert {"support_end_date", "extended_support_end_date", "support_source", "support_verified_on"} <= after, (
        f"support columns missing after upgrade: {sorted(after)}"
    )


def test_migration_copies_eos_date_into_its_element(pre_migration_db):
    engine = pre_migration_db
    org = _make_org(engine, "copy")
    element = _make_element(engine, org, "Device")
    device_eos = date(2027, 3, 15)
    _insert(
        engine,
        "INSERT INTO technology_devices (name, archimate_element_id, eos_date, organization_id) "
        "VALUES ('dev', :e, :d, :o) RETURNING id",
        e=element, d=device_eos, o=org,
    )
    # A second element with no eos_date on its device must stay NULL.
    other = _make_element(engine, org, "SystemSoftware")
    _insert(
        engine,
        "INSERT INTO technology_system_software (name, archimate_element_id, organization_id) "
        "VALUES ('soft', :e, :o) RETURNING id",
        e=other, o=org,
    )

    module = _load_migration()
    _run_migration(engine, module)

    assert _scalar(
        engine, "SELECT support_end_date FROM archimate_elements WHERE id = :i", i=element
    ) == device_eos, "device eos_date was not copied into its element's support_end_date"
    assert _scalar(
        engine, "SELECT support_end_date FROM archimate_elements WHERE id = :i", i=other
    ) is None, "element with no source eos_date was given a support date"


def test_migration_is_idempotent(pre_migration_db):
    engine = pre_migration_db
    org = _make_org(engine, "idem")
    element = _make_element(engine, org, "Device")
    _insert(
        engine,
        "INSERT INTO technology_devices (name, archimate_element_id, eos_date, organization_id) "
        "VALUES ('dev', :e, :d, :o) RETURNING id",
        e=element, d=date(2027, 3, 15), o=org,
    )
    module = _load_migration()
    _run_migration(engine, module)
    _run_migration(engine, module)  # second run must be a no-op, not an error

    assert _scalar(
        engine, "SELECT support_end_date FROM archimate_elements WHERE id = :i", i=element
    ) == date(2027, 3, 15)


def _element(db_session, org, *, name, support_end_date=None, extended_support_end_date=None, support_source=None):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=name,
        type="Device",
        layer="Technology",
        organization_id=org.id,
        support_end_date=support_end_date,
        extended_support_end_date=extended_support_end_date,
        support_source=support_source,
    )
    db_session.add(element)
    db_session.flush()
    return element


def test_returns_only_current_orgs_elements_inside_the_window(db_session, make_org, tenant_ctx):
    org_a = make_org("window-a")
    org_b = make_org("window-b")
    today = datetime.utcnow().date()
    soon = _element(
        db_session, org_a, name="Soon",
        support_end_date=today + timedelta(days=10),
        extended_support_end_date=today + timedelta(days=400),
        support_source="vendor notice",
    )
    later = _element(
        db_session, org_a, name="Later",
        support_end_date=today + timedelta(days=50),
    )
    _element(db_session, org_a, name="Outside", support_end_date=today + timedelta(days=400))
    _element(db_session, org_a, name="No date")  # no support_end_date at all
    other_org = _element(
        db_session, org_b, name="Other org", support_end_date=today + timedelta(days=10)
    )
    db_session.commit()

    with tenant_ctx(org_a.id):
        rows = technologies_reaching_end_of_support(months=12)

    assert [row["id"] for row in rows] == [soon.id, later.id], "window must be org-scoped and soonest-first"
    assert rows == [
        {
            "id": soon.id,
            "name": "Soon",
            "type": "Device",
            "support_end_date": today + timedelta(days=10),
            "extended_support_end_date": today + timedelta(days=400),
            "support_source": "vendor notice",
        },
        {
            "id": later.id,
            "name": "Later",
            "type": "Device",
            "support_end_date": today + timedelta(days=50),
            "extended_support_end_date": None,
            "support_source": None,
        },
    ], "each row carries id, name, type and the support dates, nothing else"
    assert other_org.id not in {row["id"] for row in rows}, "another organisation's element must not appear"


def test_default_window_is_the_shared_18_months(db_session, make_org, tenant_ctx):
    org = make_org("default-window")
    today = datetime.utcnow().date()
    inside = _element(db_session, org, name="Inside", support_end_date=today + timedelta(days=30))
    _element(db_session, org, name="Outside", support_end_date=today + timedelta(days=600))
    db_session.commit()

    with tenant_ctx(org.id):
        rows = technologies_reaching_end_of_support()  # default: the module's 18 months

    assert [row["id"] for row in rows] == [inside.id]
