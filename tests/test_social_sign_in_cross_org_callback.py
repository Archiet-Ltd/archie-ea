"""Continue with Google / Microsoft: callback integrity and id_token checks (M-10 to M-21)."""
import time
import uuid
from types import SimpleNamespace

import pytest

from tests.support.fake_oidc_provider import PERSONAL_TENANT, WORK_TENANT
from tests.support.social_sign_in import *  # noqa: F401,F403
from tests.support.social_sign_in import Browser


@pytest.fixture
def browser(app, social_config, db_session):
    return Browser(app, social_config)


def _email():
    return "social-%s@example.com" % uuid.uuid4().hex[:10]


def _user_exists(email):
    from app.models.user import User

    return User.find_by_email(email) is not None


def _google(browser, email=None, **extra):
    email = email or _email()
    return email, browser.providers["google"].google_claims("g-" + uuid.uuid4().hex, email, **extra)


def _microsoft(browser, email=None, **extra):
    email = email or _email()
    return email, browser.providers["microsoft"].microsoft_claims(uuid.uuid4().hex, email=email, tid=PERSONAL_TENANT, **extra)


def _refused(browser, response, email):
    assert response.status_code == 302
    assert "/account/login" in response.headers["Location"]
    assert not browser.is_signed_in()
    assert not _user_exists(email)


def test_m10_wrong_state_makes_no_token_request(browser):
    """M-10: a callback whose state is wrong never reaches the token endpoint and signs nobody in."""
    email, claims = _google(browser)
    browser.providers["google"].queue(claims)
    address = browser.at_provider(browser.start("google"))
    tampered = address.replace("state=", "state=x")
    response = browser.callback(tampered)
    _refused(browser, response, email)
    assert browser.providers["google"].token_requests == []


def test_m11_replayed_callback_signs_nobody_in(browser):
    """M-11: the address of a completed callback, used again, signs nobody in and asks for nothing more."""
    email, claims = _google(browser)
    browser.providers["google"].queue(claims)
    address = browser.at_provider(browser.start("google"))
    assert browser.callback(address).status_code == 302
    assert browser.is_signed_in()
    browser.get("/account/logout")
    assert not browser.is_signed_in()
    again = browser.callback(address)
    assert again.status_code == 302
    assert not browser.is_signed_in()
    assert len(browser.providers["google"].token_requests) == 1


def test_m12_callback_after_eleven_minutes(browser):
    """M-12: a callback 11 minutes after start signs nobody in and makes no token request."""
    email, claims = _google(browser)
    browser.providers["google"].queue(claims)
    address = browser.at_provider(browser.start("google"))
    flow = dict(browser.session()["oauth_flow"])
    flow["started_at"] = time.time() - 11 * 60
    browser.set_session(oauth_flow=flow)
    _refused(browser, browser.callback(address), email)
    assert browser.providers["google"].token_requests == []


def test_m12_callback_inside_ten_minutes_is_accepted(browser):
    """M-12 (other side): nine minutes after start the same callback still works."""
    email, claims = _google(browser)
    browser.providers["google"].queue(claims)
    address = browser.at_provider(browser.start("google"))
    flow = dict(browser.session()["oauth_flow"])
    flow["started_at"] = time.time() - 9 * 60
    browser.set_session(oauth_flow=flow)
    browser.callback(address)
    assert browser.is_signed_in()


def test_m13_token_request_carries_the_stored_verifier(browser):
    """M-13: the token endpoint receives exactly the verifier stored at start."""
    email, claims = _google(browser)
    browser.providers["google"].queue(claims)
    started = browser.start("google")
    verifier = browser.session()["oauth_flow"]["code_verifier"]
    browser.callback(browser.at_provider(started))
    sent = browser.providers["google"].token_requests
    assert len(sent) == 1
    assert sent[0]["code_verifier"] == verifier
    assert browser.is_signed_in()


def test_m14_different_nonce_refused(browser):
    """M-14: an id_token with another nonce is refused."""
    email, claims = _google(browser)
    _refused(browser, browser.sign_in_with("google", claims, nonce="somebody-elses"), email)


def test_m15_different_audience_refused(browser):
    """M-15: an id_token for another client is refused."""
    email, claims = _google(browser)
    _refused(browser, browser.sign_in_with("google", claims, audience="another-client"), email)


def test_m16_hs256_token_signed_with_public_key_refused(browser):
    """M-16: an HS256 id_token keyed with the public key material is refused."""
    email, claims = _google(browser)
    _refused(browser, browser.sign_in_with("google", claims, algorithm="HS256"), email)


def test_m17_google_token_from_another_issuer_refused(browser):
    """M-17: a Google-shaped id_token whose issuer is https://evil.example is refused."""
    email, claims = _google(browser)
    _refused(browser, browser.sign_in_with("google", claims, issuer="https://evil.example"), email)


def test_m17_expired_token_refused(browser):
    """M-17 (other side): an expired id_token is refused."""
    email, claims = _google(browser)
    _refused(browser, browser.sign_in_with("google", claims, expired=True), email)


def test_m18_microsoft_tenant_must_match_issuer(browser):
    """M-18: a Microsoft id_token whose issuer names a different tenant than its tid is refused."""
    email, claims = _microsoft(browser)
    provider = browser.providers["microsoft"]
    _refused(browser, browser.sign_in_with("microsoft", claims, issuer=provider.issuer_for(WORK_TENANT)), email)


def test_m18_microsoft_matching_tenant_accepted(browser):
    """M-18 (other side): the matching tenant issuer signs a new person in."""
    email, claims = _microsoft(browser)
    browser.sign_in_with("microsoft", claims)
    assert browser.is_signed_in()
    assert _user_exists(email)


def test_m19_microsoft_tid_must_be_a_guid(browser):
    """M-19: a Microsoft id_token whose tid is not a GUID is refused."""
    email = _email()
    claims = browser.providers["microsoft"].microsoft_claims(uuid.uuid4().hex, email=email, tid="not-a-guid")
    _refused(browser, browser.sign_in_with("microsoft", claims), email)


def test_m20_organisation_provider_with_template_issuer_still_refused(app, social_config):
    """M-20: a per-organisation provider whose discovery issuer holds {tenantid} is refused."""
    from app.services.sso_service import SSONotConfiguredError, SSOService

    config = SimpleNamespace(
        client_id="stand-in-client-id", client_secret="stand-in-client-secret",
        idp_metadata_url=social_config["microsoft"].metadata_url,
    )
    with app.test_request_context("/"):
        with pytest.raises(SSONotConfiguredError):
            SSOService().initiate_oidc_flow(config, "https://entelim.tech/auth/sso/callback/oidc")


def test_m21_no_userinfo_request_for_either_provider(browser):
    """M-21: neither provider's userinfo endpoint is called during sign-in."""
    _, claims = _google(browser)
    browser.sign_in_with("google", claims)
    assert browser.is_signed_in()
    other = Browser(browser.app, browser.providers)
    _, claims = _microsoft(browser)
    other.sign_in_with("microsoft", claims)
    assert other.is_signed_in()
    assert browser.providers["google"].userinfo_requests == 0
    assert browser.providers["microsoft"].userinfo_requests == 0
