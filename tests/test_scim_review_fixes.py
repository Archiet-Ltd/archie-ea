"""The twelve review defects on PR 424 (R1-B26 PR 1 fix round).

Each test names its defect id (D-01 ... D-12) or the coordinator note (F-04).
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from tests._scim_test_helpers import (
    GROUP_SCHEMA,
    call,
    give_unlimited_plan,
    issue_token,
    make_user,
    patch_body,
    user_body,
)

pytestmark = pytest.mark.usefixtures("db_session")

_PASSWORD = "Str0ng!Passw0rd"


def _plan(db_session, org, plan, seats):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    db_session.add(Subscription(
        organization_id=org.id, plan=SubscriptionPlan[plan],
        status=SubscriptionStatus.active, seats_purchased=seats,
    ))
    db_session.flush()


def _count(db_session, table, org_id):
    return db_session.execute(
        text(f"SELECT count(*) FROM {table} WHERE organization_id = :o"), {"o": org_id}
    ).scalar_one()


# ---------------------------------------------------------------------------
# D-01: the organisation-administrator gate on the active organisation
# ---------------------------------------------------------------------------


@pytest.fixture
def switched(db_session, make_org):
    """An organisation-A administrator holding a viewer role in organisation B."""
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.miscellaneous import SSOGroupRoleMapping
    from app.models.org_role import OrgRole
    from app.services import provisioning_service

    org_a, org_b = make_org("d01a"), make_org("d01b")
    give_unlimited_plan(db_session, org_a)
    give_unlimited_plan(db_session, org_b)
    admin_a = make_user(db_session, org_a, "admina", administrator=True)
    db_session.add(OrgRole(organization_id=org_b.id, user_id=admin_a.id, role="viewer"))
    leaver_b = make_user(db_session, org_b, "leaverb")
    new_owner_b = make_user(db_session, org_b, "newownerb")
    app_b = ApplicationComponent(name="B payroll", organization_id=org_b.id)
    db_session.add(app_b)
    db_session.flush()
    owner_b = ApplicationOwner(
        application_id=app_b.id, user_id=leaver_b.id, organization_id=org_b.id, ownership_type="primary"
    )
    mapping_b = SSOGroupRoleMapping(
        organization_id=org_b.id, sso_group_name="b-group", role_name="solution_architect", is_active=True
    )
    db_session.add_all([owner_b, mapping_b])
    db_session.flush()
    provisioning_service.deactivate_user(leaver_b, reason="leaver_deprovisioned", actor="test")
    return dict(org_a=org_a, org_b=org_b, admin_a=admin_a, owner_b=owner_b,
                new_owner_b=new_owner_b, leaver_b=leaver_b)


def _switch_to(client, login_as, user, org):
    login_as(client, user)
    with client.session_transaction() as sess:
        sess["current_org_id"] = org.id


def test_d01_switched_administrator_is_refused_on_every_route(client, login_as, db_session, switched):
    w = switched
    _switch_to(client, login_as, w["admin_a"], w["org_b"])
    mappings_before = _count(db_session, "sso_group_role_mappings", w["org_b"].id)

    assert client.post("/admin/sso-settings/scim-tokens").status_code == 403
    assert client.get("/admin/leavers").status_code == 403
    resp = client.post(
        f"/admin/leavers/ownerships/{w['owner_b'].id}/transfer",
        data={"new_owner_id": str(w["new_owner_b"].id)},
    )
    assert resp.status_code == 403
    resp = client.post(
        "/admin/sso-settings",
        data={"action": "add", "sso_group_name": "evil", "role_name": "solution_architect"},
    )
    assert resp.status_code == 403
    assert client.get("/admin/sso-settings").status_code == 403

    assert _count(db_session, "scim_tokens", w["org_b"].id) == 0
    assert _count(db_session, "sso_group_role_mappings", w["org_b"].id) == mappings_before
    db_session.refresh(w["owner_b"])
    assert w["owner_b"].user_id == w["leaver_b"].id

    # Revoking a token of B is refused too.
    from app.services import provisioning_service

    row, _raw = provisioning_service.issue_scim_token(w["org_b"].id, None)
    _switch_to(client, login_as, w["admin_a"], w["org_b"])
    assert client.post(f"/admin/sso-settings/scim-tokens/{row.id}/revoke").status_code == 403
    db_session.refresh(row)
    assert row.revoked_at is None


def test_d01_the_same_administrator_is_served_in_their_own_organisation(client, login_as, switched):
    w = switched
    _switch_to(client, login_as, w["admin_a"], w["org_a"])
    assert client.get("/admin/leavers").status_code == 200
    assert client.get("/admin/sso-settings").status_code == 200


def test_d01_scim_patch_of_an_administrators_username_is_refused(client, db_session, make_org):
    org = make_org("d01scim")
    give_unlimited_plan(db_session, org)
    admin = make_user(db_session, org, "orgadmin", administrator=True)
    member = make_user(db_session, org, "member")
    _row, raw = issue_token(db_session, org)
    original = admin.email

    resp = call(client, "PATCH", f"/Users/{admin.id}", raw, patch_body(
        {"op": "replace", "path": "userName", "value": "takeover@example.com"}))
    assert resp.status_code == 403
    db_session.refresh(admin)
    assert admin.email == original

    # Other attributes of an administrator stay updatable, as does a member's userName.
    resp = call(client, "PATCH", f"/Users/{admin.id}", raw, patch_body(
        {"op": "replace", "path": "name.givenName", "value": "Renamed"}))
    assert resp.status_code == 200
    resp = call(client, "PATCH", f"/Users/{member.id}", raw, patch_body(
        {"op": "replace", "path": "userName", "value": f"moved-{uuid.uuid4().hex[:6]}@example.com"}))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# D-02: leavers free their seat; reactivation re-checks
# ---------------------------------------------------------------------------


def _full_org(db_session, make_org, label):
    org = make_org(label)
    people = [make_user(db_session, org, f"p{i}") for i in range(3)]
    return org, people


def test_d02_deactivating_the_extra_member_of_a_full_plan_lets_scim_create_one(client, db_session, make_org):
    org, people = _full_org(db_session, make_org, "d02a")
    _row, raw = issue_token(db_session, org)
    assert call(client, "POST", "/Users", raw, user_body()).status_code == 403

    assert call(client, "DELETE", f"/Users/{people[0].id}", raw).status_code == 204
    assert call(client, "POST", "/Users", raw, user_body()).status_code == 201


def test_d02_reactivating_on_a_now_full_plan_is_refused_and_stays_deactivated(client, db_session, make_org):
    org, people = _full_org(db_session, make_org, "d02b")
    _row, raw = issue_token(db_session, org)
    assert call(client, "DELETE", f"/Users/{people[0].id}", raw).status_code == 204
    assert call(client, "POST", "/Users", raw, user_body()).status_code == 201

    resp = call(client, "PATCH", f"/Users/{people[0].id}", raw, patch_body(
        {"op": "replace", "path": "active", "value": True}))
    assert resp.status_code == 403
    db_session.refresh(people[0])
    assert people[0].deactivated_at is not None


def test_d02_reactivation_with_a_free_place_succeeds(client, db_session, make_org):
    org, people = _full_org(db_session, make_org, "d02c")
    _row, raw = issue_token(db_session, org)
    assert call(client, "DELETE", f"/Users/{people[0].id}", raw).status_code == 204

    resp = call(client, "PATCH", f"/Users/{people[0].id}", raw, patch_body(
        {"op": "replace", "path": "active", "value": True}))
    assert resp.status_code == 200
    db_session.refresh(people[0])
    assert people[0].deactivated_at is None


def test_d02_a_read_only_reactivation_on_a_full_editors_plan_succeeds(client, db_session, make_org):
    from app.models.org_role import OrgRole
    from app.services import provisioning_service

    org = make_org("d02d")
    _plan(db_session, org, "team", 1)
    reader = make_user(db_session, org, "reader")
    db_session.add(OrgRole(organization_id=org.id, user_id=reader.id, role="viewer"))
    db_session.flush()
    make_user(db_session, org, "editor")  # the one editor seat
    provisioning_service.deactivate_user(reader, reason="leaver_deprovisioned", actor="test")
    _row, raw = issue_token(db_session, org)

    resp = call(client, "PATCH", f"/Users/{reader.id}", raw, patch_body(
        {"op": "replace", "path": "active", "value": True}))
    assert resp.status_code == 200
    db_session.refresh(reader)
    assert reader.deactivated_at is None


# ---------------------------------------------------------------------------
# D-03 and F-04: both global SSO callbacks provision through the one service
# and gate an administrator on MFA
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, userinfo):
        self._userinfo = userinfo

    def authorize_access_token(self):
        return {"userinfo": self._userinfo}

    def userinfo(self):
        return self._userinfo


class _FakeOAuth:
    def __init__(self, userinfo):
        self._client = _FakeClient(userinfo)

    def create_client(self, _provider):
        return self._client


def _callbacks():
    from app.modules.account.routes import account_routes as v1
    from app.modules.account.v2.routes import account_routes as v2

    return [("v1", v1, "sso_callback"), ("v2", v2, "sso_callback")]


def _run_callback(app, monkeypatch, module, userinfo, provider="google"):
    """Run one global SSO callback; returns ``(response, session snapshot)``."""
    from flask import session

    monkeypatch.setattr(module, "_sso_enabled", lambda: True)
    monkeypatch.setattr(module, "_get_sso_oauth", lambda: _FakeOAuth(userinfo))
    # The v2 callback's audit call is owned by the separate hotfix; keep it out of these tests.
    monkeypatch.setattr(module.audit_logger, "log", lambda *a, **k: None, raising=False)
    view = module.sso_callback
    view = getattr(view, "__wrapped__", view)
    with app.test_request_context(f"/account/sso/callback/{provider}?state=s1"):
        session["sso_state"] = "s1"
        resp = view(provider)
        return resp, dict(session)


@pytest.mark.parametrize("label", ["v1", "v2"])
def test_f04_an_administrator_through_a_global_callback_is_sent_to_mfa(
    app, db_session, make_org, monkeypatch, label
):
    from app.models.user_session import UserSession

    module = {name: mod for name, mod, _ in _callbacks()}[label]
    org = make_org(f"f04{label}")
    give_unlimited_plan(db_session, org)
    admin = make_user(db_session, org, "ssoadmin", administrator=True)
    userinfo = {"email": admin.email, "sub": f"sub-{uuid.uuid4().hex[:6]}",
                "given_name": "A", "family_name": "B"}

    resp, snap = _run_callback(app, monkeypatch, module, userinfo)

    assert resp.status_code == 302 and "mfa" in resp.headers["Location"]
    assert snap.get("_mfa_pending_user_id") == admin.id
    assert snap.get("_mfa_pending_remember") is (label == "v1")
    assert "_user_id" not in snap
    assert UserSession.query.filter_by(user_id=admin.id).count() == 0


@pytest.mark.parametrize("label", ["v1", "v2"])
def test_f04_a_non_administrator_through_a_global_callback_lands_signed_in(
    app, db_session, make_org, monkeypatch, label
):
    from app.models.user_session import UserSession

    module = {name: mod for name, mod, _ in _callbacks()}[label]
    org = make_org(f"f04n{label}")
    give_unlimited_plan(db_session, org)
    member = make_user(db_session, org, "ssomember")
    userinfo = {"email": member.email, "sub": f"sub-{uuid.uuid4().hex[:6]}"}

    resp, snap = _run_callback(app, monkeypatch, module, userinfo)

    assert resp.status_code == 302 and "mfa" not in resp.headers["Location"]
    assert snap.get("_user_id") == str(member.id)
    assert UserSession.query.filter_by(user_id=member.id).count() == 1


@pytest.mark.parametrize("label", ["v1", "v2"])
def test_d03_a_global_callback_never_replaces_a_set_external_id(
    app, db_session, make_org, monkeypatch, label
):
    module = {name: mod for name, mod, _ in _callbacks()}[label]
    org = make_org(f"d03{label}")
    give_unlimited_plan(db_session, org)
    member = make_user(db_session, org, "scimid", external_id="scim-set-id", sso_provider="entra")
    userinfo = {"email": member.email, "sub": "other-sub"}

    resp, snap = _run_callback(app, monkeypatch, module, userinfo)

    assert snap.get("_user_id") == str(member.id)
    db_session.refresh(member)
    assert member.external_id == "scim-set-id"
    assert member.sso_provider == "entra"


@pytest.mark.parametrize("label", ["v1", "v2"])
def test_d03_a_global_callback_links_an_unlinked_user_and_creates_a_new_one(
    app, db_session, make_org, monkeypatch, label
):
    from app.models.user import User

    module = {name: mod for name, mod, _ in _callbacks()}[label]
    org = make_org(f"d03l{label}")
    give_unlimited_plan(db_session, org)
    member = make_user(db_session, org, "unlinked")
    sub = f"sub-{uuid.uuid4().hex[:6]}"
    _run_callback(app, monkeypatch, module, {"email": member.email, "sub": sub})
    db_session.refresh(member)
    assert member.external_id == sub and member.sso_provider == "google"

    fresh_email = f"fresh-{uuid.uuid4().hex[:8]}@example.com"
    fresh_sub = f"sub-{uuid.uuid4().hex[:6]}"
    _resp, snap = _run_callback(app, monkeypatch, module, {
        "email": fresh_email, "sub": fresh_sub, "given_name": "Fresh", "family_name": "Face"})
    created = User.find_by_email(fresh_email)
    assert created is not None and created.confirmed
    assert created.external_id == fresh_sub and created.sso_provider == "google"
    assert snap.get("_user_id") == str(created.id)


def test_d03_neither_global_callback_constructs_a_user():
    import inspect

    for _name, module, view_name in _callbacks():
        source = inspect.getsource(getattr(module, view_name))
        assert "User(" not in source


# ---------------------------------------------------------------------------
# D-04: one duplicate-owner rule
# ---------------------------------------------------------------------------


def test_d04_the_capability_writer_uses_the_models_duplicate_rule(db_session, make_org, monkeypatch):
    from app.models.application_owner import ApplicationOwner
    from app.models.unified_capability import UnifiedCapability
    from app.services import capability_ownership_service as service

    org = make_org("d04")
    give_unlimited_plan(db_session, org)
    user = make_user(db_session, org, "capowner")
    capability = UnifiedCapability(name=f"Cap {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(capability)
    db_session.flush()

    first = service.set_capability_owner(
        capability_id=capability.id, user_id=user.id, organization_id=org.id)
    calls = []
    real = ApplicationOwner.find_duplicate.__func__

    def spy(cls, *args, **kwargs):
        calls.append((args, kwargs))
        return real(cls, *args, **kwargs)

    monkeypatch.setattr(ApplicationOwner, "find_duplicate", classmethod(spy))
    second = service.set_capability_owner(
        capability_id=capability.id, user_id=user.id, organization_id=org.id)
    assert second.id == first.id
    assert calls, "the capability writer did not use ApplicationOwner.find_duplicate"


def test_d04_the_service_has_no_own_duplicate_filter():
    import inspect

    from app.services import capability_ownership_service as service

    assert "ApplicationOwner.query.filter" not in inspect.getsource(service.set_capability_owner)


# ---------------------------------------------------------------------------
# D-05: paging bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/Users", "/Groups"])
@pytest.mark.parametrize("query", [
    "startIndex=99999999999", "count=99999999999", "startIndex=abc", "count=x1",
])
def test_d05_out_of_range_paging_answers_400(client, db_session, make_org, path, query):
    org = make_org("d05a")
    give_unlimited_plan(db_session, org)
    _row, raw = issue_token(db_session, org)
    resp = call(client, "GET", f"{path}?{query}", raw)
    assert resp.status_code == 400
    assert resp.get_json()["scimType"] == "invalidValue"


def test_d05_start_index_below_one_is_one_and_beyond_the_total_is_empty_without_an_offset_query(
    app, client, db_session, make_org
):
    from sqlalchemy import event

    from app import db

    org = make_org("d05b")
    give_unlimited_plan(db_session, org)
    make_user(db_session, org, "one")
    _row, raw = issue_token(db_session, org)

    resp = call(client, "GET", "/Users?startIndex=-5", raw)
    assert resp.status_code == 200
    assert resp.get_json()["startIndex"] == 1 and resp.get_json()["Resources"]

    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        resp = call(client, "GET", "/Users?startIndex=1000", raw)
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
    assert resp.status_code == 200
    assert resp.get_json()["Resources"] == [] and resp.get_json()["totalResults"] == 1
    assert not [s for s in statements if "OFFSET" in s.upper()]

    resp = call(client, "GET", "/Groups?startIndex=50", raw)
    assert resp.status_code == 200 and resp.get_json()["Resources"] == []


# ---------------------------------------------------------------------------
# D-06: a filtered member path touches only that member
# ---------------------------------------------------------------------------


def test_d06_replace_on_a_filtered_member_path_swaps_only_that_member(client, db_session, make_org):
    org = make_org("d06")
    give_unlimited_plan(db_session, org)
    users = [make_user(db_session, org, f"m{i}") for i in range(4)]
    _row, raw = issue_token(db_session, org)
    group = call(client, "POST", "/Groups", raw, {
        "schemas": [GROUP_SCHEMA], "displayName": "d06 group",
        "members": [{"value": str(u.id)} for u in users[:3]],
    }).get_json()

    resp = call(client, "PATCH", f"/Groups/{group['id']}", raw, patch_body({
        "op": "replace", "path": f'members[value eq "{users[0].id}"]',
        "value": [{"value": str(users[3].id)}],
    }))
    assert resp.status_code == 200
    held = {m["value"] for m in resp.get_json()["members"]}
    assert held == {str(users[1].id), str(users[2].id), str(users[3].id)}


def test_d06_filtered_remove_add_and_replace_without_a_value(client, db_session, make_org):
    org = make_org("d06b")
    give_unlimited_plan(db_session, org)
    users = [make_user(db_session, org, f"n{i}") for i in range(3)]
    _row, raw = issue_token(db_session, org)
    group = call(client, "POST", "/Groups", raw, {
        "schemas": [GROUP_SCHEMA], "displayName": "d06b group",
        "members": [{"value": str(u.id)} for u in users[:2]],
    }).get_json()
    path = f"/Groups/{group['id']}"

    resp = call(client, "PATCH", path, raw, patch_body(
        {"op": "remove", "path": f'members[value eq "{users[0].id}"]'}))
    assert {m["value"] for m in resp.get_json()["members"]} == {str(users[1].id)}
    resp = call(client, "PATCH", path, raw, patch_body(
        {"op": "add", "path": f'members[value eq "{users[2].id}"]'}))
    assert {m["value"] for m in resp.get_json()["members"]} == {str(users[1].id), str(users[2].id)}
    resp = call(client, "PATCH", path, raw, patch_body(
        {"op": "replace", "path": f'members[value eq "{users[1].id}"]'}))
    assert resp.status_code == 400 and resp.get_json()["scimType"] == "noTarget"
    resp = call(client, "GET", path, raw)
    assert {m["value"] for m in resp.get_json()["members"]} == {str(users[1].id), str(users[2].id)}


# ---------------------------------------------------------------------------
# D-07: no password oracle on a deactivated account
# ---------------------------------------------------------------------------


def _strip(body):
    body = re.sub(r'(name="csrf_token"[^>]*value=")[^"]*', r"\1", body)
    body = re.sub(r'nonce="[^"]*"', 'nonce=""', body)
    return re.sub(r'csrf_token" value="[^"]*', 'csrf_token" value="', body)


def _password_user(db_session, org, label, *, administrator=False, mfa=False):
    user = make_user(db_session, org, label, administrator=administrator)
    user.password = _PASSWORD
    if mfa:
        user.mfa_enabled = True
        user.mfa_secret = "JBSWY3DPEHPK3PXP"
    db_session.flush()
    return user


def _v2_login(client, email, password):
    resp = client.post("/account/login", data={"email": email, "password": password, "remember_me": ""})
    return resp.status_code, _strip(resp.get_data(as_text=True)), resp.headers.get("Location")


def _v1_login(app, email, password):
    from app.modules.account.routes import account_routes as v1

    with app.test_request_context(
        "/account/login", method="POST",
        data={"email": email, "password": password, "remember_me": ""},
    ):
        view = getattr(v1.login, "__wrapped__", v1.login)
        resp = app.make_response(view())
        return resp.status_code, _strip(resp.get_data(as_text=True)), resp.headers.get("Location")


def _api_login(client, email, password):
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    return resp.status_code, resp.get_data(as_text=True)


def test_d07_form_logins_answer_a_deactivated_user_exactly_like_a_wrong_password(
    app, client, db_session, make_org
):
    from app.services import provisioning_service

    org = make_org("d07")
    give_unlimited_plan(db_session, org)
    leaver = _password_user(db_session, org, "leaver")
    provisioning_service.deactivate_user(leaver, reason="leaver_deprovisioned", actor="test")

    for runner in (lambda pw: _v2_login(client, leaver.email, pw),
                   lambda pw: _v1_login(app, leaver.email, pw)):
        right, wrong = runner(_PASSWORD), runner("not-the-password")
        assert right[0] == wrong[0]
        assert right[1] == wrong[1]
        assert right[2] == wrong[2]
        assert "Invalid email or password." in right[1]
        assert "not active" not in right[1]


def test_d07_api_login_answers_a_deactivated_user_exactly_like_a_wrong_password(
    client, db_session, make_org
):
    from app.services import provisioning_service

    org = make_org("d07api")
    give_unlimited_plan(db_session, org)
    leaver = _password_user(db_session, org, "apileaver")
    provisioning_service.deactivate_user(leaver, reason="leaver_deprovisioned", actor="test")

    right = _api_login(client, leaver.email, _PASSWORD)
    wrong = _api_login(client, leaver.email, "not-the-password")
    assert right == wrong
    assert right[0] == 401


def test_d07_a_deactivated_administrator_with_mfa_never_reaches_the_challenge(
    app, client, db_session, make_org
):
    from flask import session

    from app.services import provisioning_service

    org = make_org("d07mfa")
    give_unlimited_plan(db_session, org)
    admin = _password_user(db_session, org, "leaveradmin", administrator=True, mfa=True)
    provisioning_service.deactivate_user(admin, reason="leaver_deprovisioned", actor="test")

    status, _body, location = _v2_login(client, admin.email, _PASSWORD)
    assert status == 200 and not location
    with client.session_transaction() as sess:
        assert "_mfa_pending_user_id" not in sess

    from app.modules.account.routes import account_routes as v1

    with app.test_request_context(
        "/account/login", method="POST",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
    ):
        resp = app.make_response(getattr(v1.login, "__wrapped__", v1.login)())
        assert resp.status_code == 200
        assert "_mfa_pending_user_id" not in session


# ---------------------------------------------------------------------------
# D-08: no password reset for a deactivated user
# ---------------------------------------------------------------------------


def test_d08_no_reset_token_is_issued_for_a_deactivated_user(app, db_session, make_org, monkeypatch):
    from app import flask_email
    from app.models.account_token import AccountToken
    from app.modules.account.services.account_service import AccountService
    from app.services import provisioning_service

    monkeypatch.setattr(flask_email, "mail_available", lambda: True)
    sent = []
    monkeypatch.setattr(flask_email, "deliver_email_after_response", lambda **kw: sent.append(kw))
    org = make_org("d08a")
    give_unlimited_plan(db_session, org)
    active = _password_user(db_session, org, "stays")
    leaver = _password_user(db_session, org, "goes")
    provisioning_service.deactivate_user(leaver, reason="leaver_deprovisioned", actor="test")

    with app.test_request_context("/"):
        assert AccountService.request_password_reset(leaver.email) == "requested"
        assert AccountService.request_password_reset(active.email) == "requested"
    assert AccountToken.query.filter_by(user_id=leaver.id).count() == 0
    assert AccountToken.query.filter_by(user_id=active.id).count() == 1
    assert len(sent) == 1


def test_d08_a_reset_token_of_a_deactivated_user_is_invalid_and_changes_nothing(db_session, make_org):
    from app.models.account_token import PURPOSE_PASSWORD_RESET, AccountToken
    from app.modules.account.services.account_service import AccountService
    from app.services import provisioning_service

    org = make_org("d08b")
    give_unlimited_plan(db_session, org)
    leaver = _password_user(db_session, org, "tokenleaver")
    provisioning_service.deactivate_user(leaver, reason="leaver_deprovisioned", actor="test")
    _row, raw = AccountToken.issue(leaver, PURPOSE_PASSWORD_RESET)
    db_session.commit()

    assert AccountService.reset_link_usable(raw) is False
    ok, message = AccountService.reset_password(raw, "An0ther!Passw0rd")
    assert ok is False and "expired" in message
    db_session.refresh(leaver)
    assert leaver.verify_password(_PASSWORD)
    assert not leaver.verify_password("An0ther!Passw0rd")


# ---------------------------------------------------------------------------
# D-09: no ownership to a deactivated user
# ---------------------------------------------------------------------------


def test_d09_add_owner_refuses_a_deactivated_target_like_a_non_member(client, login_as, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.services import provisioning_service

    org = make_org("d09a")
    give_unlimited_plan(db_session, org)
    admin = make_user(db_session, org, "owneradmin", administrator=True)
    leaver = make_user(db_session, org, "ownerleaver")
    outsider_org = make_org("d09out")
    outsider = make_user(db_session, outsider_org, "outsider")
    app_row = ApplicationComponent(name="Ledger", organization_id=org.id)
    db_session.add(app_row)
    db_session.flush()
    provisioning_service.deactivate_user(leaver, reason="leaver_deprovisioned", actor="test")

    login_as(client, admin)
    path = f"/applications/{app_row.id}/owners"
    gone = client.post(path, json={"user_id": leaver.id, "ownership_type": "primary"})
    never = client.post(path, json={"user_id": outsider.id, "ownership_type": "primary"})
    assert gone.status_code == 404
    assert gone.get_json() == never.get_json()


def test_d09_the_capability_writer_refuses_a_deactivated_user(db_session, make_org):
    from app.models.unified_capability import UnifiedCapability
    from app.services import capability_ownership_service as service
    from app.services import provisioning_service

    org = make_org("d09b")
    give_unlimited_plan(db_session, org)
    leaver = make_user(db_session, org, "capleaver")
    capability = UnifiedCapability(name=f"Cap {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(capability)
    db_session.flush()
    provisioning_service.deactivate_user(leaver, reason="leaver_deprovisioned", actor="test")

    with pytest.raises(service.CrossOrganisationCapabilityOwner) as refused:
        service.set_capability_owner(
            capability_id=capability.id, user_id=leaver.id, organization_id=org.id)
    assert "does not belong to this organisation" in str(refused.value)


# ---------------------------------------------------------------------------
# D-11: SCIM throttles in shared storage
# ---------------------------------------------------------------------------


@pytest.fixture
def throttling_on(app):
    from app._bootstrap import rate_limiting

    if rate_limiting.limiter is None:
        pytest.skip("rate limiter not installed")
    previous = app.config.get("RATE_LIMITING_ENABLED")
    app.config["RATE_LIMITING_ENABLED"] = True
    rate_limiting.limiter.limiter.storage.reset()
    yield rate_limiting.limiter
    rate_limiting.limiter.limiter.storage.reset()
    app.config["RATE_LIMITING_ENABLED"] = previous


def test_d11_the_token_throttle_counts_in_the_limiter_storage(client, db_session, make_org, throttling_on):
    from limits import parse

    org = make_org("d11a")
    give_unlimited_plan(db_session, org)
    row, raw = issue_token(db_session, org)

    for _ in range(3):
        assert call(client, "GET", "/ServiceProviderConfig", raw).status_code == 200

    item = parse("600 per minute")
    strategy = throttling_on.limiter
    assert strategy.get_window_stats(item, "app-rate-limit", f"scim:{row.id}").remaining == 597
    strategy.storage.reset()
    assert strategy.get_window_stats(item, "app-rate-limit", f"scim:{row.id}").remaining == 600


def test_d11_failed_authentications_are_counted_per_forwarded_address(app, db_session, throttling_on):
    from werkzeug.middleware.proxy_fix import ProxyFix

    original = app.wsgi_app
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
    try:
        client = app.test_client()
        offender = {"X-Forwarded-For": "203.0.113.5"}
        statuses = [
            call(client, "GET", "/Users", "scim_wrong", headers=offender).status_code for _ in range(21)
        ]
        assert statuses[:20] == [401] * 20 and statuses[20] == 429
        other = call(client, "GET", "/Users", "scim_wrong", headers={"X-Forwarded-For": "203.0.113.6"})
        assert other.status_code == 401
    finally:
        app.wsgi_app = original


# ---------------------------------------------------------------------------
# D-12: SCIM meta timestamps
# ---------------------------------------------------------------------------


def test_d12_user_resources_carry_created_and_last_modified(client, db_session, make_org):
    org = make_org("d12a")
    give_unlimited_plan(db_session, org)
    _row, raw = issue_token(db_session, org)

    created = call(client, "POST", "/Users", raw, user_body()).get_json()
    meta = created["meta"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", meta["created"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", meta["lastModified"])

    from datetime import datetime, timedelta

    from app.models.user import User

    user = User.query.filter(User.id == int(created["id"]), User.organization_id == org.id).first()
    user.updated_at = datetime.utcnow() - timedelta(hours=1)
    db_session.flush()
    before = call(client, "GET", f"/Users/{created['id']}", raw).get_json()["meta"]["lastModified"]
    resp = call(client, "PATCH", f"/Users/{created['id']}", raw, patch_body(
        {"op": "replace", "path": "name.givenName", "value": "Changed"}))
    assert resp.get_json()["meta"]["lastModified"] > before


def test_d12_a_null_timestamp_omits_its_key(client, db_session, make_org):
    org = make_org("d12b")
    give_unlimited_plan(db_session, org)
    old = make_user(db_session, org, "oldtimer")
    db_session.execute(text("UPDATE users SET created_at = NULL, updated_at = NULL WHERE id = :i"),
                       {"i": old.id})
    db_session.expire(old)
    _row, raw = issue_token(db_session, org)
    meta = call(client, "GET", f"/Users/{old.id}", raw).get_json()["meta"]
    assert "created" not in meta and "lastModified" not in meta


def test_d12_a_group_membership_change_moves_the_groups_last_modified(client, db_session, make_org):
    from datetime import datetime, timedelta

    from app.models.miscellaneous import SSOGroupRoleMapping

    org = make_org("d12c")
    give_unlimited_plan(db_session, org)
    member = make_user(db_session, org, "grpmember")
    _row, raw = issue_token(db_session, org)
    group = call(client, "POST", "/Groups", raw, {"schemas": [GROUP_SCHEMA], "displayName": "d12 group"}).get_json()
    assert group["meta"]["created"] and group["meta"]["lastModified"]

    mapping = SSOGroupRoleMapping.query.filter(
        SSOGroupRoleMapping.id == int(group["id"]), SSOGroupRoleMapping.organization_id == org.id).first()
    mapping.updated_at = datetime.utcnow() - timedelta(hours=1)
    db_session.flush()
    before = call(client, "GET", f"/Groups/{group['id']}", raw).get_json()["meta"]["lastModified"]
    resp = call(client, "PATCH", f"/Groups/{group['id']}", raw, patch_body(
        {"op": "add", "path": "members", "value": [{"value": str(member.id)}]}))
    assert resp.status_code == 200
    assert resp.get_json()["meta"]["lastModified"] > before
