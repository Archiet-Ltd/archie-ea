"""Tenant context on a reused database connection (ADR 0013, security requirements).

Every test here runs as a role with the runtime role's privileges (no superuser,
no BYPASSRLS, not the table owner), against a scratch database built the way a
deploy builds it, with two organisations seeded, on an engine whose pool holds
exactly one connection (``pool_size=1, max_overflow=0``) so that every test reuses
the same physical connection. Each test first proves both facts: the role is not
a superuser and does not bypass row-level security, and two sequential checkouts
return the same backend process id.

Two layers, kept apart by name:

``test_defect_*``
    Defect-proof tests. They use only the API that exists today
    (``Session(db.engine)``, ``db.engine.connect()``, ``db.session``,
    ``tenant_scope``, ``platform_scope``, ``threading.Thread``, the raw DBAPI
    connection) and assert the behaviour ADR 0013 requires. They fail on a tree
    without the engine-level mechanism, with an assertion that names the rows or
    setting value seen. Tests named ``*_control`` pass today on purpose: they
    prove the seeded data and the fixture are sound, so a defect test failing is
    never a fixture problem.

``test_api_*``
    API tests for the names the mechanism adds (``TenantContext``,
    ``ambient_tenant_context``, ``bind_tenant``, ``run_with_tenant``,
    ``independent_session``, ``independent_connection``, ``NoTenantContext``,
    ``TenantBindingConflict``, ``TenantContextUnsupported``, the checkin scrub and
    the boot role check). Names are imported inside each test so the module still
    collects without them; without them these tests fail with ImportError and are
    not defect proof.

Second connections that a path legitimately needs (the advisory-lock connection of
``job_lock``, the error-tracking handler, ``CommandService``) are opened in the
tests as separately named connections on the database, never through pool
overflow. The pool here has no overflow.

Requirement 11 (boot role check) runs its failing case against the CI superuser
URL: a superuser is the finding the check exists to refuse. All row-level
results above come from the runtime-role run, not from the superuser default
the rest of the suite uses.

Requirement 14 counts ``set_config`` calls on an authenticated request; its test
is expected to fail until the per-statement ORM writers are removed.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
import uuid
from pathlib import Path

import psycopg2
import pytest
from flask import g
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from tests._tenant_cursors import recording_cursor_class
from tests.test_runtime_role_domain_writes import (  # noqa: F401  (fixture)
    _maintenance_url,
    deployed_roles_database,
)

ROOT = Path(__file__).resolve().parents[1]
ORG_A, ORG_B = 11, 22

TENANT_TABLE = "tc_probe_tenant"
HYBRID_TABLE = "tc_probe_hybrid"


# --------------------------------------------------------------------------- #
# The policies are the production ones: read from the migration, not retyped.
# --------------------------------------------------------------------------- #


def _rls_migration():
    path = ROOT / "migrations" / "versions" / "20261008_row_level_security.py"
    spec = importlib.util.spec_from_file_location("tc_rls_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_probe_tables(database_name: str, runtime_role: str) -> None:
    rls = _rls_migration()
    conn = psycopg2.connect(_maintenance_url(), dbname=database_name)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE {TENANT_TABLE} (id serial PRIMARY KEY, "
                "organization_id integer NOT NULL, label text NOT NULL)"
            )
            cur.execute(
                f"CREATE TABLE {HYBRID_TABLE} (id serial PRIMARY KEY, "
                "organization_id integer, label text NOT NULL)"
            )
            for table, defs, names in (
                (TENANT_TABLE, rls._TENANT_DEFINITIONS, rls.TENANT_POLICIES),
                (HYBRID_TABLE, rls._HYBRID_DEFINITIONS, rls.HYBRID_POLICIES),
            ):
                cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                for name, (command, using, check) in zip(names, defs):
                    clause = f"CREATE POLICY {name} ON {table} FOR {command}"
                    if using:
                        clause += f" USING ({using})"
                    if check:
                        clause += f" WITH CHECK ({check})"
                    cur.execute(clause)
                cur.execute(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {runtime_role}"
                )
                cur.execute(
                    f"GRANT USAGE ON SEQUENCE {table}_id_seq TO {runtime_role}"
                )
            cur.executemany(
                f"INSERT INTO {TENANT_TABLE} (organization_id, label) VALUES (%s, %s)",
                [(ORG_A, "a1"), (ORG_A, "a2"), (ORG_B, "b1")],
            )
            cur.executemany(
                f"INSERT INTO {HYBRID_TABLE} (organization_id, label) VALUES (%s, %s)",
                [(None, "shared"), (ORG_A, "a-private"), (ORG_B, "b-private")],
            )
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Applications built on the runtime role with a pool of exactly one connection
# --------------------------------------------------------------------------- #

STATEMENTS: list[str] = []  # every statement any cursor of these apps' connections ran


def _set_config_calls() -> list[str]:
    return [statement for statement in STATEMENTS if "set_config" in statement]


def _build_app(config_name, database_url, **overrides):
    """Create an application on ``database_url``.

    Application start-up opens more than one connection at once (the provider
    settings read, the administrator reconciliation), so the app is created on an
    ordinary pool and the engine is swapped for the pool of one afterwards by
    ``_use_pool_of_one``. Every test body runs on the pool of one.
    """
    import app as app_package
    from config import TestingConfig
    from sqlalchemy.pool import NullPool

    attrs = {
        "SQLALCHEMY_DATABASE_URI": database_url,
        "SQLALCHEMY_ENGINE_OPTIONS": {"poolclass": NullPool, **_cursor_options(database_url)},
        "WTF_CSRF_ENABLED": False,
        "PROPAGATE_EXCEPTIONS": False,
        **overrides,
    }
    app_package.Config[config_name] = type(f"TC_{config_name}", (TestingConfig,), attrs)
    return app_package.create_app(config_name)


def _cursor_options(database_url):
    return {
        "connect_args": {
            "cursor_factory": recording_cursor_class(database_url, STATEMENTS)
        }
    }


def _use_pool_of_one(application):
    """Replace the application's engine with one pooled connection, no overflow."""
    from app.extensions import db

    url = application.config["SQLALCHEMY_DATABASE_URI"]
    with application.app_context():
        old = db.engine
        engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=1,
            max_overflow=0,
            pool_timeout=5,
            pool_reset_on_return="commit",
            **_cursor_options(url),
        )
        db.session.remove()
        db._app_engines[application][None] = engine
        old.dispose()
    return application


def _dispose(application):
    from app.extensions import db

    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture(scope="module")
def probe_database(deployed_roles_database):
    _seed_probe_tables(
        deployed_roles_database["name"], deployed_roles_database["runtime_role"]
    )
    return deployed_roles_database


@pytest.fixture(scope="module")
def pooled_app(probe_database):
    application = _use_pool_of_one(
        _build_app("tc_pooled_runtime", probe_database["runtime_url"])
    )
    yield application
    _dispose(application)


@pytest.fixture
def ctx(pooled_app):
    """An application context on the pooled runtime-role engine, with the
    reuse and role facts asserted first. Leaves nothing behind in ``g``."""
    from app.extensions import db

    with pooled_app.app_context():
        db.session.remove()
        engine = db.engine
        assert isinstance(engine.pool, QueuePool)
        assert (engine.pool.size(), engine.pool._max_overflow) == (1, 0)
        with engine.connect() as first:
            role = first.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            pid_first = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with engine.connect() as second:
            pid_second = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
        assert tuple(role) == (False, False), (
            f"runtime-role tests must not run as a superuser or BYPASSRLS role: {tuple(role)}"
        )
        assert pid_first == pid_second, (
            f"the pool did not reuse its connection: backend {pid_first} then {pid_second}"
        )
        yield pooled_app
        db.session.remove()


def _labels(executor, table=TENANT_TABLE):
    return sorted(row[0] for row in executor.execute(text(f"SELECT label FROM {table}")))


def _settings(executor):
    row = executor.execute(
        text(
            "SELECT current_setting('archie.organization_id', true), "
            "current_setting('archie.platform_scope', true)"
        )
    ).one()
    return tuple(value or "" for value in row)


def _pid(executor):
    return executor.execute(text("SELECT pg_backend_pid()")).scalar_one()


# --------------------------------------------------------------------------- #
# Requirement 1
# --------------------------------------------------------------------------- #


def test_defect_db_session_control_reads_the_organisation_rows(ctx):
    """Control, passes today: the request session carries the organisation."""
    from app.extensions import db

    g.current_org_id = ORG_A
    assert _labels(db.session) == ["a1", "a2"]


def test_defect_independent_session_reads_the_organisations_rows(ctx):
    """Requirement 1: a request as A reads its own fenced rows through a session
    the request session did not open. Today the connection carries no organisation."""
    from app.extensions import db

    g.current_org_id = ORG_A
    with Session(db.engine) as session, session.begin():
        seen = _labels(session)
    assert seen == ["a1", "a2"], (
        f"independent session as organisation {ORG_A} saw {seen!r} from {TENANT_TABLE}"
    )


# --------------------------------------------------------------------------- #
# Requirement 2
# --------------------------------------------------------------------------- #


def test_defect_no_context_block_after_an_organisation_read_sees_nothing(ctx):
    """Requirement 2 (rows half): after a transaction as A has used the one
    physical connection, a block with no tenant context reads zero rows."""
    from app.extensions import db

    g.current_org_id = ORG_A
    assert _labels(db.session) == ["a1", "a2"]  # primes the connection as A
    db.session.commit()
    db.session.remove()
    g.pop("current_org_id", None)
    with Session(db.engine) as session, session.begin():
        seen = _labels(session)
    assert seen == [], f"a block with no tenant context saw {seen!r} after an organisation-A read"


def test_api_independent_session_without_context_raises(ctx):
    """Requirement 2 (factory half)."""
    from app.middleware.tenant_isolation import NoTenantContext, independent_session

    g.pop("current_org_id", None)
    with pytest.raises(NoTenantContext):
        with independent_session() as session, session.begin():
            session.execute(text("SELECT 1"))


# --------------------------------------------------------------------------- #
# Requirement 3
# --------------------------------------------------------------------------- #


def test_defect_session_level_setting_does_not_survive_checkin(ctx):
    """Requirement 3: a session-level organisation written on the raw DBAPI
    connection must not be visible to the next checkout."""
    from app.extensions import db

    engine = db.engine
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("SELECT set_config('archie.organization_id', %s, false)", (str(ORG_A),))
        cursor.execute("SELECT pg_backend_pid()")
        pid_before = cursor.fetchone()[0]
        cursor.close()
    finally:
        raw.close()
    try:
        with engine.connect() as connection:
            pid_after = _pid(connection)
            seen = _settings(connection)
        assert pid_after == pid_before, "the next checkout was not the same physical connection"
        assert seen == ("", ""), (
            f"next checkout read archie.organization_id={seen[0]!r}, "
            f"archie.platform_scope={seen[1]!r} after a session-level write"
        )
    finally:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT set_config('archie.organization_id', '', false), "
                    "set_config('archie.platform_scope', '', false)"
                )
            )
            connection.commit()


# --------------------------------------------------------------------------- #
# Requirement 4
# --------------------------------------------------------------------------- #


def test_api_failing_scrub_invalidates_the_connection(ctx, monkeypatch):
    """Requirement 4: if the checkin scrub raises, the connection is invalidated
    and the next checkout gets a new backend process."""
    from app.extensions import db
    from app.middleware import tenant_isolation

    def broken_scrub(dbapi_connection):
        raise RuntimeError("simulated broken connection")

    engine = db.engine
    with engine.connect() as connection:
        pid_before = _pid(connection)
    monkeypatch.setattr(tenant_isolation, "_scrub_connection", broken_scrub)
    with engine.connect() as connection:
        assert _pid(connection) == pid_before
    # the checkin above ran the broken scrub
    monkeypatch.undo()
    with engine.connect() as connection:
        pid_after = _pid(connection)
    assert pid_after != pid_before, "a connection whose scrub failed was handed out again"


# --------------------------------------------------------------------------- #
# Requirement 5
# --------------------------------------------------------------------------- #


def test_defect_savepoint_rollback_does_not_restore_an_older_organisation(ctx):
    """Requirement 5: ambient A, savepoint, switch ambient to B, statement,
    ROLLBACK TO SAVEPOINT, statement: the last statement sees only B's rows."""
    from app.extensions import db

    g.current_org_id = ORG_A
    with db.engine.connect() as connection:
        with connection.begin():
            first = _labels(connection)
            savepoint = connection.begin_nested()
            g.current_org_id = ORG_B
            in_savepoint = _labels(connection)
            savepoint.rollback()
            after_rollback = _labels(connection)
    assert first == ["a1", "a2"], f"statement as A saw {first!r}"
    assert in_savepoint == ["b1"], f"statement as B inside the savepoint saw {in_savepoint!r}"
    assert after_rollback == ["b1"], (
        f"statement as B after ROLLBACK TO SAVEPOINT saw {after_rollback!r}"
    )


# --------------------------------------------------------------------------- #
# Requirement 6
# --------------------------------------------------------------------------- #


def test_defect_open_session_follows_the_tenant_switch(ctx):
    """Requirement 6, strengthened: a session opened in tenant_scope(A) and held
    open across tenant_scope(B) returns A's rows while in A and B's rows after the
    switch, and never A's rows after it. (Today it returns nothing in both, so
    "never A's" alone would pass vacuously.)"""
    from app.extensions import db
    from app.jobs.tenant_safe_job import tenant_scope

    session = Session(db.engine)
    try:
        session.begin()
        with tenant_scope(ORG_A):
            in_a = _labels(session)
        with tenant_scope(ORG_B):
            in_b = _labels(session)
    finally:
        session.close()
    assert in_a == ["a1", "a2"], f"open session inside tenant_scope(A) saw {in_a!r}"
    assert not {"a1", "a2"} & set(in_b), f"open session inside tenant_scope(B) saw A's rows {in_b!r}"
    assert in_b == ["b1"], f"open session inside tenant_scope(B) saw {in_b!r}"


# --------------------------------------------------------------------------- #
# Requirement 7
# --------------------------------------------------------------------------- #


def test_defect_connection_begun_in_platform_scope_loses_it_when_the_block_ends(ctx):
    """Requirement 7: an independent connection begun inside platform_scope and
    still open after the block ends sees only shared rows on its next statement."""
    from app.extensions import db
    from app.jobs.tenant_safe_job import platform_scope

    connection = db.engine.connect()
    try:
        with platform_scope("pooled reuse test"):
            transaction = connection.begin()
            inside = _labels(connection, HYBRID_TABLE)
        after = _labels(connection, HYBRID_TABLE)
        private_after = _labels(connection, TENANT_TABLE)
        transaction.rollback()
    finally:
        connection.close()
    assert inside == ["a-private", "b-private", "shared"], (
        f"inside platform_scope the connection saw {inside!r}"
    )
    assert after == ["shared"], f"after platform_scope ended the open connection saw {after!r}"
    assert private_after == [], (
        f"after platform_scope ended the open connection saw private rows {private_after!r}"
    )


# --------------------------------------------------------------------------- #
# Requirement 8
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [None, True, "1", 0, -1])
def test_api_bind_tenant_refuses_values_that_are_not_an_organisation(ctx, bad):
    from app.middleware.tenant_isolation import bind_tenant

    with pytest.raises((TypeError, ValueError)):
        with bind_tenant(bad):
            pass


def test_api_bind_tenant_refuses_a_different_signed_in_organisation(ctx):
    from app.middleware.tenant_isolation import TenantBindingConflict, bind_tenant

    g.current_org_id = ORG_A
    with pytest.raises(TenantBindingConflict):
        with bind_tenant(ORG_B):
            pass


# --------------------------------------------------------------------------- #
# Requirement 9
# --------------------------------------------------------------------------- #


def test_api_autocommit_connection_with_an_organisation_is_refused(ctx):
    from app.extensions import db
    from app.middleware.tenant_isolation import TenantContextUnsupported

    g.current_org_id = ORG_A
    with db.engine.connect() as connection:
        autocommit = connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(TenantContextUnsupported):
            autocommit.execute(text(f"SELECT label FROM {TENANT_TABLE}"))


# --------------------------------------------------------------------------- #
# Requirement 10
# --------------------------------------------------------------------------- #


def _read_in_thread(application, target_wrapper=None):
    from app.extensions import db

    result: dict = {}

    def work():
        with application.app_context():
            with Session(db.engine) as session, session.begin():
                result["rows"] = _labels(session)

    thread = threading.Thread(target=target_wrapper(work) if target_wrapper else work)
    thread.start()
    thread.join(30)
    assert not thread.is_alive()
    return result.get("rows")


def test_defect_thread_without_run_with_tenant_reads_nothing(ctx):
    """Requirement 10 (first half). Passes today: a new thread has no tenant."""
    g.current_org_id = ORG_A
    assert _read_in_thread(ctx) == []


def test_api_thread_with_run_with_tenant_reads_the_callers_organisation(ctx):
    """Requirement 10 (second half). ``run_with_tenant(fn, *args)`` captures the
    caller's ambient context and returns a callable that runs ``fn`` under it."""
    from app.middleware.tenant_isolation import run_with_tenant

    g.current_org_id = ORG_A
    assert _read_in_thread(ctx, run_with_tenant) == ["a1", "a2"]


# --------------------------------------------------------------------------- #
# Requirement 11 (split form: alert and refuse)
# --------------------------------------------------------------------------- #

ROLE_CHECK_LOGGER = "app.middleware.tenant_isolation"


def _role_check_records(caplog, level):
    return [
        record
        for record in caplog.records
        if record.levelno == level and "tenant role check" in record.getMessage()
    ]


def test_api_role_check_refuse_stops_a_superuser_start(probe_database):
    """The failing case uses the CI superuser URL, never the runtime role."""
    from app.middleware.tenant_isolation import DatabaseRoleCheckFailed  # noqa: F401

    superuser_url = os.environ["TEST_DATABASE_URL"]
    with pytest.raises(DatabaseRoleCheckFailed):
        application = _build_app(
            "tc_role_refuse", superuser_url, ARCHIE_DB_ROLE_CHECK="refuse"
        )
        _dispose(application)


def test_api_role_check_alert_logs_one_critical_and_keeps_serving(probe_database, caplog):
    from app.middleware.tenant_isolation import DatabaseRoleCheckFailed  # noqa: F401

    caplog.set_level(logging.INFO)
    superuser_url = os.environ["TEST_DATABASE_URL"]
    application = _build_app("tc_role_alert", superuser_url, ARCHIE_DB_ROLE_CHECK="alert")
    _use_pool_of_one(application)
    try:
        assert len(_role_check_records(caplog, logging.CRITICAL)) == 1
        assert application.test_client().get("/").status_code < 500
    finally:
        _dispose(application)


def test_api_role_check_passes_for_the_runtime_role_and_logs_once(probe_database, caplog):
    from app.middleware.tenant_isolation import DatabaseRoleCheckFailed  # noqa: F401

    caplog.set_level(logging.INFO)
    application = _build_app(
        "tc_role_pass", probe_database["runtime_url"], ARCHIE_DB_ROLE_CHECK="refuse"
    )
    _use_pool_of_one(application)
    try:
        passed = _role_check_records(caplog, logging.INFO)
        assert len(passed) == 1, [r.getMessage() for r in passed]
        message = passed[0].getMessage()
        assert "rolsuper=false rolbypassrls=false owns_fenced_tables=0" in message
        assert not _role_check_records(caplog, logging.CRITICAL)
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# Requirement 14: round trips on an authenticated request
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="old writers are removed in T-004"
)
def test_defect_authenticated_request_writes_the_tenant_once_per_transaction(ctx):
    """Requirement 14: a request that runs several ORM statements and one commit
    issues no more ``set_config`` calls than transactions it began, plus one for
    the context change when the organisation is first known.

    Today's count is recorded by the build report: the ORM listeners write once
    per ORM statement and again at flush, on top of ``before_request``.
    """
    from app.extensions import db
    from tests._session_test_helpers import mint_test_sid

    application = ctx
    application.config["WTF_CSRF_ENABLED"] = False
    from app.modules.account.services.account_service import AccountService

    email = f"pooled-{uuid.uuid4().hex[:10]}@example.com"
    with application.test_request_context("/"):
        user = AccountService.register_user(
            "Pooled", "Probe", email, "Passw0rd!long1", confirmed=True
        )
        db.session.commit()
    user_id, org_id = user.id, user.organization_id
    db.session.remove()
    sid = mint_test_sid(user_id, org_id, app=application)
    # The request must not inherit the sign-up's cached user (it shares this
    # application context's ``g``); it signs in from the session cookie below.
    from app.middleware.tenant_context import clear_tenant_context_cache

    clear_tenant_context_cache()
    client = application.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["_sid"] = sid

    begins: list[int] = []
    engine = db.engine

    def count_begin(connection):
        begins.append(1)

    event.listen(engine, "begin", count_begin)
    try:
        del STATEMENTS[:]
        response = client.post(
            "/api/risks",
            json={
                "title": "Vendor outage",
                "name": "Vendor outage",
                "description": "plain",
                "likelihood": 3,
                "probability": 3,
                "impact": 3,
                "category": "operational",
            },
        )
        writes = len(_set_config_calls())
    finally:
        event.remove(engine, "begin", count_begin)
    assert response.status_code in (200, 201), response.get_data(as_text=True)[:300]
    assert writes <= len(begins) + 1, (
        f"{writes} set_config calls for {len(begins)} transactions on one request"
    )
