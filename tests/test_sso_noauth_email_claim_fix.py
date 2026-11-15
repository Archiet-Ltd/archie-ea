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

Covers the shared identity helpers in app.auth.sso. The two global callbacks
that used them (one per account blueprint) were removed when Google and
Microsoft sign-in moved to the platform OIDC client; the identity rules those
callbacks enforced are tested against that client in
tests/test_social_sign_in_cross_org_*.py.

The per-organisation SSO flow (app/modules/auth/sso_routes.py,
app/services/sso_service.py) is a different, already-protected code path
and is out of scope here -- see tests/test_sso_id_token_verification.py
and tests/test_sso_mapping_tenant_isolation.py for its coverage.
"""

from __future__ import annotations

import pytest


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
