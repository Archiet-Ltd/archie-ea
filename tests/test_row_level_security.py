"""Row-level security on the tenant and shared-catalogue tables (R1-B20 PR 3).

The database itself must refuse a query that forgets the organisation: the role
the application runs as sees and changes only the session organisation's rows,
and shared catalogue rows read-only. Every assertion here runs as a role that is
NOT the table owner, NOT a superuser and NOT ``BYPASSRLS``; a superuser skips
row-level security, so the superuser the rest of the suite connects as proves
nothing about it.

Which role: the test-only role ``archie_rls_test_runtime``, created by the
fixture below with exactly the attributes ``scripts/database/configure_roles.py``
gives the production runtime role (``NOSUPERUSER NOCREATEDB NOCREATEROLE
NOINHERIT NOREPLICATION NOBYPASSRLS``) plus data-manipulation grants in the test
database only. The real ``archie_runtime`` needs the whole schema built by the
role-separated deploy; ``test_real_configure_roles_*`` at the bottom runs that
real function on a scratch database, and the production-shaped rehearsal
(``deploy-schema.sh`` as the deploy role, then the app as ``archie_runtime``) is
recorded in the pull request.

The module makes its own policies present by calling the migration's
``upgrade()`` through an Alembic ``Operations`` context (safe because it is
idempotent), so it passes on a ``create_all`` database locally and on a database
CI built through the migrations.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

REPO = Path(__file__).resolve().parents[1]
MIGRATION_PATH = REPO / "migrations" / "versions" / "20261008_row_level_security.py"
RUNTIME_ROLE = "archie_rls_test_runtime"
RUNTIME_PASSWORD = uuid.uuid4().hex  # test-only role; regenerated each run


def _load_migration():
    spec = importlib.util.spec_from_file_location("rls_migration_under_test", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


def _apply_policies(engine):
    """Run the migration's own upgrade() on ``engine`` (idempotent)."""
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            MIGRATION.upgrade()


class RlsEnv:
    """Two engines on the test database: the owner/superuser and the runtime role."""

    def __init__(self, owner, runtime):
        self.owner = owner
        self.runtime = runtime

    @contextlib.contextmanager
    def runtime_tx(self, org_id=None, *, platform=False, commit=False, app=None):
        """A runtime-role transaction whose session organisation is ``org_id``.

        Uses the same function the ORM listeners call. ``platform=True`` sets the
        platform-scope flag the way ``platform_scope`` does.
        """
        from app.middleware.tenant_isolation import set_database_tenant_context

        with self.runtime.connect() as connection:
            transaction = connection.begin()
            try:
                set_database_tenant_context(connection, org_id)
                if platform:
                    connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
                yield connection
                if commit:
                    transaction.commit()
                else:
                    transaction.rollback()
            except BaseException:
                if transaction.is_active:
                    transaction.rollback()
                raise


@pytest.fixture(scope="module")
def rls(app, _schema):
    from app import db

    with app.app_context():
        url = db.engine.url
    owner = create_engine(url, poolclass=NullPool)
    with owner.begin() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": RUNTIME_ROLE}
        ).scalar()
        if not exists:
            connection.execute(text(f"CREATE ROLE {RUNTIME_ROLE}"))  # nosec B608 - module constant
        connection.execute(
            text(
                f"ALTER ROLE {RUNTIME_ROLE} WITH LOGIN PASSWORD '{RUNTIME_PASSWORD}' "  # nosec B608 - test-only role, generated password
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            )
        )
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE}"))
        connection.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {RUNTIME_ROLE}")
        )
        connection.execute(
            text(f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {RUNTIME_ROLE}")
        )
    _apply_policies(owner)
    runtime = create_engine(
        url.set(username=RUNTIME_ROLE, password=RUNTIME_PASSWORD), poolclass=NullPool
    )
    yield RlsEnv(owner, runtime)
    runtime.dispose()
    owner.dispose()


# --------------------------------------------------------------------------- #
# Seed data, committed as the owner, removed after each test
# --------------------------------------------------------------------------- #


class World:
    def __init__(self, rls):
        self.rls = rls
        self.org_ids = []
        self.user_ids = []
        self.shared_codes = []
        self.extra_deletes = []  # (table, column, [values])

    def org(self, label):
        suffix = uuid.uuid4().hex[:10]
        with self.rls.owner.begin() as connection:
            org_id = connection.execute(
                text(
                    "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"
                ),
                {"n": f"RLS {label} {suffix}", "s": f"rls-{label}-{suffix}"},
            ).scalar_one()
        self.org_ids.append(org_id)
        return org_id

    def application(self, org_id, name):
        with self.rls.owner.begin() as connection:
            return connection.execute(
                text(
                    "INSERT INTO application_components (name, organization_id) "
                    "VALUES (:n, :o) RETURNING id"
                ),
                {"n": name, "o": org_id},
            ).scalar_one()

    def reference_model(self, org_id, name):
        code = f"RLS-{uuid.uuid4().hex[:10]}"
        with self.rls.owner.begin() as connection:
            row_id = connection.execute(
                text(
                    "INSERT INTO reference_model (name, code, organization_id) "
                    "VALUES (:n, :c, :o) RETURNING id"
                ),
                {"n": name, "c": code, "o": org_id},
            ).scalar_one()
        self.shared_codes.append(code)
        return row_id

    def cleanup(self):
        with self.rls.owner.begin() as connection:
            for table, column, values in self.extra_deletes:
                connection.execute(
                    text(f"DELETE FROM {table} WHERE {column} = ANY(:v)"),  # nosec B608 - fixed names in this module
                    {"v": list(values)},
                )
            if self.user_ids:
                connection.execute(text("DELETE FROM soc2_audit_log WHERE user_id = ANY(:u)"), {"u": self.user_ids})
                connection.execute(text("DELETE FROM user_sessions WHERE user_id = ANY(:u)"), {"u": self.user_ids})
                connection.execute(text("DELETE FROM account_tokens WHERE user_id = ANY(:u)"), {"u": self.user_ids})
                connection.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": self.user_ids})
            if self.shared_codes:
                connection.execute(text("DELETE FROM reference_model WHERE code = ANY(:c)"), {"c": self.shared_codes})
            if self.org_ids:
                connection.execute(text("DELETE FROM event_log WHERE organization_id = ANY(:o)"), {"o": self.org_ids})
                # The outbox is append-only by trigger; the test database owner lifts that here only.
                connection.execute(text("ALTER TABLE transformation_outbox_events DISABLE TRIGGER USER"))
                connection.execute(
                    text("DELETE FROM transformation_outbox_events WHERE organization_id = ANY(:o)"),
                    {"o": self.org_ids},
                )
                connection.execute(text("ALTER TABLE transformation_outbox_events ENABLE TRIGGER USER"))
                connection.execute(text("DELETE FROM organizations WHERE id = ANY(:o)"), {"o": self.org_ids})


@pytest.fixture
def world(rls):
    world = World(rls)
    yield world
    world.cleanup()


def _count(connection, table):
    return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # nosec B608 - fixed names in this module


def _owner_value(rls, sql, **params):
    with rls.owner.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def _fenced(rls, tables):
    """Subset of ``tables`` that exist in this database."""
    with rls.owner.connect() as connection:
        return [
            t
            for t in tables
            if connection.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{t}"}).scalar()
            and connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = :t AND column_name = 'organization_id')"
                ),
                {"t": t},
            ).scalar()
        ]


# --------------------------------------------------------------------------- #
# The role under test, and the shape of the migration
# --------------------------------------------------------------------------- #


def test_runtime_role_is_not_superuser_not_bypassrls_and_not_owner(rls):
    with rls.runtime.connect() as connection:
        user, is_super, bypass = connection.execute(
            text(
                "SELECT current_user, r.rolsuper, r.rolbypassrls FROM pg_roles r "
                "WHERE r.rolname = current_user"
            )
        ).one()
        owner = connection.execute(
            text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'public.application_components'::regclass")
        ).scalar_one()
        attrs = connection.execute(
            text(
                "SELECT rolcreatedb, rolcreaterole, rolinherit, rolreplication FROM pg_roles "
                "WHERE rolname = current_user"
            )
        ).one()
    assert user == RUNTIME_ROLE
    assert is_super is False
    assert bypass is False
    assert owner != RUNTIME_ROLE
    assert tuple(attrs) == (False, False, False, False)


def test_migration_has_no_roles_grants_force_or_bypass_and_uses_null_safe_setting():
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, count=1, flags=re.S)  # the module docstring explains, not executes
    for forbidden in ("CREATE ROLE", "GRANT ", "REVOKE ", "BYPASSRLS", "FORCE ROW LEVEL", "archie_app", "archie_platform",
                      "ALTER DEFAULT PRIVILEGES", "NO FORCE"):
        assert forbidden not in code, forbidden
    assert "NULLIF(current_setting('archie.organization_id', true), '')::integer" in source
    assert "from app" not in source and "import app" not in source
    assert MIGRATION.revision == "20261008_row_level_security"


FENCING_PATTERN = re.compile(r"^(TENANT_TABLES|HYBRID_TABLES)\s*=", re.MULTILINE)


def _fencing_revisions():
    """Every revision in ``migrations/versions`` that carries table lists, loaded by path.

    Found by scanning for ``TENANT_TABLES`` / ``HYBRID_TABLES`` constants, so a future fencing
    revision is picked up without a code change here. Loaded without importing ``app``.
    """
    found = {}
    for path in sorted((REPO / "migrations" / "versions").glob("*.py")):
        if not FENCING_PATTERN.search(path.read_text(encoding="utf-8")):
            continue
        spec = importlib.util.spec_from_file_location(f"fencing_revision_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found[path.name] = {
            "tenant": set(getattr(module, "TENANT_TABLES", ())),
            "hybrid": set(getattr(module, "HYBRID_TABLES", ())),
            "excluded": set(getattr(module, "EXCLUDED", ())),
        }
    return found


def _assert_every_model_is_fenced(tenant_models, hybrid_models, revisions):
    """Fail, naming the tables, when a mixin table is in no fencing revision."""
    fenced_tenant = set().union(*(r["tenant"] for r in revisions.values())) if revisions else set()
    fenced_hybrid = set().union(*(r["hybrid"] for r in revisions.values())) if revisions else set()
    excluded = set().union(*(r["excluded"] for r in revisions.values())) if revisions else set()
    unfenced = sorted(
        {t for t in tenant_models if t not in fenced_tenant and t not in excluded}
        | {t for t in hybrid_models if t not in fenced_hybrid and t not in excluded}
    )
    assert not unfenced, (
        f"tables with an organisation fence but no row-level security: {unfenced}. "
        "Alembic runs a revision once, so add a new fencing revision for them "
        "(copy 20261008_row_level_security, list only the new tables); do not edit an applied one."
    )


def _concrete_mixin_tables(mixin):
    found, stack = {}, list(mixin.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        table = getattr(cls, "__table__", None)
        if table is not None and getattr(cls, "__tablename__", None):
            found[table.name] = cls.__name__
    return found


def _load_all_model_modules():
    import importlib
    import pkgutil

    import app.models as models_package

    for info in pkgutil.iter_modules(models_package.__path__):
        with contextlib.suppress(Exception):
            importlib.import_module(f"app.models.{info.name}")


def test_every_tenant_and_hybrid_model_is_listed_or_excluded():
    """The literal table lists cannot drift from the models unnoticed: the union of every
    fencing revision must cover every ``TenantMixin`` / ``HybridTenantMixin`` table."""
    from app import db
    from app.models.mixins.core import HybridTenantMixin, TenantMixin

    _load_all_model_modules()
    revisions = _fencing_revisions()
    assert "20261008_row_level_security.py" in revisions
    _assert_every_model_is_fenced(
        _concrete_mixin_tables(TenantMixin), _concrete_mixin_tables(HybridTenantMixin), revisions
    )
    listed_tenant, listed_hybrid = set(MIGRATION.TENANT_TABLES), set(MIGRATION.HYBRID_TABLES)
    assert not (listed_tenant & listed_hybrid)
    assert set(MIGRATION.EXCLUDED) == {"unified_capabilities"}
    assert db is not None


def test_a_mixin_table_missing_from_every_fencing_revision_fails_the_guard():
    revisions = _fencing_revisions()
    tenant = {"application_components", "brand_new_tenant_table"}
    hybrid = {"reference_model", "brand_new_hybrid_table"}
    with pytest.raises(AssertionError) as failure:
        _assert_every_model_is_fenced(tenant, hybrid, revisions)
    message = str(failure.value)
    assert "add a new fencing revision" in message
    assert "brand_new_tenant_table" in message and "brand_new_hybrid_table" in message
    assert "application_components" not in message and "reference_model" not in message


def test_a_later_fencing_revision_covers_a_table_the_first_one_does_not():
    """The guard reads the union: a second revision's lists count."""
    revisions = _fencing_revisions()
    revisions["99999999_second_fencing.py"] = {
        "tenant": {"brand_new_tenant_table"},
        "hybrid": set(),
        "excluded": set(),
    }
    _assert_every_model_is_fenced({"application_components", "brand_new_tenant_table"}, set(), revisions)


def test_every_present_table_has_four_policies_enabled_not_forced(rls):
    tenant = _fenced(rls, MIGRATION.TENANT_TABLES)
    hybrid = _fenced(rls, MIGRATION.HYBRID_TABLES)
    assert len(tenant) > 200 and len(hybrid) >= 13
    with rls.owner.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "(SELECT array_agg(p.policyname ORDER BY p.policyname) FROM pg_policies p "
                "  WHERE p.schemaname = 'public' AND p.tablename = c.relname) "
                "FROM pg_class c WHERE c.relname = ANY(:t) AND c.relnamespace = 'public'::regnamespace"
            ),
            {"t": tenant + hybrid},
        ).all()
    by_name = {r[0]: r for r in rows}
    for table in tenant:
        _, enabled, forced, policies = by_name[table]
        assert enabled and not forced, table
        assert policies == sorted(MIGRATION.TENANT_POLICIES), table
    for table in hybrid:
        _, enabled, forced, policies = by_name[table]
        assert enabled and not forced, table
        assert policies == sorted(MIGRATION.HYBRID_POLICIES), table


def test_upgrade_twice_converges_to_the_same_definitions(rls):
    def snapshot():
        with rls.owner.connect() as connection:
            return connection.execute(
                text(
                    "SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'public' ORDER BY 1, 2"
                )
            ).all()

    before = snapshot()
    _apply_policies(rls.owner)
    assert snapshot() == before


# --------------------------------------------------------------------------- #
# Raw SQL without an organisation predicate: only the session organisation's rows
# --------------------------------------------------------------------------- #


def test_raw_select_without_org_predicate_is_scoped(rls, world):
    a, b = world.org("a"), world.org("b")
    a_id, b_id = world.application(a, "App A"), world.application(b, "App B")
    with rls.runtime_tx(a) as connection:
        ids = {r[0] for r in connection.execute(text("SELECT id FROM application_components"))}
    assert a_id in ids
    assert b_id not in ids


def test_raw_update_without_org_predicate_touches_only_session_org(rls, world):
    a, b = world.org("a"), world.org("b")
    a_id, b_id = world.application(a, "Original A"), world.application(b, "Original B")
    with rls.runtime_tx(a, commit=True) as connection:
        connection.execute(text("UPDATE application_components SET name = 'Updated by A'"))
    assert _owner_value(rls, "SELECT name FROM application_components WHERE id = :i", i=b_id) == "Original B"
    assert _owner_value(rls, "SELECT name FROM application_components WHERE id = :i", i=a_id) == "Updated by A"


def test_raw_delete_without_org_predicate_touches_only_session_org(rls, world):
    a, b = world.org("a"), world.org("b")
    a_id, b_id = world.application(a, "App A"), world.application(b, "App B")
    with rls.runtime_tx(a, commit=True) as connection:
        connection.execute(text("DELETE FROM application_components"))
    assert _owner_value(rls, "SELECT count(*) FROM application_components WHERE id = :i", i=b_id) == 1
    assert _owner_value(rls, "SELECT count(*) FROM application_components WHERE id = :i", i=a_id) == 0


def test_tenant_cannot_insert_a_row_for_another_organisation(rls, world):
    a, b = world.org("a"), world.org("b")
    with pytest.raises(DBAPIError, match="row-level security"):
        with rls.runtime_tx(a) as connection:
            connection.execute(
                text("INSERT INTO application_components (name, organization_id) VALUES ('Planted', :o)"),
                {"o": b},
            )
    with pytest.raises(DBAPIError, match="row-level security"):
        with rls.runtime_tx(a) as connection:
            connection.execute(
                text("INSERT INTO application_components (name, organization_id) VALUES ('Planted', NULL)")
            )


def test_tenant_cannot_move_its_row_to_another_organisation(rls, world):
    a, b = world.org("a"), world.org("b")
    a_id = world.application(a, "Mine")
    with pytest.raises(DBAPIError, match="row-level security"):
        with rls.runtime_tx(a) as connection:
            connection.execute(
                text("UPDATE application_components SET organization_id = :b WHERE id = :i"),
                {"b": b, "i": a_id},
            )


def test_with_no_organisation_every_fenced_table_returns_zero_rows(rls, world):
    """Tenant tables: zero rows. Shared tables: only the shared (NULL) rows."""
    a = world.org("a")
    world.application(a, "Present but invisible")
    world.reference_model(a, "Override, invisible")
    world.reference_model(None, "Shared, visible")
    tenant = _fenced(rls, MIGRATION.TENANT_TABLES)
    hybrid = _fenced(rls, MIGRATION.HYBRID_TABLES)
    with rls.runtime_tx(None) as connection:
        for table in tenant:
            assert _count(connection, table) == 0, table
        for table in hybrid:
            shared = _owner_value(rls, f"SELECT count(*) FROM {table} WHERE organization_id IS NULL")  # nosec B608
            assert _count(connection, table) == shared, table


def test_a_transaction_with_an_organisation_then_one_without_returns_zero_rows_not_an_error(rls, world):
    """On ONE connection: the first transaction sets the organisation and commits,
    the second sets none. The setting then reads '' (not NULL); the policy must
    treat that as no organisation, never raise on ''::integer."""
    from app.middleware.tenant_isolation import set_database_tenant_context

    a = world.org("a")
    world.application(a, "Reused connection")
    with rls.runtime.connect() as connection:
        with connection.begin():
            set_database_tenant_context(connection, a)
            assert _count(connection, "application_components") >= 1
        with connection.begin():
            leftover = connection.execute(text("SELECT current_setting('archie.organization_id', true)")).scalar()
            assert leftover in ("", None)
            assert _count(connection, "application_components") == 0
            assert _count(connection, "reference_model") >= 0  # a hybrid table does not raise either


# --------------------------------------------------------------------------- #
# Shared catalogue (hybrid) tables
# --------------------------------------------------------------------------- #


def test_hybrid_shared_rows_are_visible_to_every_organisation(rls, world):
    a, b = world.org("a"), world.org("b")
    shared = world.reference_model(None, "Shared")
    for org in (a, b):
        with rls.runtime_tx(org) as connection:
            rows = connection.execute(text("SELECT id FROM reference_model WHERE id = :i"), {"i": shared}).all()
        assert len(rows) == 1


def test_hybrid_override_rows_are_private_to_their_organisation(rls, world):
    a, b = world.org("a"), world.org("b")
    a_override, b_override = world.reference_model(a, "A override"), world.reference_model(b, "B override")
    with rls.runtime_tx(a) as connection:
        ids = {r[0] for r in connection.execute(text("SELECT id FROM reference_model"))}
    assert a_override in ids
    assert b_override not in ids


def test_hybrid_tenant_cannot_insert_update_or_delete_a_shared_row(rls, world):
    org = world.org("a")
    shared = world.reference_model(None, "Immutable shared")
    with pytest.raises(DBAPIError, match="row-level security"):
        with rls.runtime_tx(org) as connection:
            connection.execute(
                text("INSERT INTO reference_model (name, code, organization_id) VALUES ('x', :c, NULL)"),
                {"c": f"RLS-{uuid.uuid4().hex[:10]}"},
            )
    with rls.runtime_tx(org, commit=True) as connection:
        updated = connection.execute(text("UPDATE reference_model SET name = 'Tampered' WHERE id = :i"), {"i": shared})
        deleted = connection.execute(text("DELETE FROM reference_model WHERE id = :i"), {"i": shared})
    assert updated.rowcount == 0
    assert deleted.rowcount == 0
    assert _owner_value(rls, "SELECT name FROM reference_model WHERE id = :i", i=shared) == "Immutable shared"


def test_hybrid_tenant_can_write_its_own_override_and_not_another_organisations(rls, world):
    a, b = world.org("a"), world.org("b")
    code = f"RLS-{uuid.uuid4().hex[:10]}"
    world.shared_codes.append(code)
    with rls.runtime_tx(a, commit=True) as connection:
        connection.execute(
            text("INSERT INTO reference_model (name, code, organization_id) VALUES ('own', :c, :o)"),
            {"c": code, "o": a},
        )
        assert connection.execute(text("UPDATE reference_model SET name = 'own2' WHERE code = :c"), {"c": code}).rowcount == 1
    with pytest.raises(DBAPIError, match="row-level security"):
        with rls.runtime_tx(a) as connection:
            connection.execute(
                text("INSERT INTO reference_model (name, code, organization_id) VALUES ('theirs', :c, :o)"),
                {"c": f"RLS-{uuid.uuid4().hex[:10]}", "o": b},
            )
    with rls.runtime_tx(b, commit=True) as connection:
        assert connection.execute(text("DELETE FROM reference_model WHERE code = :c"), {"c": code}).rowcount == 0


def test_framework_adoptions_shared_and_isolated(rls, world):
    """A second hybrid table, added to the fence since the first version of this work."""
    a, b = world.org("a"), world.org("b")
    code = f"RLS-{uuid.uuid4().hex[:8]}"
    with rls.owner.begin() as connection:
        framework_id = connection.execute(
            text("INSERT INTO regulatory_frameworks (code, name) VALUES (:c, 'RLS framework') RETURNING id"),
            {"c": code},
        ).scalar_one()
        shared_id = connection.execute(
            text(
                "INSERT INTO framework_adoptions (framework_id, organization_id, scope) "
                "VALUES (:f, NULL, 'reference') RETURNING id"
            ),
            {"f": framework_id},
        ).scalar_one()
        a_id = connection.execute(
            text(
                "INSERT INTO framework_adoptions (framework_id, organization_id, scope) "
                "VALUES (:f, :o, 'tenant') RETURNING id"
            ),
            {"f": framework_id, "o": a},
        ).scalar_one()
    try:
        with rls.runtime_tx(b) as connection:
            ids = {r[0] for r in connection.execute(text("SELECT id FROM framework_adoptions"))}
        assert shared_id in ids and a_id not in ids
        with pytest.raises(DBAPIError, match="row-level security"):
            with rls.runtime_tx(b) as connection:
                connection.execute(
                    text("INSERT INTO framework_adoptions (framework_id, organization_id, scope) VALUES (:f, NULL, 'reference')"),
                    {"f": framework_id},
                )
        with rls.runtime_tx(b) as connection:
            assert connection.execute(
                text("UPDATE framework_adoptions SET tailoring_notes = 'tampered' WHERE id = :i"), {"i": shared_id}
            ).rowcount == 0
    finally:
        with rls.owner.begin() as connection:
            connection.execute(text("DELETE FROM framework_adoptions WHERE framework_id = :f"), {"f": framework_id})
            connection.execute(text("DELETE FROM regulatory_frameworks WHERE id = :f"), {"f": framework_id})


def test_newly_added_tenant_table_agent_registrations_is_scoped(rls, world):
    a, b = world.org("a"), world.org("b")
    with rls.owner.begin() as connection:
        ids = [
            connection.execute(
                text("INSERT INTO agent_registrations (name, purpose, status, created_at, organization_id) VALUES (:n, 'rls test', 'active', now(), :o) RETURNING id"),
                {"n": f"Agent {o}", "o": o},
            ).scalar_one()
            for o in (a, b)
        ]
    with rls.runtime_tx(a) as connection:
        seen = {r[0] for r in connection.execute(text("SELECT id FROM agent_registrations"))}
    assert ids[0] in seen and ids[1] not in seen


def test_event_log_partitioned_parent_is_filtered(rls, world):
    """event_log is partitioned by month in production; the policies sit on the
    parent only. Postgres applies a partitioned table's own policies when the
    parent is queried, so a read through it is filtered whichever partition holds
    the row. Where this database's event_log is a plain table the same assertion
    holds; where it is partitioned, a child partition is created and used."""
    a, b = world.org("a"), world.org("b")
    with rls.owner.connect() as connection:
        kind = connection.execute(text("SELECT relkind FROM pg_class WHERE oid = 'public.event_log'::regclass")).scalar_one()
    with rls.owner.begin() as connection:
        ids = [
            connection.execute(
                text(
                    "INSERT INTO event_log (organization_id, event_type, event_id, payload_json, ordinal) "
                    "VALUES (:o, 'test.event', :e, '{}', 1) RETURNING id"
                ),
                {"o": o, "e": str(uuid.uuid4())},
            ).scalar_one()
            for o in (a, b)
        ]
    assert kind in ("r", "p")
    with rls.runtime_tx(a) as connection:
        seen = {r[0] for r in connection.execute(text("SELECT id FROM event_log"))}
    assert ids[0] in seen and ids[1] not in seen


def test_partitioned_table_policies_apply_to_the_parent_when_queried_directly(rls):
    """Prove the mechanism on a scratch partitioned table: policies declared on the
    parent filter a read through it, with rows routed to child partitions."""
    suffix = uuid.uuid4().hex[:8]
    parent = f"rls_part_{suffix}"
    child = f"{parent}_c"
    default = f"{parent}_d"
    with rls.owner.begin() as connection:
        connection.execute(text(f"CREATE TABLE {parent} (id serial, organization_id integer NOT NULL) PARTITION BY LIST (organization_id)"))  # nosec B608
        connection.execute(text(f"CREATE TABLE {child} PARTITION OF {parent} FOR VALUES IN (1)"))  # nosec B608
        connection.execute(text(f"CREATE TABLE {default} PARTITION OF {parent} DEFAULT"))  # nosec B608
        connection.execute(text(f"ALTER TABLE {parent} ENABLE ROW LEVEL SECURITY"))  # nosec B608
        connection.execute(
            text(
                f"CREATE POLICY tenant_select ON {parent} FOR SELECT USING "  # nosec B608
                f"(organization_id = {MIGRATION._ORG})"
            )
        )
        connection.execute(text(f"GRANT SELECT ON {parent}, {child}, {default} TO {RUNTIME_ROLE}"))  # nosec B608
        connection.execute(text(f"INSERT INTO {parent} (organization_id) VALUES (1), (2)"))  # nosec B608
    try:
        with rls.runtime_tx(2) as connection:
            assert [r[0] for r in connection.execute(text(f"SELECT organization_id FROM {parent}"))] == [2]  # nosec B608
        with rls.runtime_tx(None) as connection:
            assert _count(connection, parent) == 0
    finally:
        with rls.owner.begin() as connection:
            connection.execute(text(f"DROP TABLE {parent}"))  # nosec B608


def test_table_owner_is_exempt_so_backfills_keep_working(rls, world):
    """The deploy role owns the tables; with no organisation set, a backfill-shaped
    UPDATE ... WHERE organization_id IS NULL as the owner must update the row."""
    a = world.org("a")
    code = f"RLS-{uuid.uuid4().hex[:10]}"
    world.shared_codes.append(code)
    with rls.owner.begin() as connection:
        connection.execute(text("INSERT INTO reference_model (name, code, organization_id) VALUES ('legacy', :c, NULL)"), {"c": code})
        updated = connection.execute(
            text("UPDATE reference_model SET name = 'backfilled' WHERE organization_id IS NULL AND code = :c"), {"c": code}
        )
    assert updated.rowcount == 1
    assert a is not None


# --------------------------------------------------------------------------- #
# platform_scope
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def _app_runs_as(app, engine):
    """Point the Flask app's database engine at ``engine`` for the block."""
    from app import db

    engines = db._app_engines[app]
    original = engines[None]
    engines[None] = engine
    try:
        yield
    finally:
        with app.app_context():
            db.session.remove()
        engines[None] = original


def test_platform_scope_reads_both_organisations_and_is_gone_after_the_block(app, rls, world):
    from app import db
    from app.jobs.tenant_safe_job import platform_scope

    a, b = world.org("a"), world.org("b")
    a_id, b_id = world.application(a, "Platform A"), world.application(b, "Platform B")
    sql = text("SELECT id FROM application_components WHERE id IN (:a, :b)")
    with _app_runs_as(app, rls.runtime), app.app_context():
        db.session.remove()
        assert db.session.execute(sql, {"a": a_id, "b": b_id}).all() == []
        with platform_scope("test: read across organisations"):
            seen = {r[0] for r in db.session.execute(sql, {"a": a_id, "b": b_id})}
            db.session.commit()  # a new transaction inside the block still carries the scope
            seen_again = {r[0] for r in db.session.execute(sql, {"a": a_id, "b": b_id})}
        assert seen == seen_again == {a_id, b_id}
        assert db.session.execute(text("SELECT current_setting('archie.platform_scope', true)")).scalar() in ("", None)
        assert db.session.execute(sql, {"a": a_id, "b": b_id}).all() == []
        db.session.rollback()
        assert db.session.execute(sql, {"a": a_id, "b": b_id}).all() == []
        db.session.rollback()


def test_platform_scope_needs_a_reason(app):
    from app.jobs.tenant_safe_job import platform_scope

    with app.app_context():
        for bad in ("", "  ", None):
            with pytest.raises(ValueError):
                with platform_scope(bad):
                    pass


def test_platform_scope_reaches_connections_opened_outside_the_session(app, rls, world):
    """Core connections and plain Sessions on the engine (the capability projection,
    the ARB waiver expiry) carry the scope too."""
    from app import db
    from app.jobs.tenant_safe_job import platform_scope

    a = world.org("a")
    a_id = world.application(a, "Engine path")
    sql = text("SELECT id FROM application_components WHERE id = :a")
    with _app_runs_as(app, rls.runtime), app.app_context():
        with db.engine.connect() as connection:
            assert connection.execute(sql, {"a": a_id}).all() == []
        with platform_scope("test: raw engine connection"):
            with db.engine.connect() as connection:
                assert connection.execute(sql, {"a": a_id}).all() != []
            with Session(db.engine) as plain, plain.begin():
                assert plain.execute(sql, {"a": a_id}).all() != []
        with db.engine.connect() as connection:
            assert connection.execute(sql, {"a": a_id}).all() == []


def test_tenant_scope_nested_in_platform_scope_sees_only_its_organisation(app, rls, world):
    """D3: the fence is closed inside ``tenant_scope`` even when a platform scope encloses it,
    and the platform scope is active again (flag and database setting) after the inner block."""
    from flask import g

    from app import db
    from app.jobs.tenant_safe_job import platform_scope, tenant_scope

    a, b = world.org("a"), world.org("b")
    a_id, b_id = world.application(a, "Nested A"), world.application(b, "Nested B")
    sql = text("SELECT id FROM application_components WHERE id IN (:a, :b)")
    with _app_runs_as(app, rls.runtime), app.app_context():
        db.session.remove()
        with platform_scope("test: nested scopes"):
            assert {r[0] for r in db.session.execute(sql, {"a": a_id, "b": b_id})} == {a_id, b_id}
            with tenant_scope(a):
                assert getattr(g, "_platform_scope", None) is None
                assert {r[0] for r in db.session.execute(sql, {"a": a_id, "b": b_id})} == {a_id}
                with tenant_scope(b):
                    assert {r[0] for r in db.session.execute(sql, {"a": a_id, "b": b_id})} == {b_id}
                assert {r[0] for r in db.session.execute(sql, {"a": a_id, "b": b_id})} == {a_id}
            assert g._platform_scope == "test: nested scopes"
            assert {r[0] for r in db.session.execute(sql, {"a": a_id, "b": b_id})} == {a_id, b_id}
            assert db.session.execute(text("SELECT current_setting('archie.platform_scope', true)")).scalar() == "on"
        assert getattr(g, "_platform_scope", None) is None
        db.session.rollback()


_STREAMING_ROUTES = [
    ("fix-dimension", {"dimension": "test_coverage"}),
    ("fix-recommendation", {"recommendation": "add a model for the orders table"}),
    ("chat-edit", {"instruction": "rename the model", "context": {"current_file": "app/models/order.py"}}),
]


@pytest.mark.parametrize("route,body", _STREAMING_ROUTES, ids=[r for r, _ in _STREAMING_ROUTES])
def test_streaming_code_edit_generators_read_their_own_organisations_api_settings(
    app, rls, world, client, login_as, monkeypatch, route, body
):
    """D2: the three streaming generators run lazily in a fresh app context. Under the runtime
    role they must carry the caller's organisation into it, or ``api_settings`` reads 0 rows and
    the stored LLM key is lost. Never another organisation's row."""
    import json

    a, b = world.org("a"), world.org("b")
    user_id = _make_user(world, a, "gen", role="Administrator")
    with rls.owner.begin() as connection:
        setting_ids = {}
        for label, org_id in (("a", a), ("b", b)):
            setting_ids[label] = connection.execute(
                text(
                    "INSERT INTO api_settings (provider, key_label, organization_id, enabled) "
                    "VALUES ('openai', :l, :o, true) RETURNING id"
                ),
                {"l": f"rls-{label}-{uuid.uuid4().hex[:6]}", "o": org_id},
            ).scalar_one()
        solution_id = connection.execute(
            text(
                "INSERT INTO solutions (name, organization_id, created_by_id) "
                "VALUES (:n, :o, :u) RETURNING id"
            ),
            {"n": f"RLS gen {uuid.uuid4().hex[:6]}", "o": a, "u": user_id},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO codegen_generations (solution_id, version, download_count, generated_files) "
                "VALUES (:s, 1, 0, CAST(:f AS json))"
            ),
            {"s": solution_id, "f": json.dumps({"app/models/order.py": "class Order: pass\n"})},
        )
    world.extra_deletes.append(("codegen_generations", "solution_id", [solution_id]))
    world.extra_deletes.append(("solutions", "id", [solution_id]))
    world.extra_deletes.append(("api_settings", "id", list(setting_ids.values())))

    seen = {}

    def fake_stream_chat_edit(**_kwargs):
        from app import db

        rows = db.session.execute(text("SELECT id FROM api_settings")).scalars().all()
        seen["ids"] = set(rows)
        yield "event: complete\ndata: {}\n\n"

    monkeypatch.setattr(
        "app.modules.codegen.services.nl_code_editor.stream_chat_edit", fake_stream_chat_edit
    )
    monkeypatch.setattr("app.utils.csrf_helper.validate_csrf", lambda token: None)

    with _app_runs_as(app, rls.runtime):
        with app.app_context():
            from app import db

            db.session.remove()
        login_as(client, user_id)
        response = client.post(
            f"/solutions/{solution_id}/codegen/{route}",
            json=body,
            headers={"X-CSRFToken": "test"},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        assert "event: complete" in response.get_data(as_text=True)
    assert seen["ids"] == {setting_ids["a"]}


# --------------------------------------------------------------------------- #
# D1: platform administrators manage the shared catalogue under the runtime role
# --------------------------------------------------------------------------- #


def _owner_row(rls, sql, **params):
    with rls.owner.connect() as connection:
        return connection.execute(text(sql), params).mappings().first()


def _shared_configuration(world, name=None):
    """A shared (organization_id NULL) framework configuration, inserted as the owner."""
    code = f"RLS-CFG-{uuid.uuid4().hex[:8]}"
    with world.rls.owner.begin() as connection:
        config_id = connection.execute(
            text(
                "INSERT INTO capability_framework_configuration "
                "(configuration_name, configuration_code, base_framework, status) "
                "VALUES (:n, :c, 'Unified_Manufacturing_Excellence', 'draft') RETURNING id"
            ),
            {"n": name or f"Shared {code}", "c": code},
        ).scalar_one()
    world.extra_deletes.append(("capability_framework_configuration", "id", [config_id]))
    return config_id, code


def _shared_template(world):
    code = f"RLS-TPL-{uuid.uuid4().hex[:8]}"
    config = {
        "configuration_name": f"From template {code}",
        "configuration_code": f"RLS-FROM-{code}",
        "enabled_domains": [],
        "enabled_extensions": [],
    }
    import json

    with world.rls.owner.begin() as connection:
        template_id = connection.execute(
            text(
                "INSERT INTO framework_configuration_templates "
                "(template_name, template_code, template_category, template_configuration, usage_count) "
                "VALUES (:n, :c, 'manufacturing', :cfg, 0) RETURNING id"
            ),
            {"n": f"Template {code}", "c": code, "cfg": json.dumps(config)},
        ).scalar_one()
    world.extra_deletes.append(("capability_framework_configuration", "configuration_code", [config["configuration_code"]]))
    world.extra_deletes.append(("framework_configuration_templates", "id", [template_id]))
    return template_id, config


def _logged_in_post(app, client, login_as, user_id, method, url, body=None):
    from app import db

    with app.app_context():
        db.session.remove()
    login_as(client, user_id)
    return getattr(client, method)(url, json=body if body is not None else {})


def test_platform_admin_creates_edits_and_deletes_a_shared_configuration_as_the_runtime_role(
    app, rls, world, client, login_as
):
    home = world.org("home")
    admin_id = _make_user(world, home, "padm", role="Administrator", platform=True)
    code = f"RLS-NEW-{uuid.uuid4().hex[:8]}"
    world.extra_deletes.append(("capability_framework_configuration", "configuration_code", [code]))
    base = "/api/framework-config/configurations"
    with _app_runs_as(app, rls.runtime):
        created = _logged_in_post(
            app, client, login_as, admin_id, "post", base,
            {"configuration_name": "RLS created", "configuration_code": code},
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        row = _owner_row(rls, "SELECT id, configuration_name, organization_id FROM capability_framework_configuration WHERE configuration_code = :c", c=code)
        assert row is not None and row["organization_id"] is None
        config_id = row["id"]

        edited = _logged_in_post(
            app, client, login_as, admin_id, "put", f"{base}/{config_id}", {"configuration_name": "RLS edited"}
        )
        assert edited.status_code == 200, edited.get_data(as_text=True)
        assert _owner_row(rls, "SELECT configuration_name FROM capability_framework_configuration WHERE id = :i", i=config_id)["configuration_name"] == "RLS edited"

        deleted = _logged_in_post(app, client, login_as, admin_id, "delete", f"{base}/{config_id}")
        assert deleted.status_code == 200, deleted.get_data(as_text=True)
        assert _owner_row(rls, "SELECT 1 AS x FROM capability_framework_configuration WHERE id = :i", i=config_id) is None


@pytest.mark.parametrize("persona", ["Architect", "Administrator"])
def test_organisation_level_users_cannot_write_the_shared_catalogue(app, rls, world, client, login_as, persona):
    home = world.org("home")
    user_id = _make_user(world, home, "orguser", role=persona)
    config_id, code = _shared_configuration(world, name="Untouched")
    template_id, _ = _shared_template(world)
    with rls.owner.begin() as connection:
        usage_before = connection.execute(
            text("SELECT usage_count FROM framework_configuration_templates WHERE id = :i"), {"i": template_id}
        ).scalar_one()
        count_before = connection.execute(text("SELECT count(*) FROM capability_framework_configuration")).scalar_one()
    attempts = [
        ("post", "/api/framework-config/configurations", {"configuration_name": "x", "configuration_code": f"RLS-NO-{uuid.uuid4().hex[:6]}"}),
        ("put", f"/api/framework-config/configurations/{config_id}", {"configuration_name": "Hijacked"}),
        ("delete", f"/api/framework-config/configurations/{config_id}", None),
        ("post", f"/api/framework-config/templates/{template_id}/deploy", {"configuration_code": f"RLS-NO-{uuid.uuid4().hex[:6]}"}),
        ("post", "/api/framework-config/migrations", {"migration_name": "m", "migration_code": f"RLS-NO-{uuid.uuid4().hex[:6]}", "source_framework_name": "s", "target_configuration_id": config_id}),
        ("post", "/framework-management/api/apply-template", {"template_id": template_id, "configuration_name": "x"}),
        ("post", "/industry-apqc/api/seed-frameworks", {}),
    ]
    with _app_runs_as(app, rls.runtime):
        for method, url, body in attempts:
            response = _logged_in_post(app, client, login_as, user_id, method, url, body)
            assert not 200 <= response.status_code < 300, f"{method} {url} -> {response.status_code}"
    after = _owner_row(rls, "SELECT configuration_name FROM capability_framework_configuration WHERE id = :i", i=config_id)
    assert after["configuration_name"] == "Untouched"
    with rls.owner.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM capability_framework_configuration")).scalar_one() == count_before
        assert connection.execute(
            text("SELECT usage_count FROM framework_configuration_templates WHERE id = :i"), {"i": template_id}
        ).scalar_one() == usage_before


def test_platform_admin_deploys_a_template_and_counts_the_use_as_the_runtime_role(app, rls, world, client, login_as):
    home = world.org("home")
    admin_id = _make_user(world, home, "padm", role="Administrator", platform=True)
    template_id, config = _shared_template(world)
    with _app_runs_as(app, rls.runtime):
        response = _logged_in_post(
            app, client, login_as, admin_id, "post",
            f"/api/framework-config/templates/{template_id}/deploy",
            {"configuration_code": config["configuration_code"]},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
    row = _owner_row(rls, "SELECT organization_id FROM capability_framework_configuration WHERE configuration_code = :c", c=config["configuration_code"])
    assert row is not None and row["organization_id"] is None
    assert _owner_row(rls, "SELECT usage_count FROM framework_configuration_templates WHERE id = :i", i=template_id)["usage_count"] == 1


def test_platform_admin_applies_a_template_as_the_runtime_role(app, rls, world, client, login_as):
    """``apply_template`` calls ``template.template_configuration.get(...)`` on a text column,
    so on main (and without row-level security) it answers 500 for every template. That is not
    this PR's to fix; the test hands the route a text value that also answers ``.get`` so the
    catalogue writes behind it (the new configuration and the template's usage count) run."""
    import json

    from sqlalchemy import event

    from app.models.framework_configuration import FrameworkConfigurationTemplate

    class JsonText(str):
        def get(self, key, default=None):
            return json.loads(self).get(key, default)

    def wrap(target, *_args):
        value = target.__dict__.get("template_configuration")
        if isinstance(value, str) and not isinstance(value, JsonText):
            target.__dict__["template_configuration"] = JsonText(value)

    home = world.org("home")
    admin_id = _make_user(world, home, "padm", role="Administrator", platform=True)
    template_id, template_config = _shared_template(world)
    # The template's own settings override the generated code, so the row carries this one.
    auto_code = template_config["configuration_code"]
    event.listen(FrameworkConfigurationTemplate, "load", wrap)
    event.listen(FrameworkConfigurationTemplate, "refresh", wrap)
    try:
        with _app_runs_as(app, rls.runtime):
            response = _logged_in_post(
                app, client, login_as, admin_id, "post", "/framework-management/api/apply-template",
                {"template_id": template_id, "configuration_name": "RLS applied"},
            )
            assert response.status_code == 200, response.get_data(as_text=True)
    finally:
        event.remove(FrameworkConfigurationTemplate, "load", wrap)
        event.remove(FrameworkConfigurationTemplate, "refresh", wrap)
    row = _owner_row(rls, "SELECT organization_id FROM capability_framework_configuration WHERE configuration_code = :c", c=auto_code)
    assert row is not None and row["organization_id"] is None
    assert _owner_row(rls, "SELECT usage_count FROM framework_configuration_templates WHERE id = :i", i=template_id)["usage_count"] == 1


def test_platform_admin_installs_an_extension_and_runs_a_migration_as_the_runtime_role(
    app, rls, world, client, login_as
):
    import json

    home = world.org("home")
    admin_id = _make_user(world, home, "padm", role="Administrator", platform=True)
    config_id, _ = _shared_configuration(world)
    ext_code = f"RLS-EXT-{uuid.uuid4().hex[:8]}"
    with rls.owner.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO framework_extensions (extension_name, extension_code, target_framework) "
                "VALUES (:n, :c, 'Unified_Manufacturing_Excellence')"
            ),
            {"n": f"Extension {ext_code}", "c": ext_code},
        )
    world.extra_deletes.append(("framework_extensions", "extension_code", [ext_code]))
    migration_code = f"RLS-MIG-{uuid.uuid4().hex[:8]}"
    world.extra_deletes.append(("framework_migration_mappings", "migration_code", [migration_code]))
    world.extra_deletes.insert(0, ("framework_migration_mappings", "target_configuration_id", [config_id]))
    with _app_runs_as(app, rls.runtime):
        installed = _logged_in_post(
            app, client, login_as, admin_id, "post",
            f"/api/framework-config/configurations/{config_id}/extensions/{ext_code}/install",
        )
        assert installed.status_code == 200, installed.get_data(as_text=True)
        created = _logged_in_post(
            app, client, login_as, admin_id, "post", "/api/framework-config/migrations",
            {
                "migration_name": "RLS migration",
                "migration_code": migration_code,
                "source_framework_name": "Legacy",
                "target_configuration_id": config_id,
                "domain_mappings": {"a": "b"},
            },
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        migration_id = created.get_json()["data"]["id"]
        ran = _logged_in_post(
            app, client, login_as, admin_id, "post", f"/api/framework-config/migrations/{migration_id}/execute"
        )
        assert ran.status_code == 200 and ran.get_json()["success"] is True, ran.get_data(as_text=True)
    enabled = _owner_row(rls, "SELECT enabled_extensions FROM capability_framework_configuration WHERE id = :i", i=config_id)
    assert ext_code in json.loads(enabled["enabled_extensions"])
    assert _owner_row(rls, "SELECT status FROM framework_migration_mappings WHERE id = :i", i=migration_id)["status"] == "completed"


def test_platform_admin_seeds_the_shared_industry_frameworks_as_the_runtime_role(app, rls, world, client, login_as):
    home = world.org("home")
    admin_id = _make_user(world, home, "padm", role="Administrator", platform=True)
    with rls.owner.begin() as connection:
        # Remove the default frameworks that carry no processes so the route has rows to write.
        removed = connection.execute(
            text(
                "DELETE FROM industry_apqc_framework f WHERE NOT EXISTS "
                "(SELECT 1 FROM industry_apqc_process p WHERE p.industry_framework_id = f.id) "
                "RETURNING industry_code"
            )
        ).scalars().all()
    with _app_runs_as(app, rls.runtime):
        response = _logged_in_post(app, client, login_as, admin_id, "post", "/industry-apqc/api/seed-frameworks")
        assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    created = [row["industry_code"] for row in body["created_frameworks"]]
    world.extra_deletes.append(("industry_apqc_framework", "industry_code", created))
    assert body["total_created"] == len(created) > 0
    assert set(removed) <= set(created)
    with rls.owner.connect() as connection:
        present = set(connection.execute(text("SELECT industry_code FROM industry_apqc_framework")).scalars())
    assert set(created) <= present


def _service_calls(world):
    """The write functions of the two services, each with the shared row it leaves behind."""
    from app.services.framework_configuration_service import (
        FrameworkConfigurationService,
        FrameworkMigrationService,
    )
    from app.services.industry_apqc_service import IndustryAPQCService

    config_id, _ = _shared_configuration(world)
    cfg_code = f"RLS-SVC-{uuid.uuid4().hex[:8]}"
    mig_code = f"RLS-SMG-{uuid.uuid4().hex[:8]}"
    ind_code = f"Z{uuid.uuid4().hex[:6]}".upper()
    with world.rls.owner.begin() as connection:
        base_id = connection.execute(
            text(
                "INSERT INTO apqc_process (process_code, process_name, category_level_1) "
                "VALUES (:c, 'RLS base', 'RLS') RETURNING id"
            ),
            {"c": f"RLS-{uuid.uuid4().hex[:8]}"},
        ).scalar_one()
    world.extra_deletes.append(("industry_apqc_process", "base_process_id", [base_id]))
    world.extra_deletes.append(("apqc_process", "id", [base_id]))
    world.extra_deletes.append(("capability_framework_configuration", "configuration_code", [cfg_code]))
    world.extra_deletes.insert(0, ("framework_migration_mappings", "target_configuration_id", [config_id]))
    world.extra_deletes.append(("industry_apqc_framework", "industry_code", [ind_code]))

    def create_configuration():
        FrameworkConfigurationService.create_configuration(
            {"configuration_name": "RLS svc", "configuration_code": cfg_code}
        )

    def create_migration():
        FrameworkMigrationService.create_migration_mapping(
            {
                "migration_name": "RLS svc",
                "migration_code": mig_code,
                "source_framework_name": "Legacy",
                "target_configuration_id": config_id,
            }
        )

    def create_framework():
        IndustryAPQCService().create_framework(ind_code, "RLS industry")

    def map_process():
        service = IndustryAPQCService()
        service.create_framework(ind_code, "RLS industry")
        service.map_base_process_to_industry(base_id, ind_code)

    return {
        "create_configuration": (create_configuration, "capability_framework_configuration", "configuration_code", cfg_code),
        "create_migration_mapping": (create_migration, "framework_migration_mappings", "migration_code", mig_code),
        "create_framework": (create_framework, "industry_apqc_framework", "industry_code", ind_code),
        "map_base_process_to_industry": (map_process, "industry_apqc_process", "base_process_id", base_id),
    }


@pytest.mark.parametrize(
    "name", ["create_configuration", "create_migration_mapping", "create_framework", "map_base_process_to_industry"]
)
def test_service_write_functions_need_the_platform_scope_and_work_inside_it(app, rls, world, name):
    """Each shared-catalogue write function: refused for an organisation (``tenant_scope`` alone,
    which is where an organisation-level caller runs), and a shared row once a platform
    administrator's route holds ``platform_scope`` around it."""
    from app import db
    from app.jobs.tenant_safe_job import platform_scope, tenant_scope

    home = world.org("home")
    call, table, column, value = _service_calls(world)[name]
    count_sql = f"SELECT count(*) FROM {table} WHERE {column} = :v"  # nosec B608 - fixed names in this module
    with _app_runs_as(app, rls.runtime), app.app_context():
        db.session.remove()
        with tenant_scope(home):
            with pytest.raises(DBAPIError):
                call()
            db.session.rollback()
        assert _owner_row(rls, count_sql.replace("count(*)", "count(*) AS n"), v=value)["n"] == 0
        with tenant_scope(home), platform_scope("test: platform administrator writes the shared catalogue"):
            call()
            db.session.commit()
        assert _owner_row(rls, count_sql.replace("count(*)", "count(*) AS n"), v=value)["n"] == 1
        db.session.remove()


# --------------------------------------------------------------------------- #
# The application works with row-level security on
# --------------------------------------------------------------------------- #

PAGES = ["/", "/applications/", "/applications/api/list", "/capability-map/", "/risks/", "/archimate/composer", "/admin/team"]


def _make_user(world, org_id, label, *, role, platform=False):
    with world.rls.owner.begin() as connection:
        role_id = connection.execute(text("SELECT id FROM roles WHERE name = :n"), {"n": role}).scalar_one()
        user_id = connection.execute(
            text(
                "INSERT INTO users (email, organization_id, confirmed, role_id, is_platform_admin, first_name, last_name) "
                "VALUES (:e, :o, true, :r, :p, :f, 'Tester') RETURNING id"
            ),
            {"e": f"rls-{label}-{uuid.uuid4().hex[:8]}@example.com", "o": org_id, "r": role_id, "p": platform, "f": label},
        ).scalar_one()
    world.user_ids.append(user_id)
    return user_id


def _walk(app, client, login_as, user_id, label):
    from app import db

    out = {}
    for url in PAGES:
        with app.app_context():
            db.session.remove()
        login_as(client, user_id)
        response = client.get(url, follow_redirects=False)
        out[url] = (response.status_code, response.get_data(as_text=True))
    return out


@pytest.mark.parametrize(
    "persona",
    ["org_a_architect", "org_a_administrator", "org_b_architect", "platform_administrator"],
)
def test_pages_render_under_the_runtime_role_like_they_do_without_it(app, rls, world, client, login_as, persona):
    a, b = world.org("a"), world.org("b")
    name_a, name_b = f"RLS Alpha {uuid.uuid4().hex[:6]}", f"RLS Bravo {uuid.uuid4().hex[:6]}"
    world.application(a, name_a)
    world.application(b, name_b)
    users = {
        "org_a_architect": _make_user(world, a, "aa", role="Architect"),
        "org_a_administrator": _make_user(world, a, "aadm", role="Administrator"),
        "org_b_architect": _make_user(world, b, "ba", role="Architect"),
        "platform_administrator": _make_user(world, a, "plat", role="Administrator", platform=True),
    }
    user_id = users[persona]
    own, other = (name_b, name_a) if persona == "org_b_architect" else (name_a, name_b)

    baseline = _walk(app, client, login_as, user_id, "baseline")
    with _app_runs_as(app, rls.runtime):
        under_rls = _walk(app, client, login_as, user_id, "rls")

    for url in PAGES:
        assert under_rls[url][0] == baseline[url][0], f"{persona} {url}: {under_rls[url][0]} != baseline {baseline[url][0]}"
        assert other not in under_rls[url][1], f"{persona} {url} shows the other organisation's application"
    assert baseline["/applications/api/list"][0] == 200
    assert own in baseline["/applications/api/list"][1]
    assert own in under_rls["/applications/api/list"][1]


def test_a_registered_tenant_job_processes_both_organisations_under_the_runtime_role(app, rls, world):
    from app.jobs.tenant_safe_job import TENANT_JOBS, run_for_each_tenant
    from app.services.event_log_service import relay_outbox_batch

    assert "event_log_relay" in TENANT_JOBS
    a, b = world.org("a"), world.org("b")
    with rls.owner.begin() as connection:
        for org_id in (a, b):
            connection.execute(
                text(
                    "INSERT INTO transformation_outbox_events "
                    "(organization_id, event_id, ordinal, event_type, payload_json, entity_type, entity_id) "
                    "VALUES (:o, :e, 0, 'rls.test', '{}', 'application', 1)"
                ),
                {"o": org_id, "e": str(uuid.uuid4())},
            )
    with _app_runs_as(app, rls.runtime):
        run = run_for_each_tenant(
            app,
            "rls-event-log-relay",
            lambda _org: relay_outbox_batch(),
            organization_ids=[a, b],
            use_lock=False,
        )
    assert run.failed == 0 and run.succeeded == 2
    assert [r.value for r in run.results] == [1, 1]
    for org_id in (a, b):
        assert _owner_value(rls, "SELECT count(*) FROM event_log WHERE organization_id = :o", o=org_id) == 1
        assert _owner_value(
            rls, "SELECT count(*) FROM transformation_outbox_events WHERE organization_id = :o AND published_at IS NOT NULL", o=org_id
        ) == 1


def test_password_reset_works_under_the_runtime_role(app, rls, world, client):
    """AccountToken is fenced; a reset link is redeemed with no organisation set."""
    from app import db
    from app.models.account_token import PURPOSE_PASSWORD_RESET, AccountToken
    from app.models.user import User
    from app.modules.account.services.account_service import AccountService

    a = world.org("a")
    user_id = _make_user(world, a, "reset", role="Architect")
    with rls.owner.begin() as connection:
        connection.execute(
            text("UPDATE users SET password_hash = 'x' WHERE id = :i"), {"i": user_id}
        )
    with app.app_context():
        user = db.session.get(User, user_id)
        row, raw = AccountToken.issue(user, PURPOSE_PASSWORD_RESET)
        db.session.commit()
        db.session.remove()

    with _app_runs_as(app, rls.runtime):
        with app.test_request_context("/"):
            assert AccountService.reset_link_usable(raw) is True
            assert AccountService.reset_link_usable("not-a-real-token") is False
            ok, message = AccountService.reset_password(raw, "A-new-Passw0rd!x")
            assert ok, message
            assert AccountService.reset_link_usable(raw) is False  # works once
        response = client.get(f"/account/reset-password/{raw}")
        assert response.status_code in (200, 302, 410)
    assert _owner_value(rls, "SELECT used_at IS NOT NULL FROM account_tokens WHERE user_id = :u", u=user_id) is True


# --------------------------------------------------------------------------- #
# The real roles: configure_roles, deploy role owns the tables and is exempt
# --------------------------------------------------------------------------- #


def test_real_configure_roles_runtime_role_is_fenced_and_deploy_role_is_exempt():
    """Run the real ``configure_roles`` on a scratch database, create a fenced table as the
    deploy role, apply the migration as the deploy role, then read as the real runtime role."""
    from scripts.database.configure_roles import configure_database_roles
    from urllib.parse import urlsplit, urlunsplit

    import psycopg2
    from psycopg2 import sql as pg_sql

    base = os.environ["TEST_DATABASE_URL"]
    parts = urlsplit(base)
    suffix = uuid.uuid4().hex[:10]
    database, deploy_role, runtime_role = f"rls_roles_{suffix}", f"rls_deploy_{suffix}", f"rls_runtime_{suffix}"
    deploy_password, runtime_password = uuid.uuid4().hex, uuid.uuid4().hex
    admin_url = urlunsplit(parts._replace(scheme="postgresql", path="/postgres"))

    def sqlalchemy_url(role, password):
        return f"postgresql+psycopg2://{role}:{password}@{parts.hostname}:{parts.port or 5432}/{database}"

    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(pg_sql.SQL("CREATE DATABASE {}").format(pg_sql.Identifier(database)))
    try:
        configure_database_roles(
            admin_url=admin_url,
            database_names=(database,),
            deploy_password=deploy_password,
            runtime_password=runtime_password,
            deploy_role=deploy_role,
            runtime_role=runtime_role,
        )
        deploy = create_engine(sqlalchemy_url(deploy_role, deploy_password), poolclass=NullPool)
        with deploy.begin() as connection:
            connection.execute(text("CREATE TABLE organizations (id serial PRIMARY KEY, name text)"))
            connection.execute(
                text(
                    "CREATE TABLE application_components (id serial PRIMARY KEY, name text, "
                    "organization_id integer NOT NULL REFERENCES organizations(id))"
                )
            )
            connection.execute(
                text("CREATE TABLE reference_model (id serial PRIMARY KEY, name text, code text, organization_id integer)")
            )
            connection.execute(text("INSERT INTO organizations (name) VALUES ('A'), ('B')"))
            connection.execute(
                text("INSERT INTO application_components (name, organization_id) VALUES ('a-app', 1), ('b-app', 2)")
            )
            connection.execute(text("INSERT INTO reference_model (name, code, organization_id) VALUES ('shared', 'S', NULL)"))
        # The migration, run as the deploy role (a plain role: not superuser, not CREATEROLE).
        _apply_policies(deploy)
        configure_database_roles(
            admin_url=admin_url,
            database_names=(database,),
            deploy_password=deploy_password,
            runtime_password=runtime_password,
            deploy_role=deploy_role,
            runtime_role=runtime_role,
        )
        with deploy.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT relname, pg_get_userbyid(relowner), relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname IN ('application_components', 'reference_model') ORDER BY 1"
                )
            ).all()
            assert [tuple(r) for r in rows] == [
                ("application_components", deploy_role, True, False),
                ("reference_model", deploy_role, True, False),
            ]
            # Owner exempt: no organisation set, the backfill-shaped UPDATE still works.
            assert connection.execute(text("SELECT count(*) FROM application_components")).scalar_one() == 2
        runtime = create_engine(sqlalchemy_url(runtime_role, runtime_password), poolclass=NullPool)
        with runtime.connect() as connection:
            flags = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
            assert tuple(flags) == (False, False)
            assert connection.execute(text("SELECT count(*) FROM application_components")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM reference_model")).scalar_one() == 1  # shared row
            connection.rollback()
            with connection.begin():
                connection.execute(text("SELECT set_config('archie.organization_id', '2', true)"))
                assert [r[0] for r in connection.execute(text("SELECT name FROM application_components"))] == ["b-app"]
            with connection.begin():  # reused connection: '' not NULL, must not raise
                assert connection.execute(text("SELECT count(*) FROM application_components")).scalar_one() == 0
        runtime.dispose()
        deploy.dispose()
    finally:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (database,),
            )
            cursor.execute(pg_sql.SQL("DROP DATABASE IF EXISTS {}").format(pg_sql.Identifier(database)))
            for role in (runtime_role, deploy_role):
                with contextlib.suppress(Exception):
                    cursor.execute(pg_sql.SQL("DROP ROLE IF EXISTS {}").format(pg_sql.Identifier(role)))
        admin.close()


# --------------------------------------------------------------------------- #
# Checks against the changes that landed on main while this PR was open
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("platform", [True, False], ids=["platform_admin", "org_admin_by_orgrole"])
def test_switched_active_organisation_sees_that_organisations_rows(app, rls, world, client, login_as, platform):
    """PR 428: authority is judged in the ACTIVE organisation. The database setting follows
    ``g.current_org_id``, so an administrator who switched into organisation B reads B's rows
    (and not A's, their home organisation's) under the runtime role."""
    a, b = world.org("home"), world.org("switched")
    name_a, name_b = f"RLS Home {uuid.uuid4().hex[:6]}", f"RLS Switched {uuid.uuid4().hex[:6]}"
    world.application(a, name_a)
    world.application(b, name_b)
    user_id = _make_user(world, a, "sw", role="Administrator", platform=platform)
    # Switching needs a membership row in the target organisation (PR 428 removed the platform-admin shortcut).
    with rls.owner.begin() as connection:
        connection.execute(
            text("INSERT INTO org_roles (organization_id, user_id, role) VALUES (:o, :u, 'org_admin')"),
            {"o": b, "u": user_id},
        )
    world.extra_deletes.append(("org_roles", "user_id", [user_id]))

    def walk():
        with app.app_context():
            from app import db

            db.session.remove()
        login_as(client, user_id)
        with client.session_transaction() as sess:
            sess["current_org_id"] = b
        listing = client.get("/applications/api/list")
        team = client.get("/admin/team")
        return listing.status_code, listing.get_data(as_text=True), team.status_code

    base = walk()
    with _app_runs_as(app, rls.runtime):
        under = walk()
    assert under[0] == base[0] == 200
    assert under[2] == base[2], f"/admin/team {under[2]} != baseline {base[2]}"
    assert name_b in base[1] and name_b in under[1]
    assert name_a not in under[1]


def test_per_organisation_connector_credential_keys_are_readable_by_their_own_organisation_only(app, rls, world):
    """PR 275: the key row and the credential row are fenced. Inside a request or a
    ``tenant_scope`` the vault reads and writes them under the runtime role; with no organisation
    it finds nothing (and fails closed) rather than reading another organisation's key."""
    from cryptography.fernet import Fernet

    from app import db
    from app.jobs.tenant_safe_job import tenant_scope
    from app.modules.codegen.services.credential_encryption import CredentialUnreadable
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    a, b = world.org("a"), world.org("b")
    world.extra_deletes.append(("org_connector_credentials", "organization_id", [a, b]))
    world.extra_deletes.append(("organization_encryption_keys", "organization_id", [a, b]))
    app.config["ORG_ENCRYPTION_MASTER_KEY"] = Fernet.generate_key().decode()
    vault = OrgCredentialVault()
    with _app_runs_as(app, rls.runtime), app.app_context():
        for org_id, secret in ((a, "secret-a"), (b, "secret-b")):
            with tenant_scope(org_id):
                vault.store(org_id, "jira", "api_key", secret)
                db.session.commit()
        for org_id, secret in ((a, "secret-a"), (b, "secret-b")):
            with tenant_scope(org_id):  # what a scheduled connector sync does
                assert vault.retrieve(org_id, "jira", "api_key") == secret
        with tenant_scope(a):  # organisation A cannot read B's credential row, even by naming B
            with pytest.raises(ValueError):  # the vault refuses an organisation other than the session's
                vault.retrieve(b, "jira", "api_key")
            db.session.rollback()
            seen = db.session.execute(
                text("SELECT count(*) FROM org_connector_credentials WHERE organization_id = :o"), {"o": b}
            ).scalar_one()
            assert seen == 0  # and the database itself hides B's row from A
            db.session.rollback()
        db.session.remove()
        assert vault.retrieve(a, "jira", "api_key") is None  # no organisation: nothing, no error
        db.session.rollback()
        with tenant_scope(a):
            from app.modules.codegen.services.credential_encryption import _get_org_fernet

            with pytest.raises(CredentialUnreadable):
                _get_org_fernet(b)
    assert _owner_value(rls, "SELECT count(*) FROM org_connector_credentials WHERE organization_id = ANY(:o)", o=[a, b]) == 2


def test_unified_work_package_unique_element_and_conflict_inserts_are_not_hidden_by_the_fence(app, rls, world):
    """PR 421: the partial unique index on archimate_element_id and the (source_table, source_id)
    ON CONFLICT DO NOTHING key keep working for the session organisation under the runtime role."""
    from sqlalchemy.exc import IntegrityError

    from app.services.work_package_service import element_refusal

    a, b = world.org("a"), world.org("b")
    with rls.owner.begin() as connection:
        element = connection.execute(
            text(
                "INSERT INTO archimate_elements (name, type, layer, organization_id) "
                "VALUES ('RLS WP element', 'WorkPackage', 'implementation_migration', :o) RETURNING id"
            ),
            {"o": a},
        ).scalar_one()
    world.extra_deletes.append(("unified_work_packages", "organization_id", [a, b]))
    world.extra_deletes.append(("archimate_elements", "organization_id", [a, b]))
    insert = text(
        "INSERT INTO unified_work_packages (name, organization_id, archimate_element_id, source_table, source_id, context, scope) "
        "VALUES (:n, :o, :e, :t, :s, 'architecture', 'enterprise') ON CONFLICT DO NOTHING RETURNING id"
    )
    with rls.runtime_tx(a, commit=True) as connection:
        first = connection.execute(insert, {"n": "wp1", "o": a, "e": element, "t": "rls_src", "s": 1}).scalar()
        assert first is not None
    with rls.runtime_tx(a, commit=True) as connection:
        # Same source row again: conflict, skipped, no error. The first copy is visible to the
        # session organisation, so a NOT EXISTS pre-check still sees it.
        again = connection.execute(insert, {"n": "wp1", "o": a, "e": None, "t": "rls_src", "s": 1}).scalar()
        assert again is None
        assert connection.execute(
            text("SELECT count(*) FROM unified_work_packages WHERE source_table = 'rls_src' AND source_id = 1")
        ).scalar_one() == 1
    with rls.runtime_tx(b) as connection:
        assert connection.execute(
            text("SELECT count(*) FROM unified_work_packages WHERE source_table = 'rls_src'")
        ).scalar_one() == 0
    # A second copy for the same element: the one element rule sees the holder, and the index
    # itself refuses a bypass of the rule.
    with _app_runs_as(app, rls.runtime), app.app_context():
        from flask import g

        from app import db

        g.current_org_id = a
        try:
            assert element_refusal(element, a, copy_id=None) == "shared"
            db.session.rollback()
        finally:
            g.pop("current_org_id", None)
    with pytest.raises(IntegrityError, match="duplicate key"):
        with rls.runtime_tx(a) as connection:
            connection.execute(
                text(
                    "INSERT INTO unified_work_packages (name, organization_id, archimate_element_id, source_table, source_id, context, scope) "
                    "VALUES ('wp2', :o, :e, 'rls_src', 2, 'architecture', 'enterprise')"
                ),
                {"o": a, "e": element},
            )
