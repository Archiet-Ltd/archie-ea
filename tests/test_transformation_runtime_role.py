"""Deployment/runtime PostgreSQL role separation for transformation guards."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest
import yaml
from psycopg2 import sql as pg_sql
from sqlalchemy import URL, create_engine

from app.models.transformation_db_guards import ensure_transformation_db_guards


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROLE = "archie_deploy"
RUNTIME_ROLE = "archie_runtime"
COMMAND_CAPABILITY_SECRET = "63" * 32


def _claim_runtime_command(
    cursor,
    *,
    organization_id,
    actor_id,
    operation,
    command_key,
    request_digest,
    natural_key,
    claim_token,
    request_id,
    capability_secret=COMMAND_CAPABILITY_SECRET,
):
    """Exercise the same deployment-secret boundary as CommandService."""
    secret = bytes.fromhex(capability_secret)
    cursor.execute(
        "SELECT * FROM public.archie_issue_command_claim_challenge(%s, %s, %s)",
        (organization_id, actor_id, 60000),
    )
    claim_nonce, claim_issued_at_ms, claim_expires_at_ms = cursor.fetchone()
    document = json.dumps(
        {
            "schema_version": "transformation-command-claim-r1",
            "key_id": hashlib.sha256(secret).hexdigest(),
            "organization_id": organization_id,
            "actor_id": actor_id,
            "operation": operation,
            "idempotency_key": command_key,
            "request_digest": request_digest,
            "natural_key": natural_key,
            "claim_token": claim_token,
            "claimant_request_id": request_id,
            "claim_nonce": claim_nonce,
            "claim_issued_at_ms": claim_issued_at_ms,
            "claim_expires_at_ms": claim_expires_at_ms,
            "contender_reason": (
                "natural_key_contender_materialisation_conflict"
            ),
            "lease_milliseconds": 60000,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    capability = hmac.new(
        secret, document.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    cursor.execute(
        "SELECT * FROM public.archie_claim_transformation_command(%s, %s)",
        (document, capability),
    )
    claimed = cursor.fetchone()
    assert claimed[0] == "claimed"
    return claimed[1]


def _execution_capability(
    *,
    organization_id,
    actor_id,
    operation,
    command_key,
    request_digest,
    natural_key,
    receipt_id,
    generation,
    claim_token,
    request_id,
    capability_secret=COMMAND_CAPABILITY_SECRET,
):
    secret = bytes.fromhex(capability_secret)
    document = json.dumps(
        {
            "schema_version": "transformation-command-execution-r1",
            "key_id": hashlib.sha256(secret).hexdigest(),
            "organization_id": organization_id,
            "actor_id": actor_id,
            "operation": operation,
            "idempotency_key": command_key,
            "request_digest": request_digest,
            "natural_key": natural_key,
            "receipt_id": receipt_id,
            "generation": generation,
            "claim_token": claim_token,
            "claimant_request_id": request_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return document, hmac.new(
        secret, document.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _environment(service: dict) -> dict[str, str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return environment
    return dict(item.split("=", 1) for item in environment)


def _maintenance_url() -> str:
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    return urlunsplit(parsed._replace(path="/postgres"))


@dataclass(frozen=True)
class IsolatedRoleDatabase:
    database_name: str
    deploy_role: str
    runtime_role: str
    privileged_role: str
    deploy_password: str
    runtime_password: str

    def url(self, *, role: str, password: str) -> URL:
        parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
        return URL.create(
            "postgresql+psycopg2",
            username=role,
            password=password,
            host=parsed.hostname,
            port=parsed.port,
            database=self.database_name,
        )


def _render_statement(cursor, statement) -> str:
    if isinstance(statement, pg_sql.Composable):
        return statement.as_string(cursor)
    return str(statement)


class _InterceptingCursor:
    """Delegate to psycopg2 while exposing executed SQL to one test callback."""

    def __init__(self, cursor, after_execute):
        self._cursor = cursor
        self._after_execute = after_execute

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._cursor.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, statement, parameters=None):
        result = self._cursor.execute(statement, parameters)
        self._after_execute(_render_statement(self._cursor, statement))
        return result


class _InterceptingConnection:
    """Keep real transaction semantics while intercepting cursor execution."""

    def __init__(self, connection, after_execute):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_after_execute", after_execute)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._connection.__exit__(exc_type, exc_value, traceback)
        finally:
            self._connection.close()

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __setattr__(self, name, value):
        setattr(self._connection, name, value)

    def cursor(self, *args, **kwargs):
        return _InterceptingCursor(
            self._connection.cursor(*args, **kwargs),
            self._after_execute,
        )


class _FailedTerminationCursor:
    """Return a real backend PID with an unproved termination result."""

    def __init__(self, cursor, backend_pid):
        self._cursor = cursor
        self._backend_pid = backend_pid
        self._fake_rows = None

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._cursor.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, statement, parameters=None):
        rendered = " ".join(_render_statement(self._cursor, statement).upper().split())
        if (
            "PG_TERMINATE_BACKEND" in rendered
            and "FROM PG_STAT_ACTIVITY" in rendered
        ):
            self._fake_rows = [(self._backend_pid, False)]
            return None
        self._fake_rows = None
        return self._cursor.execute(statement, parameters)

    def fetchall(self):
        if self._fake_rows is not None:
            rows = self._fake_rows
            self._fake_rows = []
            return rows
        return self._cursor.fetchall()

    def fetchone(self):
        if self._fake_rows is not None:
            if not self._fake_rows:
                return None
            return self._fake_rows.pop(0)
        return self._cursor.fetchone()


class _FailedTerminationConnection:
    def __init__(self, connection, backend_pid):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_backend_pid", backend_pid)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._connection.__exit__(exc_type, exc_value, traceback)
        finally:
            self._connection.close()

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __setattr__(self, name, value):
        setattr(self._connection, name, value)

    def cursor(self, *args, **kwargs):
        return _FailedTerminationCursor(
            self._connection.cursor(*args, **kwargs),
            self._backend_pid,
        )


class _TerminationResultCursor:
    """Override one termination result after the real attacker has gone away."""

    def __init__(self, cursor, override_result):
        self._cursor = cursor
        self._override_result = override_result
        self._fake_rows = None

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._cursor.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, statement, parameters=None):
        rendered = " ".join(_render_statement(self._cursor, statement).upper().split())
        if (
            "PG_TERMINATE_BACKEND" in rendered
            and "FROM PG_STAT_ACTIVITY" in rendered
            and self._override_result is not None
        ):
            override = self._override_result()
            if override is not None:
                self._fake_rows = list(override)
                return None
        self._fake_rows = None
        return self._cursor.execute(statement, parameters)

    def fetchall(self):
        if self._fake_rows is not None:
            rows = self._fake_rows
            self._fake_rows = []
            return rows
        return self._cursor.fetchall()

    def fetchone(self):
        if self._fake_rows is not None:
            if not self._fake_rows:
                return None
            return self._fake_rows.pop(0)
        return self._cursor.fetchone()


class _TerminationResultConnection:
    def __init__(self, connection, override_result):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_override_result", override_result)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._connection.__exit__(exc_type, exc_value, traceback)
        finally:
            self._connection.close()

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __setattr__(self, name, value):
        setattr(self._connection, name, value)

    def cursor(self, *args, **kwargs):
        return _TerminationResultCursor(
            self._connection.cursor(*args, **kwargs),
            self._override_result,
        )


class _LegacyTerminationCursor:
    """Make a current PostgreSQL server exercise the one-argument fallback."""

    def __init__(self, cursor, observe_statement):
        self._cursor = cursor
        self._observe_statement = observe_statement
        self._supports_timeout_result = None

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._cursor.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, statement, parameters=None):
        rendered = " ".join(_render_statement(self._cursor, statement).upper().split())
        self._observe_statement(rendered)
        if "TO_REGPROCEDURE" in rendered and "PG_TERMINATE_BACKEND" in rendered:
            self._supports_timeout_result = (False,)
            return None
        self._supports_timeout_result = None
        return self._cursor.execute(statement, parameters)

    def fetchone(self):
        if self._supports_timeout_result is not None:
            result = self._supports_timeout_result
            self._supports_timeout_result = None
            return result
        return self._cursor.fetchone()


class _LegacyTerminationConnection:
    def __init__(self, connection, observe_statement):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_observe_statement", observe_statement)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._connection.__exit__(exc_type, exc_value, traceback)
        finally:
            self._connection.close()

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __setattr__(self, name, value):
        setattr(self._connection, name, value)

    def cursor(self, *args, **kwargs):
        return _LegacyTerminationCursor(
            self._connection.cursor(*args, **kwargs),
            self._observe_statement,
        )


class _SkippingCursor:
    """Skip one deliberately selected ACL statement without faking verification."""

    def __init__(self, cursor, should_skip):
        self._cursor = cursor
        self._should_skip = should_skip

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._cursor.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, statement, parameters=None):
        rendered = _render_statement(self._cursor, statement)
        if self._should_skip(rendered):
            return None
        return self._cursor.execute(statement, parameters)


class _SkippingConnection:
    def __init__(self, connection, should_skip):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_should_skip", should_skip)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._connection.__exit__(exc_type, exc_value, traceback)
        finally:
            self._connection.close()

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __setattr__(self, name, value):
        setattr(self._connection, name, value)

    def cursor(self, *args, **kwargs):
        return _SkippingCursor(
            self._connection.cursor(*args, **kwargs),
            self._should_skip,
        )


def _render_unit_sql(statement) -> str:
    """Render the limited psycopg SQL shapes used by membership unit tests."""
    if isinstance(statement, pg_sql.SQL):
        return statement.string
    if isinstance(statement, pg_sql.Identifier):
        return ".".join(
            f'"{part.replace(chr(34), chr(34) * 2)}"'
            for part in statement.strings
        )
    if isinstance(statement, pg_sql.Composed):
        return "".join(_render_unit_sql(part) for part in statement.seq)
    return str(statement)


class _MembershipShapeCursor:
    """Exercise membership cleanup without requiring both PostgreSQL versions."""

    def __init__(self, *, server_version, before=(), after=()):
        self.connection = type(
            "MembershipShapeConnection",
            (),
            {"server_version": server_version},
        )()
        self.executed = []
        self._membership_snapshots = iter((before, after))
        self._rows = []

    def execute(self, statement, parameters=None):
        rendered = _render_unit_sql(statement)
        normalized = " ".join(rendered.upper().split())
        self.executed.append((rendered, parameters))
        if normalized.startswith("SELECT GRANTED.ROLNAME") and (
            "MEMBERSHIP.INHERIT_OPTION" in normalized
        ):
            if "WHERE GRANTOR.ROLNAME = ANY" in normalized:
                # The pre-fix implementation selected every grant made by a
                # runtime-capable role, rather than observing actual CASCADE
                # removals. Keep RED diagnostic output on that exact defect.
                self._rows = [
                    ("parent", "dependent", True, False, True),
                    ("unrelated_parent", "unrelated_member", False, True, True),
                ]
            else:
                self._rows = list(next(self._membership_snapshots))
        elif normalized.startswith("SELECT EXISTS"):
            self._rows = [(True,)]
        else:
            self._rows = []

    def fetchall(self):
        rows = self._rows
        self._rows = []
        return rows

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows.pop(0)


def test_pg15_membership_cleanup_uses_only_cross_version_membership_sql(monkeypatch):
    """Catches PostgreSQL 16-only membership SQL reaching shipped PostgreSQL 15."""
    import scripts.database.configure_roles as roles

    snapshots = iter(((('parent',), (), ()), ((), (), ())))
    monkeypatch.setattr(
        roles,
        "_runtime_membership_snapshot",
        lambda cursor, *, runtime_role: next(snapshots),
    )
    cursor = _MembershipShapeCursor(server_version=150_018)

    roles._strip_runtime_memberships(cursor, runtime_role="runtime")

    statements = tuple(
        " ".join(statement.upper().split()) for statement, _ in cursor.executed
    )
    assert statements == (
        "SELECT PG_ADVISORY_XACT_LOCK(HASHTEXT(%S))",
        "LOCK TABLE PG_CATALOG.PG_AUTH_MEMBERS IN SHARE ROW EXCLUSIVE MODE",
        'REVOKE "PARENT" FROM "RUNTIME" CASCADE',
    )
    assert cursor.executed[0][1] == (
        "archie_runtime_membership_cleanup:runtime",
    )
    assert all("INHERIT_OPTION" not in statement for statement in statements)
    assert all("SET_OPTION" not in statement for statement in statements)
    assert all("WITH ADMIN TRUE" not in statement for statement in statements)


def test_pg16_membership_cleanup_rehomes_only_actual_cascade_removals(monkeypatch):
    """Catches unrelated grants being duplicated merely because of their grantor."""
    import scripts.database.configure_roles as roles

    snapshots = iter(
        (
            (("parent",), (), ("inbound",)),
            ((), (), ()),
        )
    )
    monkeypatch.setattr(
        roles,
        "_runtime_membership_snapshot",
        lambda cursor, *, runtime_role: next(snapshots),
    )
    before = (
        ("parent", "runtime", "bootstrap", True, False, True),
        ("parent", "dependent", "runtime", True, False, False),
        ("parent", "dependent", "inbound", False, True, True),
        (
            "unrelated_parent",
            "unrelated_member",
            "inbound",
            False,
            True,
            True,
        ),
    )
    after = (
        (
            "unrelated_parent",
            "unrelated_member",
            "inbound",
            False,
            True,
            True,
        ),
    )
    cursor = _MembershipShapeCursor(
        server_version=160_000,
        before=before,
        after=after,
    )

    roles._strip_runtime_memberships(cursor, runtime_role="runtime")

    statements = tuple(
        " ".join(statement.upper().split()) for statement, _ in cursor.executed
    )
    membership_queries = tuple(
        statement
        for statement in statements
        if statement.startswith("SELECT GRANTED.ROLNAME")
    )
    grants = tuple(
        statement for statement in statements if statement.startswith("GRANT ")
    )
    assert len(membership_queries) == 2
    assert all("MEMBERSHIP.INHERIT_OPTION" in query for query in membership_queries)
    assert all("MEMBERSHIP.SET_OPTION" in query for query in membership_queries)
    assert grants == (
        'GRANT "PARENT" TO "DEPENDENT" WITH ADMIN TRUE, INHERIT TRUE, '
        'SET TRUE GRANTED BY CURRENT_USER',
    )


@pytest.fixture
def isolated_role_database():
    """Unique cluster objects are always dropped, including after test failure."""
    suffix = uuid.uuid4().hex[:12]
    role_database = IsolatedRoleDatabase(
        database_name=f"task3_roles_{suffix}",
        deploy_role=f"task3_deploy_{suffix}",
        runtime_role=f"task3_runtime_{suffix}",
        privileged_role=f"task3_privileged_{suffix}",
        deploy_password=f"deploy-{uuid.uuid4().hex}",
        runtime_password=f"runtime-{uuid.uuid4().hex}",
    )
    maintenance = psycopg2.connect(_maintenance_url())
    maintenance.autocommit = True
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("CREATE DATABASE {}").format(
                    pg_sql.Identifier(role_database.database_name)
                )
            )
    finally:
        maintenance.close()

    # PostgreSQL 13+ exposes gen_random_uuid() in core.  PostgreSQL 12, which
    # remains a supported diagnostic environment, provides it through
    # pgcrypto.  Each test creates a brand-new database, so installing the
    # extension only in TEST_DATABASE_URL is insufficient: provision the same
    # declared database prerequisite in the isolated database before the
    # deploy/runtime roles are deliberately stripped of superuser capability.
    target_url = urlunsplit(
        urlsplit(_maintenance_url())._replace(path=f"/{role_database.database_name}")
    )
    target = psycopg2.connect(target_url)
    target.autocommit = True
    try:
        with target.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    finally:
        target.close()

    try:
        yield role_database
    finally:
        maintenance = psycopg2.connect(_maintenance_url())
        maintenance.autocommit = True
        try:
            with maintenance.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (role_database.database_name,),
                )
                cursor.execute(
                    pg_sql.SQL("DROP DATABASE IF EXISTS {}").format(
                        pg_sql.Identifier(role_database.database_name)
                    )
                )
                cursor.execute(
                    pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                        pg_sql.Identifier(role_database.runtime_role)
                    )
                )
                cursor.execute(
                    pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                        pg_sql.Identifier(role_database.privileged_role)
                    )
                )
                cursor.execute(
                    pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                        pg_sql.Identifier(role_database.deploy_role)
                    )
                )
        finally:
            maintenance.close()


_TRANSFORMATION_TABLES_SQL = """
CREATE TABLE command_idempotency_records (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    actor_id integer NOT NULL,
    operation varchar(120) NOT NULL,
    idempotency_key varchar(255) NOT NULL,
    request_digest varchar(64) NOT NULL,
    natural_key varchar(512) NOT NULL,
    status varchar(32) NOT NULL,
    lease_generation integer NOT NULL,
    claim_token varchar(64) NOT NULL,
    claimant_request_id varchar(255) NOT NULL,
    lease_expires_at timestamptz,
    operation_result_id integer,
    attempt_count integer NOT NULL,
    last_error_class varchar(255),
    terminal_reason varchar(255),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    UNIQUE (organization_id, actor_id, operation, idempotency_key)
);
CREATE TABLE command_materialisations (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    actor_id integer NOT NULL,
    operation varchar(120) NOT NULL,
    natural_key varchar(512) NOT NULL,
    request_digest varchar(64) NOT NULL,
    receipt_id integer NOT NULL,
    receipt_generation integer NOT NULL,
    object_ids json NOT NULL,
    response_json json NOT NULL,
    outbox_events json NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (organization_id, operation, natural_key),
    UNIQUE (receipt_id)
);
CREATE TABLE operation_results (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    actor_id integer NOT NULL,
    operation varchar(120) NOT NULL,
    natural_key varchar(512) NOT NULL,
    request_digest varchar(64) NOT NULL,
    receipt_id integer NOT NULL,
    receipt_generation integer NOT NULL,
    object_ids json NOT NULL,
    response_json json NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (organization_id, operation, natural_key)
);
CREATE TABLE transformation_outbox_events (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    operation_result_id integer,
    event_id varchar(36) NOT NULL,
    ordinal integer NOT NULL,
    event_type varchar(160) NOT NULL,
    payload_json json NOT NULL,
    entity_type varchar(80),
    entity_id integer,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    published_at timestamptz,
    delivery_attempts integer NOT NULL DEFAULT 0
);
CREATE TABLE users (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL
);
CREATE TABLE application_components (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    deleted_at timestamptz
);
CREATE TABLE transformation_candidates (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    workstream_id integer NOT NULL,
    subject_type varchar(40) NOT NULL,
    subject_id integer NOT NULL,
    accepted_by_id integer NOT NULL
);
CREATE TABLE candidate_signals (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    candidate_id integer NOT NULL,
    rule_code varchar(80) NOT NULL,
    payload_json json NOT NULL,
    content_hash varchar(64) NOT NULL
);
CREATE TABLE evidence_records (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    candidate_id integer NOT NULL,
    subject_type varchar(40) NOT NULL,
    subject_id integer NOT NULL,
    claim_key varchar(100) NOT NULL,
    classification varchar(40) NOT NULL,
    source_identity varchar(1024) NOT NULL,
    source_type varchar(80) NOT NULL,
    source_record_id integer,
    created_by_id integer NOT NULL,
    value_json json NOT NULL,
    cited_evidence_ids json NOT NULL DEFAULT '[]',
    supersedes_id integer
);
CREATE TABLE evidence_claim_heads (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    subject_type varchar(40) NOT NULL,
    subject_id integer NOT NULL,
    claim_key varchar(100) NOT NULL,
    source_identity varchar(1024) NOT NULL,
    current_record_id integer,
    revision integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE evidence_requests (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    candidate_id integer NOT NULL,
    subject_type varchar(40) NOT NULL,
    subject_id integer NOT NULL,
    claim_key varchar(100) NOT NULL,
    assigned_to_id integer NOT NULL
);
CREATE TABLE candidate_overlap_dispositions (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    candidate_id integer NOT NULL,
    signal_digest varchar(64) NOT NULL,
    decision varchar(40) NOT NULL,
    overlapping_application_ids json NOT NULL,
    rationale text NOT NULL,
    target_application_id integer,
    decided_by_id integer NOT NULL,
    command_receipt_id integer NOT NULL,
    command_generation integer NOT NULL,
    decided_at timestamptz NOT NULL,
    UNIQUE (organization_id, candidate_id)
);
CREATE TABLE evidence_head_events (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    head_id integer NOT NULL,
    old_record_id integer,
    new_record_id integer NOT NULL UNIQUE,
    actor_id integer NOT NULL,
    command_receipt_id integer NOT NULL,
    command_generation integer NOT NULL,
    reason text NOT NULL,
    revision integer NOT NULL,
    created_txid bigint NOT NULL DEFAULT txid_current(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (organization_id, head_id, revision)
);
CREATE TABLE decision_brief_option_citations (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    brief_version_id integer NOT NULL,
    option_version_id integer NOT NULL
);
CREATE TABLE decision_briefs (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    workstream_id integer NOT NULL,
    status varchar(30) NOT NULL,
    revision integer NOT NULL
);
CREATE TABLE decision_brief_versions (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    brief_id integer NOT NULL,
    workstream_id integer NOT NULL,
    source_revision integer NOT NULL,
    created_by_id integer NOT NULL,
    submitted_by_id integer NOT NULL,
    content_hash varchar(64) NOT NULL,
    option_version_ids json NOT NULL,
    cited_evidence_ids json NOT NULL,
    frozen_payload json NOT NULL
);
CREATE TABLE decision_brief_evidence_citations (
    id serial PRIMARY KEY,
    organization_id integer NOT NULL,
    brief_version_id integer NOT NULL,
    evidence_record_id integer NOT NULL,
    evidence_head_id integer NOT NULL,
    head_revision_at_freeze integer NOT NULL,
    current_record_id_at_freeze integer NOT NULL,
    was_current boolean NOT NULL,
    acknowledged boolean NOT NULL,
    freshness_status varchar(30) NOT NULL
)
"""


@pytest.fixture
def guarded_runtime_database(isolated_role_database):
    """Create the minimal guarded schema under deploy/runtime role separation."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = isolated_role_database
    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    deploy_engine = create_engine(
        role_database.url(
            role=role_database.deploy_role,
            password=role_database.deploy_password,
        )
    )
    try:
        with deploy_engine.begin() as connection:
            connection.exec_driver_sql(_TRANSFORMATION_TABLES_SQL)
            ensure_transformation_db_guards(
                connection,
                runtime_role=role_database.runtime_role,
                capability_secrets=(COMMAND_CAPABILITY_SECRET,),
            )
        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )
        yield role_database
    finally:
        deploy_engine.dispose()


def _runtime_attestation_conflict_pair(role_database, *, conflict_candidate_id: int):
    """Attempt one same-receipt attestation/conflict pair as the runtime role."""
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    raw = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.runtime_role,
        password=role_database.runtime_password,
    )
    organization_id = 51001
    actor_id = 52001
    attestation_candidate_id = 53002
    subject_id = 54001
    claim_key = "application_owner"
    source_identity = f"attestation:user:{actor_id}"
    source_digest = hashlib.sha256(source_identity.encode("utf-8")).hexdigest()
    natural_key = (
        f"evidence:{attestation_candidate_id}:{claim_key}:{source_digest}:1"
    )
    claim_token = "c" * 64
    try:
        cursor = raw.cursor()
        receipt_id = _claim_runtime_command(
            cursor,
            organization_id=organization_id,
            actor_id=actor_id,
            operation="evidence.attest",
            command_key="candidate-binding",
            request_digest="d" * 64,
            natural_key=natural_key,
            claim_token=claim_token,
            request_id="candidate-binding-request",
        )
        cursor.execute(
            "INSERT INTO evidence_requests "
            "(organization_id, candidate_id, subject_type, subject_id, "
            "claim_key, assigned_to_id) VALUES (%s, %s, 'application', %s, %s, %s) "
            "RETURNING id",
            (
                organization_id,
                conflict_candidate_id,
                subject_id,
                claim_key,
                actor_id,
            ),
        )
        request_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO evidence_claim_heads "
            "(organization_id, subject_type, subject_id, claim_key, source_identity) "
            "VALUES (%s, 'application', %s, %s, %s) RETURNING id",
            (organization_id, subject_id, claim_key, source_identity),
        )
        attestation_head_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO evidence_records "
            "(organization_id, candidate_id, subject_type, subject_id, claim_key, "
            "classification, source_identity, source_type, source_record_id, "
            "created_by_id, value_json, cited_evidence_ids, supersedes_id) "
            "VALUES (%s, %s, 'application', %s, %s, 'attested', %s, "
            "'attestation', %s, %s, %s::json, '[]'::json, NULL) RETURNING id",
            (
                organization_id,
                attestation_candidate_id,
                subject_id,
                claim_key,
                source_identity,
                actor_id,
                actor_id,
                json.dumps("Candidate B attestation"),
            ),
        )
        attestation_record_id = cursor.fetchone()[0]
        cursor.execute(
            "SELECT public.archie_advance_evidence_head(%s, %s, 0, %s, %s, 1, %s)",
            (
                attestation_head_id,
                attestation_record_id,
                actor_id,
                receipt_id,
                claim_token,
            ),
        )
        attestation_revision = cursor.fetchone()[0]

        conflict_identity = f"conflict:request:{request_id}"
        cursor.execute(
            "INSERT INTO evidence_claim_heads "
            "(organization_id, subject_type, subject_id, claim_key, source_identity) "
            "VALUES (%s, 'application', %s, %s, %s) RETURNING id",
            (organization_id, subject_id, claim_key, conflict_identity),
        )
        conflict_head_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO evidence_records "
            "(organization_id, candidate_id, subject_type, subject_id, claim_key, "
            "classification, source_identity, source_type, source_record_id, "
            "created_by_id, value_json, cited_evidence_ids, supersedes_id) "
            "VALUES (%s, %s, 'application', %s, %s, 'conflict', %s, "
            "'governance_conflict', %s, %s, %s::json, %s::json, NULL) RETURNING id",
            (
                organization_id,
                conflict_candidate_id,
                subject_id,
                claim_key,
                conflict_identity,
                request_id,
                actor_id,
                json.dumps({"conflicting_evidence_ids": [attestation_record_id]}),
                json.dumps([attestation_record_id]),
            ),
        )
        conflict_record_id = cursor.fetchone()[0]
        cursor.execute(
            "SELECT public.archie_advance_evidence_head(%s, %s, 0, %s, %s, 1, %s)",
            (
                conflict_head_id,
                conflict_record_id,
                actor_id,
                receipt_id,
                claim_token,
            ),
        )
        conflict_revision = cursor.fetchone()[0]
        raw.commit()
        cursor.execute(
            "SELECT revision FROM evidence_claim_heads WHERE id = %s",
            (conflict_head_id,),
        )
        persisted_revision = cursor.fetchone()[0]
        cursor.execute(
            "SELECT count(*) FROM evidence_head_events WHERE command_receipt_id = %s",
            (receipt_id,),
        )
        event_count = cursor.fetchone()[0]
        return (
            attestation_revision,
            conflict_revision,
            persisted_revision,
            event_count,
        )
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def test_restricted_runtime_cannot_forge_receipt_brief_version_and_citations(
    guarded_runtime_database,
):
    """Catches runtime composing writable command/version rows into a forged dossier."""
    role_database = guarded_runtime_database
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    raw = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.runtime_role,
        password=role_database.runtime_password,
    )
    exploit_succeeded = False
    try:
        cursor = raw.cursor()
        cursor.execute(
            "INSERT INTO decision_briefs "
            "(organization_id, workstream_id, status, revision) "
            "VALUES (71001, 72001, 'draft', 1) RETURNING id"
        )
        brief_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO command_idempotency_records "
            "(organization_id, actor_id, operation, idempotency_key, "
            " request_digest, natural_key, status, lease_generation, "
            " claim_token, claimant_request_id, lease_expires_at, attempt_count) "
            "VALUES (71001, 73001, 'brief.freeze', 'forged-brief', %s, %s, "
            " 'in_progress', 1, %s, 'forged-request', "
            " clock_timestamp() + interval '1 minute', 1) RETURNING id",
            (
                "d" * 64,
                f"brief:{brief_id}:version:1",
                "t" * 64,
            ),
        )
        receipt_id = cursor.fetchone()[0]
        frozen_payload = {
            "option_versions": [{"id": 74001}],
            "evidence": [],
        }
        cursor.execute(
            "INSERT INTO decision_brief_versions "
            "(organization_id, brief_id, workstream_id, source_revision, "
            " created_by_id, submitted_by_id, content_hash, "
            " option_version_ids, cited_evidence_ids, frozen_payload) "
            "VALUES (71001, %s, 72001, 1, 73001, 73001, %s, %s::json, "
            " '[]'::json, %s::json) RETURNING id",
            (
                brief_id,
                "a" * 64,
                json.dumps([74001]),
                json.dumps(frozen_payload),
            ),
        )
        version_id = cursor.fetchone()[0]
        cursor.execute(
            "SELECT public.archie_insert_decision_brief_citations("
            "%s, 73001, %s, 1, %s)",
            (version_id, receipt_id, "t" * 64),
        )
        raw.commit()
        exploit_succeeded = True
    except psycopg2.Error:
        raw.rollback()
    finally:
        raw.close()

    deploy_engine = create_engine(
        role_database.url(
            role=role_database.deploy_role,
            password=role_database.deploy_password,
        )
    )
    try:
        with deploy_engine.connect() as connection:
            artifact_count = connection.exec_driver_sql(
                "SELECT (SELECT count(*) FROM decision_brief_versions) + "
                "(SELECT count(*) FROM decision_brief_option_citations) + "
                "(SELECT count(*) FROM decision_brief_evidence_citations)"
            ).scalar_one()
    finally:
        deploy_engine.dispose()

    assert exploit_succeeded is False
    assert artifact_count == 0


def test_restricted_runtime_receipts_require_server_signed_claim(
    guarded_runtime_database,
):
    """Catches restoring direct receipt minting or exposing the owner-only HMAC key."""
    role_database = guarded_runtime_database
    deploy_engine = create_engine(
        role_database.url(
            role=role_database.deploy_role,
            password=role_database.deploy_password,
        )
    )
    try:
        with deploy_engine.connect() as connection:
            privileges = connection.exec_driver_sql(
                "SELECT "
                "has_table_privilege(%s, 'command_idempotency_records', 'INSERT'), "
                "has_table_privilege(%s, 'archie_command_capability_keys', 'SELECT'), "
                "has_table_privilege(%s, 'archie_command_claim_challenges', 'SELECT'), "
                "has_function_privilege(%s, "
                "'public.archie_issue_command_claim_challenge(bigint,bigint,integer)', "
                "'EXECUTE'), "
                "has_function_privilege(%s, "
                "'public.archie_claim_transformation_command(text,text)', 'EXECUTE')",
                (role_database.runtime_role,) * 5,
            ).one()
    finally:
        deploy_engine.dispose()

    assert privileges == (False, False, False, True, True)

    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    raw = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.runtime_role,
        password=role_database.runtime_password,
    )
    try:
        with raw.cursor() as cursor, pytest.raises(psycopg2.Error):
            cursor.execute(
                "INSERT INTO command_idempotency_records "
                "(organization_id, actor_id, operation, idempotency_key, "
                " request_digest, natural_key, status, lease_generation, "
                " claim_token, claimant_request_id, lease_expires_at, attempt_count) "
                "VALUES (1, 2, 'brief.freeze', 'forged', %s, 'brief:3:version:1', "
                " 'in_progress', 1, %s, 'forged-request', "
                " clock_timestamp() + interval '1 minute', 1)",
                ("d" * 64, "t" * 64),
            )
    finally:
        raw.rollback()
        raw.close()


def test_capability_key_rotation_accepts_overlap_then_retires_previous_key(
    guarded_runtime_database,
):
    role_database = guarded_runtime_database
    next_secret = "64" * 32
    deploy_engine = create_engine(
        role_database.url(
            role=role_database.deploy_role,
            password=role_database.deploy_password,
        )
    )
    try:
        with deploy_engine.begin() as connection:
            ensure_transformation_db_guards(
                connection,
                runtime_role=role_database.runtime_role,
                capability_secrets=(next_secret, COMMAND_CAPABILITY_SECRET),
            )
        parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
        raw = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=role_database.database_name,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        try:
            with raw.cursor() as cursor:
                old_receipt = _claim_runtime_command(
                    cursor,
                    organization_id=81001,
                    actor_id=82001,
                    operation="rotation.probe",
                    command_key="overlap-old",
                    request_digest="8" * 64,
                    natural_key="rotation:old",
                    claim_token="9" * 64,
                    request_id="rotation-overlap",
                )
            raw.commit()
            assert old_receipt > 0

            with deploy_engine.begin() as connection:
                ensure_transformation_db_guards(
                    connection,
                    runtime_role=role_database.runtime_role,
                    capability_secrets=(next_secret,),
                )

            with raw.cursor() as cursor, pytest.raises(psycopg2.Error):
                _claim_runtime_command(
                    cursor,
                    organization_id=81001,
                    actor_id=82001,
                    operation="rotation.probe",
                    command_key="retired-old",
                    request_digest="8" * 64,
                    natural_key="rotation:retired",
                    claim_token="a" * 64,
                    request_id="rotation-retired",
                )
            raw.rollback()
            with raw.cursor() as cursor:
                current_receipt = _claim_runtime_command(
                    cursor,
                    organization_id=81001,
                    actor_id=82001,
                    operation="rotation.probe",
                    command_key="current-new",
                    request_digest="8" * 64,
                    natural_key="rotation:current",
                    claim_token="b" * 64,
                    request_id="rotation-current",
                    capability_secret=next_secret,
                )
            raw.commit()
            assert current_receipt > old_receipt
        finally:
            raw.close()
    finally:
        deploy_engine.dispose()


def _runtime_conflict_resolution(role_database, *, supersede_governing_leaf: bool):
    """Call the definer directly with a current or superseded cited source leaf."""
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    raw = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.runtime_role,
        password=role_database.runtime_password,
    )
    organization_id = 61001
    actor_id = 62001
    conflict_candidate_id = 63001
    governing_candidate_id = 63002
    subject_id = 64001
    claim_key = "application_owner"
    source_identity = "inventory:runtime-governing-owner"
    source_digest = hashlib.sha256(source_identity.encode("utf-8")).hexdigest()
    claim_token = "e" * 64

    def insert_receipt(cursor, *, natural_key: str, command_key: str):
        return _claim_runtime_command(
            cursor,
            organization_id=organization_id,
            actor_id=actor_id,
            operation="evidence.observe",
            command_key=command_key,
            request_digest="f" * 64,
            natural_key=natural_key,
            claim_token=claim_token,
            request_id=f"{command_key}-request",
        )

    try:
        cursor = raw.cursor()
        cursor.execute(
            "INSERT INTO evidence_claim_heads "
            "(organization_id, subject_type, subject_id, claim_key, source_identity) "
            "VALUES (%s, 'application', %s, %s, %s) RETURNING id",
            (organization_id, subject_id, claim_key, source_identity),
        )
        governing_head_id = cursor.fetchone()[0]
        root_receipt_id = insert_receipt(
            cursor,
            natural_key=(
                f"evidence:{governing_candidate_id}:{claim_key}:{source_digest}:1"
            ),
            command_key="runtime-governing-root",
        )
        cursor.execute(
            "INSERT INTO evidence_records "
            "(organization_id, candidate_id, subject_type, subject_id, claim_key, "
            "classification, source_identity, source_type, source_record_id, "
            "created_by_id, value_json, cited_evidence_ids, supersedes_id) "
            "VALUES (%s, %s, 'application', %s, %s, 'observed', %s, "
            "'inventory', NULL, %s, %s::json, '[]'::json, NULL) RETURNING id",
            (
                organization_id,
                governing_candidate_id,
                subject_id,
                claim_key,
                source_identity,
                actor_id,
                json.dumps("Governed owner v1"),
            ),
        )
        governing_record_id = cursor.fetchone()[0]
        cursor.execute(
            "SELECT public.archie_advance_evidence_head(%s, %s, 0, %s, %s, 1, %s)",
            (
                governing_head_id,
                governing_record_id,
                actor_id,
                root_receipt_id,
                claim_token,
            ),
        )
        assert cursor.fetchone()[0] == 1
        raw.commit()

        if supersede_governing_leaf:
            correction_receipt_id = insert_receipt(
                cursor,
                natural_key=(
                    f"evidence:{governing_candidate_id}:{claim_key}:{source_digest}:2"
                ),
                command_key="runtime-governing-correction",
            )
            cursor.execute(
                "INSERT INTO evidence_records "
                "(organization_id, candidate_id, subject_type, subject_id, claim_key, "
                "classification, source_identity, source_type, source_record_id, "
                "created_by_id, value_json, cited_evidence_ids, supersedes_id) "
                "VALUES (%s, %s, 'application', %s, %s, 'observed', %s, "
                "'inventory', NULL, %s, %s::json, '[]'::json, %s) RETURNING id",
                (
                    organization_id,
                    governing_candidate_id,
                    subject_id,
                    claim_key,
                    source_identity,
                    actor_id,
                    json.dumps("Governed owner v2"),
                    governing_record_id,
                ),
            )
            correction_record_id = cursor.fetchone()[0]
            cursor.execute(
                "SELECT public.archie_advance_evidence_head(%s, %s, 1, %s, %s, 1, %s)",
                (
                    governing_head_id,
                    correction_record_id,
                    actor_id,
                    correction_receipt_id,
                    claim_token,
                ),
            )
            assert cursor.fetchone()[0] == 2
            raw.commit()

        cursor.execute(
            "INSERT INTO evidence_records "
            "(organization_id, candidate_id, subject_type, subject_id, claim_key, "
            "classification, source_identity, source_type, source_record_id, "
            "created_by_id, value_json, cited_evidence_ids, supersedes_id) "
            "VALUES (%s, %s, 'application', %s, %s, 'conflict', %s, "
            "'governance_conflict', NULL, %s, %s::json, %s::json, NULL) RETURNING id",
            (
                organization_id,
                conflict_candidate_id,
                subject_id,
                claim_key,
                "conflict:runtime-resolution-probe",
                actor_id,
                json.dumps({"conflicting_evidence_ids": [governing_record_id]}),
                json.dumps([governing_record_id]),
            ),
        )
        conflict_record_id = cursor.fetchone()[0]
        resolution_identity = f"resolution:conflict:{conflict_record_id}"
        cursor.execute(
            "INSERT INTO evidence_claim_heads "
            "(organization_id, subject_type, subject_id, claim_key, source_identity) "
            "VALUES (%s, 'application', %s, %s, %s) RETURNING id",
            (organization_id, subject_id, claim_key, resolution_identity),
        )
        resolution_head_id = cursor.fetchone()[0]
        resolution_receipt_id = _claim_runtime_command(
            cursor,
            organization_id=organization_id,
            actor_id=actor_id,
            operation="evidence.conflict.resolve",
            command_key="runtime-resolution",
            request_digest="a" * 64,
            natural_key=(
                f"evidence-conflict-resolution:{conflict_record_id}:"
                f"{governing_record_id}"
            ),
            claim_token=claim_token,
            request_id="runtime-resolution-request",
        )
        cursor.execute(
            "INSERT INTO evidence_records "
            "(organization_id, candidate_id, subject_type, subject_id, claim_key, "
            "classification, source_identity, source_type, source_record_id, "
            "created_by_id, value_json, cited_evidence_ids, supersedes_id) "
            "VALUES (%s, %s, 'application', %s, %s, 'derived', %s, "
            "'governance_resolution', %s, %s, %s::json, %s::json, NULL) RETURNING id",
            (
                organization_id,
                conflict_candidate_id,
                subject_id,
                claim_key,
                resolution_identity,
                conflict_record_id,
                actor_id,
                json.dumps(
                    {
                        "conflict_evidence_id": conflict_record_id,
                        "governing_evidence_id": governing_record_id,
                        "rationale": "Runtime direct-call current-head probe",
                    }
                ),
                json.dumps([conflict_record_id, governing_record_id]),
            ),
        )
        resolution_record_id = cursor.fetchone()[0]
        cursor.execute(
            "SELECT public.archie_advance_evidence_head(%s, %s, 0, %s, %s, 1, %s)",
            (
                resolution_head_id,
                resolution_record_id,
                actor_id,
                resolution_receipt_id,
                claim_token,
            ),
        )
        revision = cursor.fetchone()[0]
        raw.commit()
        return revision
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def _runtime_same_source_resolution_attack(
    role_database, *, duplicate_head_alias: bool
):
    """Attempt to make a resolution replace its own governing source head."""
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    raw = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.runtime_role,
        password=role_database.runtime_password,
    )
    organization_id = 71001
    actor_id = 72001
    candidate_id = 73001
    subject_id = 74001
    claim_key = "application_owner"
    claim_token = "1" * 64

    def persisted_state(cursor):
        cursor.execute(
            "SELECT count(*) FROM evidence_records WHERE organization_id = %s",
            (organization_id,),
        )
        record_count = cursor.fetchone()[0]
        cursor.execute(
            "SELECT count(*) FROM evidence_head_events WHERE organization_id = %s",
            (organization_id,),
        )
        event_count = cursor.fetchone()[0]
        cursor.execute(
            "SELECT id, current_record_id, revision FROM evidence_claim_heads "
            "WHERE organization_id = %s ORDER BY id",
            (organization_id,),
        )
        return record_count, event_count, tuple(cursor.fetchall())

    try:
        cursor = raw.cursor()
        cursor.execute(
            "SELECT nextval(pg_get_serial_sequence('evidence_records', 'id'))"
        )
        governing_record_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO evidence_records "
            "(organization_id, candidate_id, subject_type, subject_id, claim_key, "
            "classification, source_identity, source_type, source_record_id, "
            "created_by_id, value_json, cited_evidence_ids, supersedes_id) "
            "VALUES (%s, %s, 'application', %s, %s, 'conflict', %s, "
            "'governance_conflict', NULL, %s, %s::json, %s::json, NULL) RETURNING id",
            (
                organization_id,
                candidate_id,
                subject_id,
                claim_key,
                "conflict:runtime-self-resolution-probe",
                actor_id,
                json.dumps({"conflicting_evidence_ids": [governing_record_id]}),
                json.dumps([governing_record_id]),
            ),
        )
        conflict_record_id = cursor.fetchone()[0]
        resolution_identity = f"resolution:conflict:{conflict_record_id}"
        source_digest = hashlib.sha256(
            resolution_identity.encode("utf-8")
        ).hexdigest()
        cursor.execute(
            "INSERT INTO evidence_claim_heads "
            "(organization_id, subject_type, subject_id, claim_key, source_identity) "
            "VALUES (%s, 'application', %s, %s, %s) RETURNING id",
            (organization_id, subject_id, claim_key, resolution_identity),
        )
        governing_head_id = cursor.fetchone()[0]
        observation_receipt_id = _claim_runtime_command(
            cursor,
            organization_id=organization_id,
            actor_id=actor_id,
            operation="evidence.observe",
            command_key="self-governing-root",
            request_digest="2" * 64,
            natural_key=f"evidence:{candidate_id}:{claim_key}:{source_digest}:1",
            claim_token=claim_token,
            request_id="self-governing-root-request",
        )
        cursor.execute(
            "INSERT INTO evidence_records "
            "(id, organization_id, candidate_id, subject_type, subject_id, claim_key, "
            "classification, source_identity, source_type, source_record_id, "
            "created_by_id, value_json, cited_evidence_ids, supersedes_id) "
            "VALUES (%s, %s, %s, 'application', %s, %s, 'observed', %s, "
            "'inventory', NULL, %s, %s::json, '[]'::json, NULL)",
            (
                governing_record_id,
                organization_id,
                candidate_id,
                subject_id,
                claim_key,
                resolution_identity,
                actor_id,
                json.dumps("Self-governing owner"),
            ),
        )
        cursor.execute(
            "SELECT public.archie_advance_evidence_head(%s, %s, 0, %s, %s, 1, %s)",
            (
                governing_head_id,
                governing_record_id,
                actor_id,
                observation_receipt_id,
                claim_token,
            ),
        )
        assert cursor.fetchone()[0] == 1
        target_head_id = governing_head_id
        expected_revision = 1
        supersedes_id = governing_record_id
        if duplicate_head_alias:
            cursor.execute(
                "INSERT INTO evidence_claim_heads "
                "(organization_id, subject_type, subject_id, claim_key, "
                "source_identity) VALUES (%s, 'application', %s, %s, %s) "
                "RETURNING id",
                (organization_id, subject_id, claim_key, resolution_identity),
            )
            target_head_id = cursor.fetchone()[0]
            expected_revision = 0
            supersedes_id = None
        raw.commit()
        before = persisted_state(cursor)

        resolution_receipt_id = _claim_runtime_command(
            cursor,
            organization_id=organization_id,
            actor_id=actor_id,
            operation="evidence.conflict.resolve",
            command_key="self-governing-attack",
            request_digest="3" * 64,
            natural_key=(
                f"evidence-conflict-resolution:{conflict_record_id}:"
                f"{governing_record_id}"
            ),
            claim_token=claim_token,
            request_id="self-governing-attack-request",
        )
        cursor.execute(
            "INSERT INTO evidence_records "
            "(organization_id, candidate_id, subject_type, subject_id, claim_key, "
            "classification, source_identity, source_type, source_record_id, "
            "created_by_id, value_json, cited_evidence_ids, supersedes_id) "
            "VALUES (%s, %s, 'application', %s, %s, 'derived', %s, "
            "'governance_resolution', %s, %s, %s::json, %s::json, %s) RETURNING id",
            (
                organization_id,
                candidate_id,
                subject_id,
                claim_key,
                resolution_identity,
                conflict_record_id,
                actor_id,
                json.dumps(
                    {
                        "conflict_evidence_id": conflict_record_id,
                        "governing_evidence_id": governing_record_id,
                        "rationale": "Attempt to replace the governing source",
                    }
                ),
                json.dumps([conflict_record_id, governing_record_id]),
                supersedes_id,
            ),
        )
        resolution_record_id = cursor.fetchone()[0]
        error_message = None
        try:
            cursor.execute(
                "SELECT public.archie_advance_evidence_head("
                "%s, %s, %s, %s, %s, 1, %s)",
                (
                    target_head_id,
                    resolution_record_id,
                    expected_revision,
                    actor_id,
                    resolution_receipt_id,
                    claim_token,
                ),
            )
            raw.commit()
        except psycopg2.Error as error:
            error_message = str(error)
            raw.rollback()
        after = persisted_state(cursor)
        return error_message, before, after
    finally:
        raw.close()


def test_restricted_runtime_allows_same_candidate_attestation_conflict_pair(
    guarded_runtime_database,
):
    """Catches candidate binding accidentally rejecting the valid paired move."""
    assert _runtime_attestation_conflict_pair(
        guarded_runtime_database,
        conflict_candidate_id=53002,
    ) == (1, 1, 1, 2)


def test_restricted_runtime_rejects_cross_candidate_attestation_conflict_move(
    guarded_runtime_database,
):
    """Catches candidate B's receipt advancing candidate A's conflict head."""
    with pytest.raises(
        psycopg2.Error,
        match="attestation candidate does not match conflict request candidate",
    ):
        _runtime_attestation_conflict_pair(
            guarded_runtime_database,
            conflict_candidate_id=53001,
        )


def test_restricted_runtime_allows_resolution_with_current_cited_source_leaf(
    guarded_runtime_database,
):
    """Catches the definer rejecting a valid candidate-agnostic governing leaf."""
    assert _runtime_conflict_resolution(
        guarded_runtime_database,
        supersede_governing_leaf=False,
    ) == 1


def test_restricted_runtime_rejects_resolution_with_superseded_cited_source_leaf(
    guarded_runtime_database,
):
    """Catches a restricted direct call governing by a cited but superseded leaf."""
    with pytest.raises(psycopg2.Error, match="governing evidence is not current"):
        _runtime_conflict_resolution(
            guarded_runtime_database,
            supersede_governing_leaf=True,
        )


@pytest.mark.parametrize(
    "duplicate_head_alias",
    (False, True),
    ids=("same-head", "identical-full-key-alias"),
)
def test_restricted_runtime_rejects_self_governing_resolution_atomically(
    guarded_runtime_database,
    duplicate_head_alias,
):
    """Catches a resolution replacing its authority through the same source."""
    error, before, after = _runtime_same_source_resolution_attack(
        guarded_runtime_database,
        duplicate_head_alias=duplicate_head_alias,
    )

    assert "governing evidence source must differ from resolution source" in (
        error or ""
    )
    assert after == before


def test_compose_paths_separate_database_deployment_from_runtime():
    """Catches a web/worker process retaining database-owner credentials."""
    main = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    optimized = yaml.safe_load(
        (ROOT / "docker-compose.optimized.yml").read_text(encoding="utf-8")
    )

    for document, runtime_services in ((main, ("server", "worker")), (optimized, ("web", "web-dev"))):
        services = document["services"]
        assert {"database-bootstrap", "schema-deploy", "database-acl"} <= services.keys()
        assert services["database-bootstrap"]["command"] == (
            "python -m scripts.database.configure_roles"
        )
        assert services["database-acl"]["command"] == (
            "python -m scripts.database.configure_roles"
        )
        bootstrap_env = _environment(services["database-bootstrap"])
        deploy_env = _environment(services["schema-deploy"])
        acl_env = _environment(services["database-acl"])
        assert "postgresql://postgres:" in bootstrap_env["DATABASE_ADMIN_URL"]
        assert "postgresql://postgres:" in acl_env["DATABASE_ADMIN_URL"]
        assert f"postgresql://{DEPLOY_ROLE}:" in deploy_env["DATABASE_URL"]
        assert services["schema-deploy"]["depends_on"]["database-bootstrap"] == {
            "condition": "service_completed_successfully"
        }
        assert services["database-acl"]["depends_on"]["schema-deploy"] == {
            "condition": "service_completed_successfully"
        }
        for service_name in runtime_services:
            runtime_env = _environment(services[service_name])
            assert f"postgresql://{RUNTIME_ROLE}:" in runtime_env["DATABASE_URL"]
            assert "postgresql://postgres:" not in runtime_env["DATABASE_URL"]
            assert "DATABASE_ADMIN_URL" not in runtime_env
            assert "DATABASE_DEPLOY_PASSWORD" not in runtime_env
            assert services[service_name]["depends_on"]["database-acl"] == {
                "condition": "service_completed_successfully"
            }

    optimized_backup = _environment(optimized["services"]["backup"])
    assert optimized_backup["PGPASSWORD"] == "${POSTGRES_PASSWORD}"


def test_compose_provisions_command_capability_to_schema_and_app_only():
    """Catches deploy/app capability keys diverging or reaching database bootstrap."""
    main = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    optimized = yaml.safe_load(
        (ROOT / "docker-compose.optimized.yml").read_text(encoding="utf-8")
    )

    for document, runtime_services in (
        (main, ("server", "worker")),
        (optimized, ("web", "web-dev")),
    ):
        services = document["services"]
        assert "TRANSFORMATION_COMMAND_CAPABILITY_SECRET" not in _environment(
            services["database-bootstrap"]
        )
        assert "TRANSFORMATION_COMMAND_CAPABILITY_SECRET" not in _environment(
            services["database-acl"]
        )
        assert _environment(services["schema-deploy"])[
            "TRANSFORMATION_COMMAND_CAPABILITY_SECRET"
        ] == "${TRANSFORMATION_COMMAND_CAPABILITY_SECRET}"
        for service_name in runtime_services:
            runtime = _environment(services[service_name])
            assert runtime["TRANSFORMATION_COMMAND_CAPABILITY_SECRET"] == (
                "${TRANSFORMATION_COMMAND_CAPABILITY_SECRET}"
            )
            assert "DATABASE_DEPLOY_PASSWORD" not in runtime


def test_new_tables_have_no_implicit_runtime_access_until_acl_finalization(
    isolated_role_database,
):
    """Catches default privileges silently giving every future table write access."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = isolated_role_database
    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    deploy_engine = create_engine(
        role_database.url(
            role=role_database.deploy_role,
            password=role_database.deploy_password,
        )
    )
    try:
        with deploy_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE future_runtime_records "
                "(id serial PRIMARY KEY, payload text NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE FUNCTION future_runtime_probe() RETURNS integer "
                "LANGUAGE sql AS 'SELECT 1'"
            )

        maintenance = psycopg2.connect(
            _maintenance_url(), dbname=role_database.database_name
        )
        try:
            with maintenance.cursor() as cursor:
                cursor.execute(
                    "SELECT has_table_privilege(%s, 'future_runtime_records', 'SELECT'), "
                    "has_table_privilege(%s, 'future_runtime_records', 'INSERT'), "
                    "has_table_privilege(%s, 'future_runtime_records', 'UPDATE'), "
                    "has_table_privilege(%s, 'future_runtime_records', 'DELETE'), "
                    "has_sequence_privilege(%s, 'future_runtime_records_id_seq', 'USAGE'), "
                    "has_function_privilege(%s, 'future_runtime_probe()', 'EXECUTE')",
                    (role_database.runtime_role,) * 6,
                )
                assert cursor.fetchone() == (False, False, False, False, False, False)
        finally:
            maintenance.close()

        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

        parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
        raw = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=role_database.database_name,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        try:
            with raw.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO future_runtime_records (payload) VALUES ('created') "
                    "RETURNING id"
                )
                record_id = cursor.fetchone()[0]
                cursor.execute(
                    "UPDATE future_runtime_records SET payload = 'updated' WHERE id = %s",
                    (record_id,),
                )
                cursor.execute(
                    "SELECT payload FROM future_runtime_records WHERE id = %s",
                    (record_id,),
                )
                assert cursor.fetchone() == ("updated",)
                cursor.execute(
                    "DELETE FROM future_runtime_records WHERE id = %s",
                    (record_id,),
                )
                with pytest.raises(psycopg2.Error):
                    cursor.execute("SELECT future_runtime_probe()")
            raw.rollback()
        finally:
            raw.close()
    finally:
        deploy_engine.dispose()


def test_global_table_and_sequence_default_acl_drift_is_removed(
    isolated_role_database,
):
    """Catches schema-only repair leaving additive global defaults effective."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = isolated_role_database
    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    deploy = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.deploy_role,
        password=role_database.deploy_password,
    )
    deploy.autocommit = True
    try:
        with deploy.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL(
                    "ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO PUBLIC"
                )
            )
            cursor.execute(
                pg_sql.SQL(
                    "ALTER DEFAULT PRIVILEGES GRANT INSERT ON TABLES TO {}"
                ).format(pg_sql.Identifier(role_database.runtime_role))
            )
            cursor.execute(
                pg_sql.SQL(
                    "ALTER DEFAULT PRIVILEGES GRANT SELECT ON SEQUENCES TO PUBLIC"
                )
            )
            cursor.execute(
                pg_sql.SQL(
                    "ALTER DEFAULT PRIVILEGES GRANT USAGE ON SEQUENCES TO {}"
                ).format(pg_sql.Identifier(role_database.runtime_role))
            )

        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

        with deploy.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE global_default_future_records "
                "(id serial PRIMARY KEY, payload text NOT NULL)"
            )
            cursor.execute("CREATE SEQUENCE global_default_future_standalone_seq")

        maintenance = psycopg2.connect(
            _maintenance_url(), dbname=role_database.database_name
        )
        try:
            with maintenance.cursor() as cursor:
                cursor.execute(
                    "SELECT relation.relname, "
                    "NOT EXISTS ("
                    "SELECT 1 FROM aclexplode(relation.relacl) acl "
                    "JOIN pg_roles runtime ON runtime.rolname = %s "
                    "WHERE acl.grantee IN (0, runtime.oid)) "
                    "FROM pg_class relation "
                    "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'public' "
                    "AND relation.relname = ANY(%s) "
                    "ORDER BY relation.relname",
                    (
                        role_database.runtime_role,
                        [
                            "global_default_future_records",
                            "global_default_future_records_id_seq",
                            "global_default_future_standalone_seq",
                        ],
                    ),
                )
                assert cursor.fetchall() == [
                    ("global_default_future_records", True),
                    ("global_default_future_records_id_seq", True),
                    ("global_default_future_standalone_seq", True),
                ]
        finally:
            maintenance.close()
    finally:
        deploy.close()


def test_default_acl_verifier_accounts_for_builtin_public_function_execute(
    isolated_role_database,
    monkeypatch,
):
    """Catches an absent global function row hiding PostgreSQL's PUBLIC default."""
    import scripts.database.configure_roles as roles

    role_database = isolated_role_database
    roles.configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    deploy = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.deploy_role,
        password=role_database.deploy_password,
    )
    deploy.autocommit = True
    with deploy.cursor() as cursor:
        cursor.execute(
            "ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC"
        )
    deploy.close()

    real_connect = psycopg2.connect
    skipped = False

    def should_skip(statement):
        nonlocal skipped
        normalized = " ".join(statement.replace('"', "").upper().split())
        is_global_public_function_revoke = (
            normalized.startswith("ALTER DEFAULT PRIVILEGES FOR ROLE ")
            and " IN SCHEMA " not in normalized
            and "ON FUNCTIONS FROM PUBLIC" in normalized
        )
        if is_global_public_function_revoke:
            skipped = True
            return True
        return False

    def intercepted_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        if kwargs.get("dbname") == role_database.database_name:
            return _SkippingConnection(connection, should_skip)
        return connection

    monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
    with pytest.raises(RuntimeError, match="default privileges"):
        roles.configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )
    assert skipped


def test_public_column_acl_drift_converges_independently_of_table_acl_repair(
    guarded_runtime_database,
    monkeypatch,
):
    """Catches PUBLIC column grants surviving when only table ACLs are revoked."""
    import scripts.database.configure_roles as roles

    role_database = guarded_runtime_database
    real_connect = psycopg2.connect
    maintenance = real_connect(
        _maintenance_url(), dbname=role_database.database_name
    )
    maintenance.autocommit = True
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                "GRANT SELECT (secret) ON archie_command_capability_keys TO PUBLIC"
            )
            cursor.execute(
                pg_sql.SQL(
                    "GRANT SELECT (secret) ON archie_command_capability_keys TO {}"
                ).format(pg_sql.Identifier(role_database.runtime_role))
            )

        skipped = False

        def should_skip(statement):
            nonlocal skipped
            normalized = " ".join(statement.replace('"', "").upper().split())
            if (
                normalized
                == "REVOKE ALL PRIVILEGES ON TABLE PUBLIC.ARCHIE_COMMAND_CAPABILITY_KEYS FROM PUBLIC"
            ):
                skipped = True
                return True
            return False

        def intercepted_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            if kwargs.get("dbname") == role_database.database_name:
                return _SkippingConnection(connection, should_skip)
            return connection

        monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
        roles.configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )
        assert skipped

        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT has_column_privilege(%s, "
                "'archie_command_capability_keys', 'secret', 'SELECT'), "
                "NOT EXISTS ("
                "SELECT 1 FROM pg_attribute attribute "
                "CROSS JOIN LATERAL aclexplode(attribute.attacl) acl "
                "WHERE attribute.attrelid = "
                "'archie_command_capability_keys'::regclass "
                "AND attribute.attname = 'secret' AND acl.grantee = 0)",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (False, True)
    finally:
        maintenance.close()


def test_role_bootstrap_after_guards_preserves_exact_runtime_acl(
    guarded_runtime_database,
):
    """Catches an idempotent bootstrap reopening protected transformation tables."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = guarded_runtime_database
    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )

    expected_table_acl = {
        "archie_command_capability_keys": (False, False, False, False, False),
        "archie_command_claim_challenges": (False, False, False, False, False),
        "command_idempotency_records": (True, False, False, False, False),
        "command_materialisations": (True, False, False, False, False),
        "operation_results": (True, False, False, False, False),
        "transformation_outbox_events": (True, False, False, False, False),
        "candidate_overlap_dispositions": (True, False, False, False, False),
        "evidence_records": (True, True, False, False, False),
        "evidence_claim_heads": (True, True, False, False, False),
        "evidence_head_events": (True, False, False, False, False),
        "decision_briefs": (True, False, False, False, False),
        "decision_brief_versions": (True, False, False, False, False),
        "decision_brief_option_citations": (True, False, False, False, False),
        "decision_brief_evidence_citations": (True, False, False, False, False),
    }
    maintenance = psycopg2.connect(
        _maintenance_url(), dbname=role_database.database_name
    )
    try:
        with maintenance.cursor() as cursor:
            for table_name, expected in expected_table_acl.items():
                cursor.execute(
                    "SELECT has_table_privilege(%s, %s, 'SELECT'), "
                    "has_table_privilege(%s, %s, 'INSERT'), "
                    "has_table_privilege(%s, %s, 'UPDATE'), "
                    "has_table_privilege(%s, %s, 'DELETE'), "
                    "has_table_privilege(%s, %s, 'TRUNCATE')",
                    (
                        role_database.runtime_role,
                        table_name,
                        role_database.runtime_role,
                        table_name,
                        role_database.runtime_role,
                        table_name,
                        role_database.runtime_role,
                        table_name,
                        role_database.runtime_role,
                        table_name,
                    ),
                )
                assert cursor.fetchone() == expected, table_name

            cursor.execute(
                "SELECT has_column_privilege(%s, 'command_idempotency_records', "
                "'status', 'UPDATE'), "
                "has_column_privilege(%s, 'command_idempotency_records', "
                "'idempotency_key', 'UPDATE'), "
                "has_column_privilege(%s, 'transformation_outbox_events', "
                "'delivery_attempts', 'UPDATE'), "
                "has_column_privilege(%s, 'transformation_outbox_events', "
                "'payload_json', 'UPDATE'), "
                "has_schema_privilege(%s, 'public', 'CREATE')",
                (role_database.runtime_role,) * 5,
            )
            assert cursor.fetchone() == (True, False, True, False, False)

            cursor.execute(
                "SELECT has_function_privilege(%s, "
                "'archie_issue_command_claim_challenge(bigint,bigint,integer)', "
                "'EXECUTE'), "
                "has_function_privilege(%s, "
                "'archie_claim_transformation_command(text,text)', 'EXECUTE'), "
                "has_function_privilege(%s, "
                "'archie_create_decision_brief(text,text,text)', 'EXECUTE'), "
                "has_function_privilege(%s, "
                "'archie_freeze_decision_brief_version(bigint,bigint,bigint,integer,text,text,text,integer,text,jsonb,text)', "
                "'EXECUTE'), "
                "has_function_privilege(%s, "
                "'archie_advance_evidence_head(bigint,bigint,integer,bigint,bigint,integer,text)', "
                "'EXECUTE'), "
                "has_function_privilege(%s, "
                "'archie_verify_command_capability(text,text,text)', 'EXECUTE'), "
                "has_function_privilege(%s, "
                "'archie_hmac_sha256(bytea,bytea)', 'EXECUTE'), "
                "has_function_privilege(%s, "
                "'archie_persist_command_envelope(text,text,text)', 'EXECUTE'), "
                "has_function_privilege(%s, "
                "'archie_repair_command_envelope(text,text,bigint)', 'EXECUTE'), "
                "has_function_privilege(%s, "
                "'archie_persist_overlap_disposition(text,text,text)', 'EXECUTE')",
                (role_database.runtime_role,) * 10,
            )
            assert cursor.fetchone() == (
                True, True, True, True, True, False, False, True, True, True
            )
    finally:
        maintenance.close()


def test_failure_within_first_runtime_fence_is_recovered_durably(
    isolated_role_database,
    monkeypatch,
):
    """Catches a failed fence transaction rolling runtime back to LOGIN."""
    import scripts.database.configure_roles as roles

    role_database = isolated_role_database
    roles.configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    real_connect = psycopg2.connect
    raised = False

    def fail_first_fence(statement):
        nonlocal raised
        normalized = " ".join(statement.upper().split())
        if (
            not raised
            and normalized.startswith("ALTER ROLE")
            and " WITH NOLOGIN PASSWORD " in normalized
            and role_database.runtime_role.upper() in normalized
        ):
            raised = True
            raise RuntimeError("injected first-fence failure")

    def intercepted_connect(*args, **kwargs):
        return _InterceptingConnection(
            real_connect(*args, **kwargs),
            fail_first_fence,
        )

    monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
    with pytest.raises(RuntimeError, match="injected first-fence failure"):
        roles.configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )
    assert raised

    maintenance = real_connect(_maintenance_url())
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (False,)
    finally:
        maintenance.close()


def test_pre_repair_failure_observes_already_committed_runtime_fence(
    isolated_role_database,
    monkeypatch,
):
    """Catches membership work sharing and rolling back the NOLOGIN transaction."""
    import scripts.database.configure_roles as roles

    role_database = isolated_role_database
    roles.configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    real_connect = psycopg2.connect
    raised = False

    def fail_membership_enumeration(statement):
        nonlocal raised
        normalized = " ".join(statement.upper().split())
        if not raised and "FROM PG_AUTH_MEMBERS" in normalized:
            raised = True
            raise RuntimeError("injected pre-repair membership failure")

    def intercepted_connect(*args, **kwargs):
        return _InterceptingConnection(
            real_connect(*args, **kwargs),
            fail_membership_enumeration,
        )

    monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
    with pytest.raises(RuntimeError, match="injected pre-repair membership failure"):
        roles.configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )
    assert raised

    maintenance = real_connect(_maintenance_url())
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (False,)
    finally:
        maintenance.close()


def test_missing_second_target_is_rejected_after_durable_fence(
    isolated_role_database,
):
    """Catches target validation failing before runtime is durably fenced."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = isolated_role_database
    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    missing_database = f"{role_database.database_name}_missing"

    with pytest.raises(ValueError, match=missing_database):
        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name, missing_database),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

    maintenance = psycopg2.connect(_maintenance_url())
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (False,)
    finally:
        maintenance.close()


def test_bootstrap_revokes_inbound_runtime_membership_and_terminates_all_sessions(
    guarded_runtime_database,
):
    """Catches inherited or SET ROLE access surviving through an inbound login role."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = guarded_runtime_database
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    inbound_group = f"{role_database.runtime_role}_group"
    inbound_role = f"{role_database.runtime_role}_member"
    inbound_password = f"inbound-{uuid.uuid4().hex}"
    direct = None
    inbound = None
    inbound_after = None
    runtime_after = None
    maintenance = psycopg2.connect(_maintenance_url())
    maintenance.autocommit = True
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL(
                    "CREATE ROLE {} NOLOGIN INHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(pg_sql.Identifier(inbound_group))
            )
            cursor.execute(
                pg_sql.SQL(
                    "CREATE ROLE {} LOGIN INHERIT PASSWORD %s "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(pg_sql.Identifier(inbound_role)),
                (inbound_password,),
            )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {}").format(
                    pg_sql.Identifier(role_database.runtime_role),
                    pg_sql.Identifier(inbound_group),
                )
            )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {}").format(
                    pg_sql.Identifier(inbound_group),
                    pg_sql.Identifier(inbound_role),
                )
            )
            cursor.execute(
                pg_sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    pg_sql.Identifier(role_database.database_name),
                    pg_sql.Identifier(inbound_role),
                )
            )

        connection_args = {
            "host": parsed.hostname,
            "port": parsed.port,
            "dbname": role_database.database_name,
        }
        direct = psycopg2.connect(
            **connection_args,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        inbound = psycopg2.connect(
            **connection_args,
            user=inbound_role,
            password=inbound_password,
        )
        with inbound.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("SET ROLE {}").format(
                    pg_sql.Identifier(role_database.runtime_role)
                )
            )
        for connection in (direct, inbound):
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM operation_results")
                assert cursor.fetchone() is not None
            connection.rollback()

        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_auth_members membership "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "WHERE granted.rolname = %s AND member.rolname = %s",
                (role_database.runtime_role, inbound_group),
            )
            assert cursor.fetchone() == (0,)
            cursor.execute(
                "SELECT count(*) FROM pg_auth_members membership "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "WHERE granted.rolname = %s AND member.rolname = %s",
                (inbound_group, inbound_role),
            )
            assert cursor.fetchone() == (1,)
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = %s AND usename = ANY(%s)",
                (
                    role_database.database_name,
                    [role_database.runtime_role, inbound_role],
                ),
            )
            assert cursor.fetchone() == (0,)
            cursor.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (True,)

        for connection in (direct, inbound):
            with pytest.raises(psycopg2.Error):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")

        inbound_after = psycopg2.connect(
            **connection_args,
            user=inbound_role,
            password=inbound_password,
        )
        with pytest.raises(psycopg2.Error):
            with inbound_after.cursor() as cursor:
                cursor.execute(
                    pg_sql.SQL("SET ROLE {}").format(
                        pg_sql.Identifier(role_database.runtime_role)
                    )
                )
        inbound_after.rollback()
        with pytest.raises(psycopg2.Error):
            with inbound_after.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM operation_results")
        inbound_after.rollback()

        runtime_after = psycopg2.connect(
            **connection_args,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        with runtime_after.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM operation_results")
            assert cursor.fetchone() is not None
        runtime_after.rollback()
        with pytest.raises(psycopg2.Error):
            with runtime_after.cursor() as cursor:
                cursor.execute("SELECT secret FROM archie_command_capability_keys")
        runtime_after.rollback()
    finally:
        for connection in (direct, inbound, inbound_after, runtime_after):
            if connection is not None:
                connection.close()
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE usename = %s AND pid <> pg_backend_pid()",
                (inbound_role,),
            )
            cursor.execute(
                pg_sql.SQL("REVOKE {} FROM {}").format(
                    pg_sql.Identifier(inbound_group),
                    pg_sql.Identifier(inbound_role),
                )
            )
            cursor.execute(
                pg_sql.SQL("REVOKE {} FROM {}").format(
                    pg_sql.Identifier(role_database.runtime_role),
                    pg_sql.Identifier(inbound_group),
                )
            )
            cursor.execute(
                pg_sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(
                    pg_sql.Identifier(role_database.database_name),
                    pg_sql.Identifier(inbound_role),
                )
            )
            cursor.execute(
                pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                    pg_sql.Identifier(inbound_role)
                )
            )
            cursor.execute(
                pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                    pg_sql.Identifier(inbound_group)
                )
            )
        maintenance.close()


def test_bootstrap_terminates_runtime_session_in_non_target_database(
    guarded_runtime_database,
):
    """Catches database-local polling of cluster-global runtime sessions."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = guarded_runtime_database
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    non_target_database = f"{role_database.database_name}_outside"
    attacker = None
    maintenance = psycopg2.connect(_maintenance_url())
    maintenance.autocommit = True
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("CREATE DATABASE {}").format(
                    pg_sql.Identifier(non_target_database)
                )
            )
            cursor.execute(
                pg_sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    pg_sql.Identifier(non_target_database),
                    pg_sql.Identifier(role_database.runtime_role),
                )
            )

        attacker = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=non_target_database,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        with attacker.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            attacker_pid = cursor.fetchone()[0]
        attacker.rollback()

        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

        with pytest.raises(psycopg2.Error):
            with attacker.cursor() as cursor:
                cursor.execute("SELECT 1")
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE pid = %s",
                (attacker_pid,),
            )
            assert cursor.fetchone() == (0,)
    finally:
        if attacker is not None:
            attacker.close()
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (non_target_database,),
            )
            cursor.execute(
                pg_sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    pg_sql.Identifier(non_target_database)
                )
            )
        maintenance.close()


def test_outbound_membership_cleanup_preserves_dependent_grants(
    isolated_role_database,
):
    """Catches CASCADE deleting grants made through runtime's ADMIN option."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = isolated_role_database
    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    parent_role = f"{role_database.runtime_role}_outbound"
    dependent_role = f"{role_database.runtime_role}_dependent"
    maintenance = psycopg2.connect(_maintenance_url())
    maintenance.autocommit = True
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    runtime = None
    try:
        with maintenance.cursor() as cursor:
            server_version_num = maintenance.server_version
            cursor.execute(
                pg_sql.SQL("CREATE ROLE {} NOLOGIN").format(
                    pg_sql.Identifier(parent_role)
                )
            )
            cursor.execute(
                pg_sql.SQL("CREATE ROLE {} NOLOGIN").format(
                    pg_sql.Identifier(dependent_role)
                )
            )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                    pg_sql.Identifier(parent_role),
                    pg_sql.Identifier(role_database.runtime_role),
                )
            )

        runtime = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=role_database.database_name,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        runtime.autocommit = True
        with runtime.cursor() as cursor:
            if server_version_num >= 160_000:
                cursor.execute(
                    pg_sql.SQL(
                        "GRANT {} TO {} WITH ADMIN TRUE, INHERIT FALSE, SET TRUE"
                    ).format(
                        pg_sql.Identifier(parent_role),
                        pg_sql.Identifier(dependent_role),
                    )
                )
            else:
                cursor.execute(
                    pg_sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                        pg_sql.Identifier(parent_role),
                        pg_sql.Identifier(dependent_role),
                    )
                )
        runtime.close()
        runtime = None

        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

        with maintenance.cursor() as cursor:
            if server_version_num >= 160_000:
                cursor.execute(
                    "SELECT grantor.rolname, membership.admin_option, "
                    "membership.inherit_option, membership.set_option "
                    "FROM pg_auth_members membership "
                    "JOIN pg_roles granted ON granted.oid = membership.roleid "
                    "JOIN pg_roles member ON member.oid = membership.member "
                    "JOIN pg_roles grantor ON grantor.oid = membership.grantor "
                    "WHERE granted.rolname = %s AND member.rolname = %s",
                    (parent_role, dependent_role),
                )
                expected_grants = [("postgres", True, False, True)]
            else:
                cursor.execute(
                    "SELECT grantor.rolname, membership.admin_option "
                    "FROM pg_auth_members membership "
                    "JOIN pg_roles granted ON granted.oid = membership.roleid "
                    "JOIN pg_roles member ON member.oid = membership.member "
                    "JOIN pg_roles grantor ON grantor.oid = membership.grantor "
                    "WHERE granted.rolname = %s AND member.rolname = %s",
                    (parent_role, dependent_role),
                )
                expected_grants = [(role_database.runtime_role, True)]
            dependent_grants = cursor.fetchall()
            assert dependent_grants == expected_grants
            cursor.execute(
                "SELECT count(*) FROM pg_auth_members membership "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "WHERE granted.rolname = %s AND member.rolname = %s",
                (parent_role, role_database.runtime_role),
            )
            assert cursor.fetchone() == (0,)
    finally:
        if runtime is not None:
            runtime.close()
        with maintenance.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                    pg_sql.Identifier(dependent_role)
                )
            )
            cursor.execute(
                pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                    pg_sql.Identifier(parent_role)
                )
            )
        maintenance.close()


def test_membership_cleanup_unions_removed_and_current_grantor_options(
    isolated_role_database,
):
    """Catches re-homing that weakens true options on a surviving grant."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = isolated_role_database
    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    parent_role = f"{role_database.runtime_role}_option_parent"
    dependent_role = f"{role_database.runtime_role}_option_dependent"
    unrelated_parent = f"{role_database.runtime_role}_other_parent"
    unrelated_member = f"{role_database.runtime_role}_other_member"
    maintenance = psycopg2.connect(_maintenance_url())
    maintenance.autocommit = True
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    runtime = None
    try:
        if maintenance.server_version < 160_000:
            assert maintenance.server_version >= 120_000
            return
        with maintenance.cursor() as cursor:
            cursor.execute("SELECT current_user")
            bootstrap_role = cursor.fetchone()[0]
            for role_name in (
                parent_role,
                dependent_role,
                unrelated_parent,
                unrelated_member,
            ):
                cursor.execute(
                    pg_sql.SQL("CREATE ROLE {} NOLOGIN").format(
                        pg_sql.Identifier(role_name)
                    )
                )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                    pg_sql.Identifier(parent_role),
                    pg_sql.Identifier(role_database.runtime_role),
                )
            )
            cursor.execute(
                pg_sql.SQL(
                    "GRANT {} TO {} WITH ADMIN FALSE, INHERIT TRUE, SET TRUE "
                    "GRANTED BY CURRENT_USER"
                ).format(
                    pg_sql.Identifier(parent_role),
                    pg_sql.Identifier(dependent_role),
                )
            )
            cursor.execute(
                pg_sql.SQL(
                    "GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE "
                    "GRANTED BY CURRENT_USER"
                ).format(
                    pg_sql.Identifier(unrelated_parent),
                    pg_sql.Identifier(unrelated_member),
                )
            )

        runtime = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=role_database.database_name,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        runtime.autocommit = True
        with runtime.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL(
                    "GRANT {} TO {} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE"
                ).format(
                    pg_sql.Identifier(parent_role),
                    pg_sql.Identifier(dependent_role),
                )
            )
        runtime.close()
        runtime = None

        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT grantor.rolname, membership.admin_option, "
                "membership.inherit_option, membership.set_option "
                "FROM pg_auth_members membership "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "JOIN pg_roles grantor ON grantor.oid = membership.grantor "
                "WHERE granted.rolname = %s AND member.rolname = %s "
                "ORDER BY grantor.rolname",
                (parent_role, dependent_role),
            )
            assert cursor.fetchall() == [(bootstrap_role, True, True, True)]
            cursor.execute(
                "SELECT grantor.rolname, membership.admin_option, "
                "membership.inherit_option, membership.set_option "
                "FROM pg_auth_members membership "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "JOIN pg_roles grantor ON grantor.oid = membership.grantor "
                "WHERE granted.rolname = %s AND member.rolname = %s "
                "ORDER BY grantor.rolname",
                (unrelated_parent, unrelated_member),
            )
            assert cursor.fetchall() == [(bootstrap_role, False, False, True)]
    finally:
        if runtime is not None:
            runtime.close()
        with maintenance.cursor() as cursor:
            for role_name in (
                dependent_role,
                unrelated_member,
                parent_role,
                unrelated_parent,
            ):
                cursor.execute(
                    pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                        pg_sql.Identifier(role_name)
                    )
                )
        maintenance.close()


def test_membership_cleanup_serializes_unrelated_external_revoke(
    isolated_role_database,
):
    """Catches a concurrent unrelated REVOKE being mistaken for CASCADE."""
    import scripts.database.configure_roles as roles

    role_database = isolated_role_database
    roles.configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    parent_role = f"{role_database.runtime_role}_race_parent"
    dependent_role = f"{role_database.runtime_role}_race_dependent"
    unrelated_parent = f"{role_database.runtime_role}_race_other_parent"
    unrelated_member = f"{role_database.runtime_role}_race_other_member"
    maintenance = psycopg2.connect(_maintenance_url())
    maintenance.autocommit = True
    runtime = None
    bootstrap = None
    observer = None
    revoker_thread = None
    snapshot_seen = threading.Event()
    revoke_started = threading.Event()
    revoke_finished = threading.Event()
    revoke_waited_on_lock = threading.Event()
    revoker_errors = []
    application_name = f"archie-membership-revoker-{uuid.uuid4().hex}"
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])

    def revoke_unrelated_membership():
        connection = None
        try:
            assert snapshot_seen.wait(timeout=5)
            connection = psycopg2.connect(
                _maintenance_url(),
                application_name=application_name,
            )
            with connection.cursor() as cursor:
                revoke_started.set()
                cursor.execute(
                    pg_sql.SQL("REVOKE {} FROM {}").format(
                        pg_sql.Identifier(unrelated_parent),
                        pg_sql.Identifier(unrelated_member),
                    )
                )
            connection.commit()
        except BaseException as exc:
            revoker_errors.append(exc)
        finally:
            if connection is not None:
                connection.close()
            revoke_finished.set()

    before_snapshot_observed = False

    def observe_membership_sql(statement):
        nonlocal before_snapshot_observed
        normalized = " ".join(statement.upper().split())
        if before_snapshot_observed or not (
            normalized.startswith("SELECT GRANTED.ROLNAME")
            and "MEMBERSHIP.INHERIT_OPTION" in normalized
        ):
            return
        before_snapshot_observed = True
        snapshot_seen.set()
        if not revoke_started.wait(timeout=5):
            return
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with observer.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type FROM pg_stat_activity "
                    "WHERE application_name = %s",
                    (application_name,),
                )
                row = cursor.fetchone()
            observer.rollback()
            if row == ("Lock",):
                revoke_waited_on_lock.set()
                return
            if revoke_finished.is_set():
                return
            time.sleep(0.01)

    try:
        if maintenance.server_version < 160_000:
            assert maintenance.server_version >= 120_000
            return
        with maintenance.cursor() as cursor:
            for role_name in (
                parent_role,
                dependent_role,
                unrelated_parent,
                unrelated_member,
            ):
                cursor.execute(
                    pg_sql.SQL("CREATE ROLE {} NOLOGIN").format(
                        pg_sql.Identifier(role_name)
                    )
                )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                    pg_sql.Identifier(parent_role),
                    pg_sql.Identifier(role_database.runtime_role),
                )
            )
            cursor.execute(
                pg_sql.SQL(
                    "GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
                ).format(
                    pg_sql.Identifier(unrelated_parent),
                    pg_sql.Identifier(unrelated_member),
                )
            )

        runtime = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=role_database.database_name,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        runtime.autocommit = True
        with runtime.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL(
                    "GRANT {} TO {} WITH ADMIN TRUE, INHERIT FALSE, SET TRUE"
                ).format(
                    pg_sql.Identifier(parent_role),
                    pg_sql.Identifier(dependent_role),
                )
            )
        runtime.close()
        runtime = None

        observer = psycopg2.connect(_maintenance_url())
        revoker_thread = threading.Thread(
            target=revoke_unrelated_membership,
            daemon=True,
        )
        revoker_thread.start()
        bootstrap = psycopg2.connect(_maintenance_url())
        with bootstrap.cursor() as cursor:
            roles._strip_runtime_memberships(
                _InterceptingCursor(cursor, observe_membership_sql),
                runtime_role=role_database.runtime_role,
            )
        bootstrap.commit()
        revoker_thread.join(timeout=5)

        assert before_snapshot_observed
        assert revoke_waited_on_lock.is_set()
        assert revoke_finished.is_set()
        assert revoker_errors == []
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_auth_members membership "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "WHERE granted.rolname = %s AND member.rolname = %s",
                (unrelated_parent, unrelated_member),
            )
            assert cursor.fetchone() == (0,)
            cursor.execute(
                "SELECT bool_or(membership.admin_option), "
                "bool_or(membership.inherit_option), "
                "bool_or(membership.set_option) "
                "FROM pg_auth_members membership "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "WHERE granted.rolname = %s AND member.rolname = %s",
                (parent_role, dependent_role),
            )
            assert cursor.fetchone() == (True, False, True)
    finally:
        snapshot_seen.set()
        if bootstrap is not None:
            bootstrap.rollback()
            bootstrap.close()
        if revoker_thread is not None:
            revoker_thread.join(timeout=5)
        if observer is not None:
            observer.close()
        if runtime is not None:
            runtime.close()
        with maintenance.cursor() as cursor:
            for role_name in (
                dependent_role,
                unrelated_member,
                parent_role,
                unrelated_parent,
            ):
                cursor.execute(
                    pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                        pg_sql.Identifier(role_name)
                    )
                )
        maintenance.close()


def test_membership_cleanup_preserves_unrelated_inbound_role_grant_provenance(
    isolated_role_database,
):
    """Catches bootstrap duplicates of independent grants by an inbound role."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = isolated_role_database
    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    inbound_group = f"{role_database.runtime_role}_inbound_group"
    inbound_role = f"{role_database.runtime_role}_inbound_role"
    unrelated_parent = f"{role_database.runtime_role}_unrelated_parent"
    unrelated_member = f"{role_database.runtime_role}_unrelated_member"
    maintenance = psycopg2.connect(_maintenance_url())
    maintenance.autocommit = True
    try:
        with maintenance.cursor() as cursor:
            server_version_num = maintenance.server_version
            for role_name in (
                inbound_group,
                inbound_role,
                unrelated_parent,
                unrelated_member,
            ):
                cursor.execute(
                    pg_sql.SQL("CREATE ROLE {} NOLOGIN").format(
                        pg_sql.Identifier(role_name)
                    )
                )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {}").format(
                    pg_sql.Identifier(role_database.runtime_role),
                    pg_sql.Identifier(inbound_group),
                )
            )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {}").format(
                    pg_sql.Identifier(inbound_group),
                    pg_sql.Identifier(inbound_role),
                )
            )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                    pg_sql.Identifier(unrelated_parent),
                    pg_sql.Identifier(inbound_role),
                )
            )
            cursor.execute(
                pg_sql.SQL("SET ROLE {}").format(pg_sql.Identifier(inbound_role))
            )
            try:
                if server_version_num >= 160_000:
                    cursor.execute(
                        pg_sql.SQL(
                            "GRANT {} TO {} WITH ADMIN FALSE, "
                            "INHERIT FALSE, SET TRUE"
                        ).format(
                            pg_sql.Identifier(unrelated_parent),
                            pg_sql.Identifier(unrelated_member),
                        )
                    )
                else:
                    cursor.execute(
                        pg_sql.SQL("GRANT {} TO {}").format(
                            pg_sql.Identifier(unrelated_parent),
                            pg_sql.Identifier(unrelated_member),
                        )
                    )
            finally:
                cursor.execute("RESET ROLE")

        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

        with maintenance.cursor() as cursor:
            if server_version_num >= 160_000:
                cursor.execute(
                    "SELECT grantor.rolname, membership.admin_option, "
                    "membership.inherit_option, membership.set_option "
                    "FROM pg_auth_members membership "
                    "JOIN pg_roles granted ON granted.oid = membership.roleid "
                    "JOIN pg_roles member ON member.oid = membership.member "
                    "JOIN pg_roles grantor ON grantor.oid = membership.grantor "
                    "WHERE granted.rolname = %s AND member.rolname = %s "
                    "ORDER BY grantor.rolname",
                    (unrelated_parent, unrelated_member),
                )
                expected_grants = [(inbound_role, False, False, True)]
            else:
                cursor.execute(
                    "SELECT grantor.rolname, membership.admin_option "
                    "FROM pg_auth_members membership "
                    "JOIN pg_roles granted ON granted.oid = membership.roleid "
                    "JOIN pg_roles member ON member.oid = membership.member "
                    "JOIN pg_roles grantor ON grantor.oid = membership.grantor "
                    "WHERE granted.rolname = %s AND member.rolname = %s "
                    "ORDER BY grantor.rolname",
                    (unrelated_parent, unrelated_member),
                )
                expected_grants = [(inbound_role, False)]
            assert cursor.fetchall() == expected_grants
    finally:
        with maintenance.cursor() as cursor:
            for role_name in (
                unrelated_member,
                inbound_role,
                inbound_group,
                unrelated_parent,
            ):
                cursor.execute(
                    pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                        pg_sql.Identifier(role_name)
                    )
                )
        maintenance.close()


@pytest.mark.parametrize("reported_result", ["false", "missing", "wrong_pid"])
def test_a_surviving_session_is_not_accepted_by_a_false_or_mismatched_termination_result(
    guarded_runtime_database,
    monkeypatch,
    reported_result,
):
    """A false, missing or wrong-pid direct termination result must not be
    accepted as proof: only the fresh-connection poll's independent absence
    check may clear a runtime-capable session, and it must not clear one
    that is still genuinely connected.

    Before 18 Sep 2026 this function treated a False/missing/wrong-pid
    result from `pg_terminate_backend` itself as immediately fatal. That
    was removed after it aborted a real production deploy on a benign race
    (a pooled connection that had already disconnected on its own between
    the initial SELECT and the terminate call) -- the session was
    genuinely gone, just not via a `True` return from that one call, and
    the poll loop's independent fresh-connection proof already covered it
    correctly. `configure_roles.py`'s own comment on
    `_terminate_runtime_capable_sessions` documents this.

    This test's predecessor (same name, before this fix) exercised only
    that already-fixed case: it closed the attacker connection itself
    inside `override_result()`, so the poll loop's fresh query always
    found the session genuinely absent and the old `pytest.raises` was
    therefore asserting on a return-value contract the implementation
    deliberately no longer honours -- it was failing CI over a scenario
    the source comment explains is not a defect. This version instead
    leaves the attacker session connected throughout, so the poll loop's
    fresh query keeps finding it present, the deadline genuinely expires,
    and the resulting RuntimeError is the real absence-proof failure this
    function is supposed to raise when a session cannot be shown gone --
    not a return-value formality.
    """
    import scripts.database.configure_roles as roles

    role_database = guarded_runtime_database
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    attacker = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.runtime_role,
        password=role_database.runtime_password,
    )
    with attacker.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        attacker_pid = cursor.fetchone()[0]
    attacker.rollback()
    real_connect = psycopg2.connect
    override_used = False

    def override_result():
        # Deliberately does NOT close `attacker` -- the whole point of this
        # version of the test is that the session stays genuinely present,
        # so a false/missing/wrong-pid direct result cannot be allowed to
        # bypass the independent fresh-connection absence proof.
        nonlocal override_used
        if override_used:
            return None
        override_used = True
        if reported_result == "false":
            return ((attacker_pid, False),)
        if reported_result == "wrong_pid":
            return ((attacker_pid + 100_000, True),)
        return ()

    def intercepted_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        if kwargs.get("dbname") is None:
            return _TerminationResultConnection(connection, override_result)
        return connection

    monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
    try:
        with pytest.raises(RuntimeError, match="could not be proven"):
            roles.configure_database_roles(
                admin_url=_maintenance_url(),
                database_names=(role_database.database_name,),
                deploy_password=role_database.deploy_password,
                runtime_password=role_database.runtime_password,
                deploy_role=role_database.deploy_role,
                runtime_role=role_database.runtime_role,
            )
        assert override_used
    finally:
        # The test owns the attacker connection's lifecycle now that
        # override_result() no longer closes it -- must not leak a
        # runtime-capable session into later tests sharing this database.
        attacker.close()

    maintenance = real_connect(_maintenance_url())
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (False,)
    finally:
        maintenance.close()


def test_session_termination_uses_timeout_and_fresh_proof_connection(
    guarded_runtime_database,
    monkeypatch,
):
    """Catches polling in the coordinator's old transaction snapshot."""
    import scripts.database.configure_roles as roles

    role_database = guarded_runtime_database
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])

    # pg_terminate_backend(pid, timeout) arrived in PostgreSQL 14, and
    # configure_roles deliberately probes for it and falls back to the
    # one-argument form on older servers -- a fallback its sibling
    # test_session_termination_uses_legacy_one_argument_fallback pins.
    # Asserting the overload unconditionally therefore fails on a server that
    # is behaving exactly as the product intends, so probe the same way the
    # product does rather than reporting supported behaviour as a defect.
    with psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=parsed.username,
        password=parsed.password,
    ) as probe:
        with probe.cursor() as probe_cursor:
            probe_cursor.execute(
                "SELECT to_regprocedure("
                "'pg_catalog.pg_terminate_backend(integer,bigint)') IS NOT NULL"
            )
            has_timeout_overload = probe_cursor.fetchone()[0]
    if not has_timeout_overload:
        # PostgreSQL versions without the two-argument overload take the
        # separately-tested one-argument product path.  This is an explicit
        # applicability assertion, not an unexecuted release test.
        assert has_timeout_overload is False
        return

    attacker = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.runtime_role,
        password=role_database.runtime_password,
    )
    attacker.rollback()
    real_connect = psycopg2.connect
    session_query_connection_ids = set()
    used_timeout_overload = False
    next_connection_id = 0

    def intercepted_connect(*args, **kwargs):
        nonlocal next_connection_id
        connection = real_connect(*args, **kwargs)
        connection_id = next_connection_id
        next_connection_id += 1

        def observe(statement):
            nonlocal used_timeout_overload
            normalized = " ".join(statement.upper().split())
            if "FROM PG_STAT_ACTIVITY ACTIVITY" in normalized:
                session_query_connection_ids.add(connection_id)
            if "PG_TERMINATE_BACKEND(ACTIVITY.PID, %S)" in normalized:
                used_timeout_overload = True

        return _InterceptingConnection(connection, observe)

    monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
    roles.configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    attacker.close()

    assert used_timeout_overload
    assert len(session_query_connection_ids) >= 2


def test_session_termination_uses_legacy_one_argument_fallback(
    guarded_runtime_database,
    monkeypatch,
):
    """Pins compatibility with PostgreSQL lacking termination timeouts."""
    import scripts.database.configure_roles as roles

    role_database = guarded_runtime_database
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    attacker = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.runtime_role,
        password=role_database.runtime_password,
    )
    attacker.rollback()
    real_connect = psycopg2.connect
    used_legacy_overload = False

    def observe(statement):
        nonlocal used_legacy_overload
        if "PG_TERMINATE_BACKEND(ACTIVITY.PID)" in statement:
            used_legacy_overload = True

    def intercepted_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        if kwargs.get("dbname") is None:
            return _LegacyTerminationConnection(connection, observe)
        return connection

    monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
    roles.configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    attacker.close()

    assert used_legacy_overload


def test_unproved_session_termination_aborts_with_runtime_nologin(
    guarded_runtime_database,
    monkeypatch,
):
    """Catches ignored false results from pg_terminate_backend."""
    import scripts.database.configure_roles as roles

    role_database = guarded_runtime_database
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    attacker = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        dbname=role_database.database_name,
        user=role_database.runtime_role,
        password=role_database.runtime_password,
    )
    with attacker.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        attacker_pid = cursor.fetchone()[0]
    attacker.rollback()
    real_connect = psycopg2.connect

    def intercepted_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        if kwargs.get("dbname") is None:
            return _FailedTerminationConnection(connection, attacker_pid)
        return connection

    monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
    try:
        with pytest.raises(RuntimeError, match="terminat"):
            roles.configure_database_roles(
                admin_url=_maintenance_url(),
                database_names=(role_database.database_name,),
                deploy_password=role_database.deploy_password,
                runtime_password=role_database.runtime_password,
                deploy_role=role_database.deploy_role,
                runtime_role=role_database.runtime_role,
            )

        maintenance = real_connect(_maintenance_url())
        try:
            with maintenance.cursor() as cursor:
                cursor.execute(
                    "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                    (role_database.runtime_role,),
                )
                assert cursor.fetchone() == (False,)
        finally:
            maintenance.close()
    finally:
        attacker.close()


def test_post_login_failure_recovery_refences_memberships_and_sessions(
    guarded_runtime_database,
    monkeypatch,
):
    """Catches a post-LOGIN connection retaining runtime access after recovery."""
    import scripts.database.configure_roles as roles

    role_database = guarded_runtime_database
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    inbound_role = f"{role_database.runtime_role}_recovery"
    inbound_password = f"recovery-{uuid.uuid4().hex}"
    real_connect = psycopg2.connect
    real_verify = roles._verify_runtime_login_state
    maintenance = real_connect(_maintenance_url())
    maintenance.autocommit = True
    attackers = []
    injected = False
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("CREATE ROLE {} LOGIN INHERIT PASSWORD %s").format(
                    pg_sql.Identifier(inbound_role)
                ),
                (inbound_password,),
            )

        def fail_after_login(*, admin_url, runtime_role, expected_can_login):
            nonlocal injected
            real_verify(
                admin_url=admin_url,
                runtime_role=runtime_role,
                expected_can_login=expected_can_login,
            )
            if not expected_can_login or injected:
                return
            injected = True
            with maintenance.cursor() as cursor:
                cursor.execute(
                    pg_sql.SQL("GRANT {} TO {}").format(
                        pg_sql.Identifier(runtime_role),
                        pg_sql.Identifier(inbound_role),
                    )
                )
            direct = real_connect(
                host=parsed.hostname,
                port=parsed.port,
                dbname=role_database.database_name,
                user=runtime_role,
                password=role_database.runtime_password,
            )
            inbound = real_connect(
                host=parsed.hostname,
                port=parsed.port,
                dbname=role_database.database_name,
                user=inbound_role,
                password=inbound_password,
            )
            with inbound.cursor() as cursor:
                cursor.execute(
                    pg_sql.SQL("SET ROLE {}").format(
                        pg_sql.Identifier(runtime_role)
                    )
                )
                cursor.execute("SELECT count(*) FROM operation_results")
                assert cursor.fetchone() is not None
            inbound.rollback()
            attackers.extend((direct, inbound))
            raise RuntimeError("injected post-LOGIN verification failure")

        monkeypatch.setattr(
            roles,
            "_verify_runtime_login_state",
            fail_after_login,
        )
        with pytest.raises(
            RuntimeError, match="injected post-LOGIN verification failure"
        ):
            roles.configure_database_roles(
                admin_url=_maintenance_url(),
                database_names=(role_database.database_name,),
                deploy_password=role_database.deploy_password,
                runtime_password=role_database.runtime_password,
                deploy_role=role_database.deploy_role,
                runtime_role=role_database.runtime_role,
            )
        assert injected

        for attacker in attackers:
            with pytest.raises(psycopg2.Error):
                with attacker.cursor() as cursor:
                    cursor.execute("SELECT 1")

        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (False,)
            cursor.execute(
                "SELECT count(*) FROM pg_auth_members membership "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "WHERE granted.rolname = %s AND member.rolname = %s",
                (role_database.runtime_role, inbound_role),
            )
            assert cursor.fetchone() == (0,)
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE usename = ANY(%s)",
                ([role_database.runtime_role, inbound_role],),
            )
            assert cursor.fetchone() == (0,)
    finally:
        for attacker in attackers:
            attacker.close()
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE usename = %s AND pid <> pg_backend_pid()",
                (inbound_role,),
            )
            cursor.execute(
                pg_sql.SQL("DROP ROLE IF EXISTS {}").format(
                    pg_sql.Identifier(inbound_role)
                )
            )
        maintenance.close()


def test_second_target_acl_verification_failure_keeps_runtime_fenced(
    isolated_role_database,
    monkeypatch,
):
    """Catches LOGIN restoration after target one commits but target two drifts."""
    import scripts.database.configure_roles as roles

    role_database = isolated_role_database
    second_database = f"{role_database.database_name}_second"
    roles.configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    real_connect = psycopg2.connect
    maintenance = real_connect(_maintenance_url())
    maintenance.autocommit = True
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("CREATE DATABASE {}").format(
                    pg_sql.Identifier(second_database)
                )
            )
        for database_name, table_name in (
            (role_database.database_name, "acl_first_probe"),
            (second_database, "acl_second_probe"),
        ):
            target = real_connect(_maintenance_url(), dbname=database_name)
            target.autocommit = True
            try:
                with target.cursor() as cursor:
                    cursor.execute(
                        pg_sql.SQL("CREATE TABLE {} (id integer PRIMARY KEY)").format(
                            pg_sql.Identifier(table_name)
                        )
                    )
            finally:
                target.close()

        skipped = False

        def should_skip(statement):
            nonlocal skipped
            normalized = " ".join(statement.replace('"', "").upper().split())
            if (
                not skipped
                and normalized.startswith("GRANT ")
                and " ON TABLE PUBLIC.ACL_SECOND_PROBE TO " in normalized
            ):
                skipped = True
                return True
            return False

        def intercepted_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            if kwargs.get("dbname") == second_database:
                return _SkippingConnection(connection, should_skip)
            return connection

        monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
        with pytest.raises(RuntimeError, match="ACL verification"):
            roles.configure_database_roles(
                admin_url=_maintenance_url(),
                database_names=(role_database.database_name, second_database),
                deploy_password=role_database.deploy_password,
                runtime_password=role_database.runtime_password,
                deploy_role=role_database.deploy_role,
                runtime_role=role_database.runtime_role,
            )
        assert skipped

        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (False,)
        first_target = real_connect(
            _maintenance_url(), dbname=role_database.database_name
        )
        second_target = real_connect(_maintenance_url(), dbname=second_database)
        try:
            with first_target.cursor() as cursor:
                cursor.execute(
                    "SELECT has_table_privilege(%s, 'acl_first_probe', 'SELECT')",
                    (role_database.runtime_role,),
                )
                assert cursor.fetchone() == (True,)
            with second_target.cursor() as cursor:
                cursor.execute(
                    "SELECT has_table_privilege(%s, 'acl_second_probe', 'SELECT')",
                    (role_database.runtime_role,),
                )
                assert cursor.fetchone() == (False,)
        finally:
            first_target.close()
            second_target.close()
    finally:
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (second_database,),
            )
            cursor.execute(
                pg_sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    pg_sql.Identifier(second_database)
                )
            )
        maintenance.close()


def test_sequence_acl_denies_standalone_and_unused_owned_sequences(
    isolated_role_database,
):
    """Catches unowned or unused sequences inheriting runtime USAGE/SELECT."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = isolated_role_database
    maintenance = psycopg2.connect(
        _maintenance_url(), dbname=role_database.database_name
    )
    maintenance.autocommit = True
    try:
        with maintenance.cursor() as cursor:
            cursor.execute("CREATE SEQUENCE current_standalone_seq")
            cursor.execute(
                "CREATE TABLE current_owned_unused (id integer PRIMARY KEY)"
            )
            cursor.execute(
                "CREATE SEQUENCE current_owned_unused_seq "
                "OWNED BY current_owned_unused.id"
            )
            cursor.execute(
                "CREATE TABLE current_serial_records "
                "(id serial PRIMARY KEY, payload text NOT NULL)"
            )
            cursor.execute(
                "CREATE TABLE current_identity_records "
                "(id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
                "payload text NOT NULL)"
            )

        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

        parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
        deploy = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=role_database.database_name,
            user=role_database.deploy_role,
            password=role_database.deploy_password,
        )
        deploy.autocommit = True
        try:
            with deploy.cursor() as cursor:
                cursor.execute("CREATE SEQUENCE future_standalone_seq")
                cursor.execute(
                    "CREATE TABLE future_owned_unused (id integer PRIMARY KEY)"
                )
                cursor.execute(
                    "CREATE SEQUENCE future_owned_unused_seq "
                    "OWNED BY future_owned_unused.id"
                )
                cursor.execute(
                    "CREATE TABLE future_serial_records "
                    "(id serial PRIMARY KEY, payload text NOT NULL)"
                )
                cursor.execute(
                    "CREATE TABLE future_identity_records "
                    "(id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
                    "payload text NOT NULL)"
                )
                cursor.execute(
                    pg_sql.SQL(
                        "GRANT USAGE, SELECT ON SEQUENCE current_standalone_seq, "
                        "current_owned_unused_seq, future_standalone_seq, "
                        "future_owned_unused_seq TO {}"
                    ).format(pg_sql.Identifier(role_database.runtime_role))
                )
        finally:
            deploy.close()

        configure_database_roles(
            admin_url=_maintenance_url(),
            database_names=(role_database.database_name,),
            deploy_password=role_database.deploy_password,
            runtime_password=role_database.runtime_password,
            deploy_role=role_database.deploy_role,
            runtime_role=role_database.runtime_role,
        )

        with maintenance.cursor() as cursor:
            for sequence_name in (
                "current_standalone_seq",
                "current_owned_unused_seq",
                "future_standalone_seq",
                "future_owned_unused_seq",
            ):
                cursor.execute(
                    "SELECT has_sequence_privilege(%s, %s, 'USAGE'), "
                    "has_sequence_privilege(%s, %s, 'SELECT')",
                    (
                        role_database.runtime_role,
                        sequence_name,
                        role_database.runtime_role,
                        sequence_name,
                    ),
                )
                assert cursor.fetchone() == (False, False), sequence_name
            for sequence_name in (
                "current_serial_records_id_seq",
                "current_identity_records_id_seq",
                "future_serial_records_id_seq",
                "future_identity_records_id_seq",
            ):
                cursor.execute(
                    "SELECT has_sequence_privilege(%s, %s, 'USAGE'), "
                    "has_sequence_privilege(%s, %s, 'SELECT'), "
                    "has_sequence_privilege(%s, %s, 'UPDATE')",
                    (
                        role_database.runtime_role,
                        sequence_name,
                        role_database.runtime_role,
                        sequence_name,
                        role_database.runtime_role,
                        sequence_name,
                    ),
                )
                assert cursor.fetchone() == (True, True, False), sequence_name
            cursor.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (True,)

        runtime = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=role_database.database_name,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        try:
            with runtime.cursor() as cursor:
                for table_name in (
                    "current_serial_records",
                    "current_identity_records",
                    "future_serial_records",
                    "future_identity_records",
                ):
                    cursor.execute(
                        pg_sql.SQL(
                            "INSERT INTO {} (payload) VALUES ('allowed') RETURNING id"
                        ).format(pg_sql.Identifier(table_name))
                    )
                    assert cursor.fetchone()[0] == 1
            runtime.rollback()
        finally:
            runtime.close()
    finally:
        maintenance.close()


def test_role_bootstrap_failure_rolls_back_acl_and_keeps_runtime_fenced(
    guarded_runtime_database,
    monkeypatch,
):
    """Catches a mid-bootstrap failure committing a prefix of privilege changes."""
    import scripts.database.configure_roles as roles

    role_database = guarded_runtime_database
    deploy_engine = create_engine(
        role_database.url(
            role=role_database.deploy_role,
            password=role_database.deploy_password,
        )
    )
    try:
        with deploy_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE bootstrap_acl_probe "
                "(id serial PRIMARY KEY, payload text NOT NULL)"
            )
            connection.exec_driver_sql(
                f'REVOKE ALL ON TABLE bootstrap_acl_probe FROM "{role_database.runtime_role}"'
            )

        real_connect = psycopg2.connect
        raised = False

        def fail_after_probe_grant(statement):
            nonlocal raised
            normalized = " ".join(statement.upper().split())
            is_current_blanket_grant = " ON ALL TABLES IN SCHEMA PUBLIC " in (
                f" {normalized} "
            )
            is_explicit_probe_grant = (
                "GRANT " in normalized
                and "BOOTSTRAP_ACL_PROBE" in normalized
                and " ON TABLE " in f" {normalized} "
            )
            if not raised and (is_current_blanket_grant or is_explicit_probe_grant):
                raised = True
                raise RuntimeError("injected ACL finalization failure")

        def intercepted_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            if kwargs.get("dbname") == role_database.database_name:
                return _InterceptingConnection(connection, fail_after_probe_grant)
            return connection

        monkeypatch.setattr(roles.psycopg2, "connect", intercepted_connect)
        with pytest.raises(RuntimeError, match="injected ACL finalization failure"):
            roles.configure_database_roles(
                admin_url=_maintenance_url(),
                database_names=(role_database.database_name,),
                deploy_password=role_database.deploy_password,
                runtime_password=role_database.runtime_password,
                deploy_role=role_database.deploy_role,
                runtime_role=role_database.runtime_role,
            )
        assert raised

        maintenance = real_connect(
            _maintenance_url(), dbname=role_database.database_name
        )
        try:
            with maintenance.cursor() as cursor:
                cursor.execute(
                    "SELECT has_table_privilege(%s, 'bootstrap_acl_probe', 'SELECT'), "
                    "has_table_privilege(%s, 'bootstrap_acl_probe', 'INSERT'), "
                    "has_table_privilege(%s, 'decision_briefs', 'INSERT'), "
                    "has_table_privilege(%s, 'archie_command_capability_keys', 'SELECT'), "
                    "(SELECT rolcanlogin FROM pg_roles WHERE rolname = %s)",
                    (role_database.runtime_role,) * 5,
                )
                assert cursor.fetchone() == (False, False, False, False, False)
        finally:
            maintenance.close()
    finally:
        deploy_engine.dispose()


def test_concurrent_runtime_cannot_observe_acl_bootstrap_intermediate_state(
    guarded_runtime_database,
):
    """Catches runtime attempts reading keys or forging drafts during ACL repair."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = guarded_runtime_database
    parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
    bootstrap_finished = threading.Event()
    reader_ready = threading.Event()
    writer_ready = threading.Event()
    reader_finished = threading.Event()
    writer_finished = threading.Event()
    secret_read_succeeded = threading.Event()
    forged_write_committed = threading.Event()

    def connect_runtime():
        return psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=role_database.database_name,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
            connect_timeout=2,
        )

    def read_attacker():
        first_attempt = True
        while True:
            post_bootstrap_attempt = bootstrap_finished.is_set()
            connection = None
            try:
                connection = connect_runtime()
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '3s'")
                    cursor.execute(
                        "SELECT secret IS NOT NULL "
                        "FROM archie_command_capability_keys LIMIT 1"
                    )
                    if cursor.fetchone() == (True,):
                        secret_read_succeeded.set()
            except psycopg2.Error:
                pass
            finally:
                if connection is not None:
                    connection.close()
            if first_attempt:
                reader_ready.set()
                first_attempt = False
            if post_bootstrap_attempt:
                reader_finished.set()
                return

    def write_attacker():
        first_attempt = True
        while True:
            post_bootstrap_attempt = bootstrap_finished.is_set()
            connection = None
            try:
                connection = connect_runtime()
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '3s'")
                    cursor.execute(
                        "INSERT INTO decision_briefs "
                        "(organization_id, workstream_id, status, revision) "
                        "VALUES (91001, 92001, 'draft', 1)"
                    )
                connection.commit()
                forged_write_committed.set()
            except psycopg2.Error:
                if connection is not None:
                    try:
                        connection.rollback()
                    except psycopg2.Error:
                        pass
            finally:
                if connection is not None:
                    connection.close()
            if first_attempt:
                writer_ready.set()
                first_attempt = False
            if post_bootstrap_attempt:
                writer_finished.set()
                return

    reader = threading.Thread(target=read_attacker, daemon=True)
    writer = threading.Thread(target=write_attacker, daemon=True)
    reader.start()
    writer.start()
    assert reader_ready.wait(10)
    assert writer_ready.wait(10)

    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    bootstrap_finished.set()
    assert reader_finished.wait(15)
    assert writer_finished.wait(15)
    reader.join(timeout=5)
    writer.join(timeout=5)

    assert secret_read_succeeded.is_set() is False
    assert forged_write_committed.is_set() is False

    maintenance = psycopg2.connect(
        _maintenance_url(), dbname=role_database.database_name
    )
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM decision_briefs "
                "WHERE organization_id = 91001 AND workstream_id = 92001"
            )
            assert cursor.fetchone() == (0,)
    finally:
        maintenance.close()


def test_role_bootstrap_is_idempotent_and_runtime_cannot_bypass_guards(
    isolated_role_database,
):
    """Catches superuser/owner powers making database guards optional at runtime."""
    from scripts.database.configure_roles import configure_database_roles

    role_database = isolated_role_database
    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    maintenance = psycopg2.connect(_maintenance_url())
    maintenance.autocommit = True
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                pg_sql.SQL("CREATE ROLE {} CREATEDB").format(
                    pg_sql.Identifier(role_database.privileged_role)
                )
            )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {}").format(
                    pg_sql.Identifier(role_database.deploy_role),
                    pg_sql.Identifier(role_database.runtime_role),
                )
            )
            cursor.execute(
                pg_sql.SQL("GRANT {} TO {}").format(
                    pg_sql.Identifier(role_database.privileged_role),
                    pg_sql.Identifier(role_database.runtime_role),
                )
            )
    finally:
        maintenance.close()

    configure_database_roles(
        admin_url=_maintenance_url(),
        database_names=(role_database.database_name,),
        deploy_password=role_database.deploy_password,
        runtime_password=role_database.runtime_password,
        deploy_role=role_database.deploy_role,
        runtime_role=role_database.runtime_role,
    )
    maintenance = psycopg2.connect(_maintenance_url())
    try:
        with maintenance.cursor() as cursor:
            cursor.execute(
                "SELECT granted.rolname FROM pg_auth_members membership "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "JOIN pg_roles member ON member.oid = membership.member "
                "WHERE member.rolname = %s ORDER BY granted.rolname",
                (role_database.runtime_role,),
            )
            assert cursor.fetchall() == []
    finally:
        maintenance.close()

    deploy_engine = create_engine(
        role_database.url(
            role=role_database.deploy_role,
            password=role_database.deploy_password,
        )
    )
    try:
        with deploy_engine.begin() as connection:
            connection.exec_driver_sql(_TRANSFORMATION_TABLES_SQL)
            ensure_transformation_db_guards(
                connection,
                runtime_role=role_database.runtime_role,
                capability_secrets=(COMMAND_CAPABILITY_SECRET,),
            )

        parsed = urlsplit(os.environ["TEST_DATABASE_URL"])
        raw = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            dbname=role_database.database_name,
            user=role_database.runtime_role,
            password=role_database.runtime_password,
        )
        try:
            cursor = raw.cursor()
            cursor.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = %s",
                (role_database.runtime_role,),
            )
            assert cursor.fetchone() == (False, False, False, False, False)
            cursor.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication "
                "FROM pg_roles WHERE rolname = %s",
                (role_database.deploy_role,),
            )
            assert cursor.fetchone() == (False, False, False, False)
            cursor.execute(
                "SELECT tableowner FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = 'operation_results'"
            )
            assert cursor.fetchone() == (role_database.deploy_role,)
            cursor.execute(
                "SELECT has_table_privilege(%s, 'operation_results', 'SELECT'), "
                "has_table_privilege(%s, 'operation_results', 'INSERT'), "
                "has_table_privilege(%s, 'operation_results', 'UPDATE'), "
                "has_table_privilege(%s, 'operation_results', 'DELETE'), "
                "has_table_privilege(%s, 'operation_results', 'TRUNCATE'), "
                "has_column_privilege(%s, 'command_idempotency_records', "
                "'status', 'UPDATE'), "
                "has_column_privilege(%s, 'command_idempotency_records', "
                "'idempotency_key', 'UPDATE'), "
                "has_sequence_privilege(%s, 'operation_results_id_seq', 'USAGE'), "
                "has_function_privilege(%s, "
                "'public.archie_guard_transformation_receipt()', 'EXECUTE')",
                (role_database.runtime_role,) * 9,
            )
            assert cursor.fetchone() == (
                True,
                False,
                False,
                False,
                False,
                True,
                False,
                False,
                False,
            )
            cursor.execute(
                "SELECT has_table_privilege(%s, 'evidence_head_events', 'SELECT'), "
                "has_table_privilege(%s, 'evidence_head_events', 'INSERT'), "
                "has_table_privilege(%s, 'decision_briefs', 'INSERT'), "
                "has_table_privilege(%s, 'decision_brief_versions', 'INSERT'), "
                "has_table_privilege(%s, 'decision_brief_option_citations', 'INSERT'), "
                "has_table_privilege(%s, 'decision_brief_evidence_citations', 'INSERT'), "
                "has_function_privilege(%s, "
                "'public.archie_advance_evidence_head(bigint,bigint,integer,bigint,bigint,integer,text)', "
                "'EXECUTE'), "
                "has_function_privilege(%s, "
                "'public.archie_create_decision_brief(text,text,text)', 'EXECUTE')",
                (role_database.runtime_role,) * 8,
            )
            assert cursor.fetchone() == (
                True, False, False, False, False, False, True, True
            )
            cursor.execute(
                "SELECT p.proname, pg_get_function_identity_arguments(p.oid) "
                "FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM pg_depend dependency "
                "WHERE dependency.classid = 'pg_proc'::regclass "
                "AND dependency.objid = p.oid AND dependency.deptype = 'e'"
                ") AND has_function_privilege(%s, p.oid, 'EXECUTE')",
                (role_database.runtime_role,),
            )
            assert sorted(cursor.fetchall()) == sorted([
                (
                    "archie_advance_evidence_head",
                    "p_head_id bigint, p_new_record_id bigint, "
                    "p_expected_revision integer, p_actor_id bigint, "
                    "p_receipt_id bigint, p_generation integer, p_claim_token text",
                ),
                (
                    "archie_claim_transformation_command",
                    "p_document text, p_capability text",
                ),
                (
                    "archie_issue_command_claim_challenge",
                    "p_organization_id bigint, p_actor_id bigint, "
                    "p_valid_for_milliseconds integer",
                ),
                (
                    "archie_create_decision_brief",
                    "p_capability_document text, p_capability text, "
                    "p_request_document text",
                ),
                (
                    "archie_freeze_decision_brief_version",
                    "p_brief_id bigint, p_actor_id bigint, "
                    "p_receipt_id bigint, p_generation integer, p_claim_token text, "
                    "p_capability_document text, p_capability text, "
                    "p_expected_revision integer, p_request_document text, "
                    "p_frozen_payload jsonb, p_canonical_document text",
                ),
                (
                    "archie_persist_command_envelope",
                    "p_capability_document text, p_capability text, "
                    "p_request_document text",
                ),
                (
                    "archie_persist_overlap_disposition",
                    "p_capability_document text, p_capability text, "
                    "p_request_document text",
                ),
                (
                    "archie_repair_command_envelope",
                    "p_claim_document text, p_claim_capability text, "
                    "p_operation_result_id bigint",
                ),
            ])

            receipt_id = _claim_runtime_command(
                cursor,
                organization_id=41001,
                actor_id=42001,
                operation="runtime.probe",
                command_key="runtime-probe",
                request_digest="a" * 64,
                natural_key="runtime:probe",
                claim_token="b" * 64,
                request_id="runtime-request",
            )
            capability_document, capability = _execution_capability(
                organization_id=41001,
                actor_id=42001,
                operation="runtime.probe",
                command_key="runtime-probe",
                request_digest="a" * 64,
                natural_key="runtime:probe",
                receipt_id=receipt_id,
                generation=1,
                claim_token="b" * 64,
                request_id="runtime-request",
            )
            event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "runtime:probe:0"))
            request_document = json.dumps(
                {
                    "object_ids": {},
                    "response": {},
                    "outbox_events": [
                        {
                            "event_id": event_id,
                            "event_type": "runtime.probe.created",
                            "payload": {},
                        }
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            cursor.execute(
                "SELECT public.archie_persist_command_envelope(%s, %s, %s)",
                (capability_document, capability, request_document),
            )
            result_id = cursor.fetchone()[0]
            cursor.execute(
                "UPDATE command_idempotency_records SET status = 'succeeded', "
                "operation_result_id = %s, lease_expires_at = NULL, "
                "completed_at = clock_timestamp() "
                "WHERE id = %s AND organization_id = %s AND actor_id = %s",
                (result_id, receipt_id, 41001, 42001),
            )
            cursor.execute(
                "UPDATE transformation_outbox_events "
                "SET delivery_attempts = 1, published_at = clock_timestamp() "
                "WHERE operation_result_id = %s AND organization_id = %s",
                (result_id, 41001),
            )
            raw.commit()

            forbidden = (
                "SET session_replication_role = replica",
                f"SET ROLE {role_database.deploy_role}",
                f"SET ROLE {role_database.privileged_role}",
                "ALTER TABLE public.operation_results DISABLE TRIGGER ALL",
                "DROP TRIGGER trg_transformation_result_immutable ON public.operation_results",
                "TRUNCATE TABLE public.operation_results",
                "UPDATE public.operation_results SET request_digest = request_digest",
                "DELETE FROM public.command_idempotency_records",
                "INSERT INTO public.operation_results "
                "(organization_id, actor_id, operation, natural_key, request_digest, "
                "receipt_id, receipt_generation, object_ids, response_json) VALUES "
                "(41001, 42001, 'runtime.probe', 'runtime:probe:forged', "
                f"'{'c' * 64}', {receipt_id}, 1, '{{}}', '{{}}')",
                "INSERT INTO public.transformation_outbox_events "
                "(organization_id, operation_result_id, event_id, ordinal, "
                "event_type, payload_json) VALUES "
                f"(41001, {result_id}, '{uuid.uuid4()}', 1, 'forged', '{{}}')",
                "INSERT INTO public.evidence_head_events DEFAULT VALUES",
                "SELECT public.archie_guard_transformation_receipt()",
                "CREATE TABLE public.runtime_guard_bypass (id integer)",
            )
            for statement in forbidden:
                try:
                    cursor.execute(statement)
                except psycopg2.Error:
                    pass
                else:
                    pytest.fail(
                        f"runtime role unexpectedly executed forbidden SQL: {statement}"
                    )
                raw.rollback()
        finally:
            raw.rollback()
            raw.close()
    finally:
        deploy_engine.dispose()
