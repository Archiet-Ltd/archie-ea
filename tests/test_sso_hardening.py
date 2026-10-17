"""Sign-in hardening after per-organisation SSO: OIDC trust, session end on a link reset,
service accounts, the working organisation on the admin page, and the failed-password record.
Two real organisations wherever an organisation boundary is involved.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.sso_service import SSONotConfiguredError, SSOService
from tests.test_sso_cross_org_domain_claim_per_org import _configure_sso, _domain, _make_user

WELL_KNOWN = "/.well-known/openid-configuration"


# --------------------------------------------------------------------------- #
# OIDC: the issuer is bound to the address, the id_token is the identity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "address, issuer",
    [
        ("https://idp.example.com" + WELL_KNOWN, "https://idp.example.com"),
        ("https://idp.example.com/oauth2/default" + WELL_KNOWN, "https://idp.example.com/oauth2/default/"),
        ("https://login.example.net/tenant-1/v2.0" + WELL_KNOWN, "https://login.example.net/tenant-1/v2.0"),
    ],
)
def test_a_discovery_document_naming_the_addresss_own_issuer_is_accepted(address, issuer):
    SSOService._check_issuer_bound_to_address(address, {"issuer": issuer})


@pytest.mark.parametrize(
    "address, issuer",
    [
        ("https://idp.example.com" + WELL_KNOWN, "https://evil.example"),
        ("https://idp.example.com" + WELL_KNOWN, ""),
        ("https://login.example.net/common/v2.0" + WELL_KNOWN, "https://login.example.net/{tenantid}/v2.0"),
        ("https://idp.example.com/metadata.json", "https://idp.example.com"),
    ],
)
def test_a_discovery_document_naming_another_issuer_is_refused(address, issuer):
    with pytest.raises(SSONotConfiguredError):
        SSOService._check_issuer_bound_to_address(address, {"issuer": issuer})


class _Reply:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


@pytest.fixture
def oidc(monkeypatch, app):
    """An identity provider whose token exchange and userinfo answers the test sets."""
    import requests

    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard.socket, "getaddrinfo", lambda h, p, *a, **k: [(2, 1, 6, "", ("93.184.216.34", p))])
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())
    state = {"token": {"access_token": "tok", "id_token": "signed"}, "userinfo": None,
             "verified": {"sub": "u1", "email": "a@x.example"}}
    monkeypatch.setattr(
        SSOService, "_fetch_oidc_discovery",
        lambda self, url: {"token_endpoint": "https://idp.example.com/token",
                           "userinfo_endpoint": "https://idp.example.com/userinfo", "issuer": "https://idp.example.com"},
    )
    monkeypatch.setattr(SSOService, "_verify_id_token", lambda self, *a, **k: dict(state["verified"]))
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Reply(state["token"]))
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Reply(state["userinfo"]) if state["userinfo"] is not None else _Reply({}, 404))
    return state


def _config():
    from app.models.sso_config import SSOConfig

    return SSOConfig(organization_id=1, protocol="oidc", client_id="c", idp_metadata_url="https://idp.example.com" + WELL_KNOWN)


def test_a_token_response_without_an_id_token_is_refused_even_with_userinfo(oidc):
    oidc["token"] = {"access_token": "tok"}
    oidc["userinfo"] = {"sub": "u1", "email": "a@x.example"}
    with pytest.raises(SSONotConfiguredError, match="no id_token"):
        SSOService().handle_oidc_callback(_config(), "code", "s", "https://app.example.com/cb", expected_nonce="n")


def test_the_signed_claims_win_over_userinfo_and_userinfo_only_fills_gaps(oidc):
    oidc["userinfo"] = {"sub": "u1", "email": "attacker@x.example", "name": "Alice Smith"}
    claims = SSOService().handle_oidc_callback(_config(), "code", "s", "https://app.example.com/cb", expected_nonce="n")
    assert claims["email"] == "a@x.example"  # the signed address, not the unsigned one
    assert claims["name"] == "Alice Smith"  # a gap the id_token left


def test_userinfo_for_a_different_subject_is_refused(oidc):
    oidc["userinfo"] = {"sub": "someone-else", "email": "a@x.example"}
    with pytest.raises(SSONotConfiguredError, match="different subject"):
        SSOService().handle_oidc_callback(_config(), "code", "s", "https://app.example.com/cb", expected_nonce="n")


def test_a_failed_userinfo_call_leaves_the_signed_claims_in_force(oidc):
    claims = SSOService().handle_oidc_callback(_config(), "code", "s", "https://app.example.com/cb", expected_nonce="n")
    assert claims == {"sub": "u1", "email": "a@x.example"}


# --------------------------------------------------------------------------- #
# Reset ends sessions; service accounts; the working organisation
# --------------------------------------------------------------------------- #


def test_resetting_a_sign_in_link_ends_the_accounts_sessions(app, db_session, make_org):
    from app.services import session_registry

    org = make_org("reset")
    user = _make_user(db_session, org, f"m@{_domain('reset')}", external_id="sub-1", sso_provider="oidc")
    other = _make_user(db_session, org, f"o@{_domain('reset')}", external_id="sub-2", sso_provider="oidc")
    db_session.commit()
    with app.test_request_context("/"):
        mine = [session_registry.issue(user) for _ in range(2)]
        theirs = session_registry.issue(other)
    assert all(session_registry.is_active(sid) for sid in mine)

    SSOService().reset_member_sign_in_link(org.id, user.id, actor_id=None, reason="test")

    assert not any(session_registry.is_active(sid) for sid in mine)
    assert session_registry.is_active(theirs)  # nobody else's session is touched
    db_session.refresh(user)
    assert user.external_id is None


def test_a_service_account_is_never_signed_in_or_linked_by_sso(app, db_session, make_org):
    org = make_org("svc")
    domain = _domain("svc")
    _configure_sso(db_session, org, domain)
    linked = _make_user(db_session, org, f"bot@{domain}", external_id="bot-sub", sso_provider="oidc")
    linked.is_service_user = True
    unlinked = _make_user(db_session, org, f"bot2@{domain}")
    unlinked.is_service_user = True
    db_session.commit()
    svc = SSOService()
    for claims in ({"sub": "bot-sub", "email": linked.email}, {"sub": "new-sub", "email": unlinked.email}):
        with pytest.raises(SSONotConfiguredError, match="service account"):
            svc.provision_user(org, claims)
    db_session.refresh(unlinked)
    assert unlinked.external_id is None


def test_the_admin_page_works_on_the_organisation_the_admin_is_working_in(app, db_session, make_org, client, login_as):
    from app.models.org_role import OrgRole
    from app.models.sso_config import SSOConfig
    from app.models.user import Role

    home, working = make_org("sso-home"), make_org("sso-working")
    admin = _make_user(db_session, home, f"a@{_domain('adm')}", role=Role.query.filter_by(name="Administrator").first())
    db_session.add(OrgRole(user_id=admin.id, organization_id=working.id, role="org_admin"))
    _configure_sso(db_session, home, _domain("home"))
    db_session.commit()

    login_as(client, admin)
    with client.session_transaction() as sess:
        sess["current_org_id"] = working.id
    posted = client.post("/admin/sso", data={"protocol": "oidc", "email_domain": _domain("work"),
                                             "client_id": "working-client"}, follow_redirects=False)
    assert posted.status_code in (200, 302)
    db_session.expire_all()
    assert SSOConfig.query.filter_by(organization_id=working.id).one().client_id == "working-client"
    assert SSOConfig.query.filter_by(organization_id=home.id).one().client_id == "client-id"


# --------------------------------------------------------------------------- #
# A failed password at the sign-in form is recorded before any redirect
# --------------------------------------------------------------------------- #


def test_a_wrong_password_for_a_platform_admin_in_a_claimed_domain_is_recorded(app, db_session, make_org, monkeypatch):
    from app.services import auth_audit

    org = make_org("fail")
    domain = _domain("fail")
    _configure_sso(db_session, org, domain)
    admin = _make_user(db_session, org, f"root@{domain}", password="Str0ng!Passw0rd", is_platform_admin=True)
    db_session.commit()
    seen = []
    monkeypatch.setattr(auth_audit, "record_login_failure", lambda email: seen.append(email))
    config = SSOService().config_for_login(admin.email, "wrong-password")
    assert config is not None and seen == [admin.email]
    assert SSOService().config_for_login(admin.email, "Str0ng!Passw0rd") is None and seen == [admin.email]
