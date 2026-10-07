"""SCIM 2.0 Users, discovery, token handling, audit and throttling (R1-B26 PR 1).

Two-organisation isolation is in tests/test_scim_tenant_isolation.py; group
behaviour in tests/test_scim_groups.py; deactivation in
tests/test_leaver_deactivation.py.
"""

from __future__ import annotations

import logging

import pytest

from tests._scim_test_helpers import (
    call,
    issue_token,
    make_user,
    patch_body,
    user_body,
)

pytestmark = pytest.mark.usefixtures("db_session")

SCIM_JSON = "application/scim+json"


@pytest.fixture
def org(make_org):
    return make_org("scim-users")


@pytest.fixture
def token(db_session, org):
    _row, raw = issue_token(db_session, org)
    return raw


def _db_user(user_id):
    from app.models.user import User

    # tenant-scoping-ok: test helper reading one row by primary key
    return User.query.filter(User.id == int(user_id)).first()


class TestCreateGetListReplacePatchDelete:
    def test_create_returns_201_location_and_scim_content_type(self, client, token, org):
        body = user_body("Pat.Person@Example.com", external_id="idp-1")
        resp = call(client, "POST", "/Users", token, body)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.headers["Content-Type"].startswith(SCIM_JSON)
        data = resp.get_json(force=True)
        assert resp.headers["Location"] == data["meta"]["location"]
        assert data["userName"] == "pat.person@example.com"
        assert data["externalId"] == "idp-1"
        assert data["active"] is True
        assert data["meta"]["resourceType"] == "User"
        assert "password" not in data and "password_hash" not in str(data)

        row = _db_user(data["id"])
        assert row.organization_id == org.id
        assert row.provisioned_via == "scim"
        assert row.enterprise_role == "non_technical_owner"
        assert row.is_platform_admin in (False, None)
        assert row.password_hash is None

    def test_get_returns_the_user(self, client, token):
        created = call(client, "POST", "/Users", token, user_body()).get_json(force=True)
        resp = call(client, "GET", f"/Users/{created['id']}", token)
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith(SCIM_JSON)
        assert resp.get_json(force=True)["id"] == created["id"]

    def test_list_filter_and_paging(self, client, token):
        emails = []
        for _ in range(3):
            body = user_body()
            emails.append(body["userName"])
            assert call(client, "POST", "/Users", token, body).status_code == 201

        first = call(client, "GET", "/Users?count=2&startIndex=1", token).get_json(force=True)
        assert first["totalResults"] == 3
        assert first["itemsPerPage"] == 2
        assert first["startIndex"] == 1
        second = call(client, "GET", "/Users?count=2&startIndex=3", token).get_json(force=True)
        assert second["itemsPerPage"] == 1
        assert second["startIndex"] == 3
        assert first["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]

        flt = call(client, "GET", f'/Users?filter=userName eq "{emails[1].upper()}"', token).get_json(force=True)
        assert flt["totalResults"] == 1
        assert flt["Resources"][0]["userName"] == emails[1]

        work = call(
            client, "GET", f'/Users?filter=emails[type eq "work"].value eq "{emails[2]}"', token
        ).get_json(force=True)
        assert work["totalResults"] == 1

        assert call(client, "GET", "/Users?filter=displayName co \"x\"", token).status_code == 400
        bad = call(client, "GET", "/Users?filter=active eq true", token)
        assert bad.status_code == 400
        assert bad.get_json(force=True)["scimType"] == "invalidFilter"

    def test_list_by_external_id_and_count_cap(self, client, token):
        call(client, "POST", "/Users", token, user_body(external_id="ext-42"))
        hit = call(client, "GET", '/Users?filter=externalId eq "ext-42"', token).get_json(force=True)
        assert hit["totalResults"] == 1
        capped = call(client, "GET", "/Users?count=100000", token).get_json(force=True)
        assert capped["itemsPerPage"] <= 200

    def test_replace_updates_names_and_email(self, client, token):
        created = call(client, "POST", "/Users", token, user_body()).get_json(force=True)
        new_email = f"renamed-{created['id']}@example.com"
        resp = call(
            client, "PUT", f"/Users/{created['id']}", token,
            user_body(new_email, given="Robin", family="Renamed", active=True),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        row = _db_user(created["id"])
        assert (row.first_name, row.last_name, row.email) == ("Robin", "Renamed", new_email)

    def test_patch_with_path_without_path_and_entra_string_active(self, client, token):
        created = call(client, "POST", "/Users", token, user_body()).get_json(force=True)
        uid = created["id"]

        resp = call(client, "PATCH", f"/Users/{uid}", token, patch_body(
            {"op": "Replace", "path": "name.givenName", "value": "Ada"},
            {"op": "replace", "value": {"name": {"familyName": "Lovelace"}, "externalId": "ext-9"}},
            {"op": "replace", "path": "displayName", "value": "ignored"},
        ))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        row = _db_user(uid)
        assert (row.first_name, row.last_name, row.external_id) == ("Ada", "Lovelace", "ext-9")

        # Entra sends the boolean as the string "False".
        resp = call(client, "PATCH", f"/Users/{uid}", token, patch_body(
            {"op": "Replace", "path": "active", "value": "False"},
        ))
        assert resp.status_code == 200
        assert resp.get_json(force=True)["active"] is False
        assert _db_user(uid).deactivated_at is not None

        resp = call(client, "PATCH", f"/Users/{uid}", token, patch_body(
            {"op": "replace", "value": {"active": True}},
        ))
        assert resp.get_json(force=True)["active"] is True
        row = _db_user(uid)
        assert row.deactivated_at is None and row.deactivation_reason is None

    def test_patch_rejects_bad_operations(self, client, token):
        created = call(client, "POST", "/Users", token, user_body()).get_json(force=True)
        path = f"/Users/{created['id']}"
        assert call(client, "PATCH", path, token, {"Operations": []}).status_code == 400
        assert call(client, "PATCH", path, token, patch_body({"op": "move", "path": "active"})).status_code == 400
        assert call(client, "PATCH", path, token, patch_body(
            {"op": "replace", "path": "active", "value": "maybe"})).status_code == 400

    def test_delete_deactivates_and_keeps_the_row(self, client, token):
        created = call(client, "POST", "/Users", token, user_body()).get_json(force=True)
        resp = call(client, "DELETE", f"/Users/{created['id']}", token)
        assert resp.status_code == 204
        assert resp.get_data() == b""
        row = _db_user(created["id"])
        assert row is not None
        assert row.deactivated_at is not None
        assert row.deactivation_reason == "leaver_deprovisioned"
        assert call(client, "GET", f"/Users/{created['id']}", token).get_json(force=True)["active"] is False

    def test_invalid_json_and_missing_username(self, client, token):
        resp = client.post(
            "/scim/v2/Users", data="not json",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"},
        )
        assert resp.status_code == 400
        assert resp.get_json(force=True)["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
        assert call(client, "POST", "/Users", token, {"name": {"givenName": "x"}}).status_code == 400
        assert call(client, "POST", "/Users", token, {"userName": "not-an-email"}).status_code == 400

    def test_overlong_values_are_a_400_not_a_500(self, client, token):
        assert call(client, "POST", "/Users", token, user_body(external_id="x" * 300)).status_code == 400
        assert call(client, "POST", "/Users", token, user_body(given="g" * 80)).status_code == 400
        assert call(client, "POST", "/Users", token, user_body("a" * 60 + "@example.com")).status_code == 400

    def test_create_with_active_false_creates_a_deactivated_user(self, client, token):
        data = call(client, "POST", "/Users", token, user_body(active=False)).get_json(force=True)
        assert data["active"] is False
        assert _db_user(data["id"]).deactivated_at is not None


class TestEmailAndRoleSafety:
    def test_duplicate_scim_user_is_409_uniqueness(self, client, token):
        body = user_body()
        assert call(client, "POST", "/Users", token, body).status_code == 201
        again = call(client, "POST", "/Users", token, body)
        assert again.status_code == 409
        assert again.get_json(force=True)["scimType"] == "uniqueness"

    def test_existing_same_org_user_is_linked_not_duplicated(self, client, token, db_session, org):
        from app.models.user import User

        existing = make_user(db_session, org, "linkme")
        body = user_body(existing.email, external_id="idp-link")
        resp = call(client, "POST", "/Users", token, body)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        assert resp.get_json(force=True)["id"] == str(existing.id)
        db_session.refresh(existing)
        assert existing.provisioned_via == "scim"
        assert existing.external_id == "idp-link"
        assert User.query.filter(User.email == existing.email).count() == 1

    def test_admin_email_cannot_be_created_or_set(self, client, token, app):
        admin_email = app.config["ADMIN_EMAIL"]
        resp = call(client, "POST", "/Users", token, user_body(admin_email))
        assert resp.status_code == 409
        created = call(client, "POST", "/Users", token, user_body()).get_json(force=True)
        renamed = call(client, "PATCH", f"/Users/{created['id']}", token, patch_body(
            {"op": "replace", "path": "userName", "value": admin_email}))
        assert renamed.status_code == 409
        assert _db_user(created["id"]).email != admin_email.lower()

    def test_platform_admin_cannot_be_updated_or_deactivated(self, client, token, db_session, org):
        admin = make_user(db_session, org, "pa", administrator=True, is_platform_admin=True)
        for method, body in (
            ("PATCH", patch_body({"op": "replace", "path": "name.givenName", "value": "X"})),
            ("PUT", user_body(admin.email)),
            ("DELETE", None),
        ):
            resp = call(client, method, f"/Users/{admin.id}", token, body)
            assert resp.status_code == 403, (method, resp.status_code)
        db_session.refresh(admin)
        assert admin.deactivated_at is None and admin.first_name == "Pa"
        # linking an existing platform admin through POST is refused too
        assert call(client, "POST", "/Users", token, user_body(admin.email)).status_code == 403

    def test_scim_never_grants_administrator_or_platform_admin(self, client, token):
        data = call(client, "POST", "/Users", token, user_body()).get_json(force=True)
        row = _db_user(data["id"])
        assert not row.is_admin()
        assert row.enterprise_role != "platform_admin"
        assert not row.is_platform_admin


class TestSso:
    def test_sso_provisioning_does_not_overwrite_a_scim_external_id(self, client, token, org):
        from app.services.sso_service import SSOService

        data = call(client, "POST", "/Users", token, user_body(external_id="scim-id")).get_json(force=True)
        row = _db_user(data["id"])
        SSOService().provision_user(org, {"email": row.email, "sub": "oidc-sub", "name": "Pat Person"})
        assert _db_user(data["id"]).external_id == "scim-id"
        assert _db_user(data["id"]).sso_provider == "oidc"


class TestDiscovery:
    @pytest.mark.parametrize("path", ["/ServiceProviderConfig", "/ResourceTypes", "/Schemas"])
    def test_discovery_answers_with_a_token(self, client, token, path):
        resp = call(client, "GET", path, token)
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith(SCIM_JSON)

    def test_service_provider_config_advertises_patch_and_filter(self, client, token):
        data = call(client, "GET", "/ServiceProviderConfig", token).get_json(force=True)
        assert data["patch"]["supported"] is True
        assert data["filter"]["maxResults"] == 200
        assert data["bulk"]["supported"] is False


class TestToken:
    def test_raw_token_is_shown_once_and_only_the_digest_is_stored(self, db_session, org):
        from app.models.account_token import digest
        from app.models.scim import ScimToken

        row, raw = issue_token(db_session, org)
        assert raw.startswith("scim_") and len(raw) == 45
        assert row.token_prefix == raw[:12]
        assert row.token_hash == digest(raw)
        assert raw not in repr(row.__dict__.values())
        for stored in ScimToken.query.filter(ScimToken.organization_id == org.id):
            assert raw not in (stored.token_hash, stored.token_prefix)

    def test_a_third_unrevoked_token_is_refused(self, db_session, org):
        from app.services import provisioning_service

        issue_token(db_session, org)
        issue_token(db_session, org)
        with pytest.raises(provisioning_service.TokenLimitReached):
            issue_token(db_session, org)

    def test_revoking_one_frees_a_slot(self, db_session, org):
        from app.services import provisioning_service

        first, _ = issue_token(db_session, org)
        issue_token(db_session, org)
        provisioning_service.revoke_scim_token(org.id, first.id, "test")
        issue_token(db_session, org)

    def test_a_revoked_token_stops_working_immediately(self, client, db_session, org):
        from app.services import provisioning_service

        row, raw = issue_token(db_session, org)
        assert call(client, "GET", "/Users", raw).status_code == 200
        provisioning_service.revoke_scim_token(org.id, row.id, "test")
        assert call(client, "GET", "/Users", raw).status_code == 401

    def test_last_used_is_recorded(self, client, db_session, org):
        row, raw = issue_token(db_session, org)
        assert row.last_used_at is None
        call(client, "GET", "/Users", raw)
        db_session.refresh(row)
        assert row.last_used_at is not None

    def test_inactive_organisation_is_refused(self, client, db_session, org):
        _row, raw = issue_token(db_session, org)
        org.is_active = False
        db_session.flush()
        assert call(client, "GET", "/Users", raw).status_code == 401

    def test_the_admin_panel_issues_lists_and_revokes_tokens(self, client, db_session, org, login_as):
        from app.models.scim import ScimToken

        admin = make_user(db_session, org, "tokadmin", administrator=True)
        login_as(client, admin)
        page = client.get("/admin/sso-settings")
        assert page.status_code == 200
        assert b"Create token" in page.data and b"/scim/v2" in page.data

        login_as(client, admin)
        created = client.post("/admin/sso-settings/scim-tokens")
        assert created.status_code == 200
        text = created.get_data(as_text=True)
        row = ScimToken.query.filter(ScimToken.organization_id == org.id).one()
        assert "scim_" in text and row.token_prefix in text
        assert "no-store" in created.headers["Cache-Control"]
        raw = text.split('id="scim-token-value">')[1].split("<")[0]
        assert raw.startswith(row.token_prefix) and len(raw) == 45
        assert call(client, "GET", "/Users", raw).status_code == 200

        login_as(client, admin)
        again = client.get("/admin/sso-settings").get_data(as_text=True)
        assert raw not in again and row.token_prefix in again

        login_as(client, admin)
        revoked = client.post(f"/admin/sso-settings/scim-tokens/{row.id}/revoke")
        assert revoked.status_code == 302
        db_session.refresh(row)
        assert row.revoked_at is not None
        assert call(client, "GET", "/Users", raw).status_code == 401

    def test_a_non_administrator_cannot_issue_a_token(self, client, db_session, org, login_as):
        from app.models.scim import ScimToken

        member = make_user(db_session, org, "member")
        login_as(client, member)
        assert client.post("/admin/sso-settings/scim-tokens").status_code in (302, 403)
        assert ScimToken.query.filter(ScimToken.organization_id == org.id).count() == 0


class TestAudit:
    def _actions(self, org_id):
        from app.models.audit_log import AuditLog

        rows = AuditLog.query.filter(
            AuditLog.organization_id == org_id, AuditLog.table_name == "auth"
        ).order_by(AuditLog.id).all()
        return rows

    def test_every_provisioning_action_writes_one_row_without_the_raw_token(
        self, client, db_session, org, caplog
    ):
        from app.models.audit_log import AuditLog
        from app.services import provisioning_service

        caplog.set_level(logging.DEBUG)
        row, raw = issue_token(db_session, org, make_user(db_session, org, "issuer"))
        created = call(client, "POST", "/Users", raw, user_body()).get_json(force=True)
        uid = created["id"]
        call(client, "PATCH", f"/Users/{uid}", raw, patch_body(
            {"op": "replace", "path": "name.givenName", "value": "Changed"}))
        call(client, "DELETE", f"/Users/{uid}", raw)
        call(client, "PATCH", f"/Users/{uid}", raw, patch_body(
            {"op": "replace", "path": "active", "value": True}))
        group = call(client, "POST", "/Groups", raw, {"displayName": "Auditors"}).get_json(force=True)
        call(client, "PATCH", f"/Groups/{group['id']}", raw, patch_body(
            {"op": "add", "path": "members", "value": [{"value": uid}]}))
        call(client, "GET", "/Users", "scim_wrong")  # failed authentication
        provisioning_service.revoke_scim_token(org.id, row.id, "user:1")

        actions = [r.action for r in self._actions(org.id)]
        for expected in (
            "scim_token_issued", "scim_user_created", "scim_user_updated", "user_deactivated",
            "user_reactivated", "scim_group_changed", "scim_token_revoked",
        ):
            assert expected in actions, (expected, actions)
        assert actions.count("scim_user_created") == 1
        assert actions.count("user_deactivated") == 1
        assert actions.count("user_reactivated") == 1

        # the failed attempt is recorded (no organisation: the token matched no row)
        assert AuditLog.query.filter(AuditLog.action == "scim_auth_failed").count() >= 1

        blob = " ".join(str(r.new_value) for r in AuditLog.query.all()) + caplog.text
        assert raw not in blob
        assert "scim_wrong" not in blob

        created_row = [r for r in self._actions(org.id) if r.action == "scim_user_created"][0]
        assert created_row.new_value["actor"] == f"scim_token:{row.token_prefix}"
        assert created_row.new_value["changed"]

    def test_rows_are_readable_at_the_audit_log_only_for_that_organisation(
        self, client, db_session, org, make_org, login_as
    ):
        other = make_org("scim-audit-other")
        row, raw = issue_token(db_session, org)
        call(client, "POST", "/Users", raw, user_body())
        admin = make_user(db_session, org, "auditadmin", administrator=True)
        other_admin = make_user(db_session, other, "otheradmin", administrator=True)
        login_as(client, admin)
        mine = client.get("/admin/audit-log?export=csv&action=scim_user_created")
        assert mine.status_code == 200
        assert "scim_user_created" in mine.get_data(as_text=True)
        login_as(client, other_admin)
        theirs = client.get("/admin/audit-log?export=csv&action=scim_user_created")
        assert theirs.status_code == 200
        assert "scim_user_created" not in theirs.get_data(as_text=True)


class TestRateLimits:
    @pytest.fixture(autouse=True)
    def _frozen_clock(self, monkeypatch):
        """The limiter is a token bucket that refills with the clock; freeze it
        so the count of calls, not the speed of the machine, decides."""
        import types

        monkeypatch.setattr(
            "app.services.rate_limiter.time", types.SimpleNamespace(time=lambda: 1_000_000.0)
        )

    def test_the_601st_call_in_a_minute_is_429_and_another_token_is_unaffected(
        self, app, client, db_session, org, make_org
    ):
        from app.services.rate_limiter import _rate_limiter

        other = make_org("scim-rate-other")
        row, raw = issue_token(db_session, org)
        _other_row, other_raw = issue_token(db_session, other)
        _rate_limiter.reset(f"scim:{row.id}")
        app.config["RATE_LIMITING_ENABLED"] = True
        try:
            statuses = [call(client, "GET", "/ServiceProviderConfig", raw).status_code for _ in range(601)]
            assert statuses[:600] == [200] * 600
            assert statuses[600] == 429
            limited = call(client, "GET", "/ServiceProviderConfig", raw)
            assert limited.status_code == 429
            assert limited.get_json(force=True)["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
            assert limited.headers["Content-Type"].startswith(SCIM_JSON)
            assert "Retry-After" in limited.headers
            assert call(client, "GET", "/ServiceProviderConfig", other_raw).status_code == 200
        finally:
            app.config["RATE_LIMITING_ENABLED"] = False
            _rate_limiter.reset(f"scim:{row.id}")

    def test_repeated_bad_tokens_from_one_address_reach_429(self, app, client):
        from app.services.rate_limiter import _rate_limiter

        address = "203.0.113.77"
        _rate_limiter.reset(f"scim-auth-fail:{address}")
        app.config["RATE_LIMITING_ENABLED"] = True
        try:
            statuses = [
                call(client, "GET", "/Users", "scim_bad", environ_overrides={"REMOTE_ADDR": address}).status_code
                for _ in range(21)
            ]
            assert statuses[:20] == [401] * 20
            assert statuses[20] == 429
            other = call(client, "GET", "/Users", "scim_bad", environ_overrides={"REMOTE_ADDR": "203.0.113.78"})
            assert other.status_code == 401
        finally:
            app.config["RATE_LIMITING_ENABLED"] = False
            _rate_limiter.reset(f"scim-auth-fail:{address}")
