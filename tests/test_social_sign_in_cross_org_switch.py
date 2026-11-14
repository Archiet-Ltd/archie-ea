"""Continue with Google / Microsoft: the settings switch, the host list and the start step (M-1 to M-9, M-49, M-50)."""
import base64
import hashlib
import re
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.support.social_sign_in import *  # noqa: F401,F403
from tests.support.social_sign_in import HOST, OTHER_HOST, Browser

SETTINGS = (
    "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
    "MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_CLIENT_SECRET",
)


@pytest.fixture
def browser(app, social_config):
    return Browser(app, social_config)


@pytest.fixture
def nothing_configured(app, social_config, monkeypatch):
    for name in SETTINGS:
        monkeypatch.setitem(app.config, name, "")


def _query(response):
    return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["Location"]).query).items()}


def test_m1_no_settings_no_buttons_anywhere(browser, nothing_configured):
    """M-1: with none of the four settings, neither page mentions a provider."""
    for path in ("/account/login", "/account/register"):
        html = browser.get(path).get_data(as_text=True)
        assert "Continue with Google" not in html, path
        assert "Continue with Microsoft" not in html, path


def test_m1_both_settings_both_buttons_in_order(browser):
    """M-1 (other side): with all four set, both pages offer Google, then Microsoft."""
    for path in ("/account/login", "/account/register"):
        html = browser.get(path).get_data(as_text=True)
        assert html.index("Continue with Google") < html.index("Continue with Microsoft"), path
        assert "By continuing, you agree to the Terms and the Privacy Policy" in re.sub(r"<[^>]+>", "", html), path


def test_m2_no_settings_start_is_404(browser, nothing_configured):
    """M-2: with none of the four settings, start answers 404."""
    assert browser.start("google").status_code == 404
    assert browser.start("microsoft").status_code == 404


def test_m3_only_google_pair(browser, app, monkeypatch):
    """M-3: only the Google pair set: Google is offered and Microsoft start is 404."""
    monkeypatch.setitem(app.config, "MICROSOFT_OAUTH_CLIENT_ID", "")
    monkeypatch.setitem(app.config, "MICROSOFT_OAUTH_CLIENT_SECRET", "")
    html = browser.get("/account/login").get_data(as_text=True)
    assert "Continue with Google" in html
    assert "Continue with Microsoft" not in html
    assert browser.start("microsoft").status_code == 404
    assert browser.start("google").status_code == 302


def test_m3_one_half_of_a_pair_is_off(browser, app, monkeypatch):
    """M-3 (other side): an id without its secret does not switch the provider on."""
    monkeypatch.setitem(app.config, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    assert "Continue with Google" not in browser.get("/account/login").get_data(as_text=True)
    assert browser.start("google").status_code == 404


def test_m4_start_on_entelim_tech(app, social_config):
    """M-4: start on entelim.tech sends the .tech callback address."""
    response = Browser(app, social_config, HOST).start("google")
    assert _query(response)["redirect_uri"] == "https://entelim.tech/account/oauth/google/callback"


def test_m5_start_on_entelim_org(app, social_config):
    """M-5: start on entelim.org sends the .org callback address."""
    response = Browser(app, social_config, OTHER_HOST).start("google")
    assert _query(response)["redirect_uri"] == "https://entelim.org/account/oauth/google/callback"


def test_m6_unlisted_host_cannot_borrow_a_listed_one(app, social_config):
    """M-6: a host outside the list is 404 even with X-Forwarded-Host naming entelim.tech, and no flow is kept."""
    stranger = Browser(app, social_config, "evil.example")
    response = stranger.post("/account/oauth/google/start", headers={"X-Forwarded-Host": HOST})
    assert response.status_code == 404
    assert "oauth_flow" not in stranger.session()
    assert "Continue with Google" not in stranger.get("/account/login").get_data(as_text=True)


def test_m7_start_sends_pkce_challenge_of_the_stored_verifier(browser):
    """M-7: S256, and the challenge is the base64url SHA-256 of the verifier kept for the callback."""
    response = browser.start("google")
    query = _query(response)
    verifier = browser.session()["oauth_flow"]["code_verifier"]
    assert query["code_challenge_method"] == "S256"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == expected
    assert 43 <= len(verifier) <= 128
    assert query["state"] == browser.session()["oauth_flow"]["state"]


def test_m8_microsoft_start_parameters(browser):
    """M-8: Microsoft start asks for a query response and the account chooser."""
    query = _query(browser.start("microsoft"))
    assert query["response_mode"] == "query"
    assert query["prompt"] == "select_account"
    assert query["scope"] == "openid email profile"
    assert query["response_type"] == "code"


def test_m8_authorize_parameters_cannot_replace_the_fixed_ones(browser):
    """M-8 (other side): the fixed request parameters appear once and carry the app's values."""
    raw = urlsplit(browser.start("google").headers["Location"]).query
    parsed = parse_qs(raw)
    for name in ("response_type", "client_id", "redirect_uri", "scope", "state", "nonce", "code_challenge", "code_challenge_method"):
        assert len(parsed[name]) == 1, name
    assert parsed["prompt"] == ["select_account"]


def test_m9_start_without_csrf_token_is_400(app, social_config, monkeypatch):
    """M-9: with CSRF protection on, start without the token is refused and keeps no flow."""
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    person = Browser(app, social_config)
    response = person.post("/account/oauth/google/start")
    assert response.status_code == 400
    assert "oauth_flow" not in person.session()


def test_m49_eleventh_start_in_a_minute_is_429(app, social_config, monkeypatch):
    """M-49: the 11th start from one client inside a minute is refused."""
    monkeypatch.setitem(app.config, "RATE_LIMITING_ENABLED", True)
    address = "10.77.%d.%d" % (uuid.uuid4().int % 250 + 1, uuid.uuid4().int % 250 + 1)
    person = Browser(app, social_config)
    codes = [person.post("/account/oauth/google/start", environ_base={"REMOTE_ADDR": address}).status_code for _ in range(11)]
    assert codes[:10] == [302] * 10
    assert codes[10] == 429


def test_m50_stand_in_address_ignored_outside_testing(app, social_config, monkeypatch):
    """M-50: with testing off, the stand-in address in settings is ignored and Google's own is used."""
    from app.auth.sso import platform_provider

    with app.test_request_context("/"):
        assert platform_provider("google").idp_metadata_url == social_config["google"].metadata_url
        monkeypatch.setitem(app.config, "TESTING", False)
        assert platform_provider("google").idp_metadata_url == "https://accounts.google.com/.well-known/openid-configuration"
        assert platform_provider("microsoft").idp_metadata_url == (
            "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"
        )


def test_m1_pages_with_buttons_let_their_forms_be_answered_by_the_provider(browser):
    """The sign-in and register pages name the providers' origins in their form-action rule, and no other page does."""
    for path in ("/account/login", "/account/register"):
        rule = [part for part in browser.get(path).headers["Content-Security-Policy"].split(";") if "form-action" in part][0]
        assert browser.providers["google"].base in rule and browser.providers["microsoft"].base in rule, rule
    rule = [part for part in browser.get("/account/reset-password").headers["Content-Security-Policy"].split(";") if "form-action" in part][0]
    assert rule.strip() == "form-action 'self'"
