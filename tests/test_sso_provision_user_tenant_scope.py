"""SSOService.provision_user must not attach an SSO login to an existing user who belongs to a different
organisation than the one whose SSO config produced the login. Test 1 uses a claiming organisation with no SSO
configuration, so it is now refused at the configured-domain check; the cross-organisation refusal with a
matching configured domain is proved by test_cross_org_member_in_a_claimed_domain_is_still_refused in
tests/test_sso_cross_org_domain_claim_per_org.py.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org_a(make_org):
    return make_org("sso-victim")


@pytest.fixture
def org_b(make_org):
    return make_org("sso-attacker")


def _make_user(db_session, org_id, email):
    from app.models.user import User

    user = User(
        email=email,
        first_name="Existing",
        last_name="User",
        organization_id=org_id,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _configure_sso(db_session, org, email_domain):
    """Give ``org`` an SSO configuration that lists ``email_domain``, as an
    organisation whose own sign-in provisions these addresses has."""
    from app.models.sso_config import SSOConfig

    config = SSOConfig(
        organization_id=org.id, protocol="oidc", enabled=True, email_domain=email_domain
    )
    db_session.add(config)
    db_session.flush()
    return config


class TestProvisionUserIsTenantScoped:
    def test_refuses_to_attach_to_an_existing_user_in_a_different_org(
        self, db_session, org_a, org_b
    ):
        """Refused before any account is touched: the claiming organisation has no configured domain."""
        from app.services.sso_service import SSONotConfiguredError, SSOService

        email = f"victim-{uuid.uuid4().hex[:8]}@acme.example"
        victim = _make_user(db_session, org_a.id, email)
        original_external_id = victim.external_id
        original_sso_provider = victim.sso_provider

        svc = SSOService()
        with pytest.raises(SSONotConfiguredError):
            svc.provision_user(org_b, {"email": email, "sub": "attacker-idp-sub"})

        from app.models.user import User

        reloaded = User.query.get(victim.id)
        assert reloaded.organization_id == org_a.id, (
            "TAKEOVER: the victim's organisation changed."
        )
        assert reloaded.external_id == original_external_id, (
            "TAKEOVER: the victim's external_id was overwritten by a login "
            "through a different organisation's SSO config."
        )
        assert reloaded.sso_provider == original_sso_provider

    def test_still_provisions_a_new_user_for_the_correct_org(self, db_session, org_a):
        from app.services.sso_service import SSOService

        _configure_sso(db_session, org_a, "example.com")
        email = f"newuser-{uuid.uuid4().hex[:8]}@example.com"
        svc = SSOService()
        user = svc.provision_user(org_a, {"email": email, "sub": "real-idp-sub"})

        assert user.organization_id == org_a.id
        assert user.email == email

    def test_still_updates_an_existing_user_in_the_same_org(self, db_session, org_a):
        from app.services.sso_service import SSOService

        _configure_sso(db_session, org_a, "example.com")
        email = f"sameorg-{uuid.uuid4().hex[:8]}@example.com"
        existing = _make_user(db_session, org_a.id, email)

        svc = SSOService()
        user = svc.provision_user(
            org_a, {"email": email, "sub": "real-idp-sub", "given_name": "Updated"}
        )

        assert user.id == existing.id
        assert user.first_name == "Updated"
