"""A person whose only way in is Google sets a password from Forgot password, and then Disconnect works.

The application runs as a real server with a Google stand-in and a mail sink; a real browser clicks the controls.
"""
import os
import threading
import uuid
from unittest import mock

import pytest
from playwright.sync_api import expect

from tests.support.fake_oidc_provider import CLIENT_ID, CLIENT_SECRET, FakeOIDCProvider

from . import conftest as smoke
from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_account_mail_journey import SmtpSink, _link_in
from .test_first_screen_one_message_journey import FOLD, PHONE, _page

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("SOCIAL_SIGN_IN_SHOTS")
NEW_PASSWORD = "First-Password!2026"
REFUSAL = ("Google is your only way to sign in, so it cannot be disconnected yet. "
           "Set a password first: use Forgot password on the sign-in page.")


@pytest.fixture(scope="module")
def google():
    provider = FakeOIDCProvider("google")
    yield provider
    provider.close()


@pytest.fixture(scope="module")
def sink():
    s = SmtpSink()
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


@pytest.fixture(scope="module")
def server(request, ai_protocol_stub, app, google, sink):
    port = smoke._free_port()
    env = {
        "GOOGLE_OAUTH_CLIENT_ID": CLIENT_ID, "GOOGLE_OAUTH_CLIENT_SECRET": CLIENT_SECRET,
        "OAUTH_REDIRECT_HOSTS": "127.0.0.1:%d" % port,
        "OAUTH_TEST_METADATA_URL_GOOGLE": google.metadata_url,
        "MAIL_SERVER": "127.0.0.1", "MAIL_PORT": str(sink.server_address[1]),
        "MAIL_USE_TLS": "false", "MAIL_USE_SSL": "false", "MAIL_USERNAME": "", "MAIL_PASSWORD": "",
        "MAIL_DEFAULT_SENDER": "no-reply@example.com", "MAIL_SUPPRESS_SEND": "false",
    }
    with mock.patch.object(smoke, "_free_port", return_value=port):
        yield boot_live_server(request, ai_protocol_stub, app, extra_env=env)


def _shoot(page, name, width):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)


def _arrive(page, pattern):
    page.wait_for_url(pattern, timeout=PAGE_TIMEOUT)


def _google_rows(email):
    from app import create_app
    from app.models.sign_in_identity import SignInIdentity
    from app.models.user import User

    with create_app("testing").app_context():
        return SignInIdentity.query.filter_by(user_id=User.find_by_email(email).id, provider="google").count()


def _disconnect(page, server):
    page.goto(server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("sign-in-method-google").get_by_role("button", name="Disconnect Google").click()
    page.locator("[role='dialog']:visible").first.get_by_role("button", name="Confirm").click()
    _arrive(page, "**/account/manage**")


@pytest.mark.parametrize("viewport,width,dark", [(FOLD, 1440, False), (PHONE, 390, True)])
def test_a_google_only_person_sets_a_password_and_then_disconnects_google(browser, server, google, sink, viewport, width, dark):
    email = "only.%s@example.com" % uuid.uuid4().hex[:8]
    google.queue(google.google_claims("g-" + uuid.uuid4().hex, email, given_name="Sam", family_name="Solo"))
    context, page = _page(browser, viewport)
    try:
        page.goto(server + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("social-sign-in-google").click()
        _arrive(page, "**/account/welcome**")

        _disconnect(page, server)
        expect(page.get_by_text(REFUSAL)).to_be_visible()
        page.wait_for_timeout(800)
        assert _google_rows(email) == 1
    finally:
        context.close()

    context, page = _page(browser, viewport)
    try:
        page.goto(server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if dark:
            page.evaluate("() => document.documentElement.classList.add('dark')")
        page.get_by_role("link", name="Forgot password?").click()
        _arrive(page, "**/account/reset-password")
        page.fill("#email", email)
        page.get_by_role("button", name="Reset password").click()
        page.get_by_text("Check your e-mail").wait_for(timeout=PAGE_TIMEOUT)

        link = _link_in(sink.text_to(email), "/account/reset-password/")
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if dark:
            page.evaluate("() => document.documentElement.classList.add('dark')")
        page.get_by_text("Choose a new password").wait_for(timeout=PAGE_TIMEOUT)
        _shoot(page, "set-first-password", width)
        page.fill("#password", NEW_PASSWORD)
        page.fill("#password2", NEW_PASSWORD)
        page.get_by_role("button", name="Set new password").click()
        _arrive(page, "**/account/login")

        page.fill("#email", email)
        page.fill("#password", NEW_PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)

        _disconnect(page, server)
        expect(page.get_by_text("Google sign-in was disconnected from your account.")).to_be_visible()
        page.wait_for_timeout(800)
        _shoot(page, "disconnected", width)
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("sign-in-method-google")).to_have_count(0)
        assert _google_rows(email) == 0
        assert page.evaluate("document.documentElement.scrollWidth") <= viewport["width"]
    finally:
        context.close()
