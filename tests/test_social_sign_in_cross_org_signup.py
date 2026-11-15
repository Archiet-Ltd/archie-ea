"""Continue with Google / Microsoft: new sign-ups."""
import uuid

import pytest

from tests.support.fake_oidc_provider import PERSONAL_TENANT, WORK_TENANT
from tests.support.social_sign_in import *  # noqa: F401,F403
from tests.support.social_sign_in import Browser


@pytest.fixture
def browser(app, social_config, db_session):
    return Browser(app, social_config)


@pytest.fixture
def outbox(app, monkeypatch):
    from app.extensions import mail

    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", "no-reply@example.com")
    with mail.record_messages() as messages:
        yield messages


def _email():
    return "social-%s@example.com" % uuid.uuid4().hex[:10]


def _counts():
    from app.models.organization import Organization
    from app.models.sign_in_identity import SignInIdentity
    from app.models.user import User

    return (
        Organization.query.count(), User.query.count(), SignInIdentity.query.count(),
)


def test_m22_new_google_person_gets_an_organisation_and_no_confirmation_email(browser, outbox):
    """A verified Google sign-up makes a confirmed administrator in a new organisation, with one identity row and no email."""
    from app.models.organization import Organization
    from app.models.sign_in_identity import SignInIdentity
    from app.models.user import User

    existing_orgs = {o.id for o in Organization.query.all()}
    email = _email()
    sub = "g-" + uuid.uuid4().hex
    response = browser.sign_in_with("google", browser.providers["google"].google_claims(sub, email))
    assert response.status_code == 302
    assert browser.is_signed_in()
    user = User.find_by_email(email)
    assert user is not None and user.confirmed is True
    assert user.organization_id not in existing_orgs
    assert user.is_org_admin is True or user.is_admin()
    rows = SignInIdentity.query.filter_by(user_id=user.id).all()
    assert [(r.provider, r.subject) for r in rows] == [("google", sub)]
    assert browser.signed_in_user_id() == user.id
    assert outbox == []
    from app.services.billing_plans import is_holding_slug

    organisation = Organization.query.filter_by(id=user.organization_id).one()
    assert organisation.slug != "default" and not is_holding_slug(organisation.slug)
    assert user.first_name == "Ada" and user.last_name == "Founder"


def test_m22_name_split_when_only_a_full_name_is_given(browser):
    """Names: without given and family names, the full name is split at the first space."""
    from app.models.user import User

    email = _email()
    claims = browser.providers["google"].google_claims("g-" + uuid.uuid4().hex, email)
    claims.pop("given_name")
    claims.pop("family_name")
    claims["name"] = "Grace Brewster Hopper"
    browser.sign_in_with("google", claims)
    user = User.find_by_email(email)
    assert (user.first_name, user.last_name) == ("Grace", "Brewster Hopper")


def test_m23_unverified_google_email_creates_nothing(browser):
    """A Google identity with email_verified false makes no user, organisation or identity row."""
    before = _counts()
    response = browser.sign_in_with("google", browser.providers["google"].google_claims("g-" + uuid.uuid4().hex, _email(), verified=False))
    assert response.status_code == 302 and "/account/login" in response.headers["Location"]
    assert not browser.is_signed_in()
    assert _counts() == before
    page = browser.get("/account/login").get_data(as_text=True)
    assert "Google did not confirm this email address" in page


def test_m23_string_true_counts_as_verified(browser):
    """Other side: Google's string "true" is a verified address."""
    email = _email()
    browser.sign_in_with("google", browser.providers["google"].google_claims("g-" + uuid.uuid4().hex, email, verified="true"))
    assert browser.is_signed_in()


def test_m24_microsoft_work_account_without_verified_domain_creates_no_user(browser):
    """A Microsoft work identity without xms_edov makes no user."""
    before = _counts()
    email = _email()
    claims = browser.providers["microsoft"].microsoft_claims(uuid.uuid4().hex, email=email, tid=WORK_TENANT)
    browser.sign_in_with("microsoft", claims)
    assert not browser.is_signed_in()
    assert _counts() == before
    assert "Microsoft did not confirm this email address" in browser.get("/account/login").get_data(as_text=True)


def test_m24_microsoft_work_account_with_verified_domain_is_accepted(browser):
    """Other side: xms_edov true, as boolean, string or 1, lets a work identity sign up."""
    for value in (True, "true", 1):
        person = Browser(browser.app, browser.providers)
        email = _email()
        claims = person.providers["microsoft"].microsoft_claims(uuid.uuid4().hex, email=email, tid=WORK_TENANT, edov=value)
        person.sign_in_with("microsoft", claims)
        assert person.is_signed_in(), value


def test_m25_personal_microsoft_account_creates_a_user(browser):
    """A personal Microsoft account (the fixed consumer tenant) with an email creates a user."""
    from app.models.user import User

    email = _email()
    claims = browser.providers["microsoft"].microsoft_claims(uuid.uuid4().hex, email=email, tid=PERSONAL_TENANT)
    browser.sign_in_with("microsoft", claims)
    assert browser.is_signed_in()
    assert User.find_by_email(email) is not None


def test_m26_preferred_username_is_never_an_email(browser, db_session, make_org):
    """A Microsoft identity whose preferred_username is an existing address, with no email, creates and links nothing."""
    from tests.test_team_invite_acceptance import _make_org, _make_user

    victim = _make_user(db_session, _make_org(db_session, "victim"), email=_email())
    before = _counts()
    claims = browser.providers["microsoft"].microsoft_claims(
        uuid.uuid4().hex, email=None, tid=WORK_TENANT, edov=True, preferred_username=victim.email, upn=victim.email,
)
    browser.sign_in_with("microsoft", claims)
    assert not browser.is_signed_in()
    assert _counts() == before


def test_m27_address_in_a_domain_with_its_own_single_sign_on(browser, db_session):
    """An address in a domain verified by an organisation with enabled SSO creates no user and says so."""
    from app.models.sso_config import SSOConfig
    from tests.test_team_invite_acceptance import _make_org

    org = _make_org(db_session, "sso-a")
    domain = "sso-%s.example.com" % uuid.uuid4().hex[:8]
    db_session.add(SSOConfig(organization_id=org.id, protocol="oidc", enabled=True, client_id="x", idp_metadata_url="https://idp.example.com/.well-known/openid-configuration", email_domain=domain))
    db_session.flush()
    before = _counts()
    email = "someone@" + domain
    browser.sign_in_with("google", browser.providers["google"].google_claims("g-" + uuid.uuid4().hex, email))
    assert not browser.is_signed_in()
    assert _counts() == before
    assert "Your organisation signs in through its own single sign-on" in browser.get("/account/login").get_data(as_text=True)


def test_m41_signup_never_joins_an_organisation_by_email_domain(browser, db_session):
    """Alice at acme.example gets a new organisation and organisation A's member count is unchanged."""
    from app.models.user import User
    from tests.test_team_invite_acceptance import _make_org, _make_user

    domain = "acme-%s.example.com" % uuid.uuid4().hex[:8]
    org_a = _make_org(db_session, "acme")
    for n in range(2):
        _make_user(db_session, org_a, email="member%d@%s" % (n, domain))
    members_before = User.query.filter_by(organization_id=org_a.id).count()
    alice = "alice@" + domain
    browser.sign_in_with("google", browser.providers["google"].google_claims("g-" + uuid.uuid4().hex, alice))
    user = User.find_by_email(alice)
    assert user is not None
    assert user.organization_id != org_a.id
    assert User.query.filter_by(organization_id=org_a.id).count() == members_before
    assert User.query.filter_by(organization_id=user.organization_id).count() == 1
