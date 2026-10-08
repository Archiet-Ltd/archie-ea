"""nOAuth fix: the global Azure/Okta SSO callbacks must not resolve or
first-link an existing user purely from an IdP's unverified `email` claim.

"nOAuth" is the vulnerability class publicly disclosed in 2023 against
Azure AD multi-tenant apps generally: Azure's `email` claim is a
self-service, mutable tenant attribute, not cryptographically tied to a
verified mailbox. Before this fix, both global SSO callbacks here would
resolve-or-link an existing account purely from that claim -- an attacker
who registers their own free Azure AD tenant (or an Okta account with an
unverified email) could set `email` to a victim's real address and be
logged in as, or permanently linked onto, the victim's existing Entelim
account.

Covers:
  - app.modules.account.routes.account_routes.sso_callback (v1)
  - app.modules.account.v2.routes.account_routes.sso_callback (v2)
  - the shared helpers in app.auth.sso that both routes now call

The per-organisation SSO flow (app/modules/auth/sso_routes.py,
app/services/sso_service.py) is a different, already-protected code path
and is out of scope here -- see tests/test_sso_id_token_verification.py
and tests/test_sso_mapping_tenant_isolation.py for its coverage.
"""

from __future__ import annotations

import importlib
import uuid
from unittest import mock
from urllib.parse import urlparse

import pytest

from flask import session, url_for


MODULE_V1 = "app.modules.account.routes.account_routes"
MODULE_V2 = "app.modules.account.v2.routes.account_routes"
MODULES = [MODULE_V1, MODULE_V2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_oauth(userinfo):
    """Stand in for the authlib OAuth registry: create_client(provider)
    returns a client whose authorize_access_token()/userinfo() hand back
    *userinfo*, exactly the shape both callbacks already handle.
    """
    client = mock.Mock()
    client.authorize_access_token.return_value = {"userinfo": userinfo}
    client.userinfo.return_value = userinfo
    oauth = mock.Mock()
    oauth.create_client.return_value = client
    return oauth


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


def _make_user(db_session, email, **kwargs):
    from app.models.user import User

    user = User(email=email, first_name="Vic", last_name="Tim", confirmed=True, **kwargs)
    user.password = "correct horse battery staple"
    db_session.add(user)
    db_session.flush()
    return user


def _call_sso_callback(app, module_path, provider, userinfo, monkeypatch, state="state-abc"):
    """Invoke module.sso_callback(provider) directly inside a request
    context carrying the CSRF state the function expects, with the OAuth
    client mocked out. Both route modules define a module-level
    `sso_callback` function (the Flask Blueprint.route decorator returns it
    unchanged), so it is callable on its own regardless of whether this
    particular module is the one actually mounted on `app`'s live URL map --
    letting this file exercise both v1 and v2 in one running app.

    Returns (response, flashes) -- flashes read while still inside the
    request context, since the success path clears the session right before
    flashing its own message.
    """
    module = importlib.import_module(module_path)
    monkeypatch.setattr(module, "_get_sso_oauth", lambda: _mock_oauth(userinfo))

    with app.test_request_context(f"/account/sso/callback/{provider}?state={state}"):
        session["sso_state"] = state
        resp = module.sso_callback(provider)
        flashes = list(session.get("_flashes", []))
        return resp, flashes


def _redirect_path(resp):
    return urlparse(resp.location).path


def _expect_path(app, endpoint):
    with app.test_request_context():
        return url_for(endpoint)


# ---------------------------------------------------------------------------
# Unit coverage of the shared app.auth.sso helpers (no DB/app context needed
# except where User is imported for mapper configuration)
# ---------------------------------------------------------------------------


def test_azure_identity_key_is_tenant_qualified():
    from app.auth.sso import azure_identity_key

    assert azure_identity_key({"tid": "tenant-1", "oid": "obj-1"}) == "tenant-1:obj-1"


def test_external_id_for_azure_uses_oid_tid_not_raw_sub():
    from app.auth.sso import external_id_for

    userinfo = {"sub": "raw-sub", "oid": "obj-1", "tid": "tenant-1"}
    assert external_id_for("azure", userinfo) == "tenant-1:obj-1"
    assert external_id_for("azure", userinfo) != userinfo["sub"]


def test_external_id_for_okta_uses_sub():
    from app.auth.sso import external_id_for

    assert external_id_for("okta", {"sub": "okta-sub-1"}) == "okta-sub-1"


def test_verified_identity_email_okta_requires_email_verified_true():
    from app.auth.sso import verified_identity_email

    assert verified_identity_email("okta", {"email": "a@example.com", "email_verified": False}) is None
    assert verified_identity_email("okta", {"email": "a@example.com"}) is None
    assert verified_identity_email("okta", {"email": "a@example.com", "email_verified": True}) == "a@example.com"


def test_verified_identity_email_azure_default_tenant_is_never_trusted(monkeypatch):
    from app.auth.sso import verified_identity_email

    monkeypatch.delenv("AZURE_AD_TENANT_ID", raising=False)
    userinfo = {
        "tid": "common",
        "preferred_username": "a@example.com",
        "email_verified": True,  # even if an attacker could forge this shape
    }
    assert verified_identity_email("azure", userinfo) is None


@pytest.mark.parametrize("placeholder", ["common", "organizations", "consumers"])
def test_verified_identity_email_azure_multi_tenant_placeholders_are_never_trusted(monkeypatch, placeholder):
    from app.auth.sso import verified_identity_email

    monkeypatch.setenv("AZURE_AD_TENANT_ID", placeholder)
    userinfo = {"tid": placeholder, "preferred_username": "a@example.com"}
    assert verified_identity_email("azure", userinfo) is None


def test_verified_identity_email_azure_configured_tenant_mismatch_is_refused(monkeypatch):
    from app.auth.sso import verified_identity_email

    monkeypatch.setenv("AZURE_AD_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    userinfo = {"tid": "22222222-2222-2222-2222-222222222222", "preferred_username": "a@example.com"}
    assert verified_identity_email("azure", userinfo) is None


def test_verified_identity_email_azure_configured_tenant_match_returns_preferred_username(monkeypatch):
    from app.auth.sso import verified_identity_email

    monkeypatch.setenv("AZURE_AD_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    userinfo = {
        "tid": "11111111-1111-1111-1111-111111111111",
        "preferred_username": "a@example.com",
        "email": "different@example.com",
    }
    assert verified_identity_email("azure", userinfo) == "a@example.com"


# ---------------------------------------------------------------------------
# Route-level coverage: each case against BOTH v1 and v2 sso_callback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path", MODULES)
def test_okta_unverified_email_claim_is_refused(app, db_session, module_path, monkeypatch):
    """An Okta attacker presenting email_verified: false (or absent) with an
    existing victim's email must be refused outright: no link, no session,
    victim's account completely unchanged."""
    _enable_sso(db_session)
    victim = _make_user(db_session, f"victim-okta-unverified-{uuid.uuid4().hex[:6]}@example.com")
    db_session.commit()
    stored_external_id, stored_provider = victim.external_id, victim.sso_provider

    userinfo = {
        "sub": "attacker-okta-sub",
        "email": victim.email,
        "email_verified": False,
        "given_name": "Att",
        "family_name": "Acker",
    }
    resp, flashes = _call_sso_callback(app, module_path, "okta", userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "account.login")
    assert any(cat == "error" for cat, _msg in flashes), f"expected an error flash, got {flashes}"

    db_session.expire(victim)
    assert victim.external_id == stored_external_id
    assert victim.sso_provider == stored_provider


@pytest.mark.parametrize("module_path", MODULES)
def test_okta_verified_email_claim_links_and_signs_in(app, db_session, module_path, monkeypatch):
    """An Okta attacker presenting email_verified: true is the legitimate
    first-link case -- a real user genuinely linking Okta SSO -- and must
    succeed."""
    _enable_sso(db_session)
    victim = _make_user(db_session, f"victim-okta-verified-{uuid.uuid4().hex[:6]}@example.com")
    db_session.commit()

    userinfo = {
        "sub": "real-okta-sub",
        "email": victim.email,
        "email_verified": True,
        "given_name": victim.first_name,
        "family_name": victim.last_name,
    }
    resp, _flashes = _call_sso_callback(app, module_path, "okta", userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "main.index")
    db_session.expire(victim)
    assert victim.external_id == "real-okta-sub"
    assert victim.sso_provider == "okta"


@pytest.mark.parametrize("module_path", MODULES)
@pytest.mark.parametrize(
    "extra_claims",
    [
        {},
        {"email_verified": True},  # forging the Okta-shaped claim must not help on Azure
    ],
    ids=["no-verified-claim", "forged-email-verified-true"],
)
def test_azure_default_tenant_claim_is_refused(app, db_session, module_path, extra_claims, monkeypatch):
    """An Azure attacker on today's actual configuration (AZURE_AD_TENANT_ID
    unset, defaulting to the multi-tenant "common" authority) claiming an
    existing victim's email must be refused outright, regardless of what
    email_verified-shaped claim they include."""
    monkeypatch.delenv("AZURE_AD_TENANT_ID", raising=False)
    _enable_sso(db_session)
    victim = _make_user(db_session, f"victim-azure-{uuid.uuid4().hex[:6]}@example.com")
    db_session.commit()
    stored_external_id = victim.external_id

    userinfo = {
        "sub": "attacker-azure-sub",
        "oid": "attacker-oid",
        "tid": "attacker-own-free-tenant-guid",
        "email": victim.email,
        "preferred_username": victim.email,
        "given_name": "Att",
        "family_name": "Acker",
        **extra_claims,
    }
    resp, flashes = _call_sso_callback(app, module_path, "azure", userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "account.login")
    assert any(cat == "error" for cat, _msg in flashes)

    db_session.expire(victim)
    assert victim.external_id == stored_external_id
    assert victim.sso_provider is None


@pytest.mark.parametrize("module_path", MODULES)
def test_azure_configured_tenant_but_tid_mismatch_is_refused(app, db_session, module_path, monkeypatch):
    """Even with a specific tenant configured, a token from a DIFFERENT
    tenant (an attacker's own) claiming a victim's email must be refused."""
    monkeypatch.setenv("AZURE_AD_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    _enable_sso(db_session)
    victim = _make_user(db_session, f"victim-azure-mismatch-{uuid.uuid4().hex[:6]}@example.com")
    db_session.commit()
    stored_external_id = victim.external_id

    userinfo = {
        "sub": "attacker-sub",
        "oid": "attacker-oid",
        "tid": "22222222-2222-2222-2222-222222222222",
        "email": victim.email,
        "preferred_username": victim.email,
    }
    resp, flashes = _call_sso_callback(app, module_path, "azure", userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "account.login")
    db_session.expire(victim)
    assert victim.external_id == stored_external_id


@pytest.mark.parametrize("module_path", MODULES)
def test_azure_configured_tenant_matching_preferred_username_succeeds(app, db_session, module_path, monkeypatch):
    """The legitimate Azure first-link case: a specific tenant is
    configured, the token is actually from that tenant, and its
    preferred_username matches the existing user's stored email."""
    monkeypatch.setenv("AZURE_AD_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    _enable_sso(db_session)
    victim = _make_user(db_session, f"victim-azure-legit-{uuid.uuid4().hex[:6]}@example.com")
    db_session.commit()

    userinfo = {
        "sub": "real-azure-sub",
        "oid": "real-oid",
        "tid": "11111111-1111-1111-1111-111111111111",
        "email": victim.email,
        "preferred_username": victim.email,
        "given_name": victim.first_name,
        "family_name": victim.last_name,
    }
    resp, _flashes = _call_sso_callback(app, module_path, "azure", userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "main.index")
    db_session.expire(victim)
    assert victim.external_id == "11111111-1111-1111-1111-111111111111:real-oid"
    assert victim.sso_provider == "azure"


@pytest.mark.parametrize("module_path", MODULES)
@pytest.mark.parametrize(
    "provider,userinfo_extra",
    [
        ("okta", {"sub": "new-okta-sub-unverified", "email_verified": False}),
        ("okta", {"sub": "new-okta-sub-verified", "email_verified": True}),
        ("azure", {"sub": "new-azure-sub", "oid": "new-oid", "tid": "common"}),
    ],
    ids=["okta-unverified", "okta-verified", "azure-default-tenant"],
)
def test_brand_new_email_always_succeeds_in_creating_an_account(
    app, db_session, module_path, provider, userinfo_extra, monkeypatch
):
    """A brand-new email (no existing user) must still succeed in creating a
    new account for either provider, verified or not -- this is a fresh
    signup, not a takeover, and must not be blocked."""
    monkeypatch.delenv("AZURE_AD_TENANT_ID", raising=False)
    _enable_sso(db_session)
    from app.models.user import User

    email = f"brand-new-{uuid.uuid4().hex[:10]}@example.com"
    assert User.find_by_email(email) is None

    userinfo = {"email": email, "given_name": "New", "family_name": "User", **userinfo_extra}
    resp, _flashes = _call_sso_callback(app, module_path, provider, userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "main.index"), (
        f"a brand-new email via {provider} must still create an account, "
        f"got redirect {resp.location!r}"
    )
    created = User.find_by_email(email)
    assert created is not None
    assert created.sso_provider == provider


@pytest.mark.parametrize("module_path", MODULES)
def test_already_linked_okta_user_signs_in_despite_mismatched_email_claim(app, db_session, module_path, monkeypatch):
    """The regression check that matters most: an already-linked user
    (existing external_id + sso_provider match) keeps signing in exactly as
    before, completely unaffected by the new email-claim checks, even when
    the current token's email claim does not match their stored email at
    all -- their identity is anchored to the subject, not the email."""
    _enable_sso(db_session)
    user = _make_user(
        db_session,
        f"already-linked-okta-{uuid.uuid4().hex[:6]}@example.com",
        external_id="okta-subject-999",
        sso_provider="okta",
    )
    db_session.commit()
    original_email = user.email

    userinfo = {
        "sub": "okta-subject-999",
        "email": "someone-else-entirely@example.com",
        "email_verified": False,
    }
    resp, _flashes = _call_sso_callback(app, module_path, "okta", userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "main.index")
    db_session.expire(user)
    assert user.external_id == "okta-subject-999"
    assert user.sso_provider == "okta"
    assert user.email == original_email


@pytest.mark.parametrize("module_path", MODULES)
def test_already_linked_azure_user_signs_in_despite_mismatched_email_claim(app, db_session, module_path, monkeypatch):
    """Same regression check, Azure side: a user already linked under the
    new oid+tid composite keeps signing in unaffected, even with a
    non-matching email claim."""
    monkeypatch.delenv("AZURE_AD_TENANT_ID", raising=False)
    _enable_sso(db_session)
    composite = "33333333-3333-3333-3333-333333333333:already-oid"
    user = _make_user(
        db_session,
        f"already-linked-azure-{uuid.uuid4().hex[:6]}@example.com",
        external_id=composite,
        sso_provider="azure",
    )
    db_session.commit()

    userinfo = {
        "sub": "irrelevant-sub-not-used-for-lookup",
        "oid": "already-oid",
        "tid": "33333333-3333-3333-3333-333333333333",
        "email": "someone-else-entirely@example.com",
    }
    resp, _flashes = _call_sso_callback(app, module_path, "azure", userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "main.index")
    db_session.expire(user)
    assert user.external_id == composite
    assert user.sso_provider == "azure"


@pytest.mark.parametrize("module_path", MODULES)
def test_legacy_azure_sub_link_is_recognised_and_migrated_to_oid_tid_composite(
    app, db_session, module_path, monkeypatch
):
    """A user already linked BEFORE this fix shipped has external_id stored
    as the raw Azure `sub` value (the old scheme). Signing in again with a
    token whose `sub` matches that stored value -- but which naturally also
    carries `oid`/`tid` now -- must be recognised as already-linked (no
    email check applied), must succeed, and must end up with external_id
    rewritten to the new oid+tid composite so future sign-ins use the new
    scheme."""
    monkeypatch.delenv("AZURE_AD_TENANT_ID", raising=False)
    _enable_sso(db_session)
    legacy_sub = "legacy-raw-sub-value"
    user = _make_user(
        db_session,
        f"pre-fix-linked-{uuid.uuid4().hex[:6]}@example.com",
        external_id=legacy_sub,
        sso_provider="azure",
    )
    db_session.commit()

    userinfo = {
        "sub": legacy_sub,
        "oid": "new-scheme-oid",
        "tid": "44444444-4444-4444-4444-444444444444",
        "email": user.email,
    }
    resp, _flashes = _call_sso_callback(app, module_path, "azure", userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "main.index"), (
        "a pre-fix Azure link (external_id == raw sub) must be recognised "
        "as already-linked and succeed, not fall through to the "
        "email-verification gate"
    )
    db_session.expire(user)
    assert user.external_id == "44444444-4444-4444-4444-444444444444:new-scheme-oid", (
        "the legacy link must be migrated to the new tenant-qualified "
        "oid+tid composite so future sign-ins use the new scheme"
    )
    assert user.sso_provider == "azure"


def test_route1_new_azure_user_external_id_is_oid_tid_composite_not_raw_sub(app, db_session, monkeypatch):
    """Route 1 specifically: confirm the Azure external_id stored for a
    brand-new Azure link is the tenant-qualified oid+tid composite, not the
    raw sub claim."""
    monkeypatch.delenv("AZURE_AD_TENANT_ID", raising=False)
    _enable_sso(db_session)
    from app.models.user import User

    email = f"brand-new-azure-{uuid.uuid4().hex[:10]}@example.com"
    userinfo = {
        "sub": "raw-sub-must-not-be-stored",
        "oid": "fresh-oid",
        "tid": "common",
        "email": email,
        "given_name": "Fresh",
        "family_name": "Signup",
    }
    resp, _flashes = _call_sso_callback(app, MODULE_V1, "azure", userinfo, monkeypatch)

    assert _redirect_path(resp) == _expect_path(app, "main.index")
    created = User.find_by_email(email)
    assert created is not None
    assert created.external_id == "common:fresh-oid"
    assert created.external_id != "raw-sub-must-not-be-stored"
