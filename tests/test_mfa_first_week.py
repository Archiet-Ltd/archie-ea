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
    from app.models.connector_config import DevOpsConnectorConfig
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
    db_session.add(DevOpsConnectorConfig(organization_id=org.id, provider="github", enabled=True))
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


def test_enrolled_owner_is_never_in_the_window(db_session, make_org):
    _org, owner = _owner(db_session, make_org)
    owner.mfa_enabled, owner.mfa_secret = True, pyotp.random_base32()
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    assert not mfa_service.must_enrol_now(owner)


def test_window_closed_when_a_second_member_is_added_by_another_route(db_session, make_org):
    from app.models.org_role import OrgRole

    org, owner = _owner(db_session, make_org)
    _other_workspace(db_session, make_org)
    # An existing account from elsewhere is given a role here directly (not via the invite form).
    outsider = _user(db_session, make_org("elsewhere"), admin=False, signed_up=False)
    OrgRole.set_role(org.id, outsider.id, "viewer", granted_by_id=owner.id)
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.window_closed_reason(owner) == "Your workspace now has a second member."


def test_window_closed_when_a_member_is_created_in_the_organisation(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    _user(db_session, org, admin=False, signed_up=False)
    assert not mfa_service.may_defer_enrolment(owner)


def test_window_closed_by_an_open_invitation_but_not_an_expired_one(db_session, make_org):
    from app.models.pending_invitation import PendingInvitation

    org, owner = _owner(db_session, make_org)
    invitee = _user(db_session, make_org("invitee"), admin=False, signed_up=False)
    invitation = PendingInvitation(organization_id=org.id, user_id=invitee.id, role="viewer", invited_by=owner.id)
    db_session.add(invitation)
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.window_closed_reason(owner) == "Your workspace has an invitation waiting to be accepted."
    invitation.expires_at = datetime.utcnow() - timedelta(hours=1)
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_window_closed_by_an_oauth_token(db_session, make_org):
    from app.modules.oauth_provider.models import OAuthToken

    org, owner = _owner(db_session, make_org)
    token = OAuthToken(
        client_id="cl-1", user_id=owner.id, access_token=uuid.uuid4().hex,
        expires_at=datetime.utcnow() + timedelta(days=1), organization_id=org.id,
    )
    db_session.add(token)
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    token.revoked_at = datetime.utcnow()
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_window_closed_by_a_service_account(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    service = _user(db_session, org, admin=False, signed_up=False)
    service.is_service_user = True
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)


def test_window_closed_by_connector_delegation(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    org.settings = {"connector_action_delegation": {"sync": True}}
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    org.settings = {"connector_action_delegation": {"sync": False}}
    db_session.commit()
    assert mfa_service.may_defer_enrolment(owner)


def test_window_closed_by_a_write_scope_connection(db_session, make_org):
    from app.models.connector_config import DevOpsConnectorConfig

    org, owner = _owner(db_session, make_org)
    db_session.add(DevOpsConnectorConfig(organization_id=org.id, provider="github", enabled=True))
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)


def test_window_closed_by_single_sign_on(db_session, make_org):
    from app.models.sso_config import SSOConfig

    org, owner = _owner(db_session, make_org)
    db_session.add(SSOConfig(organization_id=org.id, protocol="oidc", enabled=False))
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)


def test_window_closed_by_require_two_factor(db_session, make_org):
    org, owner = _owner(db_session, make_org)
    org.settings = {mfa_service.REQUIRE_SETTING: True}
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.window_closed_reason(owner) == "Your workspace requires two-factor sign-in."


def test_never_open_for_a_platform_administrator(db_session, make_org):
    _org, owner = _owner(db_session, make_org, platform=True)
    assert mfa_service.required_for(owner)
    assert not mfa_service.may_defer_enrolment(owner)


def test_never_open_for_an_owner_who_administers_another_organisation(db_session, make_org):
    from app.models.org_role import OrgRole

    _org, owner = _owner(db_session, make_org)
    other = make_org("also-admin")
    OrgRole.set_role(other.id, owner.id, "org_admin", granted_by_id=owner.id)
    db_session.commit()
    assert not mfa_service.may_defer_enrolment(owner)


def test_never_open_for_an_administrator_of_an_organisation_that_existed_before(db_session, make_org):
    _org, owner = _owner(db_session, make_org, signed_up=False)
    assert mfa_service.required_for(owner)
    assert not mfa_service.may_defer_enrolment(owner)
    assert mfa_service.must_enrol_now(owner)


def test_the_window_in_one_organisation_is_not_changed_by_another(db_session, make_org):
    _org_a, owner_a = _owner(db_session, make_org)
    _org_b, owner_b = _owner(db_session, make_org)
    assert mfa_service.may_defer_enrolment(owner_a) and mfa_service.may_defer_enrolment(owner_b)
    _other_workspace(db_session, make_org)
    _user(db_session, _org_b, admin=False, signed_up=False)
    assert not mfa_service.may_defer_enrolment(owner_b)
    assert mfa_service.may_defer_enrolment(owner_a)


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
    ("POST", "/admin/billing/change-plan", {"plan": "team", "interval": "year"}, "billing"),
    ("POST", "/admin/billing/cancel", {}, "billing"),
    ("POST", "/admin/billing/details", {"billing_email": "a@example.com"}, "billing"),
    ("GET", "/admin/billing/portal", None, "billing"),
    ("POST", "/admin/connectors", {"connector_type": "jira", "name": "Jira"}, "connector"),
    ("POST", "/admin/connectors/not-a-connector/sync", {}, "connector"),
    ("POST", "/admin/connectors/not-a-connector/delete", {}, "connector"),
    ("POST", "/admin/connectors/m365", {"tenant_id": "t"}, "connector"),
    ("POST", "/admin/connectors/servicenow", {"instance_url": "https://x.service-now.com"}, "connector"),
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
