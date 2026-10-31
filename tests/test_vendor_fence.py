"""Vendor products are shared-plus-override, and the tables hanging from them are fenced.

vendor_products is hybrid: a row with no organisation is a shared catalogue row every organisation
reads and only a platform administrator changes; a row with one belongs to that company. The two
junction tables (solution_vendor_products, application_component_vendor_products) carry their parent's
organisation. Two organisations, a shared row, and the database role the application runs as.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "migrations" / "versions" / "20261027_fence_vendor.py"
ROLE = "archie_rls_test_vendor"
PASSWORD = uuid.uuid4().hex


def _load():
    spec = importlib.util.spec_from_file_location("fence_vendor_under_test", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- the real schema, as the runtime role


@pytest.fixture
def world(app, _schema):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization
    from app.models.solution_models import Solution
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        owner = db.engine
        with owner.begin() as c:
            if not c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}).scalar():
                c.execute(text(f"CREATE ROLE {ROLE}"))  # nosec B608 - module constant
            c.execute(text(
                f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{PASSWORD}' "  # nosec B608 - test-only role
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"))
            c.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE}"))  # nosec B608
            c.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE}"))  # nosec B608
            c.execute(text(f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {ROLE}"))  # nosec B608
        module = _load()
        with owner.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        org_a = Organization(name="Ven A %s" % marker, slug="ven-a-%s" % marker)
        org_b = Organization(name="Ven B %s" % marker, slug="ven-b-%s" % marker)
        db.session.add_all([org_a, org_b])
        db.session.flush()
        vendor = VendorOrganization(name="Vendor %s" % marker)
        db.session.add(vendor)
        db.session.flush()
        shared = VendorProduct(vendor_organization_id=vendor.id, name="Shared %s" % marker)
        mine_a = VendorProduct(vendor_organization_id=vendor.id, name="A %s" % marker, organization_id=org_a.id)
        mine_b = VendorProduct(vendor_organization_id=vendor.id, name="B %s" % marker, organization_id=org_b.id)
        sol_a = Solution(name="Sol A %s" % marker, organization_id=org_a.id)
        sol_b = Solution(name="Sol B %s" % marker, organization_id=org_b.id)
        app_a = ApplicationComponent(name="App A %s" % marker, organization_id=org_a.id)
        app_b = ApplicationComponent(name="App B %s" % marker, organization_id=org_b.id)
        db.session.add_all([shared, mine_a, mine_b, sol_a, sol_b, app_a, app_b])
        db.session.commit()
        ids = dict(org_a=org_a.id, org_b=org_b.id, vendor=vendor.id, shared=shared.id, a=mine_a.id, b=mine_b.id,
                   sol_a=sol_a.id, sol_b=sol_b.id, app_a=app_a.id, app_b=app_b.id)
        runtime = sa.create_engine(owner.url.set(username=ROLE, password=PASSWORD), poolclass=NullPool)
        yield ids, runtime, owner
        runtime.dispose()
        with owner.begin() as c:
            # organisations, solutions and applications stay: audit rows reference them and are append-only
            for sql, params in (
                ("DELETE FROM solution_vendor_products WHERE vendor_product_id = ANY(:p)", {"p": [ids["shared"], ids["a"], ids["b"]]}),
                ("DELETE FROM application_component_vendor_products WHERE vendor_product_id = ANY(:p)", {"p": [ids["shared"], ids["a"], ids["b"]]}),
                ("DELETE FROM vendor_products WHERE vendor_organization_id = :v", {"v": ids["vendor"]}),
                ("DELETE FROM vendor_organizations WHERE id = :v", {"v": ids["vendor"]}),
            ):
                c.execute(text(sql), params)


def _tx(runtime, org_id=None, platform=False):
    from app.middleware.tenant_isolation import set_database_tenant_context

    conn = runtime.connect()
    tx = conn.begin()
    set_database_tenant_context(conn, org_id)
    if platform:
        conn.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
    return conn, tx


def _visible(runtime, org_id, ids):
    conn, tx = _tx(runtime, org_id)
    try:
        return {r[0] for r in conn.execute(text("SELECT id FROM vendor_products WHERE id = ANY(:i)"), {"i": ids})}
    finally:
        tx.rollback()
        conn.close()


def test_b_cannot_read_a_company_product_and_both_read_the_shared_one(world):
    ids, runtime, _ = world
    every = [ids["shared"], ids["a"], ids["b"]]
    assert _visible(runtime, ids["org_a"], every) == {ids["shared"], ids["a"]}
    assert _visible(runtime, ids["org_b"], every) == {ids["shared"], ids["b"]}


def test_a_shared_product_can_only_be_edited_by_a_platform_administrator(world):
    ids, runtime, _ = world
    for org in (ids["org_a"], ids["org_b"]):
        conn, tx = _tx(runtime, org)
        try:
            changed = conn.execute(text("UPDATE vendor_products SET name = name || ' x' WHERE id = :i"), {"i": ids["shared"]}).rowcount
            deleted = conn.execute(text("DELETE FROM vendor_products WHERE id = :i"), {"i": ids["shared"]}).rowcount
        finally:
            tx.rollback()
            conn.close()
        assert (changed, deleted) == (0, 0), "an organisation changed a shared catalogue row"
    conn, tx = _tx(runtime, ids["org_a"], platform=True)
    try:
        assert conn.execute(text("UPDATE vendor_products SET name = name || ' x' WHERE id = :i"), {"i": ids["shared"]}).rowcount == 1
    finally:
        tx.rollback()
        conn.close()


def test_an_organisation_edits_its_own_product_and_not_anothers(world):
    ids, runtime, _ = world
    conn, tx = _tx(runtime, ids["org_a"])
    try:
        own = conn.execute(text("UPDATE vendor_products SET name = name || ' x' WHERE id = :i"), {"i": ids["a"]}).rowcount
        theirs = conn.execute(text("UPDATE vendor_products SET name = name || ' x' WHERE id = :i"), {"i": ids["b"]}).rowcount
    finally:
        tx.rollback()
        conn.close()
    assert (own, theirs) == (1, 0)


def test_an_organisation_cannot_write_a_shared_row_or_a_row_for_another_organisation(world):
    ids, runtime, _ = world
    for org_value in (None, ids["org_b"]):
        conn, tx = _tx(runtime, ids["org_a"])
        try:
            with pytest.raises(DBAPIError):
                conn.execute(text(
                    "INSERT INTO vendor_products (vendor_organization_id, name, organization_id) VALUES (:v, 'planted', :o)"),
                    {"v": ids["vendor"], "o": org_value})
        finally:
            tx.rollback()
            conn.close()
    conn, tx = _tx(runtime, ids["org_a"])
    try:
        conn.execute(text("INSERT INTO vendor_products (vendor_organization_id, name, organization_id) VALUES (:v, 'mine', :o)"),
                     {"v": ids["vendor"], "o": ids["org_a"]})
    finally:
        tx.rollback()
        conn.close()


# ---------------------------------------------------------------- the two junction tables


def test_b_cannot_read_a_junction_row_through_either_parent(world):
    from app import db
    from app.models.relationship_tables import application_component_vendor_products
    from app.models.solution_models import solution_vendor_products

    ids, runtime, _ = world
    # The organisation is filled from the parent at insert (the relationship supplies only the keys).
    db.session.execute(solution_vendor_products.insert().values(solution_id=ids["sol_a"], vendor_product_id=ids["shared"]))
    db.session.execute(application_component_vendor_products.insert().values(
        application_component_id=ids["app_a"], vendor_product_id=ids["shared"]))
    db.session.commit()
    assert db.session.execute(text("SELECT organization_id FROM solution_vendor_products WHERE solution_id = :s"),
                              {"s": ids["sol_a"]}).scalar() == ids["org_a"]
    assert db.session.execute(text("SELECT organization_id FROM application_component_vendor_products WHERE application_component_id = :s"),
                              {"s": ids["app_a"]}).scalar() == ids["org_a"]
    for table, column, parent in (("solution_vendor_products", "solution_id", ids["sol_a"]),
                                  ("application_component_vendor_products", "application_component_id", ids["app_a"])):
        sql = f"SELECT count(*) FROM {table} WHERE {column} = :p"  # nosec B608 - literals
        for org, expected in ((ids["org_a"], 1), (ids["org_b"], 0)):
            conn, tx = _tx(runtime, org)
            try:
                assert conn.execute(text(sql), {"p": parent}).scalar() == expected, (table, org)
            finally:
                tx.rollback()
                conn.close()


def test_a_child_of_a_company_product_takes_the_products_organisation(world):
    from app import db
    from app.middleware.tenant_isolation import organization_from_parent
    from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping

    ids, _, _ = world
    assert organization_from_parent(db.session, VendorProductAPQCMapping(vendor_product_id=ids["a"])) == ids["org_a"]
    assert organization_from_parent(db.session, VendorProductAPQCMapping(vendor_product_id=ids["shared"])) is None


# ---------------------------------------------------------------- the backfill, on a legacy database


@pytest.fixture
def legacy_db():
    admin_url = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_url:
        pytest.skip("DATABASE_ADMIN_URL not set")
    name = f"mig_vendor_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    engine = sa.create_engine(sa.engine.make_url(admin_url).set(database=name))
    with engine.begin() as c:
        for sql in (
            "CREATE TABLE organizations (id serial primary key)",
            "CREATE TABLE archimate_elements (id serial primary key, organization_id integer)",
            "CREATE TABLE application_components (id serial primary key, organization_id integer, vendor_product_id integer)",
            "CREATE TABLE solutions (id serial primary key, organization_id integer)",
            "CREATE TABLE application_vendor_product_mappings (id serial primary key, application_component_id integer, vendor_product_id integer)",
            "CREATE TABLE application_component_vendor_products (id serial primary key, application_component_id integer, vendor_product_id integer)",
            "CREATE TABLE solution_vendor_products (solution_id integer, vendor_product_id integer)",
            "CREATE TABLE vendor_organizations (id serial primary key, named_by_organization_id integer, seed_source_id varchar(100))",
            "CREATE TABLE vendor_products (id serial primary key, vendor_organization_id integer, archimate_product_element_id integer, name varchar(50))",
            "CREATE TABLE vendor_product_apqc_mapping (id serial primary key, vendor_product_id integer)",
            "CREATE TABLE vendor_product_embeddings (id serial primary key, vendor_product_id integer)",
            "CREATE TABLE vendor_product_pricing (id serial primary key, product_id integer, organization_id integer)",
        ):
            c.execute(text(sql))
    yield engine
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _upgrade(engine):
    module = _load()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()


def test_product_organisation_is_derived_in_the_ruled_order_and_the_rest_stay_shared(legacy_db, capsys):
    with legacy_db.begin() as c:
        c.execute(text("INSERT INTO organizations DEFAULT VALUES"))
        c.execute(text("INSERT INTO organizations DEFAULT VALUES"))
        c.execute(text("INSERT INTO archimate_elements (organization_id) VALUES (1)"))
        c.execute(text("INSERT INTO application_components (organization_id) VALUES (2), (1), (2)"))
        c.execute(text("INSERT INTO vendor_organizations (named_by_organization_id, seed_source_id) VALUES "
                       "(NULL, 'seed-sap'), (2, 'auto-abc'), (1, 'seed-okta')"))
        c.execute(text(
            "INSERT INTO vendor_products (vendor_organization_id, archimate_product_element_id, name) VALUES "
            "(1, 1, 'p1-element'), "     # (a) element of org 1
            "(1, NULL, 'p2-app'), "      # (b) referenced by org 2's application
            "(1, NULL, 'p3-ambiguous'), "  # (b) referenced by two organisations
            "(2, NULL, 'p4-runtime'), "  # (c) vendor made at runtime and named by org 2
            "(3, NULL, 'p5-seeded-named'), "  # seeded vendor that happens to carry a naming organisation: stays shared
            "(1, NULL, 'p6-nothing')"))  # shared
        c.execute(text("INSERT INTO application_vendor_product_mappings (application_component_id, vendor_product_id) VALUES (1, 2), (1, 3), (2, 3)"))
        c.execute(text("INSERT INTO vendor_product_apqc_mapping (vendor_product_id) VALUES (2), (6)"))
        c.execute(text("INSERT INTO vendor_product_embeddings (vendor_product_id) VALUES (1)"))
    _upgrade(legacy_db)
    out = capsys.readouterr().out
    with legacy_db.connect() as c:
        got = dict(c.execute(text("SELECT name, organization_id FROM vendor_products")).all())
        apqc = [r[0] for r in c.execute(text("SELECT organization_id FROM vendor_product_apqc_mapping ORDER BY id"))]
        emb = [r[0] for r in c.execute(text("SELECT organization_id FROM vendor_product_embeddings"))]
        nullable = c.execute(text("SELECT is_nullable FROM information_schema.columns WHERE table_name = 'vendor_products' "
                                  "AND column_name = 'organization_id'")).scalar()
        policies = c.execute(text("SELECT count(*) FROM pg_policies WHERE tablename = 'vendor_products' AND policyname LIKE 'hybrid_%'")).scalar()
    assert got == {"p1-element": 1, "p2-app": 2, "p3-ambiguous": None, "p4-runtime": 2, "p5-seeded-named": None, "p6-nothing": None}
    assert apqc == [2, None] and emb == [1]
    assert nullable == "YES" and policies == 4
    assert "AMBIGUOUS vendor_products id=3" in out
    assert "NULL-ROWS vendor_products: 3" in out and "SHARED-ROWS vendor_products: 2" in out
    _upgrade(legacy_db)  # a second run changes nothing
    with legacy_db.connect() as c:
        assert dict(c.execute(text("SELECT name, organization_id FROM vendor_products")).all()) == got
