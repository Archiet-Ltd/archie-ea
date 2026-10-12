"""A connector token must end when the account behind it changes.

Covers: password reset by an administrator, a person changing their own
password, deactivation, multi-factor and single-sign-on changes, a move to
another organisation, deleting the user, refresh-token reuse, the screens a
person and an administrator use to disconnect an assistant, and the bounds on
dynamic registration. Each test is written to fail without the change it names.
"""

from __future__ import annotations

import json

import pytest

from app.modules.mcp.tests.test_scope_enforcement import (
    _clear_cached_identity,
    _mcp_call,
    _mcp_resource,
)
from app.modules.oauth_provider.tests.test_oauth_flow import _make_user, _pkce_pair, _register_client


def _issue(app, user, org, scope="mcp:read"):
    """A connector token pair for *user*, as the token endpoint would mint it."""
    from app.modules.oauth_provider.models import OAuthToken

    oc = _register_client()
    access, refresh, row = OAuthToken.issue(
        client_id=oc.client_id,
        user_id=user.id,
        scope=scope,
        resource=_mcp_resource(app),
        organization_id=org.id,
        refresh_expires_in=30 * 86400,
    )
    return oc, access, refresh, row


def _list_canvases_status(client, access):
    status, _body = _mcp_call(client, access, "list_canvases", {})
    return status


def _refresh(app, oc, refresh):
    _clear_cached_identity()
    return app.test_client().post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": oc.client_id,
        "resource": _mcp_resource(app),
    })


@pytest.fixture
def connected(client, db_session, make_org, app):
    org = make_org("connector")
    user = _make_user(db_session, org, "connector-user@example.com")
    oc, access, refresh, row = _issue(app, user, org)
    assert _list_canvases_status(client, access) == 200
    return {"org": org, "user": user, "oc": oc, "access": access, "refresh": refresh, "row": row}


class TestAccountChangesEndTheToken:
    def test_administrator_password_reset(self, client, app, connected):
        from app.modules.admin.services.admin_user_service import AdminUserService

        AdminUserService.set_user_password(connected["user"], "a-new-Passw0rd!")
        assert _list_canvases_status(client, connected["access"]) == 401
        assert _refresh(app, connected["oc"], connected["refresh"]).status_code == 400

    def test_administrator_reset_through_the_session_revocation_path(self, client, app, connected):
        from app.services import session_registry

        session_registry.revoke_all_for_user(connected["user"].id, "admin")
        assert _list_canvases_status(client, connected["access"]) == 401
        assert _refresh(app, connected["oc"], connected["refresh"]).status_code == 400

    def test_own_password_change_keeps_the_session_but_not_the_assistant(self, client, app, connected):
        from app.modules.account.services.account_service import AccountService

        with app.test_request_context():
            ok, _msg, _n = AccountService.change_password(connected["user"], "test", "another-Passw0rd!")
        assert ok
        assert _list_canvases_status(client, connected["access"]) == 401
        assert _refresh(app, connected["oc"], connected["refresh"]).status_code == 400

    def test_deactivated_account(self, client, app, db_session, connected):
        connected["user"].confirmed = False
        db_session.flush()
        assert _list_canvases_status(client, connected["access"]) == 401
        assert _refresh(app, connected["oc"], connected["refresh"]).status_code == 400

    def test_multi_factor_enrolment(self, client, app, db_session, connected):
        connected["user"].mfa_enabled = True
        connected["user"].mfa_secret = "JBSWY3DPEHPK3PXP"
        db_session.flush()
        assert _list_canvases_status(client, connected["access"]) == 401
        assert _refresh(app, connected["oc"], connected["refresh"]).status_code == 400

    def test_single_sign_on_link(self, client, app, db_session, connected):
        connected["user"].sso_provider = "oidc"
        connected["user"].external_id = "subject-1"
        db_session.flush()
        assert _list_canvases_status(client, connected["access"]) == 401
        assert _refresh(app, connected["oc"], connected["refresh"]).status_code == 400

    def test_move_to_another_organisation_and_back_does_not_revive_the_token(
        self, client, app, db_session, make_org, connected
    ):
        other = make_org("connector-other")
        user = connected["user"]
        home = user.organization_id
        user.organization_id = other.id
        db_session.flush()
        assert _list_canvases_status(client, connected["access"]) == 401
        user.organization_id = home
        db_session.flush()
        assert _list_canvases_status(client, connected["access"]) == 401
        assert _refresh(app, connected["oc"], connected["refresh"]).status_code == 400

    def test_unrelated_profile_change_leaves_the_token_working(self, client, db_session, connected):
        connected["user"].first_name = "Renamed"
        db_session.flush()
        assert _list_canvases_status(client, connected["access"]) == 200


class TestDeletingAUser:
    def test_user_with_a_connected_assistant_can_be_deleted(self, db_session, app, make_org):
        from app.models.user import User
        from app.modules.admin.services.admin_user_service import AdminUserService
        from app.modules.oauth_provider.models import OAuthAuthorizationCode, OAuthToken

        org = make_org("connector-delete")
        user = _make_user(db_session, org, "connector-delete@example.com")
        oc, _access, _refresh_tok, _row = _issue(app, user, org)
        _verifier, challenge = _pkce_pair()
        OAuthAuthorizationCode.issue(
            client_id=oc.client_id, user_id=user.id, redirect_uri="http://localhost/callback",
            code_challenge=challenge, resource=_mcp_resource(app), organization_id=org.id,
        )
        user_id = user.id
        ok, _msg = AdminUserService.delete_user(user)
        assert ok
        assert db_session.get(User, user_id) is None  # tenant-scoping-ok: test asserting a deleted row is gone
        assert OAuthToken.query.filter_by(user_id=user_id).count() == 0  # tenant-scoping-ok: test asserting cascade; token table has no tenant fence
        assert OAuthAuthorizationCode.query.filter_by(user_id=user_id).count() == 0  # tenant-scoping-ok: test asserting cascade; code table has no tenant fence


class TestRefreshTokenReuse:
    def test_replaying_a_rotated_refresh_token_revokes_the_whole_grant(self, client, app, connected):
        first = _refresh(app, connected["oc"], connected["refresh"])
        assert first.status_code == 200, first.get_json()
        rotated = first.get_json()
        assert _list_canvases_status(client, rotated["access_token"]) == 200

        replay = _refresh(app, connected["oc"], connected["refresh"])
        assert replay.status_code == 400

        assert _list_canvases_status(client, rotated["access_token"]) == 401
        assert _refresh(app, connected["oc"], rotated["refresh_token"]).status_code == 400

    def test_replay_by_another_client_does_not_touch_the_grant(self, client, app, connected):
        first = _refresh(app, connected["oc"], connected["refresh"])
        rotated = first.get_json()
        stranger = _register_client()
        assert _refresh(app, stranger, connected["refresh"]).status_code == 400
        assert _list_canvases_status(client, rotated["access_token"]) == 200

    def test_refresh_stops_when_the_client_is_deactivated(self, client, app, db_session, connected):
        connected["oc"].is_active = False
        db_session.flush()
        assert _refresh(app, connected["oc"], connected["refresh"]).status_code == 401


class TestConnectedAssistantsScreens:
    def test_person_sees_and_disconnects_their_assistant(self, client, app, db_session, login_as, connected):
        login_as(client, connected["user"])
        page = client.get("/account/manage/connected-assistants")
        assert page.status_code == 200
        assert b"Test Client" in page.data
        grant_id = connected["row"].grant_id
        assert grant_id

        login_as(client, connected["user"])
        resp = client.post(f"/account/manage/connected-assistants/{grant_id}/revoke")
        assert resp.status_code == 302
        assert _list_canvases_status(client, connected["access"]) == 401

        login_as(client, connected["user"])
        assert b"No assistants are connected" in client.get("/account/manage/connected-assistants").data

    def test_person_cannot_disconnect_someone_elses_assistant(self, client, db_session, make_org, login_as, connected):
        other = _make_user(db_session, connected["org"], "connector-other@example.com")
        login_as(client, other)
        resp = client.post(f"/account/manage/connected-assistants/{connected['row'].grant_id}/revoke")
        assert resp.status_code == 302
        assert _list_canvases_status(client, connected["access"]) == 200

    def test_administrator_disconnects_a_users_assistants(self, client, db_session, login_as, connected):
        admin = _make_user(db_session, connected["org"], "connector-admin@example.com")
        login_as(client, admin)
        page = client.get(f"/admin/user/{connected['user'].id}/connected-assistants")
        assert page.status_code == 200
        assert b"Test Client" in page.data

        login_as(client, admin)
        resp = client.post(f"/admin/user/{connected['user'].id}/connected-assistants/revoke")
        assert resp.status_code == 302
        assert _list_canvases_status(client, connected["access"]) == 401

    def test_administrator_of_another_organisation_cannot_reach_the_user(
        self, client, db_session, make_org, login_as, connected
    ):
        other_org = make_org("connector-foreign")
        foreign_admin = _make_user(db_session, other_org, "connector-foreign-admin@example.com")
        login_as(client, foreign_admin)
        resp = client.post(f"/admin/user/{connected['user'].id}/connected-assistants/revoke")
        assert resp.status_code in (403, 404)
        assert _list_canvases_status(client, connected["access"]) == 200

    def test_consent_screen_links_to_the_revocation_screen(self, client, db_session, login_as, connected, app):
        from urllib.parse import urlencode

        _v, challenge = _pkce_pair()
        login_as(client, connected["user"])
        resp = client.get("/oauth/authorize?" + urlencode({
            "client_id": connected["oc"].client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": _mcp_resource(app),
        }))
        assert resp.status_code == 200
        assert b"/account/manage/connected-assistants" in resp.data


class TestRegistrationBounds:
    def _post(self, app, payload=None, raw=None):
        kwargs = {"data": raw, "content_type": "application/json"} if raw is not None else {"json": payload}
        return app.test_client().post("/oauth/register", **kwargs)

    def test_more_than_ten_redirect_uris_is_refused(self, app):
        uris = [f"https://client.example/cb{i}" for i in range(11)]
        assert self._post(app, {"redirect_uris": uris}).status_code == 400

    def test_ten_redirect_uris_is_accepted(self, app):
        uris = [f"https://client.example/cb{i}" for i in range(10)]
        assert self._post(app, {"redirect_uris": uris}).status_code == 201

    def test_an_overlong_redirect_uri_is_refused(self, app):
        uri = "https://client.example/" + "a" * 2000
        assert self._post(app, {"redirect_uris": [uri]}).status_code == 400

    def test_an_oversized_body_is_refused(self, app):
        body = json.dumps({"redirect_uris": ["https://client.example/cb"], "padding": "x" * 40000})
        assert self._post(app, raw=body).status_code == 400


class TestAuthorizeParameters:
    def _params(self, oc, app, **override):
        _v, challenge = _pkce_pair()
        params = {
            "client_id": oc.client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": _mcp_resource(app),
        }
        params.update(override)
        return params

    def test_plain_challenge_method_is_refused(self, client, db_session, make_org, login_as, app):
        from urllib.parse import urlencode

        user = _make_user(db_session, make_org("connector"), "connector-plain@example.com")
        login_as(client, user)
        oc = _register_client()
        resp = client.get("/oauth/authorize?" + urlencode(self._params(oc, app, code_challenge_method="plain")))
        assert resp.status_code == 400
        assert "S256" in resp.get_json()["error_description"]

    @pytest.mark.parametrize("challenge", ["short", "a" * 129, "has spaces " + "a" * 40, "a" * 42 + "!"])
    def test_malformed_code_challenge_is_refused(self, client, db_session, make_org, login_as, app, challenge):
        from urllib.parse import urlencode

        user = _make_user(db_session, make_org("connector"), "connector-challenge@example.com")
        login_as(client, user)
        oc = _register_client()
        resp = client.get("/oauth/authorize?" + urlencode(self._params(oc, app, code_challenge=challenge)))
        assert resp.status_code == 400

    def test_write_scope_is_no_longer_offered(self, client, app):
        meta = client.get("/.well-known/oauth-authorization-server").get_json()
        assert meta["scopes_supported"] == ["mcp:read"]

    def test_requested_write_scope_is_not_granted(self, client, db_session, make_org, login_as, app):
        from app.modules.oauth_provider.routes import _filter_granted_scopes

        user = _make_user(db_session, make_org("connector"), "connector-scope@example.com")
        assert _filter_granted_scopes("mcp:read mcp:propose", user) == ["mcp:read"]
