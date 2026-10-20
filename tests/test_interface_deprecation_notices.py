"""Interface deprecation notices: name the consumers of an interface version
and notify them before it retires.

Covers:
- the api_operation_calls migration creates the table and is idempotent
- flask db heads shows one head
- require_api_scope records one row per bearer call (organisation, client,
  operation, last called at, call count)
- two-organisation isolation: org A's admin list names only A's clients
- marking an operation as retiring adds the Deprecation and Link headers
- marking creates exactly one notice per affected client owner; a second
  marking creates no duplicate
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = REPO_ROOT / "migrations" / "versions" / "20261020_api_op_calls.py"


def _load_revision():
    spec = importlib.util.spec_from_file_location("migration_20261020_api_op_calls", MIGRATION_FILE)
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


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


def test_migration_creates_table(app, db_session):
    """The migration creates the api_operation_calls table."""
    _run_upgrade(app)
    from app.modules.oauth_provider.models import ApiOperationCall

    from app import db
    with db.engine.connect() as connection:
        from sqlalchemy import text
        result = connection.execute(
            text("SELECT to_regclass('public.api_operation_calls')")
        ).scalar()
    assert result is not None, "api_operation_calls table was not created"


def test_migration_runs_twice_without_error(app, db_session):
    """The migration runs twice without error (idempotent)."""
    _run_upgrade(app, times=2)


def test_flask_db_heads_shows_one_head(app):
    """flask db heads shows one head."""
    import subprocess
    result = subprocess.run(
        ["python", "-m", "flask", "--app", "manage", "db", "heads"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    heads = [ln for ln in result.stdout.splitlines() if ln.strip() and "(head)" in ln]
    assert len(heads) == 1, f"Expected 1 head, got {len(heads)}: {heads}"


# ---------------------------------------------------------------------------
# Call recording tests
# ---------------------------------------------------------------------------


def _make_org_and_user(db_session, label):
    from app.models.organization import Organization
    from app.models.user import User

    org = Organization(name=f"Org {label} {uuid.uuid4().hex[:6]}", slug=f"org-{label}-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    user = User(
        email=f"user-{label}-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
    )
    user.password = "x"
    db_session.add(user)
    db_session.flush()
    return org, user


def _make_client_and_token(db_session, user, org, label, scope="api:read"):
    from app.modules.oauth_provider.models import OAuthClient, OAuthToken, _new_client_id, hash_secret

    client = OAuthClient(
        client_id=_new_client_id("cl"),
        client_name=f"Client {label}",
        client_secret_hash=hash_secret("secret"),
        grant_types="client_credentials",
        scope=scope,
        service_user_id=user.id,
        is_active=True,
    )
    db_session.add(client)
    db_session.flush()

    public_base = "http://localhost"
    raw_access, _raw_refresh, token = OAuthToken.issue(
        client_id=client.client_id,
        user_id=user.id,
        scope=scope,
        resource=f"{public_base}/api/v1",
        grant_type="client_credentials",
        organization_id=org.id,
    )
    return client, raw_access


def _enable_bearer(app):
    """Switch the app into bearer-token mode for the test."""
    app.config["MCP_ENABLED"] = True
    app.config["PUBLIC_BASE_URL"] = "http://localhost"


def test_require_api_scope_records_call(app, db_session, client):
    """A bearer call to a token-accepting route records in api_operation_calls."""
    _enable_bearer(app)
    _run_upgrade(app)

    org, user = _make_org_and_user(db_session, "a")
    oauth_client, raw_access = _make_client_and_token(db_session, user, org, "a")

    # Three calls to the elements list route
    for _ in range(3):
        response = client.get(
            "/api/v1/elements",
            headers={"Authorization": f"Bearer {raw_access}"},
        )
        assert response.status_code in (200, 404), response.status_code

    db_session.flush()

    from app.modules.oauth_provider.models import ApiOperationCall
    rows = db_session.query(ApiOperationCall).all()
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    row = rows[0]
    assert row.organization_id == org.id
    assert row.client_id == oauth_client.client_id
    assert row.operation_id == "listElements"
    assert row.call_count == 3
    assert row.last_called_at is not None


def test_two_org_isolation(app, db_session, client):
    """Organisation A's admin list names only A's clients, nothing of B's."""
    _enable_bearer(app)
    _run_upgrade(app)

    org_a, user_a = _make_org_and_user(db_session, "a")
    org_b, user_b = _make_org_and_user(db_session, "b")

    client_a, token_a = _make_client_and_token(db_session, user_a, org_a, "a")
    client_b, token_b = _make_client_and_token(db_session, user_b, org_b, "b")

    from app import db
    from app.modules.oauth_provider.models import ApiOperationCall
    from flask import g
    org_a_id = org_a.id
    org_b_id = org_b.id

    # Two calls from org A's client, one from org B's client.
    # g persists between test-client requests (db_session holds one app
    # context); flask_login caches the resolved bearer user on g._login_user,
    # so delete it before switching orgs so the next request re-resolves its
    # own token instead of reusing the previous org's user.
    responses = []
    for _ in range(2):
        response = client.get("/api/v1/elements", headers={"Authorization": f"Bearer {token_a}"})
        responses.append((response.status_code, response.get_json()))
    for attr in ("_login_user", "_current_user", "bearer_token", "auth_mode", "current_org_id", "current_org"):
        if hasattr(g, attr):
            delattr(g, attr)
    response_b = client.get("/api/v1/elements", headers={"Authorization": f"Bearer {token_b}"})
    responses.append((response_b.status_code, response_b.get_json()))

    db_session.flush()

    # TenantMixin auto-filters by g.current_org_id; set it per query to avoid
    # double-filtering (CLAUDE.md: don't hand-write org filters on TenantMixin).
    g.current_org_id = org_a_id
    rows_a = db_session.query(ApiOperationCall).all()
    g.current_org_id = org_b_id
    rows_b = db_session.query(ApiOperationCall).all()

    assert len(rows_a) == 1, f"Expected 1 row for org A, got {len(rows_a)}: responses={responses}"
    assert rows_a[0].client_id == client_a.client_id
    assert rows_a[0].call_count == 2, f"Expected call_count 2, got {rows_a[0].call_count}: responses={responses}"
    assert len(rows_b) == 1
    assert rows_b[0].client_id == client_b.client_id
    assert rows_b[0].call_count == 1


# ---------------------------------------------------------------------------
# Deprecation header tests
# ---------------------------------------------------------------------------


def test_deprecation_header_on_retiring_operation(app):
    """Marking an operation as retiring adds Deprecation and Link headers."""
    from app.utils.deprecation import deprecated_route, mark_operation_retiring

    @deprecated_route("test_endpoint")
    def mock_view():
        return "ok"

    with app.test_request_context("/test-path", method="GET"):
        # Before retiring: no Deprecation header
        response = mock_view()
        assert response.headers.get("Deprecation") is None

        # After retiring: Deprecation and Link headers present
        mark_operation_retiring("/test-path", "GET", "2027-06-01", "replacement-op")
        response = mock_view()
        assert response.headers.get("Deprecation") == "2027-06-01"
        assert 'rel="deprecation"' in response.headers.get("Link", "")


def test_no_deprecation_header_when_not_retiring(app):
    """An operation that is not marked retiring answers without the header."""
    from app.utils.deprecation import deprecated_route

    @deprecated_route("test_endpoint_2")
    def mock_view():
        return "ok"

    with app.test_request_context("/test-path-2", method="GET"):
        response = mock_view()
        assert response.headers.get("Deprecation") is None


# ---------------------------------------------------------------------------
# Notice creation tests
# ---------------------------------------------------------------------------


def test_mark_retiring_creates_one_notice_per_owner(app, db_session, client):
    """Marking an operation as retiring creates one notice per affected client owner."""
    _enable_bearer(app)
    _run_upgrade(app)

    from flask import g
    from app.utils.deprecation import (
        create_retiring_notices,
        mark_operation_retiring,
    )

    org, user = _make_org_and_user(db_session, "a")
    oauth_client, raw_access = _make_client_and_token(db_session, user, org, "a")

    # Make a call so the client is recorded
    client.get("/api/v1/elements", headers={"Authorization": f"Bearer {raw_access}"})
    db_session.flush()

    mark_operation_retiring("/elements", "GET", "2027-06-01", "listElementsV2")

    g.current_org_id = org.id
    created = create_retiring_notices(
        "listElements", "2027-06-01", "listElementsV2", org.id
    )
    assert created == 1, f"Expected 1 notice created, got {created}"

    from app.models.models import Notification
    notices = (
        db_session.query(Notification)
        .filter(Notification.user_id == user.id, Notification.event_type == "deprecation_notice")
        .all()
    )
    assert len(notices) == 1, f"Expected 1 notice, got {len(notices)}"
    assert "retiring" in notices[0].message


def test_second_marking_creates_no_duplicate(app, db_session, client):
    """A second marking of the same operation creates no duplicate notice."""
    _enable_bearer(app)
    _run_upgrade(app)

    from flask import g
    from app.utils.deprecation import (
        create_retiring_notices,
        mark_operation_retiring,
    )

    org, user = _make_org_and_user(db_session, "a")
    oauth_client, raw_access = _make_client_and_token(db_session, user, org, "a")

    client.get("/api/v1/elements", headers={"Authorization": f"Bearer {raw_access}"})
    db_session.flush()

    mark_operation_retiring("/elements", "GET", "2027-06-01", "listElementsV2")
    mark_operation_retiring("/elements", "GET", "2027-06-01", "listElementsV2")

    g.current_org_id = org.id
    created1 = create_retiring_notices(
        "listElements", "2027-06-01", "listElementsV2", org.id
    )
    created2 = create_retiring_notices(
        "listElements", "2027-06-01", "listElementsV2", org.id
    )
    assert created1 == 1, f"Expected 1 notice on first marking, got {created1}"
    assert created2 == 0, f"Expected 0 notices on second marking, got {created2}"

    from app.models.models import Notification
    notices = (
        db_session.query(Notification)
        .filter(Notification.user_id == user.id, Notification.event_type == "deprecation_notice")
        .all()
    )
    assert len(notices) == 1, f"Expected 1 notice total, got {len(notices)}"

    from app.models.models import Notification
    notices = (
        db_session.query(Notification)
        .filter(Notification.user_id == user.id, Notification.event_type == "deprecation_notice")
        .all()
    )
    assert len(notices) == 1, f"Expected 1 notice, got {len(notices)}"
