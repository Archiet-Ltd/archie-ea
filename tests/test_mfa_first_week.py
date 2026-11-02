"""A new workspace's sole owner may start without two-factor sign-in for up to
seven days; every other administrator is held to today's rule.

The first-week window is one question answered by
``mfa_service.may_defer_enrolment``, computed from the database on every call.
Each test builds two real organisations: the one under test and a second that
holds members, invitations and credentials of its own, which must never change
the answer for the first.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pyotp
import pytest

from app.services import mfa_service

PASSWORD = "Str0ng!Passw0rd"

pytestmark = [pytest.mark.usefixtures("db_session"), pytest.mark.mfa_first_week]


def _user(db_session, org, *, admin=True, signed_up=True, age_days=0, platform=False):
    from app.models import Role
    from app.models.org_role import OrgRole
    from app.models.user import User

    Role.insert_roles()
    user = User(
        first_name="Test",
        last_name="Owner",
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
        confirmed=True,
        organization_id=org.id,
        role=Role.query.filter_by(name="Administrator").first() if admin else None,
        is_platform_admin=platform,
    )
    if signed_up:
        user.signup_at = datetime.utcnow() - timedelta(days=age_days)
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin" if admin else "viewer", granted_by_id=user.id)
    db_session.commit()
    return user


def _owner(db_session, make_org, **kwargs):
    org = make_org("first-week")
    return org, _user(db_session, org, **kwargs)


def _other_workspace(db_session, make_org):
    """A second, busy workspace: members, an invitation, a token, a write connection."""
    from app.models.connector_config import ConnectorConfig
    from app.models.pending_invitation import PendingInvitation
    from app.models.sso_config import SSOConfig
    from app.modules.oauth_provider.models import OAuthToken

    org = make_org("busy")
    first = _user(db_session, org)
    second = _user(db_session, org, admin=False, signed_up=False)
    db_session.add(PendingInvitation(organization_id=org.id, user_id=second.id, role="viewer", invited_by=first.id))
    db_session.add(OAuthToken(
        client_id="cl-other", user_id=first.id, access_token=uuid.uuid4().hex,
        expires_at=datetime.utcnow() + timedelta(days=1), organization_id=org.id,
    ))
    db_session.add(ConnectorConfig(organization_id=org.id, connector_type="jira", name="Jira", config={}))
    db_session.add(SSOConfig(organization_id=org.id, protocol="oidc", enabled=True))
    org.settings = {mfa_service.REQUIRE_SETTING: True, "connector_action_delegation": {"create": True}}
    db_session.commit()
    return org


def test_window_open_for_a_sole_owner_on_day_one(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    _other_workspace(db_session, make_org)
    assert mfa_service.required_for(owner)
    assert mfa_service.may_defer_enrolment(owner)
    assert mfa_service.window_days_left(owner) == 7
    assert mfa_service.window_closed_reason(owner) is None


def test_window_closed_on_day_eight(db_session, make_org):
    _org, owner = _owner(db_session, make_org, age_days=8)
    _other_workspace(db_session, make_org)
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.must_enrol_now(owner)
    assert mfa_service.window_closed_reason(owner) == "Your first week is over."


def test_window_is_open_at_six_days_twenty_three_hours_and_shut_at_seven_days(db_session, make_org, monkeypatch):
    _org, owner = _owner(db_session, make_org)
    signed_up = owner.signup_at
    monkeypatch.setattr(mfa_service, "_utcnow", lambda: signed_up + timedelta(days=6, hours=23))
    assert mfa_service.may_defer_enrolment(owner)
    monkeypatch.setattr(mfa_service, "_utcnow", lambda: signed_up + timedelta(days=7) - timedelta(microseconds=1))
    assert mfa_service.may_defer_enrolment(owner)
    monkeypatch.setattr(mfa_service, "_utcnow", lambda: signed_up + timedelta(days=7))
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.window_closed_reason(owner) == "Your first week is over."


def test_window_does_not_reopen_if_two_factor_is_switched_off_after_enrolment(db_session, make_org):
    _org, owner = _owner(db_session, make_org)
    secret = pyotp.random_base32()
    mfa_service.enroll(owner, secret, pyotp.TOTP(secret).now())
    assert owner.mfa_enrolled_at is not None
    owner.mfa_enabled = False
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.must_enrol_now(owner)


def test_enrolled_owner_is_never_in_the_window(db_session, make_org):
    _org, owner = _owner(db_session, make_org)
    owner.mfa_enabled, owner.mfa_secret = True, pyotp.random_base32()
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    assert not mfa_service.must_enrol_now(owner)


def test_window_stays_open_when_a_second_member_is_added_by_another_route(db_session, make_org):
    from app.models.org_role import OrgRole

    org, owner = _owner(db_session, make_org)
    _other_workspace(db_session, make_org)
    # An existing account from elsewhere is given a role here directly (not via the invite form).
    outsider = _user(db_session, make_org("elsewhere"), admin=False, signed_up=False)
    OrgRole.set_role(org.id, outsider.id, "viewer", granted_by_id=owner.id)
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)
    assert mfa_service.window_closed_reason(owner) is None


def test_window_stays_open_when_a_member_is_created_in_the_organisation(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    member = _user(db_session, org, admin=False, signed_up=False)
    assert mfa_service.may_defer_enrolment(owner)
    assert mfa_service.may_defer_enrolment(member)


def test_an_open_invitation_does_not_shorten_the_window(db_session, make_org):
    from app.models.pending_invitation import PendingInvitation

    org, owner = _owner(db_session, make_org)
    invitee = _user(db_session, make_org("invitee"), admin=False, signed_up=False)
    invitation = PendingInvitation(organization_id=org.id, user_id=invitee.id, role="viewer", invited_by=owner.id)
    db_session.add(invitation)
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)
    invitation.expires_at = datetime.utcnow() - timedelta(hours=1)
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_an_oauth_token_does_not_shorten_the_window(db_session, make_org):
    from app.modules.oauth_provider.models import OAuthToken

    org, owner = _owner(db_session, make_org)
    token = OAuthToken(
        client_id="cl-1", user_id=owner.id, access_token=uuid.uuid4().hex,
        expires_at=datetime.utcnow() + timedelta(days=1), organization_id=org.id,
    )
    db_session.add(token)
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)
    token.revoked_at = datetime.utcnow()
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_a_service_account_does_not_shorten_the_window_and_has_none_of_its_own(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    service = _user(db_session, org, admin=False, signed_up=False)
    service.is_service_user = True
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)
    assert not mfa_service.required_for(service)


def test_connector_delegation_does_not_shorten_the_window(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    org.settings = {"connector_action_delegation": {"sync": True}}
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)
    org.settings = {"connector_action_delegation": {"sync": False}}
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_a_write_scope_connection_does_not_shorten_the_window(db_session, make_org):
    from app.models.connector_config import ConnectorConfig

    org, owner = _owner(db_session, make_org)
    db_session.add(ConnectorConfig(organization_id=org.id, connector_type="jira", name="Jira", config={}))
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_a_read_only_connection_does_not_close_the_window(db_session, make_org):
    from app.models.connector_config import ConnectorConfig

    org, owner = _owner(db_session, make_org)
    db_session.add(ConnectorConfig(organization_id=org.id, connector_type="github", name="GitHub", config={}))
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_a_write_connection_of_another_organisation_does_not_close_this_window(db_session, make_org):
    from app.models.connector_config import ConnectorConfig

    _org, owner = _owner(db_session, make_org)
    other = make_org("other-connector")
    db_session.add(ConnectorConfig(organization_id=other.id, connector_type="m365", name="M365", config={}))
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_single_sign_on_does_not_shorten_the_window(db_session, make_org):
    from app.models.sso_config import SSOConfig

    org, owner = _owner(db_session, make_org)
    db_session.add(SSOConfig(organization_id=org.id, protocol="oidc", enabled=False))
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_require_two_factor_ends_the_allowance_of_its_administrators(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    assert mfa_service.may_defer_enrolment(owner)
    org.settings = {mfa_service.REQUIRE_SETTING: True}
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.must_enrol_now(owner)
    assert mfa_service.window_closed_reason(owner) == "Your organisation requires two-factor sign-in."
    org.settings = {}
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)
    assert mfa_service.window_closed_reason(owner) is None


def test_require_two_factor_leaves_a_member_and_another_organisation_alone(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    member = _user(db_session, org, admin=False)
    other_org, other_owner = _owner(db_session, make_org)
    org.settings = {mfa_service.REQUIRE_SETTING: True}
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.may_defer_enrolment(member)
    assert mfa_service.may_defer_enrolment(other_owner)


def test_start_window_if_due_commits_nothing_else(db_session, make_org):
    org, owner = _owner(db_session, make_org, signed_up=False)
    db_session.refresh(owner)
    db_session.refresh(org)
    org.name = "Renamed and not yet saved"
    mfa_service.start_window_if_due(owner)
    assert owner.mfa_window_started_at is not None
    # The rename is still waiting in the request's session: the stamp did not save it.
    assert org in db_session.dirty
    assert db_session.is_modified(org)


def test_a_platform_administrator_has_the_same_window(db_session, make_org):
    _org, owner = _owner(db_session, make_org, platform=True)
    assert mfa_service.required_for(owner)
    assert mfa_service.may_defer_enrolment(owner)
    owner.signup_at = datetime.utcnow() - timedelta(days=8)
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)


def test_an_owner_who_administers_another_organisation_has_the_same_window(db_session, make_org):
    from app.models.org_role import OrgRole

    _org, owner = _owner(db_session, make_org)
    other = make_org("also-admin")
    OrgRole.set_role(other.id, owner.id, "org_admin", granted_by_id=owner.id)
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_an_account_with_no_sign_up_record_starts_its_week_at_its_first_sign_in(db_session, make_org):
    _org, owner = _owner(db_session, make_org, signed_up=False)
    assert mfa_service.required_for(owner)
    assert mfa_service.may_defer_enrolment(owner)
    mfa_service.start_window_if_due(owner)
    assert owner.mfa_window_started_at is not None
    started = owner.mfa_window_started_at
    mfa_service.start_window_if_due(owner)
    assert owner.mfa_window_started_at == started
    assert mfa_service.window_days_left(owner) == 7
    owner.mfa_window_started_at = datetime.utcnow() - timedelta(days=8)
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.must_enrol_now(owner)


def test_the_window_in_one_organisation_is_not_changed_by_another(db_session, make_org):
    _org_a, owner_a = _owner(db_session, make_org)
    _org_b, owner_b = _owner(db_session, make_org, age_days=8)
    assert mfa_service.may_defer_enrolment(owner_a) and not mfa_service.may_defer_enrolment(owner_b)
    _other_workspace(db_session, make_org)
    _user(db_session, _org_b, admin=False, signed_up=False)
    assert mfa_service.may_defer_enrolment(owner_a)
    assert not mfa_service.may_defer_enrolment(owner_b)


def test_nothing_in_the_request_or_session_opens_the_window(app, db_session, make_org, login_as):
    """A closed window stays closed whatever the caller sends."""
    _org, owner = _owner(db_session, make_org, age_days=8)
    client = app.test_client()
    login_as(client, owner)
    with client.session_transaction() as sess:
        sess["_mfa_window_open"] = True
    resp = client.get("/dashboard/overview?mfa_window=1", headers={"X-Mfa-Window": "open"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-enrol")


# -- sign-in and signed-in requests ------------------------------------------------------------


def test_sign_in_finishes_without_a_code_only_while_the_window_is_open(app, db_session, make_org):
    _org, owner = _owner(db_session, make_org)
    resp = app.test_client().post("/account/login", data={"email": owner.email, "password": PASSWORD, "remember_me": ""})
    assert resp.status_code == 302 and "/mfa-challenge" not in resp.headers["Location"]

    from flask import g

    g.pop("_login_user", None)  # the shared test context keeps the first sign-in's user
    owner.signup_at = datetime.utcnow() - timedelta(days=8)
    db_session.commit()
    resp = app.test_client().post("/account/login", data={"email": owner.email, "password": PASSWORD, "remember_me": ""})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-challenge")


def test_an_enrolled_owner_is_challenged_at_every_sign_in(app, db_session, make_org):
    _org, owner = _owner(db_session, make_org)
    owner.mfa_enabled, owner.mfa_secret = True, pyotp.random_base32()
    db_session.commit()
    resp = app.test_client().post("/account/login", data={"email": owner.email, "password": PASSWORD, "remember_me": ""})
    assert resp.headers["Location"].endswith("/account/mfa-challenge")


def test_a_signed_in_owner_is_sent_to_enrol_when_the_window_closes(app, db_session, make_org, login_as):
    org, owner = _owner(db_session, make_org)
    client = app.test_client()
    login_as(client, owner)
    assert client.get("/dashboard/overview").status_code == 200

    owner.signup_at = datetime.utcnow() - timedelta(days=8)
    db_session.commit()
    login_as(client, owner)
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-enrol")
    page = client.get("/account/mfa-enrol")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Your first week is over." in text
    assert "Remind me later" not in text


def test_a_remember_me_session_is_sent_to_enrol_on_day_eight(app, db_session, make_org):
    _org, owner = _owner(db_session, make_org, age_days=8)
    client = app.test_client()
    client.set_cookie("remember_token", f"{owner.id}|x", domain="localhost")
    # The remember cookie is not what authenticates here: a session minted at sign-up is.
    from tests._session_test_helpers import mint_test_sid

    sid = mint_test_sid(owner.id, organization_id=owner.organization_id, app=app)
    with client.session_transaction() as sess:
        sess["_user_id"], sess["_fresh"], sess["_sid"], sess["_remember"] = str(owner.id), False, sid, "set"
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-enrol")


def test_json_sign_in_still_refuses_an_owner_inside_the_window(app, db_session, make_org):
    _org, owner = _owner(db_session, make_org)
    resp = app.test_client().post("/api/auth/login", json={"email": owner.email, "password": PASSWORD})
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "mfa_required"


def test_api_requests_from_a_closed_window_get_a_plain_refusal(app, db_session, make_org, login_as):
    _org, owner = _owner(db_session, make_org, age_days=8)
    client = app.test_client()
    login_as(client, owner)
    resp = client.get("/api/v1/capabilities/", headers={"Accept": "application/json"})
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "mfa_enrolment_required"


# -- the trigger routes -----------------------------------------------------------------------

TRIGGERS = [
    ("POST", "/admin/team/invite", {"email": "someone@example.com", "role": "viewer"}, "invite"),
    ("GET", "/admin/invite-user", None, "invite"),
    ("GET", "/admin/new-user", None, "invite"),
    ("POST", "/admin/new-user", {"first_name": "A", "last_name": "B", "email": "new@example.com"}, "invite"),
    ("POST", "/admin/team/invitations/1/resend", {}, "invite"),
    ("POST", "/admin/billing/change-plan", {"plan": "team", "interval": "year"}, "billing"),
    ("POST", "/admin/billing/cancel", {}, "billing"),
    ("POST", "/admin/billing/details", {"billing_email": "a@example.com"}, "billing"),
    ("GET", "/admin/billing/portal", None, "billing"),
    ("POST", "/admin/connectors", {"connector_type": "jira", "name": "Jira"}, "connector"),
    ("POST", "/admin/connectors/not-a-connector/sync", {}, "connector"),
    ("POST", "/admin/connectors/not-a-connector/delete", {}, "connector"),
    ("POST", "/admin/connectors/m365", {"tenant_id": "t", "client_id": "c"}, "connector"),
    ("POST", "/admin/connectors/jira", {"instance_url": "https://example.atlassian.net"}, "connector"),
    ("GET", "/admin/sso", None, "sso"),
    ("POST", "/admin/team/require-two-factor", {"require": "0"}, "require"),
]


@pytest.mark.parametrize("method,path,data,trigger", TRIGGERS, ids=[f"{m} {p}" for m, p, _d, _t in TRIGGERS])
def test_each_trigger_route_sends_an_unenrolled_owner_to_enrol_and_back(
    app, db_session, make_org, login_as, method, path, data, trigger
):
    org, owner = _owner(db_session, make_org)
    # Before the change an unenrolled owner reached these directly; now the window is irrelevant to them.
    client = app.test_client()
    login_as(client, owner)
    resp = client.open(path, method=method, data=data or None)
    assert resp.status_code == 302, (path, resp.status_code)
    assert resp.headers["Location"].endswith("/account/mfa-enrol"), (path, resp.headers["Location"])
    page = client.get("/account/mfa-enrol")
    assert mfa_flow_line(trigger) in page.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert sess["_mfa_return_to"].startswith("/")


def test_an_unenrolled_owner_adds_no_member_through_new_user(app, db_session, make_org, login_as):
    from app.models.user import User

    org, owner = _owner(db_session, make_org)
    client = app.test_client()
    login_as(client, owner)
    resp = client.post("/admin/new-user", data={
        "first_name": "Sam", "last_name": "Member", "email": "sam-member@example.com",
        "password": PASSWORD, "password2": PASSWORD,
    })
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-enrol")
    assert User.query.filter_by(email="sam-member@example.com").first() is None


def test_resending_an_expired_invitation_asks_an_unenrolled_owner_to_enrol(app, db_session, make_org, login_as):
    from app.models.pending_invitation import PendingInvitation

    org, owner = _owner(db_session, make_org)
    invitee = _user(db_session, make_org("invitee-home"), admin=False, signed_up=False)
    invitation = PendingInvitation(organization_id=org.id, user_id=invitee.id, role="viewer", invited_by=owner.id)
    db_session.add(invitation)
    db_session.flush()
    invitation.expires_at = datetime.utcnow() - timedelta(days=1)
    db_session.commit()
    before = invitation.token_hash
    client = app.test_client()
    login_as(client, owner)
    resp = client.post(f"/admin/team/invitations/{invitation.id}/resend")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-enrol")
    db_session.refresh(invitation)
    assert invitation.token_hash == before


READ_ONLY_ROUTES = [
    ("POST", "/admin/connectors", {"connector_type": "github"}),
    ("POST", "/admin/connectors", {"connector_type": "servicenow"}),
    ("POST", "/admin/connectors/servicenow", {"instance_url": ""}),
    ("POST", "/admin/connectors/servicenow/sync", {}),
    ("POST", "/admin/connectors/github", {"organization": "x", "api_token": ""}),
]


@pytest.mark.parametrize("method,path,data", READ_ONLY_ROUTES, ids=[f"{m} {p} {d}" for m, p, d in READ_ONLY_ROUTES])
def test_read_only_connections_do_not_ask_the_owner_to_enrol(app, db_session, make_org, login_as, method, path, data):
    _org, owner = _owner(db_session, make_org)
    client = app.test_client()
    login_as(client, owner)
    resp = client.open(path, method=method, data=data)
    assert "/account/mfa-enrol" not in (resp.headers.get("Location") or ""), path


def test_syncing_a_saved_read_only_connector_does_not_ask_to_enrol_but_a_writing_one_does(
    app, db_session, make_org, login_as
):
    from app.models.connector_config import ConnectorConfig

    org, owner = _owner(db_session, make_org)
    reader = ConnectorConfig(organization_id=org.id, connector_type="github", name="GitHub", config={})
    db_session.add(reader)
    db_session.commit()
    client = app.test_client()
    login_as(client, owner)
    read = client.post(f"/admin/connectors/{reader.id}/sync")
    assert "/account/mfa-enrol" not in (read.headers.get("Location") or "")
    writer = ConnectorConfig(organization_id=org.id, connector_type="jira", name="Jira", config={})
    db_session.add(writer)
    db_session.commit()
    write = client.post(f"/admin/connectors/{writer.id}/sync")
    assert write.status_code == 302 and write.headers["Location"].endswith("/account/mfa-enrol")


def test_an_enrolled_owner_reaches_the_trigger_routes(app, db_session, make_org, login_as):
    _org, owner = _owner(db_session, make_org)
    owner.mfa_enabled, owner.mfa_secret = True, pyotp.random_base32()
    db_session.commit()
    client = app.test_client()
    login_as(client, owner)
    resp = client.get("/admin/sso")
    assert not (resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-enrol"))


def test_turning_on_require_two_factor_takes_the_owner_to_enrol_at_once(app, db_session, make_org, login_as):
    org, owner = _owner(db_session, make_org)
    client = app.test_client()
    login_as(client, owner)
    resp = client.post("/admin/team/require-two-factor", data={"require": "1"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-enrol")
    db_session.refresh(org)
    assert org.settings[mfa_service.REQUIRE_SETTING] is True
    # Turning it on ends the owner's first-week allowance at once.
    assert not mfa_service.may_defer_enrolment(owner)


# -- enrolment from a signed-in session -------------------------------------------------------


def mfa_flow_line(trigger):
    from app.modules.account import mfa_flow

    return mfa_flow.TRIGGER_LINES[trigger].replace("'", "&#39;")


def test_enrolment_returns_the_owner_to_the_invite_form(app, db_session, make_org, login_as):
    org, owner = _owner(db_session, make_org)
    client = app.test_client()
    login_as(client, owner)
    assert client.post("/admin/team/invite", data={"email": "x@example.com"}).status_code == 302

    page = client.get("/account/mfa-enrol")
    text = page.get_data(as_text=True)
    assert "Before you invite someone, set up two-factor sign-in." in text.replace("&#39;", "'")
    assert "Remind me later (7 days left)" in text

    with client.session_transaction() as sess:
        secret = sess["_mfa_enroll_secret"]
    wrong = client.post("/account/mfa-enrol", data={"code": "000000"})
    assert wrong.status_code == 200 and "That code did not match" in wrong.get_data(as_text=True)

    codes = client.post("/account/mfa-enrol", data={"code": pyotp.TOTP(secret).now()})
    body = codes.get_data(as_text=True)
    assert body.count("<li><code") == 8
    db_session.refresh(owner)
    assert owner.mfa_enabled

    done = client.post("/account/mfa-enrol", data={"step": "codes", "recovery_confirmed": "1"})
    assert done.status_code == 302 and done.headers["Location"].endswith("/admin/team")
    form = client.get("/admin/team").get_data(as_text=True)
    assert 'name="email"' in form


def test_an_already_enrolled_person_is_not_shown_enrolment_again(app, db_session, make_org, login_as):
    _org, owner = _owner(db_session, make_org)
    owner.mfa_enabled, owner.mfa_secret = True, pyotp.random_base32()
    db_session.commit()
    client = app.test_client()
    login_as(client, owner)
    resp = client.get("/account/mfa-enrol")
    assert resp.status_code == 302 and "mfa-enrol" not in resp.headers["Location"]


def test_enrolment_needs_a_signed_in_person(app):
    resp = app.test_client().get("/account/mfa-enrol")
    assert resp.status_code == 302 and "/account/login" in resp.headers["Location"]


def test_the_first_answer_notice_shows_only_inside_the_window_with_its_date(app, db_session, make_org, login_as):
    from app.models.conversation import ConversationMessageRecord, ConversationThreadRecord

    org, owner = _owner(db_session, make_org)
    client = app.test_client()
    login_as(client, owner)
    assert 'data-testid="two-factor-notice"' not in client.get("/ai-chat/").get_data(as_text=True)

    thread = ConversationThreadRecord(id=uuid.uuid4().hex, user_id=owner.id, organization_id=org.id, title="First", model="test-model",
                                    created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db_session.add(thread)
    db_session.flush()
    db_session.add(ConversationMessageRecord(id=uuid.uuid4().hex, thread_id=thread.id, role="assistant", content="An answer.", created_at=datetime.utcnow()))
    db_session.commit()
    login_as(client, owner)
    page = client.get("/ai-chat/").get_data(as_text=True)
    ends = (owner.signup_at + timedelta(days=7)).strftime("%-d %B %Y")
    assert 'data-testid="two-factor-notice"' in page
    assert f"Required from {ends}, or before you invite your team." in page
    assert "Later (7 days left)" in page


def test_the_sign_up_page_states_the_first_week_rule(app):
    text = app.test_client().get("/account/register").get_data(as_text=True)
    assert "Your first week: you can start without a phone app." in text.replace("&#39;", "'")


# -- ServiceNow stays on the read-only list only while nothing writes to it ---------------------


def _service_now_write_methods():
    """Public methods of the ServiceNow service that send a write request to the source."""
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "app" / "services" / "servicenow_connector_service.py"
    tree = ast.parse(path.read_text())
    writes = set()
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ServiceNowConnectorService"):
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")):
            for call in ast.walk(fn):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("post", "put", "patch", "delete")
                ):
                    writes.add(fn.name)
    return writes


def _service_now_write_callers(sources, writes):
    """Every ``file:line`` in ``sources`` (a mapping of name to python text) that names one of
    ``writes`` as an attribute, in the service's own file as much as anywhere else. A method's
    own definition is not a use of it."""
    import ast

    callers = []
    for name, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute) and node.attr in writes:
                callers.append("%s:%s" % (name, node.lineno))
    return callers


def _app_sources():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    return {str(path.relative_to(root)): path.read_text() for path in root.rglob("*.py")}


def test_servicenow_is_read_only_only_while_no_write_method_has_a_caller():
    from app.services import mfa_service

    if mfa_service.connector_can_write("servicenow"):
        return  # already treated as write-capable: nothing left to guard
    writes = _service_now_write_methods()
    assert writes == {"push_arb_decision"}, (
        "A ServiceNow method that writes was added or renamed: %s. Take servicenow off "
        "READ_ONLY_CONNECTOR_TYPES in app/services/mfa_service.py." % sorted(writes)
    )
    callers = _service_now_write_callers(_app_sources(), writes)
    assert not callers, (
        "ServiceNow writes now have a caller (%s) while servicenow is on READ_ONLY_CONNECTOR_TYPES; "
        "take it off the list in app/services/mfa_service.py so its connector saves ask for enrolment." % callers
    )


def test_the_servicenow_guard_finds_a_caller_in_the_services_own_file():
    sources = _app_sources()
    key = "services/servicenow_connector_service.py"
    writes = {"push_arb_decision"}
    assert _service_now_write_callers(sources, writes) == []
    sources[key] += "\n\ndef _sneaky(service):\n    return service.push_arb_decision({})\n"
    found = _service_now_write_callers(sources, writes)
    assert len(found) == 1 and found[0].startswith(key + ":"), found


# -- the administrator check is asked once per request, not once per caller ---------------------


def test_is_administrator_asks_the_database_once_per_request_and_again_in_the_next(app, db_session, make_org, monkeypatch):
    from app.services.rbac_service import rbac_service

    _org, owner = _owner(db_session, make_org)
    asked = []
    real = rbac_service.is_org_admin_anywhere

    def counting(user):
        asked.append(user.id)
        return real(user)

    monkeypatch.setattr(rbac_service, "is_org_admin_anywhere", counting)
    # A platform administrator and an org admin are answered without that query; use a plain member.
    member = _user(db_session, make_org("member-home"), admin=False, signed_up=False)
    member.is_platform_admin = False
    db_session.commit()
    with app.test_request_context("/"):
        assert [mfa_service.is_administrator(member) for _ in range(4)] == [False] * 4
        assert len(asked) == 1
        assert mfa_service.is_administrator(owner) is True
        assert len(asked) <= 2
    with app.test_request_context("/"):
        mfa_service.is_administrator(member)
    assert len(asked) >= 2 and asked.count(member.id) == 2


def test_is_administrator_follows_a_role_change_made_between_requests(app, db_session, make_org):
    from app.models.org_role import OrgRole

    org, owner = _owner(db_session, make_org, admin=False)
    with app.test_request_context("/"):
        assert mfa_service.is_administrator(owner) is False
    OrgRole.set_role(org.id, owner.id, "org_admin", granted_by_id=owner.id)
    db_session.commit()
    with app.test_request_context("/"):
        assert mfa_service.is_administrator(owner) is True


# -- "Remind me later" uses up what the visit remembered ------------------------------------------


def test_remind_me_later_clears_what_the_visit_remembered(app, db_session, make_org, login_as):
    org, owner = _owner(db_session, make_org)
    client = app.test_client()
    login_as(client, owner)
    assert client.post("/admin/team/invite", data={"email": "x@example.com"}, headers={"Referer": "http://localhost/admin/team"}).status_code == 302
    page = client.get("/account/mfa-enrol").get_data(as_text=True)
    assert "Before you invite someone, set up two-factor sign-in." in page
    with client.session_transaction() as sess:
        assert {"_mfa_return_to", "_mfa_trigger", "_mfa_later_to"} <= set(sess)

    login_as(client, owner)
    later = client.get("/account/mfa-enrol?later=1")
    assert later.status_code == 302 and "/account/mfa-enrol" not in later.headers["Location"]
    with client.session_transaction() as sess:
        assert not {"_mfa_return_to", "_mfa_trigger", "_mfa_later_to"} & set(sess)

    login_as(client, owner)
    direct = client.get("/account/mfa-enrol").get_data(as_text=True)
    assert "Before you invite someone" not in direct
    assert "Remind me later (7 days left)" in direct


def _queries_in_one_request(app, user, checks):
    """How many statements ``checks`` sends to the database in one request, for ``user``."""
    from sqlalchemy import event

    from app import db

    sent = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        sent.append(statement)

    event.listen(db.engine, "before_cursor_execute", _count)
    try:
        with app.test_request_context("/dashboard/overview"):
            checks(user)
    finally:
        event.remove(db.engine, "before_cursor_execute", _count)
    return len(sent)


def test_the_window_is_read_once_per_request_however_often_it_is_asked(app, db_session, make_org):
    """An unenrolled owner inside the window is asked about the window by the
    request hook, the notice, the date and the days left. It is judged from the
    person's own record, so asking again adds no query."""
    org, owner = _owner(db_session, make_org)

    def ask_once(user):
        assert mfa_service.may_defer_enrolment(user)

    def ask_many(user):
        for _ in range(6):
            assert mfa_service.may_defer_enrolment(user)
            assert not mfa_service.must_enrol_now(user)
            assert mfa_service.window_closed_reason(user) is None
            assert mfa_service.window_ends_at(user) is not None
            assert mfa_service.window_days_left(user) == 7

    _queries_in_one_request(app, owner, ask_once)  # loads the person once, so only the window is counted
    once = _queries_in_one_request(app, owner, ask_once)
    many = _queries_in_one_request(app, owner, ask_many)
    assert many == once


def test_the_window_answer_of_one_request_is_not_reused_by_the_next(app, db_session, make_org):
    org, owner = _owner(db_session, make_org)
    with app.test_request_context("/"):
        assert mfa_service.may_defer_enrolment(owner)
    owner.signup_at = datetime.utcnow() - timedelta(days=8)
    db_session.commit()
    with app.test_request_context("/"):
        assert not mfa_service.may_defer_enrolment(owner)
        assert mfa_service.window_closed_reason(owner) == "Your first week is over."


# -- every person, owner or member, has the same first week -------------------------------------


def _member(db_session, org, age_days=0):
    return _user(db_session, org, admin=False, age_days=age_days)


def test_a_member_has_the_same_window_as_an_owner(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    member = _member(db_session, org)
    assert mfa_service.required_for(member)
    assert not mfa_service.is_administrator(member)
    assert mfa_service.may_defer_enrolment(member) and mfa_service.may_defer_enrolment(owner)
    assert mfa_service.window_days_left(member) == mfa_service.window_days_left(owner) == 7
    old = _member(db_session, org, age_days=8)
    assert not mfa_service.may_defer_enrolment(old)
    assert mfa_service.must_enrol_now(old)
    assert mfa_service.window_closed_reason(old) == "Your first week is over."


def test_no_message_mentions_sole_ownership():
    assert not any("sole owner" in line or "owner of a workspace" in line for line in mfa_service._WINDOW_CLOSED_REASONS.values())


def test_a_member_inside_the_window_signs_in_and_sees_the_reminder_with_the_date(app, db_session, make_org, login_as):
    org, owner = _owner(db_session, make_org)
    member = _member(db_session, org)
    client = app.test_client()
    resp = client.post("/account/login", data={"email": member.email, "password": PASSWORD, "remember_me": ""})
    assert resp.status_code == 302 and "/mfa" not in resp.headers["Location"]
    page = client.get("/dashboard/overview")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    ends = (member.signup_at + timedelta(days=7)).strftime("%-d %B %Y")
    assert 'data-testid="two-factor-banner"' in html and ends in html


def test_a_member_after_the_window_is_sent_to_set_up_before_any_page(app, db_session, make_org, login_as):
    org, _owner_user = _owner(db_session, make_org)
    member = _member(db_session, org, age_days=8)
    client = app.test_client()
    login_as(client, member)
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-enrol")
    page = client.get("/account/mfa-enrol")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Your first week is over." in html and "sole owner" not in html


def test_an_account_from_before_the_rule_gets_its_week_at_the_first_request(app, db_session, make_org, login_as):
    org, _owner_user = _owner(db_session, make_org)
    member = _member(db_session, org)
    member.signup_at = None
    db_session.commit()
    assert member.mfa_window_started_at is None
    client = app.test_client()
    login_as(client, member)
    assert client.get("/dashboard/overview").status_code == 200
    db_session.refresh(member)
    assert member.mfa_window_started_at is not None
    assert mfa_service.window_days_left(member) == 7
