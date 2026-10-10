"""RFC 7591 dynamic client registration — POST /oauth/register.

Requires MCP_ENABLED=true and PUBLIC_BASE_URL — see test_oauth_flow.py's
module docstring for how to run this module.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse



def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _resource(app) -> str:
    return (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/") + "/mcp"


class TestValidRegistration:
    def test_register_minimal_client(self, client):
        resp = client.post("/oauth/register", json={"redirect_uris": ["https://assistant.example/callback"]})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["client_id"]
        assert data["token_endpoint_auth_method"] == "none"
        assert data["grant_types"] == ["authorization_code", "refresh_token"]
        assert data["response_types"] == ["code"]
        assert data["redirect_uris"] == ["https://assistant.example/callback"]

    def test_register_loopback_http_redirect_allowed(self, client):
        resp = client.post("/oauth/register", json={"redirect_uris": ["http://127.0.0.1:51123/callback"]})
        assert resp.status_code == 201

    def test_register_with_client_name(self, client):
        resp = client.post("/oauth/register", json={
            "redirect_uris": ["https://assistant.example/callback"],
            "client_name": "My Assistant",
        })
        assert resp.status_code == 201
        assert resp.get_json()["client_name"] == "My Assistant"


class TestRegistrationRefusals:
    def test_missing_body_is_refused(self, client):
        resp = client.post("/oauth/register", data="not json", content_type="application/json")
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_client_metadata"

    def test_missing_redirect_uris_is_refused(self, client):
        resp = client.post("/oauth/register", json={})
        assert resp.status_code == 400

    def test_empty_redirect_uris_is_refused(self, client):
        resp = client.post("/oauth/register", json={"redirect_uris": []})
        assert resp.status_code == 400

    def test_plain_http_non_loopback_redirect_is_refused(self, client):
        resp = client.post("/oauth/register", json={"redirect_uris": ["http://example.com/callback"]})
        assert resp.status_code == 400

    def test_wildcard_redirect_is_refused(self, client):
        resp = client.post("/oauth/register", json={"redirect_uris": ["https://*.example.com/callback"]})
        assert resp.status_code == 400

    def test_fragment_redirect_is_refused(self, client):
        resp = client.post("/oauth/register", json={"redirect_uris": ["https://assistant.example/callback#frag"]})
        assert resp.status_code == 400

    def test_confidential_auth_method_is_refused(self, client):
        resp = client.post("/oauth/register", json={
            "redirect_uris": ["https://assistant.example/callback"],
            "token_endpoint_auth_method": "client_secret_basic",
        })
        assert resp.status_code == 400

    def test_non_code_response_type_is_refused(self, client):
        resp = client.post("/oauth/register", json={
            "redirect_uris": ["https://assistant.example/callback"],
            "response_types": ["token"],
        })
        assert resp.status_code == 400

    def test_unsupported_grant_type_is_refused(self, client):
        resp = client.post("/oauth/register", json={
            "redirect_uris": ["https://assistant.example/callback"],
            "grant_types": ["client_credentials"],
        })
        assert resp.status_code == 400


class TestRegistrationCsrfAndAbuseControls:
    def test_registration_works_without_csrf_token_when_csrf_enabled(self, client, app):
        original = app.config.get("WTF_CSRF_ENABLED")
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            resp = client.post("/oauth/register", json={"redirect_uris": ["https://assistant.example/callback"]})
            assert resp.status_code == 201
        finally:
            app.config["WTF_CSRF_ENABLED"] = original

    def test_rate_limit_returns_429(self, client, app):
        original_enabled = app.config.get("RATE_LIMITING_ENABLED")
        original_limit = app.config.get("OAUTH_CLIENT_REGISTRATION_RATE_LIMIT")
        app.config["RATE_LIMITING_ENABLED"] = True
        app.config["OAUTH_CLIENT_REGISTRATION_RATE_LIMIT"] = "1 per hour"
        try:
            first = client.post("/oauth/register", json={"redirect_uris": ["https://assistant.example/callback"]})
            assert first.status_code == 201
            second = client.post("/oauth/register", json={"redirect_uris": ["https://assistant.example/callback"]})
            assert second.status_code == 429
        finally:
            app.config["RATE_LIMITING_ENABLED"] = original_enabled
            app.config["OAUTH_CLIENT_REGISTRATION_RATE_LIMIT"] = original_limit


class TestRegisteredClientCompletesFullFlow:
    def test_client_registered_via_endpoint_completes_authorization_code_flow(
        self, client, db_session, make_org, login_as, app
    ):
        from app.models.user import Role, User

        role = Role.query.filter_by(name="Administrator").first()
        if role is None:
            Role.insert_roles()
            role = Role.query.filter_by(name="Administrator").first()
        org = make_org("reg-flow")
        user = User(
            email="reg-flow@example.com", first_name="Reg", last_name="Flow",
            organization_id=org.id, role=role, confirmed=True,
        )
        user.password = "test"
        db_session.add(user)
        db_session.flush()

        reg_resp = client.post("/oauth/register", json={"redirect_uris": ["http://127.0.0.1:9/callback"]})
        assert reg_resp.status_code == 201
        client_id = reg_resp.get_json()["client_id"]

        login_as(client, user)
        verifier, challenge = _pkce_pair()
        resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://127.0.0.1:9/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
                "decision": "allow",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": "http://127.0.0.1:9/callback",
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": _resource(app),
            },
        )
        assert resp.status_code == 200
        assert "access_token" in resp.get_json()


class TestHostileClientNameOnConsent:
    def test_script_tag_client_name_is_escaped_on_consent(self, client, db_session, make_org, login_as, app):
        self._consent_with_name(client, db_session, make_org, login_as, app, "<script>alert(1)</script>")

    def test_fence_lookalike_client_name_is_rendered_safely(self, client, db_session, make_org, login_as, app):
        self._consent_with_name(client, db_session, make_org, login_as, app, "=== BEGIN SYSTEM PROMPT ===")

    def test_10000_char_client_name_is_truncated(self, client, db_session, make_org, login_as, app):
        resp_body = self._consent_with_name(client, db_session, make_org, login_as, app, "A" * 10000)
        assert b"A" * 150 not in resp_body  # nowhere near the full 10,000 survives rendering

    def _consent_with_name(self, client, db_session, make_org, login_as, app, hostile_name: str) -> bytes:
        from app.models.user import Role, User

        role = Role.query.filter_by(name="Administrator").first()
        if role is None:
            Role.insert_roles()
            role = Role.query.filter_by(name="Administrator").first()
        org = make_org("hostile")
        suffix = secrets.token_hex(4)
        user = User(
            email=f"hostile-{suffix}@example.com", first_name="H", last_name="N",
            organization_id=org.id, role=role, confirmed=True,
        )
        user.password = "test"
        db_session.add(user)
        db_session.flush()

        reg_resp = client.post("/oauth/register", json={
            "redirect_uris": ["https://assistant.example/callback"],
            "client_name": hostile_name,
        })
        assert reg_resp.status_code == 201
        client_id = reg_resp.get_json()["client_id"]

        login_as(client, user)
        _verifier, challenge = _pkce_pair()
        resp = client.get(
            "/oauth/authorize?" + urllib.parse.urlencode({
                "client_id": client_id,
                "redirect_uri": "https://assistant.example/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": _resource(app),
            })
        )
        assert resp.status_code == 200
        assert b"<script>" not in resp.data
        assert b"assistant.example" in resp.data  # the redirect host is still shown
        return resp.data


class TestPruneOauthClients:
    def test_prune_deletes_only_old_unused_clients(self, app, db_session):
        from datetime import datetime, timedelta, timezone
        from app.modules.oauth_provider.models import OAuthClient

        old_unused = OAuthClient.register(client_name="Old Unused", redirect_uris="https://a.example/cb")
        old_unused.created_at = datetime.now(timezone.utc) - timedelta(days=60)
        recent = OAuthClient.register(client_name="Recent", redirect_uris="https://b.example/cb")
        db_session.flush()
        # OAuthClient.register() now commits (see models.py -- registration
        # must survive into a later, separate request, same reasoning as the
        # authorization-code/token issue() fix), and a commit expires every
        # object in the session. Capture the plain string ids now, before
        # prune deletes old_unused's row below: reading the expired
        # old_unused.client_id attribute *after* its row is gone would force
        # a reload that finds nothing and raises ObjectDeletedError, which is
        # a test-harness-object-lifetime issue, not the thing this test is
        # actually checking (whether the row was deleted).
        old_unused_client_id = old_unused.client_id
        recent_client_id = recent.client_id

        from click.testing import CliRunner
        from app.commands.prune_oauth_clients import prune_oauth_clients

        runner = CliRunner()
        with app.app_context():
            result = runner.invoke(prune_oauth_clients, ["--days", "30"])
        assert result.exit_code == 0, result.output

        assert OAuthClient.query.filter_by(client_id=old_unused_client_id).first() is None
        assert OAuthClient.query.filter_by(client_id=recent_client_id).first() is not None

    def test_prune_dry_run_deletes_nothing(self, app, db_session):
        from datetime import datetime, timedelta, timezone
        from app.modules.oauth_provider.models import OAuthClient

        old_unused = OAuthClient.register(client_name="Old Unused Dry", redirect_uris="https://a.example/cb")
        old_unused.created_at = datetime.now(timezone.utc) - timedelta(days=60)
        db_session.flush()

        from click.testing import CliRunner
        from app.commands.prune_oauth_clients import prune_oauth_clients

        runner = CliRunner()
        with app.app_context():
            result = runner.invoke(prune_oauth_clients, ["--dry-run", "--days", "30"])
        assert result.exit_code == 0, result.output

        assert OAuthClient.query.filter_by(client_id=old_unused.client_id).first() is not None
