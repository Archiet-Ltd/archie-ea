"""Per-organisation sign-in domain scope.

An organisation's own identity provider signs in that organisation's own
members, in the domains its SSO configuration lists, by a stable subject:

- ``SSOService.provision_user`` (shared by the OIDC and SAML callbacks)
  requires a subject, matches a returning member by organisation and
  subject, refuses an address outside the configured domains, never
  re-points an already-linked member, never reaches a platform
  administrator, and tells a password member when a sign-in method is
  connected to their account.
- ``SSOService.config_for_login`` (both ``login()`` functions) sends only the
  configuring organisation's own accounts to its identity provider.
- ``SSOService.get_config_for_email`` falls back to password sign-in when
  two enabled configurations list the same domain.
- ``/admin/sso`` refuses a domain another organisation lists and a public
  mailbox domain, leaving the saved configuration as it was.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from tests import _isolation_sweep as sweep

_PASSWORD = "Str0ng!Passw0rd"
SENDER = "no-reply@example.com"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _domain(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:8]}.example"


def _configure_sso(db_session, org, email_domain, *, enabled=True, protocol="oidc", **fields):
    from app.models.sso_config import SSOConfig

    config = SSOConfig(
        organization_id=org.id,
        protocol=protocol,
        enabled=enabled,
        email_domain=email_domain,
        client_id="client-id",
        idp_metadata_url="https://idp.example/.well-known/openid-configuration",
        **fields,
    )
    db_session.add(config)
    db_session.flush()
    return config


def _make_user(db_session, org, email, *, password=None, external_id=None,
               sso_provider=None, is_platform_admin=False, role=None, confirmed=True):
    from app.models.user import User

    kwargs = dict(
        email=email,
        first_name="Existing",
        last_name="Member",
        organization_id=org.id,
        confirmed=confirmed,
        external_id=external_id,
        sso_provider=sso_provider,
        is_platform_admin=is_platform_admin,
    )
    if role is not None:
        kwargs["role"] = role
    user = User(**kwargs)
    if password is not None:
        user.password = password
    db_session.add(user)
    db_session.flush()
    return user


def _issue_reset_token(db_session, user):
    """Issue a password-reset token for ``user`` outside a request, the way
    TS-36 needs one in hand. Shared rather than duplicated."""
    from app.jobs.tenant_safe_job import platform_scope
    from app.models.account_token import PURPOSE_PASSWORD_RESET, AccountToken

    with platform_scope("test: issue a reset token"):
        _row, raw = AccountToken.issue(user, PURPOSE_PASSWORD_RESET)
        db_session.commit()
    return raw


def _snapshot(user):
    return (
        user.email,
        user.organization_id,
        user.external_id,
        user.sso_provider,
        user.first_name,
        user.last_name,
        user.is_platform_admin,
    )


def _reload(db_session, user_id):
    from app.models.user import User

    db_session.expire_all()
    return db_session.get(User, user_id)


def _users_with_email(db_session, email):
    from sqlalchemy import func

    from app.models.user import User

    return db_session.query(User).filter(func.lower(User.email) == email.lower()).all()


def _anonymous_client(app):
    """A client with no session, and no signed-in user cached on ``g``."""
    sweep._clear_g()
    return app.test_client()


def _session_user_id(client):
    with client.session_transaction() as sess:
        return sess.get("_user_id")


@pytest.fixture
def outbox(app):
    from app.extensions import mail

    with mail.record_messages() as messages:
        yield messages


@pytest.fixture
def mail_on(app, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", SENDER)


# --------------------------------------------------------------------------- #
# TS-20  P-1 and P-0                                                           #
# --------------------------------------------------------------------------- #


def test_asserted_email_outside_configured_domains_is_refused(db_session, make_org):
    from app.services.sso_service import SSONotConfiguredError, SSOService

    org = make_org("scope-outside")
    domain = _domain("acme")
    _configure_sso(db_session, org, domain)
    outside = f"x-{uuid.uuid4().hex[:6]}@other.example"

    with pytest.raises(SSONotConfiguredError) as refused:
        SSOService().provision_user(org, {"email": outside, "sub": "subject-outside"})
    assert "not in a domain your organisation's SSO is configured for" in str(refused.value)
    assert _users_with_email(db_session, outside) == []

    # An organisation with no SSO configuration lists no domains at all.
    bare = make_org("scope-unconfigured")
    with pytest.raises(SSONotConfiguredError):
        SSOService().provision_user(bare, {"email": f"y@{domain}", "sub": "subject-bare"})
    assert _users_with_email(db_session, f"y@{domain}") == []

    # P-0: claims with no subject are refused; the address is never the key.
    inside = f"no-subject-{uuid.uuid4().hex[:6]}@{domain}"
    for claims in ({"email": inside}, {"email": inside, "sub": ""}, {"email": inside, "sub": "  "},
                   {"email": inside, "sub": None}):
        with pytest.raises(SSONotConfiguredError) as missing:
            SSOService().provision_user(org, claims)
        assert "did not return the details needed to sign you in" in str(missing.value)
    assert _users_with_email(db_session, inside) == []

    # Positive control: the same organisation provisions an address in its domain.
    user = SSOService().provision_user(org, {"email": inside, "sub": "subject-inside"})
    assert user.organization_id == org.id
    assert user.external_id == "subject-inside"


# --------------------------------------------------------------------------- #
# TS-21  P-2                                                                   #
# --------------------------------------------------------------------------- #


def test_returning_member_matched_by_org_and_subject(db_session, make_org):
    from app.services.sso_service import SSOService

    org = make_org("scope-returning")
    domain = _domain("acme")
    _configure_sso(db_session, org, domain)
    member = _make_user(db_session, org, f"alice@{domain}",
                        external_id="subject-alice", sso_provider="oidc")

    renamed = SSOService().provision_user(org, {
        "email": f"alice.renamed@{domain}", "sub": "subject-alice", "given_name": "Alicia",
    })
    assert renamed.id == member.id
    assert renamed.first_name == "Alicia"

    # The subject, not the address, identifies a returning member: an
    # address outside the configured domains still reaches the linked member,
    # and the account's own address never changes.
    moved = SSOService().provision_user(org, {
        "email": "alice@elsewhere.example", "sub": "subject-alice",
    })
    assert moved.id == member.id
    assert _reload(db_session, member.id).email == f"alice@{domain}"
    assert _users_with_email(db_session, f"alice.renamed@{domain}") == []
    assert _users_with_email(db_session, "alice@elsewhere.example") == []

    # Another member's address with alice's subject still reaches alice: an
    # implementation that looked the address up first would land on bob.
    bob = _make_user(db_session, org, f"bob@{domain}",
                     external_id="subject-bob", sso_provider="oidc")
    bob_before = _snapshot(bob)
    alice_email = _reload(db_session, member.id).email
    crossed = SSOService().provision_user(org, {"email": f"bob@{domain}", "sub": "subject-alice"})
    assert crossed.id == member.id
    assert _snapshot(_reload(db_session, bob.id)) == bob_before
    assert _reload(db_session, member.id).email == alice_email


# --------------------------------------------------------------------------- #
# TS-22  P-4 (re-pointing)                                                     #
# --------------------------------------------------------------------------- #


def test_member_linked_to_another_subject_is_not_repointed(db_session, make_org):
    from app.services.sso_service import SSONotConfiguredError, SSOService

    org = make_org("scope-repoint")
    domain = _domain("acme")
    _configure_sso(db_session, org, domain)
    member = _make_user(db_session, org, f"bob@{domain}", password=_PASSWORD,
                        external_id="old", sso_provider="oidc")
    before = _snapshot(member)

    with pytest.raises(SSONotConfiguredError) as refused:
        SSOService().provision_user(org, {"email": f"bob@{domain}", "sub": "new", "given_name": "Robert"})
    assert "linked to a different sign-in" in str(refused.value)

    after = _reload(db_session, member.id)
    assert after.external_id == "old"
    assert _snapshot(after) == before

    # A member linked by another sign-in method keeps that link too.
    other = _make_user(db_session, org, f"carol@{domain}", password=_PASSWORD,
                       external_id="shared-subject", sso_provider="okta")
    with pytest.raises(SSONotConfiguredError):
        SSOService().provision_user(org, {"email": f"carol@{domain}", "sub": "shared-subject"})
    reloaded = _reload(db_session, other.id)
    assert (reloaded.external_id, reloaded.sso_provider) == ("shared-subject", "okta")


# --------------------------------------------------------------------------- #
# TS-23  P-4 (platform administrators)                                         #
# --------------------------------------------------------------------------- #


def test_platform_admin_is_not_signed_in_through_org_sso(db_session, make_org):
    from app.services.sso_service import SSONotConfiguredError, SSOService

    org = make_org("scope-platform-admin")
    domain = _domain("acme")
    _configure_sso(db_session, org, domain)

    # Found by address, not yet linked.
    by_email = _make_user(db_session, org, f"operator@{domain}", password=_PASSWORD,
                          is_platform_admin=True)
    before = _snapshot(by_email)
    with pytest.raises(SSONotConfiguredError) as refused:
        SSOService().provision_user(org, {"email": f"operator@{domain}", "sub": "idp-operator"})
    assert "signs in with a password" in str(refused.value)
    assert _snapshot(_reload(db_session, by_email.id)) == before

    # Already linked to this organisation and subject (the returning-member path).
    linked = _make_user(db_session, org, f"operator2@{domain}", password=_PASSWORD,
                        external_id="idp-operator-2", sso_provider="oidc", is_platform_admin=True)
    before_linked = _snapshot(linked)
    with pytest.raises(SSONotConfiguredError) as refused_linked:
        SSOService().provision_user(org, {
            "email": f"operator2@{domain}", "sub": "idp-operator-2", "given_name": "Changed",
        })
    assert "signs in with a password" in str(refused_linked.value)
    assert _snapshot(_reload(db_session, linked.id)) == before_linked


# --------------------------------------------------------------------------- #
# TS-24  P-5 and the connection notice                                         #
# --------------------------------------------------------------------------- #


def _oidc_callback(app, monkeypatch, org, claims):
    """Drive /auth/sso/callback/oidc with the IdP exchange replaced by
    ``claims``, and close the response so mail sent after it goes out."""
    from app.modules.auth import sso_routes

    monkeypatch.setattr(
        sso_routes._svc, "handle_oidc_callback", lambda *args, **kwargs: dict(claims)
    )
    client = _anonymous_client(app)
    with client.session_transaction() as sess:
        sess["sso_state"] = "state-scope"
        sess["sso_nonce"] = "nonce-scope"
        sess["sso_org_id"] = org.id
    resp = client.get("/auth/sso/callback/oidc?code=abc&state=state-scope")
    resp.close()
    return client, resp


def test_first_link_of_password_member_sends_notice(app, db_session, make_org, mail_on, outbox,
                                                     monkeypatch):
    org = make_org("scope-notice")
    domain = _domain("acme")
    _configure_sso(db_session, org, domain)
    member = _make_user(db_session, org, f"dana@{domain}", password=_PASSWORD)
    db_session.commit()

    client, resp = _oidc_callback(app, monkeypatch, org, {"email": f"dana@{domain}", "sub": "subject-dana"})

    assert resp.status_code in (302, 303)
    assert _session_user_id(client) == str(member.id)
    reloaded = _reload(db_session, member.id)
    assert (reloaded.external_id, reloaded.sso_provider) == ("subject-dana", "oidc")
    assert len(outbox) == 1
    message = outbox[0]
    assert message.recipients == [member.email]
    assert "A sign-in method was connected to your account" in message.subject
    assert "your organisation's single sign-on" in message.body
    assert "UTC" in message.body
    assert "If this wasn't you, reset your password and contact your administrator." in message.body
    assert org.name not in message.body
    assert org.name not in message.html
    assert "subject-dana" not in message.body

    # The returning sign-in that follows sends nothing more.
    _oidc_callback(app, monkeypatch, org, {"email": f"dana@{domain}", "sub": "subject-dana"})
    assert len(outbox) == 1

    # A member invited but never signed in (no password) is linked silently.
    invited = _make_user(db_session, org, f"erin@{domain}")
    db_session.commit()
    _oidc_callback(app, monkeypatch, org, {"email": f"erin@{domain}", "sub": "subject-erin"})
    assert _reload(db_session, invited.id).external_id == "subject-erin"
    assert len(outbox) == 1

    # A brand-new address is created, with no notice.
    _oidc_callback(app, monkeypatch, org, {"email": f"frank@{domain}", "sub": "subject-frank"})
    assert len(_users_with_email(db_session, f"frank@{domain}")) == 1
    assert len(outbox) == 1


def test_connection_notice_failure_never_blocks_sign_in(db_session, make_org, mail_on, monkeypatch):
    import app.flask_email as flask_email
    from app.services.sso_service import SSOService

    org = make_org("scope-notice-failure")
    domain = _domain("acme")
    _configure_sso(db_session, org, domain)
    member = _make_user(db_session, org, f"gina@{domain}", password=_PASSWORD)

    def _broken(*args, **kwargs):
        raise RuntimeError("mail server unavailable")

    monkeypatch.setattr(flask_email, "deliver_email_after_response", _broken)
    user = SSOService().provision_user(org, {"email": f"gina@{domain}", "sub": "subject-gina"})
    assert user.id == member.id
    assert _reload(db_session, member.id).external_id == "subject-gina"


# --------------------------------------------------------------------------- #
# TS-25  P-6, both login() functions                                           #
# --------------------------------------------------------------------------- #


def _post_login(client, email, password=_PASSWORD):
    return client.post(
        "/account/login",
        data={"email": email, "password": password, "remember_me": ""},
        follow_redirects=False,
    )


def test_login_does_not_redirect_another_orgs_user_to_the_claiming_idp(app, db_session, make_org):
    """The registered (v2) login."""
    from app.services.sso_service import SSOService

    claimed = _domain("claimedcorp")
    org_x = make_org("scope-claiming")
    org_y = make_org("scope-holding")
    config = _configure_sso(db_session, org_x, claimed)
    other_orgs_user = _make_user(db_session, org_y, f"pat@{claimed}", password=_PASSWORD)
    own_member = _make_user(db_session, org_x, f"quinn@{claimed}", password=_PASSWORD)
    db_session.commit()

    assert SSOService().config_for_login(other_orgs_user.email, _PASSWORD) is None
    assert SSOService().config_for_login(own_member.email, _PASSWORD).id == config.id
    assert SSOService().config_for_login(f"newcomer@{claimed}", _PASSWORD).id == config.id
    assert SSOService().config_for_login(other_orgs_user.email, "wrong-password").id == config.id

    client = _anonymous_client(app)
    resp = _post_login(client, other_orgs_user.email)
    assert resp.status_code in (302, 303)
    assert "/auth/sso/initiate" not in resp.headers.get("Location", "")
    assert _session_user_id(client) == str(other_orgs_user.id)

    # Positive control: the claiming organisation's own member is sent to its IdP.
    own_client = _anonymous_client(app)
    own = _post_login(own_client, own_member.email)
    assert own.status_code in (302, 303)
    assert "/auth/sso/initiate" in own.headers.get("Location", "")
    assert _session_user_id(own_client) is None


def test_v1_login_does_not_redirect_another_orgs_user_to_the_claiming_idp(monkeypatch):
    """The rollback (v1) login, on an app built with USE_ACCOUNT_GUARDRAILS
    off, the same forced-v1 pattern ``tests/test_mfa_login_gate.py`` uses.
    That app has its own engine, so its rows are committed for real and the
    SSO configuration is removed afterwards."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.sso_config import SSOConfig
    from app.models.user import User

    monkeypatch.setenv("USE_ACCOUNT_GUARDRAILS", "false")
    v1_app = create_app("testing")
    v1_app.config["TESTING"] = True
    v1_app.config["WTF_CSRF_ENABLED"] = False
    assert "app.modules.account.routes.account_routes" in v1_app.view_functions["account.login"].__module__

    claimed = _domain("claimedcorp-v1")
    with v1_app.app_context():
        suffix = uuid.uuid4().hex[:10]
        org_x = Organization(name=f"Test v1-claiming {suffix}", slug=f"test-v1-claiming-{suffix}")
        org_y = Organization(name=f"Test v1-holding {suffix}", slug=f"test-v1-holding-{suffix}")
        db.session.add_all([org_x, org_y])
        db.session.flush()
        config = SSOConfig(organization_id=org_x.id, protocol="oidc", enabled=True,
                           email_domain=claimed, client_id="client-id",
                           idp_metadata_url="https://idp.example/.well-known/openid-configuration")
        db.session.add(config)
        holder = User(email=f"pat@{claimed}", organization_id=org_y.id, confirmed=True)
        holder.password = _PASSWORD
        member = User(email=f"quinn@{claimed}", organization_id=org_x.id, confirmed=True)
        member.password = _PASSWORD
        db.session.add_all([holder, member])
        db.session.commit()
        config_id, holder_id, holder_email, member_email = (
            config.id, holder.id, holder.email, member.email,
        )
        db.session.remove()

    try:
        client = v1_app.test_client()
        resp = _post_login(client, holder_email)
        assert resp.status_code in (302, 303)
        assert "/auth/sso/initiate" not in resp.headers.get("Location", "")
        assert _session_user_id(client) == str(holder_id)

        own_client = v1_app.test_client()
        own = _post_login(own_client, member_email)
        assert own.status_code in (302, 303)
        assert "/auth/sso/initiate" in own.headers.get("Location", "")
        assert _session_user_id(own_client) is None
    finally:
        with v1_app.app_context():
            leftover = db.session.get(SSOConfig, config_id)
            if leftover is not None:
                db.session.delete(leftover)
                db.session.commit()
            db.session.remove()


# --------------------------------------------------------------------------- #
# TS-26  P-7                                                                   #
# --------------------------------------------------------------------------- #


def test_contested_domain_fails_closed(db_session, make_org, caplog):
    from app.services.sso_service import SSOService

    contested = _domain("contested")
    first = _configure_sso(db_session, make_org("scope-contest-a"), contested)
    second = _configure_sso(db_session, make_org("scope-contest-b"), f"spare.example, {contested.upper()}")
    address = f"someone@{contested}"

    from app.services.sso_service import SSODomainConflictError

    with caplog.at_level(logging.WARNING, logger="app.services.sso_service"):
        with pytest.raises(SSODomainConflictError):
            SSOService().get_config_for_email(address)
        with pytest.raises(SSODomainConflictError):
            SSOService().config_for_login(address, _PASSWORD)

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(str(first.id) in m and str(second.id) in m for m in warnings), warnings
    assert not any(address in m for m in warnings)

    # Positive control: a domain exactly one enabled configuration lists.
    sole = _domain("sole")
    only = _configure_sso(db_session, make_org("scope-sole"), sole)
    assert SSOService().get_config_for_email(f"someone@{sole}").id == only.id

    # A disabled configuration does not contest an enabled one.
    shared = _domain("shared")
    enabled = _configure_sso(db_session, make_org("scope-shared-on"), shared)
    _configure_sso(db_session, make_org("scope-shared-off"), shared, enabled=False)
    assert SSOService().get_config_for_email(f"someone@{shared}").id == enabled.id


# --------------------------------------------------------------------------- #
# TS-27  P-8                                                                   #
# --------------------------------------------------------------------------- #


def _org_admin(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()
    admin = _make_user(db_session, org, f"admin-{uuid.uuid4().hex[:8]}@example.com",
                       password=_PASSWORD, role=admin_role)
    admin.is_org_admin = True
    OrgRole.set_role(org.id, admin.id, "org_admin", granted_by_id=admin.id)
    db_session.flush()
    return admin


def _flashes(client):
    with client.session_transaction() as sess:
        return [message for _, message in sess.get("_flashes", [])]


def test_admin_sso_refuses_claimed_and_public_domains(app, db_session, make_org, login_as, monkeypatch):
    # The outbound address guard has its own tests; the replacement host here does not resolve.
    from app.services import sso_service
    monkeypatch.setattr(sso_service, "check_idp_url", lambda url: url)
    from app.models.sso_config import SSOConfig

    org_a = make_org("scope-admin-own")
    org_b = make_org("scope-admin-other")
    own_domain = _domain("own")
    other_domain = _domain("other")
    disabled_domain = _domain("parked")
    _configure_sso(db_session, org_a, own_domain)
    _configure_sso(db_session, org_b, other_domain)
    _configure_sso(db_session, make_org("scope-admin-parked"), disabled_domain, enabled=False)
    admin = _org_admin(db_session, org_a)
    db_session.commit()

    def saved():
        db_session.expire_all()
        config = SSOConfig.query.filter_by(organization_id=org_a.id).one()
        return (config.protocol, config.email_domain, config.enabled, config.client_id,
                config.idp_metadata_url)

    before = saved()

    def post(email_domain):
        client = app.test_client()
        login_as(client, admin)
        resp = client.post("/admin/sso", data={
            "protocol": "oidc", "enabled": "on", "email_domain": email_domain,
            "client_id": "replacement-client",
            "idp_metadata_url": "https://replacement.example/.well-known/openid-configuration",
        }, follow_redirects=False)
        return client, resp

    for refused_value, problem in (
        (other_domain, other_domain),
        (f"{own_domain}, {other_domain.upper()} ", other_domain),
        ("gmail.com", "gmail.com"),
        (f"{own_domain},  Hotmail.COM", "hotmail.com"),
    ):
        client, resp = post(refused_value)
        assert resp.status_code in (302, 303), refused_value
        assert resp.headers["Location"].endswith("/admin/sso"), resp.headers["Location"]
        messages = _flashes(client)
        assert any(problem in m for m in messages), (refused_value, messages)
        assert not any("SSO configuration saved." in m for m in messages)
        assert saved() == before, refused_value

    # A disabled configuration holds no claim, so its domain can be saved here.
    client, resp = post(disabled_domain)
    assert any("SSO configuration saved." in m for m in _flashes(client))

    # Re-saving the organisation's own, already-claimed domain is allowed.
    client, resp = post(own_domain)
    assert resp.status_code in (302, 303)
    assert any("SSO configuration saved." in m for m in _flashes(client))
    after = saved()
    assert after[1] == own_domain
    assert after[3] == "replacement-client"


# --------------------------------------------------------------------------- #
# TS-28  P-3                                                                   #
# --------------------------------------------------------------------------- #


def test_mixed_case_stored_email_matches_case_insensitively(db_session, make_org):
    from sqlalchemy import text

    from app.services.sso_service import SSOService

    org = make_org("scope-case")
    domain = _domain("acme")
    _configure_sso(db_session, org, domain)
    stored = _make_user(db_session, org, f"alice@{domain}")
    mixed = f"Alice@{domain.capitalize()}"
    # Bypass the model's address validator, as an older row may predate it.
    db_session.execute(text("UPDATE users SET email = :email WHERE id = :id"),
                       {"email": mixed, "id": stored.id})
    db_session.flush()

    user = SSOService().provision_user(org, {"email": f"alice@{domain}", "sub": "subject-alice"})

    assert user.id == stored.id
    assert [u.id for u in _users_with_email(db_session, mixed)] == [stored.id]


def test_cross_org_member_in_a_claimed_domain_is_still_refused(db_session, make_org):
    """P-3: an address in a domain this organisation lists, whose account
    belongs to another organisation, is refused and that account unchanged."""
    from app.services.sso_service import SSONotConfiguredError, SSOService

    claimed = _domain("claimedcorp")
    home = make_org("scope-home")
    claiming = make_org("scope-claimer")
    _configure_sso(db_session, claiming, claimed)
    holder = _make_user(db_session, home, f"hana@{claimed}", password=_PASSWORD)
    before = _snapshot(holder)

    with pytest.raises(SSONotConfiguredError) as refused:
        SSOService().provision_user(claiming, {"email": holder.email, "sub": "claimer-subject"})
    assert "different organisation" in str(refused.value)
    assert _snapshot(_reload(db_session, holder.id)) == before
    assert len(_users_with_email(db_session, holder.email)) == 1


# --------------------------------------------------------------------------- #
# TS-29  SAML caller                                                           #
# --------------------------------------------------------------------------- #


def _saml_callback(app, monkeypatch, org, claims):
    from app.modules.auth import sso_routes

    monkeypatch.setattr(
        sso_routes._svc, "handle_saml_callback",
        lambda config, saml_response_b64, base_url, expected_request_id=None: dict(claims),
    )
    client = _anonymous_client(app)
    with client.session_transaction() as sess:
        sess["saml_request_id"] = "request-id"
        sess["sso_org_id"] = org.id
    resp = client.post("/auth/sso/callback/saml", data={"SAMLResponse": "signed-response"})
    return client, resp


def test_saml_assertion_outside_configured_domains_is_refused(app, db_session, make_org, monkeypatch):
    org = make_org("scope-saml")
    domain = _domain("acme")
    _configure_sso(
        db_session, org, domain, protocol="saml",
        idp_sso_url="https://idp.example/saml/sso",
        idp_x509_cert="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
    )
    db_session.commit()
    outside = f"x-{uuid.uuid4().hex[:6]}@other.example"

    client, resp = _saml_callback(app, monkeypatch, org, {"email": outside, "sub": "saml-name-id"})

    assert resp.status_code == 400
    assert _session_user_id(client) is None
    assert _users_with_email(db_session, outside) == []

    # Positive control: an assertion in the configured domain signs in.
    inside = f"y-{uuid.uuid4().hex[:6]}@{domain}"
    ok_client, ok = _saml_callback(app, monkeypatch, org, {"email": inside, "sub": "saml-name-id-2"})
    assert ok.status_code in (302, 303), ok.get_data(as_text=True)[:500]
    created = _users_with_email(db_session, inside)
    assert len(created) == 1 and created[0].organization_id == org.id
    assert _session_user_id(ok_client) == str(created[0].id)


# --------------------------------------------------------------------------- #
# TS-30, TS-31  SAML subject                                                   #
# --------------------------------------------------------------------------- #

_SAML_ASSERTION_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_TRANSIENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:transient"
_PERSISTENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
_SUBJECT_ID = "urn:oasis:names:tc:SAML:attribute:subject-id"
_PAIRWISE_ID = "urn:oasis:names:tc:SAML:attribute:pairwise-id"


def _assertion(name_id, *, name_id_format=None, attributes=()):
    """A parsed <saml:Assertion> with one NameID and the given attributes.
    No signature: _extract_claims takes the already-verified assertion."""
    from lxml import etree

    ns = _SAML_ASSERTION_NS
    assertion = etree.Element(f"{{{ns}}}Assertion", nsmap={"saml": ns})
    subject = etree.SubElement(assertion, f"{{{ns}}}Subject")
    element = etree.SubElement(subject, f"{{{ns}}}NameID")
    if name_id_format is not None:
        element.set("Format", name_id_format)
    element.text = name_id
    statement = etree.SubElement(assertion, f"{{{ns}}}AttributeStatement")
    for name, value in attributes:
        attribute = etree.SubElement(statement, f"{{{ns}}}Attribute", Name=name)
        etree.SubElement(attribute, f"{{{ns}}}AttributeValue").text = value
    return assertion


def test_saml_transient_nameid_uses_the_stable_subject_attribute(db_session, make_org):
    from app.services.saml_service import SAMLService
    from app.services.sso_service import SSOService

    org = make_org("scope-saml-transient")
    domain = _domain("acme")
    _configure_sso(db_session, org, domain, protocol="saml")
    email = f"alice@{domain}"
    attributes = (("email", email), (_SUBJECT_ID, "alice-7@acme"))

    first_claims = SAMLService()._extract_claims(
        _assertion("T1", name_id_format=_TRANSIENT, attributes=attributes))
    second_claims = SAMLService()._extract_claims(
        _assertion("T2", name_id_format=_TRANSIENT, attributes=attributes))
    assert first_claims["sub"] == "alice-7@acme"
    assert second_claims["sub"] == "alice-7@acme"
    assert first_claims["email"] == email

    first = SSOService().provision_user(org, first_claims)
    assert first.external_id == "alice-7@acme"
    second = SSOService().provision_user(org, second_claims)
    assert second.id == first.id
    assert len(_users_with_email(db_session, email)) == 1

    # pairwise-id wins over subject-id, whichever order they arrive in.
    both = SAMLService()._extract_claims(_assertion(
        "T3", name_id_format=_TRANSIENT,
        attributes=(("email", email), (_SUBJECT_ID, "alice-7@acme"), (_PAIRWISE_ID, "pairwise-alice")),
    ))
    assert both["sub"] == "pairwise-alice"


def test_saml_transient_nameid_without_a_stable_subject_is_refused():
    from app.services.saml_service import (
        _MSG_TRANSIENT_SUBJECT,
        SAMLService,
        SAMLVerificationError,
    )

    with pytest.raises(SAMLVerificationError) as refused:
        SAMLService()._extract_claims(_assertion(
            "T1", name_id_format=_TRANSIENT, attributes=(("email", "erin@acme.example"),)))
    assert str(refused.value) == _MSG_TRANSIENT_SUBJECT

    # A transient value is never the address either.
    with pytest.raises(SAMLVerificationError) as no_subject:
        SAMLService()._extract_claims(_assertion("t1@acme.example", name_id_format=_TRANSIENT))
    assert str(no_subject.value) == _MSG_TRANSIENT_SUBJECT

    # Positive controls: a persistent NameID, and one with no Format, are the
    # subject exactly as before.
    persistent = SAMLService()._extract_claims(_assertion(
        " persistent-1 ", name_id_format=_PERSISTENT, attributes=(("email", "erin@acme.example"),)))
    assert persistent["sub"] == "persistent-1"
    # With no Format the NameID is unspecified: it is the address, never the subject.
    with pytest.raises(SAMLVerificationError):
        SAMLService()._extract_claims(_assertion("Carol@Acme.example"))


# --------------------------------------------------------------------------- #
# TS-32  Resetting a member's sign-in link                                     #
# --------------------------------------------------------------------------- #


def test_org_admin_can_reset_a_members_sign_in_link(app, db_session, make_org, login_as):
    import json

    from app.security.audit import AuditEvent
    from app.services.sso_service import SSONotConfiguredError, SSOService

    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    org = make_org("scope-reset")
    # Four people share this organisation; an unlimited plan keeps the seat
    # check out of a test that is not about it.
    db_session.add(Subscription(
        organization_id=org.id, plan=SubscriptionPlan.enterprise,
        status=SubscriptionStatus.active, seats_purchased=0,
    ))
    db_session.flush()
    other_org = make_org("scope-reset-other")
    domain = _domain("acme")
    _configure_sso(db_session, org, domain)
    admin = _org_admin(db_session, org)
    member = _make_user(db_session, org, f"ivy@{domain}", external_id="old-subject", sso_provider="oidc")
    outsider = _make_user(db_session, other_org, f"jon-{uuid.uuid4().hex[:6]}@other.example",
                          external_id="outsider-subject", sso_provider="oidc")
    operator = _make_user(db_session, org, f"operator@{domain}", password=_PASSWORD,
                          external_id="operator-subject", sso_provider="oidc", is_platform_admin=True)
    plain = _make_user(db_session, org, f"kim@{domain}", password=_PASSWORD)
    db_session.commit()

    def events():
        db_session.expire_all()
        return AuditEvent.query.filter_by(action="sso_link_reset", resource_id=str(member.id)).all()

    # The lockout: a new subject for an already-linked member is refused.
    with pytest.raises(SSONotConfiguredError) as locked:
        SSOService().provision_user(org, {"email": member.email, "sub": "new-subject"})
    assert "linked to a different sign-in" in str(locked.value)

    client = app.test_client()
    login_as(client, admin)
    resp = client.post(f"/admin/team/member/{member.id}/reset-sign-in")
    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].endswith("/admin/team"), resp.headers["Location"]
    assert any("Single sign-on link reset." in m for m in _flashes(client))
    reset = _reload(db_session, member.id)
    assert (reset.external_id, reset.sso_provider) == (None, None)

    recorded = events()
    assert len(recorded) == 1
    details = json.loads(recorded[0].details)
    assert details["organization_id"] == org.id
    assert details["actor_id"] == admin.id
    assert details["previous_provider"] == "oidc"
    assert "old-subject" not in recorded[0].details
    assert member.email not in recorded[0].details

    # The next sign-in links the member to the new subject.
    relinked = SSOService().provision_user(org, {"email": member.email, "sub": "new-subject"})
    assert relinked.id == member.id
    assert relinked.external_id == "new-subject"

    # Another organisation's member is not found, and stays as it was.
    outsider_before = _snapshot(outsider)
    login_as(client, admin)
    assert client.post(f"/admin/team/member/{outsider.id}/reset-sign-in").status_code == 404
    assert _snapshot(_reload(db_session, outsider.id)) == outsider_before

    # A platform administrator in the same organisation is refused with the error flash.
    operator_before = _snapshot(operator)
    flash_client = app.test_client()
    login_as(flash_client, admin)
    refused = flash_client.post(f"/admin/team/member/{operator.id}/reset-sign-in")
    assert refused.status_code in (302, 303)
    assert "A platform administrator's sign-in cannot be reset here." in _flashes(flash_client)
    assert _snapshot(_reload(db_session, operator.id)) == operator_before

    # A member who is not an admin is refused exactly as the Team page refuses them.
    relinked_before = _snapshot(_reload(db_session, member.id))
    plain_client = app.test_client()
    login_as(plain_client, plain)
    team_status = plain_client.get("/admin/team").status_code
    assert team_status == 403
    login_as(plain_client, plain)
    assert plain_client.post(f"/admin/team/member/{member.id}/reset-sign-in").status_code == team_status
    assert _snapshot(_reload(db_session, member.id)) == relinked_before

    assert len(events()) == 1


# --------------------------------------------------------------------------- #
# TS-33, TS-34  The sign-in form does not reveal what an address holds         #
# --------------------------------------------------------------------------- #


def _claimed_domain_accounts(db_session, make_org, label):
    claimed = _domain(label)
    org = make_org(f"scope-{label}")
    config = _configure_sso(db_session, org, claimed)
    _make_user(db_session, org, f"admin@{claimed}", password=_PASSWORD, is_platform_admin=True)
    _make_user(db_session, org, f"member@{claimed}", password=_PASSWORD)
    _make_user(db_session, org, f"nopass@{claimed}", is_platform_admin=True)
    db_session.commit()
    return claimed, config


def _without_email_param(location):
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(location)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "email"]
    return urlunsplit(parts._replace(query=urlencode(query)))


def test_login_form_does_not_reveal_platform_administrators(app, db_session, make_org):
    claimed, _ = _claimed_domain_accounts(db_session, make_org, "operatorcorp")

    locations = []
    for local in ("admin", "member", "nopass", "newcomer"):
        client = _anonymous_client(app)
        resp = _post_login(client, f"{local}@{claimed}", password="wrong-password")
        assert resp.status_code in (302, 303), local
        location = resp.headers.get("Location", "")
        assert "/auth/sso/initiate" in location, (local, location)
        assert _session_user_id(client) is None, local
        locations.append(_without_email_param(location))
    assert len(set(locations)) == 1, locations

    # The administrator who knows the password still reaches the password path.
    admin_client = _anonymous_client(app)
    admin_resp = _post_login(admin_client, f"admin@{claimed}")
    assert admin_resp.status_code in (302, 303)
    assert "/auth/sso/initiate" not in admin_resp.headers.get("Location", "")

    # Positive control: an organisation member with the right password still goes to the IdP.
    member_client = _anonymous_client(app)
    member_resp = _post_login(member_client, f"member@{claimed}")
    assert member_resp.status_code in (302, 303)
    assert "/auth/sso/initiate" in member_resp.headers.get("Location", "")


def test_config_for_login_runs_exactly_one_hash_check_per_claimed_address(db_session, make_org,
                                                                          monkeypatch):
    import app.services.sso_service as sso_service
    from app.services.sso_service import SSOService

    claimed, config = _claimed_domain_accounts(db_session, make_org, "hashcorp")
    real = sso_service.check_password_hash
    calls = []

    def counting(stored, password):
        calls.append(stored)
        return real(stored, password)

    monkeypatch.setattr(sso_service, "check_password_hash", counting)

    for email, password, expect_config in (
        (f"admin@{claimed}", "wrong-password", True),
        (f"admin@{claimed}", _PASSWORD, False),
        (f"member@{claimed}", _PASSWORD, True),
        (f"nopass@{claimed}", _PASSWORD, True),
        (f"newcomer@{claimed}", _PASSWORD, True),
    ):
        before = len(calls)
        result = SSOService().config_for_login(email, password)
        assert len(calls) == before + 1, email
        if expect_config:
            assert result is not None and result.id == config.id, email
        else:
            assert result is None, email

    # An address in a domain no configuration lists takes the ordinary
    # password path, which does its own check.
    before = len(calls)
    assert SSOService().config_for_login(f"someone@{_domain('unclaimed')}", _PASSWORD) is None
    assert len(calls) == before


# --------------------------------------------------------------------------- #
# TS-35, TS-36, TS-37  D-3 hash-format uniformity                             #
# --------------------------------------------------------------------------- #

_LEGACY_260K = "pbkdf2:sha256:260000"
_LEGACY_600K = "pbkdf2:sha256:600000"
_FOREIGN_HASH = "$2b$12$" + "a" * 53


def test_claimed_domain_check_costs_the_same_for_every_stored_hash_format(app, db_session, make_org,
                                                                           monkeypatch):
    import app.services.sso_service as sso_service
    from app.services.sso_service import SSOService
    from werkzeug.security import generate_password_hash
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
    from app.models.user import PASSWORD_HASH_METHOD, password_hash_is_current

    assert password_hash_is_current(PASSWORD_HASH_METHOD + "$onlysalt") is False
    assert password_hash_is_current(PASSWORD_HASH_METHOD) is False
    assert password_hash_is_current(PASSWORD_HASH_METHOD + "$salt$") is True
    assert password_hash_is_current(None) is False

    org = make_org("scope-hash-uniform")
    claimed = _domain("hashformat")
    _configure_sso(db_session, org, claimed)
    # Seven accounts in one organisation: an unlimited plan keeps the seat
    # check out of a test that is not about it, as TS-32 also needs.
    db_session.add(Subscription(
        organization_id=org.id, plan=SubscriptionPlan.enterprise,
        status=SubscriptionStatus.active, seats_purchased=0,
    ))
    db_session.flush()

    current_admin = _make_user(db_session, org, f"current@{claimed}", password=_PASSWORD,
                               is_platform_admin=True)
    legacy_260_admin = _make_user(db_session, org, f"legacy260@{claimed}", is_platform_admin=True)
    legacy_260_admin.password_hash = generate_password_hash(_PASSWORD, method=_LEGACY_260K)
    legacy_600_admin = _make_user(db_session, org, f"legacy600@{claimed}", is_platform_admin=True)
    legacy_600_admin.password_hash = generate_password_hash(_PASSWORD, method=_LEGACY_600K)
    foreign_admin = _make_user(db_session, org, f"foreign@{claimed}", is_platform_admin=True)
    foreign_admin.password_hash = _FOREIGN_HASH
    incomplete_admin = _make_user(db_session, org, f"incomplete@{claimed}", is_platform_admin=True)
    incomplete_admin.password_hash = PASSWORD_HASH_METHOD + "$onlysalt"
    nopass_admin = _make_user(db_session, org, f"nopass@{claimed}", is_platform_admin=True)
    legacy_member = _make_user(db_session, org, f"member@{claimed}")
    legacy_member.password_hash = generate_password_hash(_PASSWORD, method=_LEGACY_260K)
    db_session.commit()

    addresses = [
        current_admin.email, legacy_260_admin.email, legacy_600_admin.email,
        foreign_admin.email, incomplete_admin.email, nopass_admin.email, legacy_member.email,
        f"newcomer@{claimed}",
    ]

    real = sso_service.check_password_hash
    calls = []

    def counting(stored, password):
        calls.append(stored)
        return real(stored, password)

    monkeypatch.setattr(sso_service, "check_password_hash", counting)

    for email in addresses:
        before = len(calls)
        SSOService().config_for_login(email, "wrong-password")
        assert len(calls) == before + 1, email
        hash_argument = calls[-1]
        assert hash_argument.startswith(sso_service.PASSWORD_HASH_METHOD), (email, hash_argument)
        if email == current_admin.email:
            assert hash_argument != sso_service._DUMMY_PASSWORD_HASH, email
        else:
            assert hash_argument == sso_service._DUMMY_PASSWORD_HASH, email

    # Every one of these addresses, right down to the foreign-format account,
    # is sent to the identity provider the same way for a wrong password.
    locations = []
    for email in addresses:
        client = _anonymous_client(app)
        resp = _post_login(client, email, password="wrong-password")
        assert resp.status_code in (302, 303), email
        location = resp.headers.get("Location", "")
        assert "/auth/sso/initiate" in location, (email, location)
        assert _session_user_id(client) is None, email
        locations.append(_without_email_param(location))
    assert len(set(locations)) == 1, locations


def test_legacy_hash_platform_admin_recovers_by_password_reset(app, db_session, make_org):
    from werkzeug.security import generate_password_hash

    from app.models.user import PASSWORD_HASH_METHOD
    from app.modules.account.services.account_service import AccountService

    org = make_org("scope-legacy-recovery")
    claimed = _domain("legacyrecovery")
    _configure_sso(db_session, org, claimed)
    admin = _make_user(db_session, org, f"admin@{claimed}", is_platform_admin=True)
    admin.password_hash = generate_password_hash(_PASSWORD, method=_LEGACY_600K)
    db_session.commit()

    client = _anonymous_client(app)
    resp = _post_login(client, admin.email, _PASSWORD)
    assert resp.status_code in (302, 303)
    assert "/auth/sso/initiate" in resp.headers.get("Location", "")
    assert _session_user_id(client) is None

    raw = _issue_reset_token(db_session, admin)
    success, message = AccountService.reset_password(raw, _PASSWORD)
    assert success, message
    reloaded = _reload(db_session, admin.id)
    assert reloaded.password_hash.startswith(PASSWORD_HASH_METHOD)

    after_client = _anonymous_client(app)
    after_resp = _post_login(after_client, admin.email, _PASSWORD)
    assert after_resp.status_code in (302, 303)
    # A platform administrator still completes multi-factor before a session
    # is minted (mfa_service.required_for), so the proof here is the same one
    # the sign-in-form-disclosure tests use: the password path was taken, not
    # the identity provider.
    assert "/auth/sso/initiate" not in after_resp.headers.get("Location", "")


def test_password_sign_in_upgrades_legacy_hash(app, db_session, make_org):
    from werkzeug.security import check_password_hash, generate_password_hash

    from app.models.user import PASSWORD_HASH_METHOD

    org = make_org("scope-upgrade")
    unclaimed = f"person-{uuid.uuid4().hex[:8]}@unclaimed.example"
    member = _make_user(db_session, org, unclaimed)
    member.password_hash = generate_password_hash(_PASSWORD, method=_LEGACY_260K)
    db_session.commit()

    wrong_client = _anonymous_client(app)
    _post_login(wrong_client, unclaimed, "wrong-password")
    assert _session_user_id(wrong_client) is None
    unchanged = _reload(db_session, member.id)
    assert unchanged.password_hash.startswith(_LEGACY_260K)

    right_client = _anonymous_client(app)
    right_resp = _post_login(right_client, unclaimed, _PASSWORD)
    assert right_resp.status_code in (302, 303)
    assert _session_user_id(right_client) == str(member.id)
    upgraded = _reload(db_session, member.id)
    assert upgraded.password_hash.startswith(PASSWORD_HASH_METHOD)
    assert check_password_hash(upgraded.password_hash, _PASSWORD)

    foreign = _make_user(db_session, org, f"foreign-{uuid.uuid4().hex[:8]}@unclaimed.example")
    foreign.password_hash = _FOREIGN_HASH
    db_session.commit()
    foreign_client = _anonymous_client(app)
    foreign_resp = _post_login(foreign_client, foreign.email, "anything")
    assert foreign_resp.status_code == 200
    assert "Invalid email or password." in foreign_resp.get_data(as_text=True)
    assert _session_user_id(foreign_client) is None


# --------------------------------------------------------------------------- #
# TS-38  D-1, end to end through the real routes                              #
# --------------------------------------------------------------------------- #


def test_invited_member_linked_by_sso_stays_activated_after_a_reset(app, db_session, make_org, mail_on,
                                                                     login_as):
    from flask import template_rendered

    from app.models.organization import Organization
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
    from app.modules.account.services import invitation_service
    from app.services.sso_service import SSOService

    org_a = make_org("scope-ts38")
    # An unlimited plan keeps the seat check out of a test that is not about
    # it, as TS-32 also needs for its four-person organisation.
    db_session.add(Subscription(
        organization_id=org_a.id, plan=SubscriptionPlan.enterprise,
        status=SubscriptionStatus.active, seats_purchased=0,
    ))
    db_session.flush()
    domain = _domain("ts38")
    _configure_sso(db_session, org_a, domain)
    admin = _org_admin(db_session, org_a)
    db_session.commit()

    bob_email = f"bob@{domain}"

    client = app.test_client()
    login_as(client, admin)
    invite_resp = client.post("/admin/team/invite", data={"email": bob_email, "role": "viewer"})
    assert invite_resp.status_code in (302, 303)

    found = _users_with_email(db_session, bob_email)
    assert len(found) == 1
    bob_id = found[0].id
    assert found[0].password_hash is None
    assert found[0].confirmed is False
    assert invitation_service.is_unactivated(found[0])

    linked = SSOService().provision_user(org_a, {"email": bob_email, "sub": "bob-subject"})
    assert linked.id == bob_id
    assert linked.confirmed is True
    assert linked.external_id == "bob-subject"

    login_as(client, admin)
    reset_resp = client.post(f"/admin/team/member/{bob_id}/reset-sign-in")
    assert reset_resp.status_code in (302, 303)
    reset_bob = _reload(db_session, bob_id)
    assert reset_bob.external_id is None
    assert reset_bob.sso_provider is None
    assert reset_bob.confirmed is True
    assert not invitation_service.is_unactivated(reset_bob)

    seen = []

    def capture(sender, template, context, **extra):
        seen.append(context)

    template_rendered.connect(capture, app)
    try:
        login_as(client, admin)
        team_resp = client.get("/admin/team")
        assert team_resp.status_code == 200
    finally:
        template_rendered.disconnect(capture, app)
    member_ids = [m.id for ctx in seen for m in ctx.get("members", [])]
    assert bob_id in member_ids

    register_client = _anonymous_client(app)
    register_resp = register_client.post("/account/register", data={
        "first_name": "Someone", "last_name": "Else", "email": bob_email,
        "password": _PASSWORD, "password2": _PASSWORD,
    })
    assert register_resp.status_code == 200
    assert "Email already registered" in register_resp.get_data(as_text=True)
    assert _session_user_id(register_client) is None
    still_there = _reload(db_session, bob_id)
    assert still_there is not None
    assert still_there.organization_id == org_a.id
    assert len(_users_with_email(db_session, bob_email)) == 1
    # No organisation was made for Bob: every account under his address sits in org_a. (A count of
    # all organisations is not scoped to this test and moves when parallel workers create their own.)
    assert {u.organization_id for u in _users_with_email(db_session, bob_email)} == {org_a.id}

    relinked = SSOService().provision_user(org_a, {"email": bob_email, "sub": "bob-new-subject"})
    assert relinked.id == bob_id
    assert relinked.external_id == "bob-new-subject"


# --------------------------------------------------------------------------- #
# TS-39  D-1, another organisation's invitation after a reset                 #
# --------------------------------------------------------------------------- #


def test_reset_member_keeps_their_address_against_another_organisations_invitation(
    app, db_session, make_org, mail_on, login_as,
):
    from datetime import datetime, timedelta, timezone

    from app.models.pending_invitation import PendingInvitation
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
    from app.modules.account.services import invitation_service
    from app.services.sso_service import SSOService

    org_a = make_org("scope-ts39-a")
    db_session.add(Subscription(
        organization_id=org_a.id, plan=SubscriptionPlan.enterprise,
        status=SubscriptionStatus.active, seats_purchased=0,
    ))
    db_session.flush()
    domain = _domain("ts39")
    _configure_sso(db_session, org_a, domain)
    admin_a = _org_admin(db_session, org_a)
    db_session.commit()

    bob_email = f"bob@{domain}"
    client = app.test_client()
    login_as(client, admin_a)
    invite_resp = client.post("/admin/team/invite", data={"email": bob_email, "role": "viewer"})
    assert invite_resp.status_code in (302, 303)
    bob_id = _users_with_email(db_session, bob_email)[0].id

    linked = SSOService().provision_user(org_a, {"email": bob_email, "sub": "bob-subject"})
    assert linked.confirmed is True

    login_as(client, admin_a)
    reset_resp = client.post(f"/admin/team/member/{bob_id}/reset-sign-in")
    assert reset_resp.status_code in (302, 303)
    reset_bob = _reload(db_session, bob_id)
    assert reset_bob.confirmed is True
    assert not invitation_service.is_unactivated(reset_bob)

    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    for row in PendingInvitation.query.filter_by(user_id=bob_id).all():
        row.expires_at = past
    db_session.commit()

    org_b = make_org("scope-ts39-b")
    db_session.add(Subscription(
        organization_id=org_b.id, plan=SubscriptionPlan.enterprise,
        status=SubscriptionStatus.active, seats_purchased=0,
    ))
    db_session.flush()
    admin_b = _org_admin(db_session, org_b)
    db_session.commit()

    with pytest.raises(invitation_service.InvitationError) as refused:
        invitation_service.invite_new_person(org_b.id, admin_b, bob_email)
    assert refused.value.status == 409
    db_session.rollback()

    login_as(client, admin_b)
    client.post("/admin/team/invite", data={"email": bob_email, "role": "viewer"})

    survivor = _reload(db_session, bob_id)
    assert survivor is not None
    assert survivor.organization_id == org_a.id
    assert len(_users_with_email(db_session, bob_email)) == 1


# --------------------------------------------------------------------------- #
# TS-40  D-1, rows linked before this change                                  #
# --------------------------------------------------------------------------- #


def test_reset_keeps_accounts_linked_before_this_change_activated(db_session, make_org):
    from app.modules.account.services import invitation_service
    from app.services.sso_service import SSOService

    # (a) a platform-provider link, made before this change, confirmed=False.
    org_a = make_org("scope-ts40-a")
    member = _make_user(db_session, org_a, f"legacy-{uuid.uuid4().hex[:8]}@example.com",
                        external_id="legacy-subject", sso_provider="google", confirmed=False)
    db_session.commit()

    reset = SSOService().reset_member_sign_in_link(org_a.id, member.id, actor_id=None, reason="test")
    assert reset.confirmed is True
    assert not invitation_service.is_unactivated(reset)
    assert invitation_service.release_for_registration(member.email) is False
    assert _reload(db_session, member.id) is not None

    # (b) a pre-existing organisation-SSO link, healed at its next sign-in.
    org_b = make_org("scope-ts40-b")
    domain = _domain("ts40")
    _configure_sso(db_session, org_b, domain)
    kept = _make_user(db_session, org_b, f"kept@{domain}",
                      external_id="kept-subject", sso_provider="oidc", confirmed=False)
    db_session.commit()

    healed = SSOService().provision_user(org_b, {"email": kept.email, "sub": "kept-subject"})
    assert healed.id == kept.id
    assert healed.confirmed is True


# --------------------------------------------------------------------------- #
# TS-41  D-2, runbook Case 2b proved end to end, including the authenticator  #
# step                                                                        #
# --------------------------------------------------------------------------- #


def test_password_less_admin_recovers_through_their_own_mailbox_before_sso_is_disabled(
    app, db_session, make_org, mail_on, monkeypatch,
):
    # This admin is an organisation administrator, so sign-in requires an
    # authenticator code (mfa_service.required_for). This test completes that
    # step once for an admin enrolling for the first time and again for one
    # already enrolled, checking the settings page on the same session each time.
    import secrets
    import time
    from urllib.parse import urlsplit

    import pyotp
    from werkzeug.security import check_password_hash

    import app.flask_email as flask_email
    from app.flask_email import mail_available
    from app.jobs.tenant_safe_job import platform_scope
    from app.models.account_token import PURPOSE_PASSWORD_RESET, AccountToken
    from app.models.org_role import OrgRole
    from app.models.user import Role
    from app.modules.admin.services.admin_user_service import AdminUserService
    from app.services.sso_service import SSOService

    org = make_org("scope-ts41")
    domain = _domain("ts41")
    config = _configure_sso(db_session, org, domain)
    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()
    admin = _make_user(db_session, org, f"admin@{domain}",
                       external_id="admin-subject", sso_provider="oidc", role=admin_role)
    admin.is_org_admin = True
    OrgRole.set_role(org.id, admin.id, "org_admin", granted_by_id=admin.id)
    db_session.commit()

    assert admin.password_hash is None
    assert admin.mfa_enabled is not True
    assert SSOService().config_for_login(admin.email, "anything") is not None

    # Runbook step 1: mail check first.
    assert mail_available(app) is True

    # Runbook step 2: give the admin a password nobody knows, while SSO is on.
    AdminUserService.set_user_password(admin, secrets.token_urlsafe(32))

    def _reset_token_count():
        with platform_scope("test: count password reset tokens"):
            return AccountToken.query.filter_by(
                user_id=admin.id, purpose=PURPOSE_PASSWORD_RESET,
            ).count()

    before = _reset_token_count()

    # Runbook step 3: the admin's own mailbox, redeemed through the link that
    # would actually have been mailed, captured at the transport boundary.
    sent = []
    monkeypatch.setattr(flask_email, "deliver_email_after_response", lambda **kw: sent.append(kw))

    anon = _anonymous_client(app)
    reset_resp = anon.post("/account/reset-password", data={"email": admin.email})
    assert reset_resp.status_code == 200
    assert _reset_token_count() == before + 1
    assert len(sent) == 1
    assert sent[0]["recipient"] == admin.email

    new_password = "N3w!Str0ngPassw0rd"
    reset_path = urlsplit(sent[0]["reset_link"]).path
    assert reset_path.startswith("/account/reset-password/")
    reset_client = _anonymous_client(app)
    reset_submit = reset_client.post(
        reset_path, data={"password": new_password, "password2": new_password},
        follow_redirects=False,
    )
    assert reset_submit.status_code in (302, 303)
    assert reset_submit.headers["Location"].endswith("/account/login")
    assert check_password_hash(_reload(db_session, admin.id).password_hash, new_password)

    # SSO still on: the correct new password still goes to the identity provider.
    sso_client = _anonymous_client(app)
    sso_resp = _post_login(sso_client, admin.email, new_password)
    assert sso_resp.status_code in (302, 303)
    assert "/auth/sso/initiate" in sso_resp.headers.get("Location", "")
    assert _session_user_id(sso_client) is None

    # Runbook step 4: disable the organisation's single sign-on configuration.
    config.enabled = False
    db_session.commit()

    # Runbook step 5, first sign-in: the admin has never set up an
    # authenticator, so the password alone does not finish the sign-in.
    password_client = _anonymous_client(app)
    resp = _post_login(password_client, admin.email, new_password)
    assert resp.status_code in (302, 303)
    assert "/account/mfa-challenge" in resp.headers["Location"]
    assert _session_user_id(password_client) is None

    page = password_client.get("/account/mfa-challenge")
    assert page.status_code == 200
    with password_client.session_transaction() as sess:
        secret = sess["_mfa_enroll_secret"]
    assert secret in page.get_data(as_text=True)

    enrolled = password_client.post(
        "/account/mfa-challenge", data={"code": pyotp.TOTP(secret).now()},
        follow_redirects=False,
    )
    # A right code at first enrolment turns two-factor on and shows the eight recovery
    # codes once; the sign-in finishes when the person confirms they saved them.
    assert enrolled.status_code == 200
    assert b"recovery-codes" in enrolled.data
    assert _session_user_id(password_client) is None
    done = password_client.post(
        "/account/mfa-challenge", data={"step": "codes", "recovery_confirmed": "1"},
        follow_redirects=False,
    )
    assert done.status_code in (302, 303)
    assert "/account/mfa-challenge" not in done.headers["Location"]
    assert _session_user_id(password_client) == str(admin.id)

    reloaded = _reload(db_session, admin.id)
    assert reloaded.mfa_enabled is True
    assert reloaded.mfa_secret == secret

    sweep._clear_g()
    assert password_client.get("/admin/sso").status_code == 200

    # Runbook step 5, the common case: an admin who already set one up.
    second_client = _anonymous_client(app)
    second_login = _post_login(second_client, admin.email, new_password)
    assert second_login.status_code in (302, 303)
    assert "/account/mfa-challenge" in second_login.headers["Location"]
    assert _session_user_id(second_client) is None

    # A different time step from the one used to enrol, so this does not
    # depend on whether a code may be reused; verify_code tolerates one
    # step either side.
    next_code = pyotp.TOTP(secret).at(int(time.time()) + 30)
    second_done = second_client.post(
        "/account/mfa-challenge", data={"code": next_code}, follow_redirects=False,
    )
    assert second_done.status_code in (302, 303)
    assert _session_user_id(second_client) == str(admin.id)

    sweep._clear_g()
    assert second_client.get("/admin/sso").status_code == 200


# --------------------------------------------------------------------------- #
# Hardening after review                                                        #
# --------------------------------------------------------------------------- #


def test_wrong_password_for_an_account_outside_the_claiming_org_is_recorded(db_session, make_org, monkeypatch):
    from app.services import auth_audit
    from app.services.sso_service import SSOService

    claimed = _domain("recordcorp")
    config = _configure_sso(db_session, make_org("scope-record-x"), claimed)
    outsider = _make_user(db_session, make_org("scope-record-y"), f"pat@{claimed}", password=_PASSWORD)
    own_member = _make_user(db_session, make_org("scope-record-z"), f"sam@{claimed}", password=_PASSWORD)
    own_member.organization_id = config.organization_id
    db_session.commit()

    failures = []
    monkeypatch.setattr(auth_audit, "record_login_failure", lambda email=None: failures.append(email))

    # Wrong password: redirected as before, but the failure is recorded.
    assert SSOService().config_for_login(outsider.email, "wrong-password").id == config.id
    assert failures == [outsider.email]
    # Right password: proceeds to the password check, nothing recorded.
    assert SSOService().config_for_login(outsider.email, _PASSWORD) is None
    assert failures == [outsider.email]
    # The claiming organisation's own member is redirected to the IdP, not a failure.
    assert SSOService().config_for_login(own_member.email, _PASSWORD).id == config.id
    assert failures == [outsider.email]


def test_disabled_configuration_holds_no_domain_claim(db_session, make_org):
    from app.services.sso_service import SSOService

    domain = _domain("dormant")
    holder = make_org("scope-dormant-a")
    other = make_org("scope-dormant-b")
    _configure_sso(db_session, holder, domain, enabled=False)
    assert SSOService().domain_claim_problem(other.id, domain) is None

    live = _domain("live")
    _configure_sso(db_session, make_org("scope-live-a"), live)
    assert SSOService().domain_claim_problem(other.id, live) is not None


def test_domain_claim_collides_across_spellings(db_session, make_org):
    from app.services.sso_service import SSOService

    domain = _domain("spelling")
    _configure_sso(db_session, make_org("scope-spell-a"), f"{domain.upper()}.")
    other = make_org("scope-spell-b")
    assert SSOService().domain_claim_problem(other.id, domain) is not None
    assert SSOService().domain_claim_problem(other.id, f"{domain}.") is not None


def test_contested_domain_refuses_password_sign_in_and_records_it(app, db_session, make_org, monkeypatch):
    from app.services import auth_audit

    contested = _domain("contested-login")
    _configure_sso(db_session, make_org("scope-cl-a"), contested)
    _configure_sso(db_session, make_org("scope-cl-b"), contested)
    member = _make_user(db_session, make_org("scope-cl-c"), f"vic@{contested}", password=_PASSWORD)
    db_session.commit()

    failures = []
    monkeypatch.setattr(auth_audit, "record_login_failure", lambda email=None: failures.append(email))

    client = _anonymous_client(app)
    resp = _post_login(client, member.email)
    assert "/auth/sso/initiate" not in resp.headers.get("Location", "")
    assert _session_user_id(client) is None
    assert failures
    # The login page renders (and so consumes) the flashed message, so read the body.
    assert resp.status_code == 200
    assert "claimed by more than one organisation" in resp.get_data(as_text=True)
    assert "Invalid email or password" not in resp.get_data(as_text=True)

    initiate = _anonymous_client(app).get(f"/auth/sso/initiate?email={member.email}")
    assert initiate.status_code == 409
