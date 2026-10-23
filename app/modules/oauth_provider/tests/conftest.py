"""Shared fixtures for the connector (MCP/OAuth) tests: two organisations, bearer tokens per
organisation, and an identity-clear helper to switch users between requests.
Test selection is handled by scripts/ci/mcp_selection.py (loaded through pytest.ini).
"""

from __future__ import annotations

import uuid

import pytest

from app.models.organization import Organization
from app.models.user import User
from app.modules.oauth_provider.models import OAuthClient, OAuthToken, _new_client_id, hash_secret


@pytest.fixture
def org_pair(db_session):
    """Two real organisations; every row is rolled back when the test ends."""
    a = Organization(name="Organisation A", slug="org-a-%s" % uuid.uuid4().hex[:8])
    b = Organization(name="Organisation B", slug="org-b-%s" % uuid.uuid4().hex[:8])
    db_session.add_all([a, b])
    db_session.flush()
    return a, b


@pytest.fixture
def reader_tokens(db_session, org_pair, app):
    """One api:read bearer token per organisation, minted through OAuthToken.issue
    (the same path the original tests/api/conftest.py used). Returns a dict
    keyed by organisation id.

    The bearer mechanism is only active when MCP_ENABLED is true and
    PUBLIC_BASE_URL is set, so tests in this directory must run with both
    exported (for example MCP_ENABLED=true PUBLIC_BASE_URL=https://verify.local).
    """
    tokens = {}
    public_base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    resource = public_base + "/api/v1"

    for org in org_pair:
        user = User(
            email="reader-%s@example.com" % uuid.uuid4().hex[:8],
            first_name="Reader", last_name="Probe",
            organization_id=org.id, confirmed=True, enterprise_role="Viewer",
        )
        user.password = "reader-probe-%s" % uuid.uuid4().hex[:8]
        db_session.add(user)
        db_session.flush()

        client = OAuthClient(
            client_id=_new_client_id("svc"),
            client_name="Reader probe client",
            client_secret_hash=hash_secret("probe-secret"),
            grant_types="client_credentials",
            scope="api:read",
            service_user_id=user.id,
            is_active=True,
        )
        db_session.add(client)
        db_session.flush()

        raw_access, _raw_refresh, _row = OAuthToken.issue(
            client_id=client.client_id,
            user_id=user.id,
            scope="api:read",
            resource=resource,
            grant_type="client_credentials",
            organization_id=org.id,
        )
        tokens[org.id] = raw_access
    return tokens


def clear_identity(app, client=None):
    """Drop the cached flask-login/tenant state so the next request resolves
    its bearer token from the current Authorization header.

    The db_session fixture holds one app context for the whole test, so g
    survives between test-client requests; without this a second token keeps
    running as the first request's user. Also clears the session cookie so it
    cannot leak into bearer calls.
    """
    from flask import g, has_app_context

    if not has_app_context():
        return
    for attr in ("_login_user", "_current_user", "current_org_id", "current_org",
                 "auth_mode", "bearer_token"):
        g.pop(attr, None)
    if client is not None:
        with client.session_transaction() as sess:
            sess.clear()
