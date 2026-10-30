"""mcp01 / QA-042: a connector set-up that needs sign-in (and two-factor) lands on the allow page in one go,
and a next parameter that points off the site is refused."""
import base64
import hashlib
import re
import urllib.parse

import pyotp
import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD, boot_live_server
from tests.smoke.fresh_org import create_fresh_org

PAGE_TIMEOUT = 30000


@pytest.fixture(scope="module")
def server(request, ai_protocol_stub, app):
    """A server with the real administrator two-factor gate (the shared smoke server skips it)."""
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"FLASK_CONFIG": "testing"})


@pytest.fixture()
def authorize_path(server):
    from app import create_app
    from app.modules.oauth_provider.models import OAuthClient

    with create_app("testing").app_context():
        client = OAuthClient.register(client_name="Connector journey", redirect_uris="http://localhost/callback")
        client_id = client.client_id
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": "http://localhost/callback", "response_type": "code",
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": "mcp:read",
        "resource": server.rstrip("/") + "/mcp", "state": "xyz",
    })
    return "/oauth/authorize?" + query


def _make_admin(*, enrolled):
    from app import create_app, db
    from app.models.user import User
    from app.services import mfa_service

    org = create_fresh_org("enterprise_architect", org_admin=True)
    email = org["emails"]["enterprise_architect"]
    secret = None
    if enrolled:
        app = create_app("testing")
        with app.app_context():
            user = User.query.filter_by(email=email).one()
            secret = mfa_service.generate_secret()
            user.mfa_secret = secret
            user.mfa_enabled = True
            db.session.commit()
            db.session.remove()
    return email, secret


def _sign_in(page, base, email, path):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert "/account/login" in page.url, page.url
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()


def _on_allow_page(page):
    expect(page.get_by_role("button", name=re.compile("^Allow", re.I))).to_be_visible(timeout=PAGE_TIMEOUT)
    assert "/oauth/authorize" in page.url


@pytest.mark.smoke
@pytest.mark.journey
def test_a_person_without_two_factor_lands_on_the_allow_page(browser, server, authorize_path):
    org = create_fresh_org("enterprise_architect")
    page = browser.new_context(ignore_https_errors=True).new_page()
    _sign_in(page, server, org["emails"]["enterprise_architect"], authorize_path)
    _on_allow_page(page)


@pytest.mark.smoke
@pytest.mark.journey
def test_an_enrolled_administrator_lands_on_the_allow_page_after_the_code(browser, server, authorize_path):
    email, secret = _make_admin(enrolled=True)
    page = browser.new_context(ignore_https_errors=True).new_page()
    _sign_in(page, server, email, authorize_path)
    page.wait_for_url(re.compile("mfa-challenge"), timeout=PAGE_TIMEOUT)
    page.fill("input[name=code]", pyotp.TOTP(secret).now())
    page.locator("form button[type=submit]").first.click()
    _on_allow_page(page)


@pytest.mark.smoke
@pytest.mark.journey
def test_a_first_time_administrator_lands_on_the_allow_page_after_enrolling(browser, server, authorize_path):
    email, _ = _make_admin(enrolled=False)
    page = browser.new_context(ignore_https_errors=True).new_page()
    _sign_in(page, server, email, authorize_path)
    page.wait_for_url(re.compile("mfa-challenge"), timeout=PAGE_TIMEOUT)
    secret = re.search(r"\b([A-Z2-7]{32})\b", page.content()).group(1)
    page.fill("input[name=code]", pyotp.TOTP(secret).now())
    page.locator("form button[type=submit]").first.click()
    page.wait_for_selector("input[name=recovery_confirmed]", timeout=PAGE_TIMEOUT)
    page.check("input[name=recovery_confirmed]")
    page.locator("form button[type=submit]").first.click()
    _on_allow_page(page)


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("target", ["https://evil.example/steal", "//evil.example/steal", "/" + chr(92) + "evil.example"])
def test_a_next_that_points_off_the_site_is_refused(browser, server, target):
    org = create_fresh_org("enterprise_architect")
    page = browser.new_context(ignore_https_errors=True).new_page()
    _sign_in(page, server, org["emails"]["enterprise_architect"], "/account/login?next=" + urllib.parse.quote(target, safe=""))
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    assert "evil.example" not in page.url, page.url
