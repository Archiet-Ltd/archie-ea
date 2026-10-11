"""Connector hardening: credentials never leave, failures stay generic, every
call is audited, and a token's redirect address is checked at redemption."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse

from app.modules.mcp.tests.test_scope_enforcement import (
    _clear_cached_identity,
    _mcp_call,
    _mcp_resource,
    _mint_token_with_scope,
)
from app.modules.mcp.tests.test_tools import _make_element, _make_user


def test_scrub_credentials_withholds_secret_keys_at_any_depth():
    from app.modules.mcp.tools import REDACTED, scrub_credentials

    out = scrub_credentials({
        "name": "kept",
        "api_key": "sk-live-1",
        "nested": [{"password_hash": "x", "ok": 1, "Client_Secret": "y"}],
    })
    assert out["name"] == "kept"
    assert out["api_key"] == REDACTED
    assert out["nested"][0] == {"password_hash": REDACTED, "ok": 1, "Client_Secret": REDACTED}


def test_tool_result_with_a_credential_field_is_scrubbed(client, db_session, make_org, monkeypatch):
    from flask import current_app

    from app.modules.mcp.tools import TOOL_REGISTRY

    org = make_org("harden")
    user = _make_user(db_session, org, "harden-scrub@example.com")
    token = _mint_token_with_scope(current_app, user, org, "mcp:read")
    handler = TOOL_REGISTRY["list_canvases"]
    monkeypatch.setattr(handler, "_execute", lambda a: {"rows": [{"api_key": "sk-live-9", "n": 1}]})
    status, body = _mcp_call(client, token, "list_canvases", {})
    assert status == 200, body
    text = body["result"]["content"][0]["text"]
    assert "sk-live-9" not in text
    assert json.loads(text)["rows"][0]["n"] == 1


def test_tool_failure_does_not_leak_the_exception_text(client, db_session, make_org, monkeypatch):
    from flask import current_app

    from app.modules.mcp.tools import TOOL_REGISTRY

    org = make_org("harden")
    user = _make_user(db_session, org, "harden-err@example.com")
    token = _mint_token_with_scope(current_app, user, org, "mcp:read")

    def boom(_a):
        raise RuntimeError("SELECT secret FROM other_org_table")

    monkeypatch.setattr(TOOL_REGISTRY["list_canvases"], "_execute", boom)
    status, body = _mcp_call(client, token, "list_canvases", {})
    assert status == 500
    assert "SELECT" not in json.dumps(body)


def test_each_tool_call_is_written_to_the_audit_log(client, db_session, make_org):
    from flask import current_app

    from app.models.audit_log import AuditLog

    org = make_org("harden")
    user = _make_user(db_session, org, "harden-audit@example.com")
    element = _make_element(db_session, org.id, "harden-audit")
    token = _mint_token_with_scope(current_app, user, org, "mcp:read")
    status, body = _mcp_call(client, token, "get_element", {"element_id": element.id})
    assert status == 200, body
    rows = AuditLog.query.filter_by(action="mcp_tool_call", user_id=user.id).all()
    assert rows and all(r.organization_id == org.id for r in rows)


def test_tool_call_without_matching_organisation_scope_is_refused(client, db_session, make_org, monkeypatch):
    """If the request's organisation scope is not the caller's own, no tool runs."""
    from flask import current_app

    from app.modules.mcp.tools import TOOL_REGISTRY

    org = make_org("harden")
    user = _make_user(db_session, org, "harden-scope@example.com")
    token = _mint_token_with_scope(current_app, user, org, "mcp:read")
    ran = []
    monkeypatch.setattr(TOOL_REGISTRY["list_canvases"], "_execute", lambda a: ran.append(1) or {})

    # The identity loader accepts the token (the user really belongs to the
    # organisation); the request's organisation scope is then lost between the
    # tenant middleware and the tool, which only the guard in the endpoint can
    # catch. The metering call runs just before the guard, so it is the seam.
    from flask import g

    from app.services.usage_metering_service import UsageMeteringService

    monkeypatch.setattr(
        UsageMeteringService, "record",
        staticmethod(lambda **kw: setattr(g, "current_org_id", -1)),
    )
    status, body = _mcp_call(client, token, "list_canvases", {})
    assert status == 403
    assert "Organisation scope unavailable" in json.dumps(body)
    assert not ran


def _pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def test_code_redeemed_with_a_different_redirect_uri_is_refused(client, db_session, make_org, login_as, app):
    from app.modules.oauth_provider.models import OAuthClient

    org = make_org("harden")
    user = _make_user(db_session, org, "harden-redirect@example.com")
    oc = OAuthClient.register(client_name="Redirect Client", redirect_uris="http://localhost/callback")
    verifier, challenge = _pkce()
    resource = _mcp_resource(app)
    login_as(client, user)
    resp = client.post("/oauth/authorize", data={
        "client_id": oc.client_id, "redirect_uri": "http://localhost/callback",
        "code_challenge": challenge, "code_challenge_method": "S256",
        "scope": "mcp:read", "resource": resource, "decision": "allow",
    }, follow_redirects=False)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(resp.headers["Location"]).query)["code"][0]
    _clear_cached_identity()
    resp = app.test_client().post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost/other", "client_id": oc.client_id,
        "code_verifier": verifier, "resource": resource,
    })
    assert resp.status_code == 400
    assert "access_token" not in (resp.get_json() or {})
