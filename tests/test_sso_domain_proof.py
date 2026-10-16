"""A domain routes sign-ins to an organisation's identity provider only once the organisation
has proved it controls the domain. Two real organisations throughout.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.sso_service import SSONotConfiguredError, SSOService
from tests.test_sso_cross_org_domain_claim_per_org import _configure_sso, _domain, _make_user


def _claim(org_id, domain):
    from app.models.sso_domain_claim import SSODomainClaim

    return SSODomainClaim.query.filter_by(organization_id=org_id, domain=domain).one()


def _publish(monkeypatch, domain, *tokens):
    """The DNS answers for the challenge name of *domain* with *tokens* (and nothing elsewhere)."""
    name = SSOService.challenge_record_name(domain)
    monkeypatch.setattr(
        SSOService, "_lookup_txt", staticmethod(lambda asked: list(tokens) if asked == name else [])
    )


def test_a_listed_but_unproven_domain_routes_nothing_and_signs_nobody_in(app, db_session, make_org):
    org = make_org("proof-unproven")
    domain = _domain("unproven")
    _configure_sso(db_session, org, domain, verified=False)
    svc = SSOService()
    assert svc.get_config_for_email(f"someone@{domain}") is None
    assert svc.config_for_login(f"someone@{domain}", "x") is None
    with pytest.raises(SSONotConfiguredError):
        svc.provision_user(org, {"email": f"someone@{domain}", "sub": "s1"})


def test_a_proven_domain_routes_and_signs_in_and_spelling_does_not_matter(app, db_session, make_org):
    org = make_org("proof-proven")
    domain = _domain("proven")
    config = _configure_sso(db_session, org, domain.upper() + ".")
    svc = SSOService()
    assert svc.get_config_for_email(f"Someone@{domain}").id == config.id
    assert svc.provision_user(org, {"email": f"someone@{domain}", "sub": "s1"}).organization_id == org.id


def test_verifying_needs_the_token_in_dns_and_records_the_claim(app, db_session, make_org, monkeypatch):
    org = make_org("proof-verify")
    domain = _domain("verify")
    _configure_sso(db_session, org, domain, verified=False)
    claim = _claim(org.id, domain)
    svc = SSOService()

    monkeypatch.setattr(SSOService, "_lookup_txt", staticmethod(lambda name: []))
    ok, message = svc.verify_domain(org.id, domain)
    assert not ok and SSOService.challenge_record_name(domain) in message and claim.verified_at is None

    _publish(monkeypatch, domain, "some-other-value")
    assert svc.verify_domain(org.id, domain)[0] is False

    _publish(monkeypatch, domain, "unrelated", f" {claim.token} ")
    ok, _ = svc.verify_domain(org.id, domain)
    db_session.refresh(claim)
    assert ok and claim.verified_at is not None
    assert svc.get_config_for_email(f"x@{domain}") is not None
    assert svc.verified_domains_for(org.id) == [domain]
    assert svc.verify_domain(org.id, domain) == (True, f"{domain} is already verified.")


def test_a_domain_the_organisation_does_not_list_cannot_be_verified(app, db_session, make_org, monkeypatch):
    org = make_org("proof-unlisted")
    _configure_sso(db_session, org, _domain("mine"), verified=False)
    stranger = _domain("stranger")
    _publish(monkeypatch, stranger, "anything")
    ok, _ = SSOService().verify_domain(org.id, stranger)
    assert ok is False


def test_only_one_organisation_can_hold_a_verified_claim(app, db_session, make_org):
    from datetime import datetime

    from app.models.sso_domain_claim import SSODomainClaim

    domain = _domain("single")
    a, b = make_org("proof-single-a"), make_org("proof-single-b")
    db_session.add(SSODomainClaim(organization_id=a.id, domain=domain, token="a", verified_at=datetime.utcnow()))
    db_session.flush()
    # An unproven claim holds nothing, so a second row is fine.
    db_session.add(SSODomainClaim(organization_id=b.id, domain=domain, token="b"))
    db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _claim(b.id, domain).verified_at = datetime.utcnow()
            db_session.flush()


def test_another_organisations_live_claim_blocks_verification_and_a_stale_one_is_released(
    app, db_session, make_org, monkeypatch
):
    from datetime import datetime

    domain = _domain("contest")
    holder, challenger = make_org("proof-holder"), make_org("proof-challenger")
    holder_config = _configure_sso(db_session, holder, domain)
    _configure_sso(db_session, challenger, domain, verified=False)
    svc = SSOService()
    token = _claim(challenger.id, domain).token
    _publish(monkeypatch, domain, token)

    ok, message = svc.verify_domain(challenger.id, domain)
    assert not ok and "another organisation" in message
    assert svc.get_config_for_email(f"x@{domain}").organization_id == holder.id
    assert svc.domain_claim_problem(challenger.id, domain)

    # The holder switches single sign-on off: its claim no longer holds the domain back.
    holder_config.enabled = False
    db_session.flush()
    assert svc.domain_claim_problem(challenger.id, domain) is None
    ok, _ = svc.verify_domain(challenger.id, domain)
    db_session.expire_all()
    assert ok
    assert _claim(holder.id, domain).verified_at is None
    assert _claim(challenger.id, domain).verified_at is not None


def test_unlisting_a_domain_removes_its_proof_and_relisting_needs_a_new_one(app, db_session, make_org):
    org = make_org("proof-sync")
    domain, keep = _domain("drop"), _domain("keep")
    _configure_sso(db_session, org, f"{domain},{keep}")
    svc = SSOService()
    old_token = _claim(org.id, domain).token

    svc.sync_domain_claims(org.id, [keep])
    assert [c.domain for c in svc.domain_claims_for(org.id)] == [keep]
    assert svc.verified_domains_for(org.id) == [keep]

    svc.sync_domain_claims(org.id, [keep, domain])
    again = _claim(org.id, domain)
    assert again.verified_at is None and again.token != old_token


def test_the_admin_page_gives_the_record_to_publish_and_verifies_it(app, db_session, make_org, client, login_as, monkeypatch):
    from app.models.org_role import OrgRole
    from app.models.user import Role
    from app.services import sso_service

    monkeypatch.setattr(sso_service, "check_idp_url", lambda url: url)
    org, other = make_org("proof-admin"), make_org("proof-admin-other")
    admin = _make_user(db_session, org, f"a@{_domain('adm')}", role=Role.query.filter_by(name="Administrator").first())
    OrgRole.set_role(org.id, admin.id, "org_admin", granted_by_id=admin.id)
    outsider_admin = _make_user(db_session, other, f"o@{_domain('oth')}", role=Role.query.filter_by(name="Administrator").first())
    OrgRole.set_role(other.id, outsider_admin.id, "org_admin", granted_by_id=outsider_admin.id)
    db_session.commit()
    domain = _domain("page")

    login_as(client, admin)
    saved = client.post("/admin/sso", data={"protocol": "oidc", "email_domain": domain, "client_id": "c"})
    assert saved.status_code == 302
    claim = _claim(org.id, domain)
    login_as(client, admin)
    page = client.get("/admin/sso").get_data(as_text=True)
    assert SSOService.challenge_record_name(domain) in page and claim.token in page and "Not verified" in page

    # Another organisation's administrator cannot verify this organisation's domain.
    _publish(monkeypatch, domain, claim.token)
    login_as(client, outsider_admin)
    client.post("/admin/sso/domains/verify", data={"domain": domain})
    db_session.expire_all()
    assert _claim(org.id, domain).verified_at is None

    login_as(client, admin)
    client.post("/admin/sso/domains/verify", data={"domain": domain})
    db_session.expire_all()
    assert _claim(org.id, domain).verified_at is not None
    login_as(client, admin)
    assert "Verified" in client.get("/admin/sso").get_data(as_text=True)
