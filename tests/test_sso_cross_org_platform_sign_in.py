"""Platform SSO sign-in identity resolution, across organisations.

Both account blueprints' ``sso_callback`` (v2, registered by default, and v1,
the ``USE_ACCOUNT_GUARDRAILS=false`` copy) hand the identity provider's claims
to ``AccountService.sign_in_external_identity``, the one place that decides
whether those claims sign in to an account, create one, or are refused:

- an account already linked to the provider subject signs in, whatever the
  email claim says;
- otherwise only an address the provider verified is considered;
- a verified address with no account gets an organisation of its own;
- a verified address that already has an account, in any organisation, signs
  nobody in and changes nothing;
- a platform administrator never signs in this way;
- every outcome is audited, without claims or tokens.

Route-level tests call each module's ``sso_callback`` directly inside a
request context (the pattern of ``tests/test_sso_noauth_email_claim_fix.py``)
so both copies run against one app and the ``db_session`` rollback; the
end-to-end tests drive the registered v2 route over HTTP and the v1 route on
an app built with ``USE_ACCOUNT_GUARDRAILS=false`` (the pattern of
``tests/test_mfa_login_gate.py``).
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import re
import time
import uuid
from urllib.parse import urlparse

import pyotp
import pytest
from flask import session, url_for

MODULE_V1 = "app.modules.account.routes.account_routes"
MODULE_V2 = "app.modules.account.v2.routes.account_routes"
MODULES = [MODULE_V1, MODULE_V2]

PINNED_TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
_PASSWORD = "correct horse battery staple"

MSG_MISSING = "Your identity provider did not return the details needed to sign you in."
MSG_UNVERIFIED = (
    "We couldn't confirm the email address your identity provider gave us. "
    "Sign in with your email and password instead."
)
MSG_INVITATION = "You have an invitation waiting. Open the invitation email to join."
MSG_EXISTS = "An account already exists for this address. Sign in with your password."
MSG_PLATFORM_ADMIN = "This account signs in with a password."
MSG_SERVICE = "This account cannot sign in here."

# Values that must never reach a log line or an audit row.
_SECRET_ID_TOKEN = "id-token-value-that-must-not-be-logged"
_SECRET_ACCESS_TOKEN = "access-token-value-that-must-not-be-logged"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, claims):
        self._claims = claims

    def authorize_access_token(self):
        return {
            "userinfo": self._claims,
            "id_token": _SECRET_ID_TOKEN,
            "access_token": _SECRET_ACCESS_TOKEN,
        }

    def userinfo(self):  # pragma: no cover - the token already carries userinfo
        return self._claims


class _FakeOAuth:
    def __init__(self, claims):
        self._claims = claims

    def create_client(self, provider):
        return _FakeClient(self._claims)


def _uid():
    return uuid.uuid4().hex[:10]


def _enable_sso(db_session):
    from app.models.feature_flags import FeatureFlag

    flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
    if flag is None:
        flag = FeatureFlag(key="sso_authentication", name="SSO (test)", enabled=True)
        db_session.add(flag)
    else:
        flag.enabled = True
    db_session.flush()
    return flag


def _make_account(db_session, org, label, *, password=True, confirmed=True, **fields):
    from app.models.user import User

    user = User(
        email=f"{label}-{_uid()}@example.com",
        first_name="Pat",
        last_name="Holder",
        confirmed=confirmed,
        organization_id=org.id,
        **fields,
    )
    if password:
        user.password = _PASSWORD
    db_session.add(user)
    db_session.commit()
    return user


def _snapshot(db_session, user):
    db_session.expire(user)
    return (
        user.email,
        user.external_id,
        user.sso_provider,
        user.organization_id,
        user.password_hash,
        user.confirmed,
        user.is_platform_admin,
    )


def _okta(email, sub, verified):
    claims = {"sub": sub, "email": email, "given_name": "Sam", "family_name": "Signer"}
    if verified is not None:
        claims["email_verified"] = verified
    return claims


def _azure(email, oid, tid=PINNED_TENANT, sub=None):
    return {
        "sub": sub or f"azure-sub-{_uid()}",
        "oid": oid,
        "tid": tid,
        "email": email,
        "preferred_username": email,
        "given_name": "Sam",
        "family_name": "Signer",
    }


def _call(app, module_path, provider, claims, monkeypatch, environ_base=None):
    """Run ``module.sso_callback(provider)`` in a request context carrying the
    expected state. Returns (response, flashes, session_user_id, session_copy)."""
    module = importlib.import_module(module_path)
    monkeypatch.setattr(module, "_get_sso_oauth", lambda: _FakeOAuth(claims))
    with app.test_request_context(
        f"/account/sso/callback/{provider}?state=state-xyz", environ_base=environ_base or {}
    ):
        session["sso_state"] = "state-xyz"
        resp = module.sso_callback(provider)
        flashes = list(session.get("_flashes", []))
        return resp, flashes, session.get("_user_id"), dict(session)


def _path(resp):
    return urlparse(resp.location).path


def _endpoint_path(app, endpoint):
    with app.test_request_context():
        return url_for(endpoint)


def _resolve(app, provider, claims):
    from app.modules.account.services.account_service import AccountService

    with app.test_request_context("/account/sso/callback/" + provider):
        return AccountService.sign_in_external_identity(provider, claims)


def _tokens_for(user_id):
    from app.models.account_token import AccountToken

    return AccountToken.query.filter_by(user_id=user_id).count()


def _sso_events():
    from app.security.audit import AuditEvent

    return [
        json.loads(e.details) if e.details else {}
        for e in AuditEvent.query.filter_by(action="sso_callback").order_by(AuditEvent.id).all()
    ]


def _login_attempt_events():
    """Every successful-login audit row, oldest first, as its ``details``
    dict. Read from the persisted ``AuditEvent`` rows rather than a spy, so
    this proves what actually reached the audit log."""
    from app.security.audit import AuditEvent

    return [
        json.loads(e.details) if e.details else {}
        for e in AuditEvent.query.filter_by(action="login_attempt").order_by(AuditEvent.id).all()
    ]


@pytest.fixture
def sent_mail(app, monkeypatch):
    """Every message handed to the mail extension, recorded; nothing leaves.

    Patched on the shared ``mail`` object rather than on the functions in
    ``app.flask_email``: a module imported for the first time during a test
    would otherwise keep the stand-in after the test ends.
    """
    from app.extensions import mail

    calls = []
    monkeypatch.setattr(mail, "send", lambda message, *a, **k: calls.append(message))
    with mail.record_messages() as outbox:
        yield calls, outbox


@pytest.fixture
def pinned_tenant(monkeypatch):
    monkeypatch.setenv("AZURE_AD_TENANT_ID", PINNED_TENANT)
    return PINNED_TENANT


# ---------------------------------------------------------------------------
# TS-1: an unverified email never reaches an existing account
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path", MODULES)
def test_unverified_email_never_signs_into_existing_account_cross_org(
    app, db_session, make_org, module_path, monkeypatch, sent_mail
):
    _enable_sso(db_session)
    org_a = make_org("ts1-a")
    org_b = make_org("ts1-b")
    holder = _make_account(db_session, org_a, "ts1-holder")
    other = _make_account(db_session, org_b, "ts1-other")
    before_holder = _snapshot(db_session, holder)
    before_other = _snapshot(db_session, other)

    claims = _okta(holder.email, f"okta-{_uid()}", verified=False)
    resp, flashes, signed_in, _ = _call(app, module_path, "okta", claims, monkeypatch)

    assert _path(resp) == _endpoint_path(app, "account.login")
    assert ("error", MSG_UNVERIFIED) in flashes
    assert signed_in is None
    assert _snapshot(db_session, holder) == before_holder
    assert _snapshot(db_session, other) == before_other
    assert _tokens_for(holder.id) == 0
    assert sent_mail[0] == [] and sent_mail[1] == []

    from app.modules.account.services.account_service import REFUSED_UNVERIFIED

    result = _resolve(app, "okta", claims)
    assert result.outcome == REFUSED_UNVERIFIED and result.user is None


# ---------------------------------------------------------------------------
# TS-2 (this change's form): a verified email of an active account in another
# organisation signs nobody in and changes nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path", MODULES)
@pytest.mark.parametrize("provider", ["okta", "azure"])
def test_verified_email_matching_active_account_does_not_sign_in_or_link(
    app, db_session, make_org, module_path, provider, monkeypatch, sent_mail, pinned_tenant
):
    _enable_sso(db_session)
    org_a = make_org("ts2-a")
    holder = _make_account(db_session, org_a, "ts2-holder")
    before = _snapshot(db_session, holder)

    if provider == "okta":
        claims = _okta(holder.email, f"okta-{_uid()}", verified=True)
    else:
        claims = _azure(holder.email, oid=f"oid-{_uid()}")

    resp, flashes, signed_in, sess = _call(app, module_path, provider, claims, monkeypatch)

    assert _path(resp) == _endpoint_path(app, "account.login")
    assert ("error", MSG_EXISTS) in flashes
    assert signed_in is None
    assert "_mfa_pending_user_id" not in sess
    assert _snapshot(db_session, holder) == before
    assert _tokens_for(holder.id) == 0
    assert sent_mail[0] == [] and sent_mail[1] == []

    from app.modules.account.services.account_service import REFUSED_ACCOUNT_EXISTS

    assert _resolve(app, provider, claims).outcome == REFUSED_ACCOUNT_EXISTS


# ---------------------------------------------------------------------------
# TS-3: a platform administrator is never reached, even when already linked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path", MODULES)
@pytest.mark.parametrize("how", ["verified-email", "linked-subject", "legacy-azure-link"])
def test_platform_admin_account_is_never_reached_by_sso(
    app, db_session, make_org, module_path, how, monkeypatch, sent_mail, pinned_tenant
):
    _enable_sso(db_session)
    org = make_org("ts3")
    oid = f"oid-{_uid()}"
    legacy_sub = f"legacy-{_uid()}"
    fields = {"is_platform_admin": True}
    if how == "linked-subject":
        fields.update(external_id=f"{PINNED_TENANT}:{oid}", sso_provider="azure")
    elif how == "legacy-azure-link":
        fields.update(external_id=legacy_sub, sso_provider="azure")
    admin = _make_account(db_session, org, "ts3-admin", **fields)
    before = _snapshot(db_session, admin)

    claims = _azure(admin.email, oid=oid, sub=legacy_sub if how == "legacy-azure-link" else None)
    resp, flashes, signed_in, sess = _call(app, module_path, "azure", claims, monkeypatch)

    assert _path(resp) == _endpoint_path(app, "account.login")
    assert ("error", MSG_PLATFORM_ADMIN) in flashes
    assert signed_in is None
    assert "_mfa_pending_user_id" not in sess
    assert _snapshot(db_session, admin) == before
    assert _tokens_for(admin.id) == 0
    assert sent_mail[0] == [] and sent_mail[1] == []

    from app.modules.account.services.account_service import REFUSED_PLATFORM_ADMIN

    assert _resolve(app, "azure", claims).outcome == REFUSED_PLATFORM_ADMIN
    assert _snapshot(db_session, admin) == before


# ---------------------------------------------------------------------------
# TS-4: a returning identity is matched by subject, never by email
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path", MODULES)
def test_returning_identity_signs_in_by_subject_not_email(
    app, db_session, make_org, module_path, monkeypatch, pinned_tenant
):
    _enable_sso(db_session)
    org_1 = make_org("ts4-1")
    org_2 = make_org("ts4-2")
    s1, s2 = f"okta-s1-{_uid()}", f"okta-s2-{_uid()}"
    u1 = _make_account(db_session, org_1, "ts4-u1", external_id=s1, sso_provider="okta")
    u2 = _make_account(db_session, org_2, "ts4-u2")
    before_u1, before_u2 = _snapshot(db_session, u1), _snapshot(db_session, u2)

    # Subject s1 with U2's verified address: U1 signs in, never U2.
    resp, _f, signed_in, _ = _call(
        app, module_path, "okta", _okta(u2.email, s1, verified=True), monkeypatch
    )
    assert _path(resp) == _endpoint_path(app, "main.index")
    assert signed_in == str(u1.id)
    assert _snapshot(db_session, u1) == before_u1  # B-9: email not changed
    assert _snapshot(db_session, u2) == before_u2

    # A different subject with U1's verified address: U1 is not signed in.
    resp, flashes, signed_in, _ = _call(
        app, module_path, "okta", _okta(u1.email, s2, verified=True), monkeypatch
    )
    assert _path(resp) == _endpoint_path(app, "account.login")
    assert signed_in is None
    assert ("error", MSG_EXISTS) in flashes
    assert _snapshot(db_session, u1) == before_u1

    # Azure: the identity is tid:oid, not the email claim.
    oid = f"oid-{_uid()}"
    a1 = _make_account(
        db_session, org_1, "ts4-a1", external_id=f"{PINNED_TENANT}:{oid}", sso_provider="azure"
    )
    before_a1 = _snapshot(db_session, a1)
    resp, _f, signed_in, _ = _call(
        app, module_path, "azure", _azure(u2.email, oid=oid), monkeypatch
    )
    assert signed_in == str(a1.id)
    assert _snapshot(db_session, a1) == before_a1
    resp, _f, signed_in, _ = _call(
        app, module_path, "azure", _azure(a1.email, oid=f"oid-{_uid()}"), monkeypatch
    )
    assert signed_in is None
    assert _snapshot(db_session, a1) == before_a1

    from app.modules.account.services.account_service import SIGNED_IN_RETURNING

    result = _resolve(app, "okta", _okta(u2.email, s1, verified=False))
    assert result.outcome == SIGNED_IN_RETURNING and result.user.id == u1.id


# ---------------------------------------------------------------------------
# TS-5: a new verified identity gets an organisation of its own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path", MODULES)
@pytest.mark.parametrize("provider", ["okta", "azure"])
def test_new_verified_identity_gets_its_own_organisation_not_default(
    app, db_session, module_path, provider, monkeypatch, pinned_tenant, sent_mail
):
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.account.services.account_service import AccountService

    _enable_sso(db_session)
    address = f"ts5-new-{_uid()}@example.com"
    if provider == "okta":
        sub = f"okta-{_uid()}"
        claims = _okta(address, sub, verified=True)
        expected_external_id = sub
    else:
        oid = f"oid-{_uid()}"
        claims = _azure(address, oid=oid)
        # The verified address is the tenant-scoped UPN, not the email claim.
        claims["email"] = f"ts5-unverified-{_uid()}@example.com"
        expected_external_id = f"{PINNED_TENANT}:{oid}"
    orgs_before = Organization.query.count()

    resp, _flashes, signed_in, sess = _call(app, module_path, provider, claims, monkeypatch)

    created = User.find_by_email(address)
    assert created is not None
    assert User.find_by_email(claims["email"]) is None or claims["email"] == address
    # A new account owns its organisation, so the MFA gate sends it to enrol
    # before any session exists.
    assert _path(resp) == _endpoint_path(app, "account.mfa_challenge")
    assert sess.get("_mfa_pending_user_id") == created.id
    assert sess.get("_mfa_pending_remember") is False
    assert signed_in is None

    org = db_session.get(Organization, created.organization_id)
    assert Organization.query.count() == orgs_before + 1
    assert org.slug != "default"
    assert created.is_org_admin is True
    assert OrgRole.get_role(org.id, created.id) == "org_admin"
    assert created.external_id == expected_external_id
    assert created.sso_provider == provider
    assert created.confirmed is True
    assert created.password_hash is None
    assert created.is_platform_admin in (False, None)

    # The same role values self-serve registration produces.
    with app.test_request_context("/account/register"):
        registered = AccountService.register_user(
            "Reg", "Istered", f"ts5-reg-{_uid()}@example.com", _PASSWORD
        )
    assert created.enterprise_role == registered.enterprise_role
    assert created.role_id == registered.role_id
    assert sent_mail[0] == [] and sent_mail[1] == []

    from app.modules.account.services.account_service import SIGNED_IN_NEW

    again = _resolve(app, provider, dict(claims, sub=f"other-{_uid()}", oid=f"oid-{_uid()}"))
    assert again.outcome != SIGNED_IN_NEW  # the address now has an account


# ---------------------------------------------------------------------------
# TS-6: an invitation not yet taken up is not taken up by SSO
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path", MODULES)
def test_invited_unactivated_account_is_not_taken_up_by_sso(
    app, db_session, make_org, module_path, monkeypatch, sent_mail
):
    from app.modules.account.services.invitation_service import is_unactivated

    _enable_sso(db_session)
    org = make_org("ts6")
    invitee = _make_account(db_session, org, "ts6-invitee", password=False, confirmed=False)
    assert is_unactivated(invitee)
    before = _snapshot(db_session, invitee)

    claims = _okta(invitee.email, f"okta-{_uid()}", verified=True)
    resp, flashes, signed_in, _ = _call(app, module_path, "okta", claims, monkeypatch)

    assert _path(resp) == _endpoint_path(app, "account.login")
    assert ("error", MSG_INVITATION) in flashes
    assert signed_in is None
    assert _snapshot(db_session, invitee) == before
    assert sent_mail[0] == [] and sent_mail[1] == []

    from app.modules.account.services.account_service import REFUSED_INVITATION_PENDING

    assert _resolve(app, "okta", claims).outcome == REFUSED_INVITATION_PENDING


# ---------------------------------------------------------------------------
# TS-10: the verified-email rule is the one shared helper
# ---------------------------------------------------------------------------


def test_verified_email_rule_is_the_one_shared_helper(monkeypatch):
    from app.auth.sso import verified_identity_email
    from app.modules.account.services.account_service import AccountService

    addr = "someone@example.com"
    assert verified_identity_email("okta", {"email": addr, "email_verified": True}) == addr
    for value in ("true", False):
        assert verified_identity_email("okta", {"email": addr, "email_verified": value}) is None
    assert verified_identity_email("okta", {"email": addr}) is None
    assert verified_identity_email("okta", {"email": addr, "xms_edov": True}) is None

    azure_claims = {"tid": PINNED_TENANT, "preferred_username": addr, "email": addr}
    monkeypatch.delenv("AZURE_AD_TENANT_ID", raising=False)
    assert verified_identity_email("azure", azure_claims) is None
    for placeholder in ("common", "organizations", "consumers"):
        monkeypatch.setenv("AZURE_AD_TENANT_ID", placeholder)
        assert verified_identity_email("azure", dict(azure_claims, tid=placeholder)) is None
    monkeypatch.setenv("AZURE_AD_TENANT_ID", PINNED_TENANT)
    assert verified_identity_email("azure", dict(azure_claims, tid=OTHER_TENANT)) is None
    assert (
        verified_identity_email("azure", dict(azure_claims, email="different@example.com"))
        == addr
    )

    assert "verified_identity_email" in inspect.getsource(AccountService.sign_in_external_identity)


# ---------------------------------------------------------------------------
# TS-11: both blueprints delegate to the one resolver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path", MODULES)
def test_both_account_blueprints_delegate_sso_callback_to_one_resolver(module_path):
    module = importlib.import_module(module_path)
    source = inspect.getsource(module.sso_callback)

    assert "sign_in_external_identity" in source
    for forbidden in ("find_by_email", "filter_by(email", "User("):
        assert forbidden not in source, f"{module_path}.sso_callback contains {forbidden!r}"
    assert '@rate_limit(10, "1m")' in source
    assert 'session["_mfa_pending_remember"] = False' in source
    assert "login_and_register(user, remember=False)" in source


@pytest.mark.parametrize("module_path", MODULES)
def test_both_copies_hold_the_cross_org_mfa_pending_sign_in_without_remember(
    app, db_session, make_org, module_path, monkeypatch
):
    from app.models import Role

    _enable_sso(db_session)
    org = make_org("ts11-mfa")
    sub = f"okta-{_uid()}"
    admin = _make_account(
        db_session, org, "ts11-admin", external_id=sub, sso_provider="okta",
        role=Role.query.filter_by(name="Administrator").first(),
    )
    resp, _f, signed_in, sess = _call(
        app, module_path, "okta", _okta(admin.email, sub, verified=False), monkeypatch
    )
    assert _path(resp) == _endpoint_path(app, "account.mfa_challenge")
    assert sess.get("_mfa_pending_user_id") == admin.id
    assert sess.get("_mfa_pending_remember") is False
    assert signed_in is None


# ---------------------------------------------------------------------------
# TS-12: end to end over HTTP, on the registered route and on v1
# ---------------------------------------------------------------------------


def _http_callback(client, claims, provider="okta"):
    with client.session_transaction() as sess:
        sess["sso_state"] = "state-e2e"
    return client.get(f"/account/sso/callback/{provider}?state=state-e2e", follow_redirects=False)


def test_sso_callback_route_refuses_cross_org_sign_in_end_to_end(
    app, db_session, make_org, monkeypatch, caplog, sent_mail
):
    from app.modules.account.v2.routes import account_routes as v2

    caplog.set_level(logging.DEBUG)
    _enable_sso(db_session)
    db_session.commit()
    org_a = make_org("ts12-a")
    holder = _make_account(db_session, org_a, "ts12-holder")
    before = _snapshot(db_session, holder)
    events_before = len(_sso_events())
    login_path = _endpoint_path(app, "account.login")

    for verified, expected, expected_target in (
        (False, "refused_unverified", None),
        (True, "refused_account_exists", holder.id),
    ):
        sub = f"okta-e2e-{_uid()}"
        claims = _okta(holder.email, sub, verified=verified)
        monkeypatch.setattr(v2, "_get_sso_oauth", lambda c=claims: _FakeOAuth(c))
        client = app.test_client()
        resp = _http_callback(client, claims)

        assert resp.status_code in (302, 303)
        assert urlparse(resp.headers["Location"]).path == login_path
        with client.session_transaction() as sess:
            assert "_user_id" not in sess
            assert "_mfa_pending_user_id" not in sess
        page = client.get("/account/manage", follow_redirects=False)
        assert page.status_code in (302, 401)
        if page.status_code == 302:
            assert urlparse(page.headers["Location"]).path == login_path

        events = _sso_events()[events_before:]
        assert events[-1] == {
            "provider": "okta",
            "outcome": expected,
            "user_id": None,
            "target_user_id": expected_target,
        }
        assert sub not in caplog.text

    assert _snapshot(db_session, holder) == before
    assert _tokens_for(holder.id) == 0
    assert sent_mail[0] == [] and sent_mail[1] == []

    from app.security.audit import AuditEvent

    all_details = " ".join(
        (e.details or "") for e in AuditEvent.query.filter_by(action="sso_callback").all()
    )
    for secret in (_SECRET_ID_TOKEN, _SECRET_ACCESS_TOKEN):
        assert secret not in caplog.text
        assert secret not in all_details
    assert holder.email not in all_details


def _v1_forced_app(monkeypatch):
    from app import create_app

    monkeypatch.setenv("USE_ACCOUNT_GUARDRAILS", "false")
    v1_app = create_app("testing")
    v1_app.config["TESTING"] = True
    v1_app.config["WTF_CSRF_ENABLED"] = False
    return v1_app


def test_v1_sso_callback_route_refuses_cross_org_sign_in_end_to_end(monkeypatch):
    """The rollback copy, registered on its own app. Its rows are committed
    for real (the db_session rollback does not cover a second app), so they
    are removed afterwards."""
    from app import db
    from app.models.feature_flags import FeatureFlag
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.account.routes import account_routes as v1

    v1_app = _v1_forced_app(monkeypatch)
    label = _uid()
    with v1_app.app_context():
        org = Organization(name=f"Test v1-e2e {label}", slug=f"test-v1-e2e-{label}")
        db.session.add(org)
        db.session.flush()
        holder = User(email=f"v1-e2e-{label}@example.com", organization_id=org.id, confirmed=True)
        holder.password = _PASSWORD
        db.session.add(holder)
        flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
        flag_existed = flag is not None
        if flag is None:
            flag = FeatureFlag(key="sso_authentication", name="SSO (test)", enabled=True)
            db.session.add(flag)
        flag_was_enabled = bool(flag_existed and flag.enabled)
        flag.enabled = True
        db.session.commit()
        holder_id, org_id, holder_email = holder.id, org.id, holder.email
        db.session.remove()

    try:
        for verified, expected_outcome, expected_target in (
            (False, "refused_unverified", None),
            (True, "refused_account_exists", holder_id),
        ):
            claims = _okta(holder_email, f"okta-v1-{_uid()}", verified=verified)
            monkeypatch.setattr(v1, "_get_sso_oauth", lambda c=claims: _FakeOAuth(c))
            client = v1_app.test_client()
            resp = _http_callback(client, claims)
            assert resp.status_code in (302, 303)
            assert urlparse(resp.headers["Location"]).path.endswith("/account/login")
            with client.session_transaction() as sess:
                assert "_user_id" not in sess
                assert "_mfa_pending_user_id" not in sess

            with v1_app.app_context():
                from app.security.audit import AuditEvent

                last_event = (
                    AuditEvent.query.filter_by(action="sso_callback")
                    .order_by(AuditEvent.id.desc())
                    .first()
                )
                details = json.loads(last_event.details)
                db.session.remove()
            assert details == {
                "provider": "okta",
                "outcome": expected_outcome,
                "user_id": None,
                "target_user_id": expected_target,
            }

        with v1_app.app_context():
            row = db.session.get(User, holder_id)
            assert row.external_id is None and row.sso_provider is None
            assert row.organization_id == org_id
            db.session.remove()
    finally:
        with v1_app.app_context():
            from app.models.org_role import OrgRole

            OrgRole.query.filter_by(user_id=holder_id).delete()
            User.query.filter_by(id=holder_id).delete()
            Organization.query.filter_by(id=org_id).delete()
            flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
            if flag is not None:
                if flag_existed:
                    flag.enabled = flag_was_enabled
                else:
                    db.session.delete(flag)
            db.session.commit()
            db.session.remove()


# ---------------------------------------------------------------------------
# TS-13: the callback is rate limited
# ---------------------------------------------------------------------------


def test_sso_callback_is_rate_limited(app, db_session, make_org, monkeypatch):
    from app.modules.account.v2.routes import account_routes as v2

    monkeypatch.setitem(app.config, "RATE_LIMITING_ENABLED", True)
    _enable_sso(db_session)
    db_session.commit()
    org = make_org("ts13")
    holder = _make_account(db_session, org, "ts13-holder")
    claims = _okta(holder.email, f"okta-{_uid()}", verified=True)
    monkeypatch.setattr(v2, "_get_sso_oauth", lambda: _FakeOAuth(claims))

    client = app.test_client()
    addr = {"REMOTE_ADDR": "198.51.100.{}".format(uuid.uuid4().int % 250 + 1)}
    statuses = []
    for _ in range(11):
        with client.session_transaction() as sess:
            sess["sso_state"] = "state-rl"
        statuses.append(
            client.get(
                "/account/sso/callback/okta?state=state-rl", environ_base=addr
            ).status_code
        )
    assert statuses[:10] == [302] * 10
    assert statuses[10] == 429
    assert _tokens_for(holder.id) == 0


def test_v1_sso_callback_is_rate_limited_cross_org(app, db_session, monkeypatch):
    from app.services.rate_limiter import RateLimitExceeded

    monkeypatch.setitem(app.config, "RATE_LIMITING_ENABLED", True)
    _enable_sso(db_session)
    claims = _okta(f"ts13-v1-{_uid()}@example.com", f"okta-{_uid()}", verified=False)
    addr = {"REMOTE_ADDR": "203.0.113.{}".format(uuid.uuid4().int % 250 + 1)}
    for _ in range(10):
        resp, *_rest = _call(app, MODULE_V1, "okta", claims, monkeypatch, environ_base=addr)
        assert resp.status_code == 302
    with pytest.raises(RateLimitExceeded):
        _call(app, MODULE_V1, "okta", claims, monkeypatch, environ_base=addr)


# ---------------------------------------------------------------------------
# TS-14: the platform OIDC client still validates the issuer
# ---------------------------------------------------------------------------


def test_platform_oidc_client_still_validates_issuer(app, monkeypatch):
    from joserfc import jwt
    from joserfc.jwk import KeySet, RSAKey

    from app.modules.account.v2.routes import account_routes as v2

    issuer = "https://idp.example.test/oauth2/default"
    monkeypatch.setitem(
        app.config,
        "SSO_PROVIDERS",
        {
            "okta": {
                "client_id": "client-ts14",
                "client_secret": "not-a-real-secret",
                "server_metadata_url": "https://idp.example.test/.well-known/openid-configuration",
                "client_kwargs": {"scope": "openid email profile"},
            }
        },
    )
    key = RSAKey.generate_key(2048, parameters={"kid": "ts14"}, private=True)
    now = int(time.time())

    def _id_token(iss):
        claims = {
            "iss": iss, "sub": "ts14-sub", "aud": "client-ts14",
            "iat": now, "exp": now + 300, "nonce": "n-ts14",
        }
        return jwt.encode({"alg": "RS256", "kid": "ts14"}, claims, key)

    had_registry = hasattr(app, "_sso_oauth")
    saved = getattr(app, "_sso_oauth", None)
    if had_registry:
        delattr(app, "_sso_oauth")
    try:
        with app.test_request_context("/"):
            client = v2._get_sso_oauth().create_client("okta")
            assert client is not None
            client.server_metadata.update(
                {
                    "issuer": issuer,
                    "jwks": KeySet([key]).as_dict(private=False),
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "_loaded_at": time.time(),
                }
            )
            good = client.parse_id_token(
                {"id_token": _id_token(issuer), "access_token": "a"}, nonce="n-ts14"
            )
            assert good["sub"] == "ts14-sub"
            with pytest.raises(Exception) as excinfo:
                client.parse_id_token(
                    {"id_token": _id_token("https://other-issuer.example.test/"), "access_token": "a"},
                    nonce="n-ts14",
                )
            assert "iss" in str(excinfo.value).lower()
    finally:
        if hasattr(app, "_sso_oauth"):
            delattr(app, "_sso_oauth")
        if had_registry:
            app._sso_oauth = saved


# ---------------------------------------------------------------------------
# TS-15 and B-0: missing claims are refused before anything is looked up
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["oid", "tid"])
def test_azure_identity_without_oid_or_tid_is_refused_as_missing_claims(
    app, db_session, missing, monkeypatch, pinned_tenant, sent_mail
):
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.account.services.account_service import REFUSED_MISSING_CLAIMS

    _enable_sso(db_session)
    address = f"ts15-{_uid()}@example.com"
    claims = _azure(address, oid=f"oid-{_uid()}")
    claims.pop(missing)
    orgs_before = Organization.query.count()

    result = _resolve(app, "azure", claims)
    assert result.outcome == REFUSED_MISSING_CLAIMS and result.user is None

    for module_path in MODULES:
        resp, flashes, signed_in, _ = _call(app, module_path, "azure", claims, monkeypatch)
        assert _path(resp) == _endpoint_path(app, "account.login")
        assert ("error", MSG_MISSING) in flashes
        assert signed_in is None

    assert User.find_by_email(address) is None
    assert Organization.query.count() == orgs_before


@pytest.mark.parametrize("missing", ["email", "sub"])
def test_okta_identity_without_email_or_subject_is_refused_cross_org(
    app, db_session, missing, monkeypatch
):
    from app.modules.account.services.account_service import REFUSED_MISSING_CLAIMS

    _enable_sso(db_session)
    claims = _okta(f"b0-{_uid()}@example.com", f"okta-{_uid()}", verified=True)
    claims.pop(missing)
    assert _resolve(app, "okta", claims).outcome == REFUSED_MISSING_CLAIMS
    for module_path in MODULES:
        resp, flashes, signed_in, _ = _call(app, module_path, "okta", claims, monkeypatch)
        assert ("error", MSG_MISSING) in flashes and signed_in is None


# ---------------------------------------------------------------------------
# B-10: every outcome writes one audit event with provider, outcome, user id
# ---------------------------------------------------------------------------


def test_every_outcome_writes_one_cross_org_audit_event(
    app, db_session, make_org, monkeypatch, pinned_tenant, caplog
):
    from app.models import Role

    caplog.set_level(logging.DEBUG)
    org = make_org("b10")
    linked_sub = f"okta-linked-{_uid()}"
    linked = _make_account(db_session, org, "b10-linked", external_id=linked_sub, sso_provider="okta")
    active = _make_account(db_session, org, "b10-active")
    invitee = _make_account(db_session, org, "b10-invitee", password=False, confirmed=False)
    admin = _make_account(
        db_session, make_org("b10-admin"), "b10-admin", is_platform_admin=True,
        role=Role.query.filter_by(name="Administrator").first(),
    )
    service_sub = f"okta-service-{_uid()}"
    service = _make_account(
        db_session, org, "b10-service", password=False, is_service_user=True,
        external_id=service_sub, sso_provider="okta",
    )
    new_address = f"b10-new-{_uid()}@example.com"

    cases = [
        ("okta", {"sub": f"s-{_uid()}"}, "refused_missing_claims", False, None),
        ("okta", _okta(linked.email, linked_sub, verified=False), "signed_in_returning", True, None),
        ("okta", _okta(new_address, f"s-{_uid()}", verified=False), "refused_unverified", False, None),
        ("okta", _okta(new_address, f"s-{_uid()}", verified=True), "signed_in_new", True, None),
        ("okta", _okta(active.email, f"s-{_uid()}", verified=True), "refused_account_exists", False, active.id),
        (
            "okta", _okta(invitee.email, f"s-{_uid()}", verified=True), "refused_invitation_pending",
            False, invitee.id,
        ),
        ("azure", _azure(admin.email, oid=f"oid-{_uid()}"), "refused_platform_admin", False, admin.id),
        (
            "okta", _okta(service.email, service_sub, verified=False), "refused_service_account",
            False, service.id,
        ),
    ]
    for provider, claims, outcome, signed_in, target in cases:
        before = len(_sso_events())
        result = _resolve(app, provider, claims)
        assert result.outcome == outcome
        assert result.signed_in is signed_in
        assert (result.user is not None) is signed_in
        events = _sso_events()[before:]
        assert events == [
            {
                "provider": provider,
                "outcome": outcome,
                "user_id": result.user.id if result.user is not None else None,
                "target_user_id": target,
            }
        ]
        if claims.get("sub"):
            assert claims["sub"] not in caplog.text


# ---------------------------------------------------------------------------
# F1-F9: fix round 1 -- the provider is audited after MFA, refusal targets
# are audited, account creation cannot 500 without an audit row, provider
# values are bounded, and a service account is refused.
# ---------------------------------------------------------------------------


def test_sso_sign_in_completed_through_mfa_is_audited_with_the_provider(
    app, db_session, make_org, monkeypatch, pinned_tenant, sent_mail
):
    """F1"""
    from app.models import Role
    from app.models.user import User
    from app.modules.account.v2.routes import account_routes as v2

    _enable_sso(db_session)

    # A new verified Okta identity owns its own organisation, so it is sent
    # to MFA enrolment before any session exists.
    address = f"f1-new-{_uid()}@example.com"
    sub = f"okta-{_uid()}"
    claims = _okta(address, sub, verified=True)
    monkeypatch.setattr(v2, "_get_sso_oauth", lambda: _FakeOAuth(claims))
    client = app.test_client()
    resp = _http_callback(client, claims, provider="okta")
    assert _path(resp) == _endpoint_path(app, "account.mfa_challenge")

    client.get("/account/mfa-challenge")
    with client.session_transaction() as sess:
        secret = sess["_mfa_enroll_secret"]
    code = pyotp.TOTP(secret).now()

    new_user = User.find_by_email(address)
    events_before = len(_login_attempt_events())
    complete = client.post("/account/mfa-challenge", data={"code": code}, follow_redirects=False)
    assert complete.status_code in (302, 303)

    with client.session_transaction() as sess:
        assert sess.get("_user_id") == str(new_user.id)
        assert sess.get("_login_audit_id") is not None
        assert "_mfa_pending_sso_provider" not in sess

    events = _login_attempt_events()[events_before:]
    assert len(events) == 1
    assert events[0]["method"] == "sso:okta"

    # A returning, already-enrolled Azure org-admin completes the same gate.
    org = make_org("f1-azure")
    azure_oid = f"oid-{_uid()}"
    azure_secret = pyotp.random_base32()
    admin = _make_account(
        db_session, org, "f1-azure-admin",
        external_id=f"{PINNED_TENANT}:{azure_oid}", sso_provider="azure",
        mfa_enabled=True, mfa_secret=azure_secret,
        role=Role.query.filter_by(name="Administrator").first(),
    )
    azure_claims = _azure(admin.email, oid=azure_oid)
    monkeypatch.setattr(v2, "_get_sso_oauth", lambda: _FakeOAuth(azure_claims))
    client2 = app.test_client()
    resp2 = _http_callback(client2, azure_claims, provider="azure")
    assert _path(resp2) == _endpoint_path(app, "account.mfa_challenge")
    with client2.session_transaction() as sess:
        assert sess.get("_mfa_pending_sso_provider") == "azure"

    events_before2 = len(_login_attempt_events())
    azure_code = pyotp.TOTP(azure_secret).now()
    complete2 = client2.post(
        "/account/mfa-challenge", data={"code": azure_code}, follow_redirects=False
    )
    assert complete2.status_code in (302, 303)
    with client2.session_transaction() as sess:
        assert sess.get("_user_id") == str(admin.id)
        assert "_mfa_pending_sso_provider" not in sess

    events2 = _login_attempt_events()[events_before2:]
    assert len(events2) == 1
    assert events2[0]["method"] == "sso:azure"


def test_v1_sso_sign_in_completed_through_mfa_is_audited_with_the_provider(monkeypatch):
    """F2"""
    from app import db
    from app.models.feature_flags import FeatureFlag
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.account.routes import account_routes as v1
    from app.security.audit import AuditEvent

    v1_app = _v1_forced_app(monkeypatch)
    with v1_app.app_context():
        flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
        flag_existed = flag is not None
        if flag is None:
            flag = FeatureFlag(key="sso_authentication", name="SSO (test)", enabled=True)
            db.session.add(flag)
        flag_was_enabled = bool(flag_existed and flag.enabled)
        flag.enabled = True
        db.session.commit()
        db.session.remove()

    address = f"f2-new-{_uid()}@example.com"
    sub = f"okta-{_uid()}"
    claims = _okta(address, sub, verified=True)
    monkeypatch.setattr(v1, "_get_sso_oauth", lambda: _FakeOAuth(claims))
    client = v1_app.test_client()
    resp = _http_callback(client, claims, provider="okta")
    assert urlparse(resp.headers["Location"]).path.endswith("/account/mfa-challenge")

    client.get("/account/mfa-challenge")
    with client.session_transaction() as sess:
        secret = sess["_mfa_enroll_secret"]
    code = pyotp.TOTP(secret).now()

    with v1_app.app_context():
        new_user = User.query.filter_by(email=address).first()
        new_user_id, org_id = new_user.id, new_user.organization_id
        db.session.remove()

    try:
        complete = client.post("/account/mfa-challenge", data={"code": code}, follow_redirects=False)
        assert complete.status_code in (302, 303)
        with client.session_transaction() as sess:
            assert sess.get("_user_id") == str(new_user_id)
            assert "_mfa_pending_sso_provider" not in sess

        with v1_app.app_context():
            events = [
                json.loads(e.details)
                for e in AuditEvent.query.filter_by(action="login_attempt").order_by(AuditEvent.id).all()
            ]
            db.session.remove()
        assert len(events) == 1
        assert events[0]["method"] == "sso:okta"
    finally:
        with v1_app.app_context():
            OrgRole.query.filter_by(user_id=new_user_id).delete()
            User.query.filter_by(id=new_user_id).delete()
            Organization.query.filter_by(id=org_id).delete()
            flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
            if flag is not None:
                if flag_existed:
                    flag.enabled = flag_was_enabled
                else:
                    db.session.delete(flag)
            db.session.commit()
            db.session.remove()


def test_password_sign_in_after_an_unfinished_sso_mfa_step_is_audited_as_password(
    app, db_session, make_org, monkeypatch, pinned_tenant
):
    """F3"""
    from app.models import Role

    _enable_sso(db_session)
    org = make_org("f3")
    sub = f"okta-{_uid()}"
    admin = _make_account(
        db_session, org, "f3-admin",
        external_id=sub, sso_provider="okta",
        role=Role.query.filter_by(name="Administrator").first(),
    )

    claims = _okta(admin.email, sub, verified=False)
    monkeypatch.setattr(
        importlib.import_module(MODULE_V2), "_get_sso_oauth", lambda: _FakeOAuth(claims)
    )
    client = app.test_client()
    resp = _http_callback(client, claims, provider="okta")
    assert _path(resp) == _endpoint_path(app, "account.mfa_challenge")
    with client.session_transaction() as sess:
        assert sess.get("_mfa_pending_sso_provider") == "okta"

    # The same client now signs in with the account's password instead.
    login_resp = client.post(
        "/account/login",
        data={"email": admin.email, "password": _PASSWORD, "remember_me": ""},
        follow_redirects=False,
    )
    assert _path(login_resp) == _endpoint_path(app, "account.mfa_challenge")
    with client.session_transaction() as sess:
        assert "_mfa_pending_sso_provider" not in sess

    client.get("/account/mfa-challenge")
    with client.session_transaction() as sess:
        secret = sess["_mfa_enroll_secret"]
    code = pyotp.TOTP(secret).now()

    events_before = len(_login_attempt_events())
    complete = client.post("/account/mfa-challenge", data={"code": code}, follow_redirects=False)
    assert complete.status_code in (302, 303)
    events = _login_attempt_events()[events_before:]
    assert len(events) == 1
    assert events[0]["method"] == "password"

    # Cross-check: a stale _mfa_pending_sso_provider key beside a
    # password-only pending login is never honoured.
    password_only = _make_account(db_session, org, "f3-password-only")
    client2 = app.test_client()
    with client2.session_transaction() as sess:
        sess["_mfa_pending_user_id"] = password_only.id
        sess["_mfa_pending_remember"] = False
        sess["_mfa_pending_next"] = ""
        sess["_mfa_pending_sso_provider"] = "okta"

    client2.get("/account/mfa-challenge")
    with client2.session_transaction() as sess:
        secret2 = sess["_mfa_enroll_secret"]
    code2 = pyotp.TOTP(secret2).now()

    events_before2 = len(_login_attempt_events())
    complete2 = client2.post("/account/mfa-challenge", data={"code": code2}, follow_redirects=False)
    assert complete2.status_code in (302, 303)
    events2 = _login_attempt_events()[events_before2:]
    assert len(events2) == 1
    assert events2[0]["method"] == "password"


def test_refused_sign_in_records_the_targeted_account(
    app, db_session, make_org, monkeypatch, pinned_tenant
):
    """F4"""
    from app.models import Role

    _enable_sso(db_session)
    org = make_org("f4")

    # Platform admin, linked branch.
    oid = f"oid-{_uid()}"
    admin_linked = _make_account(
        db_session, org, "f4-admin-linked", is_platform_admin=True,
        external_id=f"{PINNED_TENANT}:{oid}", sso_provider="azure",
        role=Role.query.filter_by(name="Administrator").first(),
    )
    events_before = len(_sso_events())
    result = _resolve(app, "azure", _azure(admin_linked.email, oid=oid))
    assert result.user is None
    events = _sso_events()[events_before:]
    assert events[-1]["user_id"] is None
    assert events[-1]["target_user_id"] == admin_linked.id

    # Platform admin, verified-email branch.
    admin_email = _make_account(
        db_session, org, "f4-admin-email", is_platform_admin=True,
        role=Role.query.filter_by(name="Administrator").first(),
    )
    events_before = len(_sso_events())
    result = _resolve(app, "okta", _okta(admin_email.email, f"okta-{_uid()}", verified=True))
    assert result.user is None
    events = _sso_events()[events_before:]
    assert events[-1]["user_id"] is None
    assert events[-1]["target_user_id"] == admin_email.id

    # Pending invitation.
    invitee = _make_account(db_session, org, "f4-invitee", password=False, confirmed=False)
    events_before = len(_sso_events())
    result = _resolve(app, "okta", _okta(invitee.email, f"okta-{_uid()}", verified=True))
    assert result.user is None
    events = _sso_events()[events_before:]
    assert events[-1]["user_id"] is None
    assert events[-1]["target_user_id"] == invitee.id

    # Existing active account.
    active = _make_account(db_session, org, "f4-active")
    events_before = len(_sso_events())
    result = _resolve(app, "okta", _okta(active.email, f"okta-{_uid()}", verified=True))
    assert result.user is None
    events = _sso_events()[events_before:]
    assert events[-1]["user_id"] is None
    assert events[-1]["target_user_id"] == active.id

    # Service account, linked branch.
    service_oid = f"oid-{_uid()}"
    service = _make_account(
        db_session, org, "f4-service", password=False, is_service_user=True,
        external_id=f"{PINNED_TENANT}:{service_oid}", sso_provider="azure",
    )
    events_before = len(_sso_events())
    result = _resolve(app, "azure", _azure(service.email, oid=service_oid))
    assert result.user is None
    events = _sso_events()[events_before:]
    assert events[-1]["user_id"] is None
    assert events[-1]["target_user_id"] == service.id


def test_long_provider_names_are_bounded_on_a_new_account(app, db_session, monkeypatch):
    """F5"""
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.account.services.account_service import SIGNED_IN_NEW

    _enable_sso(db_session)
    address = f"f5-{_uid()}@example.com"
    sub = f"okta-{_uid()}"
    claims = _okta(address, sub, verified=True)
    claims["given_name"] = "G" * 80
    claims["family_name"] = "F" * 80

    result = _resolve(app, "okta", claims)
    assert result.outcome == SIGNED_IN_NEW

    created = User.find_by_email(address)
    assert len(created.first_name) == 64
    assert len(created.last_name) == 64
    org = db_session.get(Organization, created.organization_id)
    assert len(org.slug) <= 100


def test_over_length_verified_address_is_refused_before_lookup(app, db_session, monkeypatch):
    """F6"""
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.account.services.account_service import REFUSED_MISSING_CLAIMS

    _enable_sso(db_session)
    long_address = "a" * 58 + "@example.com"
    assert len(long_address) >= 65
    sub = f"okta-{_uid()}"
    claims = _okta(long_address, sub, verified=True)
    orgs_before = Organization.query.count()
    events_before = len(_sso_events())

    result = _resolve(app, "okta", claims)
    assert result.outcome == REFUSED_MISSING_CLAIMS
    assert result.user is None

    assert User.find_by_email(long_address) is None
    assert Organization.query.count() == orgs_before

    events = _sso_events()[events_before:]
    assert events[-1]["target_user_id"] is None


def test_failed_account_creation_is_audited_and_raised(app, db_session, monkeypatch):
    """F7"""
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.account.services.account_service import AccountService

    _enable_sso(db_session)
    address = f"f7-{_uid()}@example.com"
    sub = f"okta-{_uid()}"
    claims = _okta(address, sub, verified=True)

    def _boom(cls, *a, **k):
        raise RuntimeError("set_role boom (test-induced)")

    monkeypatch.setattr(OrgRole, "set_role", classmethod(_boom))

    orgs_before = Organization.query.count()
    events_before = len(_sso_events())
    with pytest.raises(RuntimeError):
        AccountService.sign_in_external_identity("okta", claims)

    assert User.find_by_email(address) is None
    assert Organization.query.count() == orgs_before

    events = _sso_events()[events_before:]
    assert len(events) == 1
    assert events[0]["outcome"] == "failed_account_creation"
    assert events[0]["user_id"] is None
    assert events[0]["target_user_id"] is None


@pytest.mark.parametrize("module_path", MODULES)
def test_service_account_is_never_signed_in_through_sso(
    app, db_session, make_org, module_path, monkeypatch, pinned_tenant
):
    """F8"""
    from app.modules.account.services.account_service import REFUSED_SERVICE_ACCOUNT

    _enable_sso(db_session)
    org = make_org("f8")

    # Linked branch.
    oid = f"oid-{_uid()}"
    linked = _make_account(
        db_session, org, "f8-linked", password=False, is_service_user=True,
        external_id=f"{PINNED_TENANT}:{oid}", sso_provider="azure",
    )
    before_linked = _snapshot(db_session, linked)
    resp, flashes, signed_in, sess = _call(
        app, module_path, "azure", _azure(linked.email, oid=oid), monkeypatch
    )
    assert _path(resp) == _endpoint_path(app, "account.login")
    assert ("error", MSG_SERVICE) in flashes
    assert signed_in is None
    assert "_mfa_pending_user_id" not in sess
    assert _snapshot(db_session, linked) == before_linked

    result = _resolve(app, "azure", _azure(linked.email, oid=oid))
    assert result.outcome == REFUSED_SERVICE_ACCOUNT

    # Email branch.
    email_only = _make_account(db_session, org, "f8-email", password=False, is_service_user=True)
    before_email = _snapshot(db_session, email_only)
    claims = _okta(email_only.email, f"okta-{_uid()}", verified=True)
    resp, flashes, signed_in, sess = _call(app, module_path, "okta", claims, monkeypatch)
    assert _path(resp) == _endpoint_path(app, "account.login")
    assert ("error", MSG_SERVICE) in flashes
    assert signed_in is None
    assert "_mfa_pending_user_id" not in sess
    assert _snapshot(db_session, email_only) == before_email

    result = _resolve(app, "okta", claims)
    assert result.outcome == REFUSED_SERVICE_ACCOUNT


def test_one_function_creates_an_organisation_with_its_first_administrator():
    """F9"""
    from app.services.org_service import OrgService

    assert not hasattr(OrgService, "create_org")

    tests_dir = os.path.dirname(os.path.abspath(__file__))
    app_root = os.path.join(os.path.dirname(tests_dir), "app")
    organization_model_path = os.path.join(app_root, "models", "organization.py")

    matches = set()
    for dirpath, dirnames, filenames in os.walk(app_root):
        dirnames[:] = [d for d in dirnames if d != "tests"]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            if path == organization_model_path:
                # The class Organization( definition itself, not a call.
                continue
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            # A constructor call, not a class whose own name merely ends in
            # "Organization" (e.g. "class VendorOrganization(db.Model)").
            if re.search(r"\bOrganization\(", text):
                matches.add(os.path.relpath(path, app_root).replace(os.sep, "/"))

    expected = {
        "modules/account/services/account_service.py",
        "modules/admin/v2/routes/admin_routes.py",
        "commands/seed_demo_company.py",
    }
    assert matches == expected
