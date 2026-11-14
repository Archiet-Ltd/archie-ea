"""Continue with Google and Continue with Microsoft, as a startup CTO would use them.

A stand-in provider plays Google and Microsoft. The application runs as a real server and a real browser
clicks the real buttons.

J-1: a new CTO on the register page clicks Continue with Google and lands on the welcome steps signed in,
     in an organisation of their own, never seeing a "check your email" page; the team page lists only them,
     as administrator, and none of the other organisation's records show.
J-2: someone with a password account clicks Continue with Microsoft on the sign-in page, is asked to prove
     the account first, and afterwards Continue with Microsoft signs them straight in; the dashboard's
     application count equals the applications list.
J-3: a Google account whose email Google has not verified is told so, in plain words, and no account exists.
"""
import os
import uuid
from unittest import mock

import pytest
from playwright.sync_api import expect

from tests.support.fake_oidc_provider import CLIENT_ID, CLIENT_SECRET, PERSONAL_TENANT, FakeOIDCProvider

from . import conftest as smoke
from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from .overview_data import purge_org, seed_overview_org
from .test_first_screen_one_message_journey import FOLD, PHONE, _page

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("SOCIAL_SIGN_IN_SHOTS")

APPS = [
    {"name": "Flow Tracker", "domain": "Operations"},
    {"name": "Ledger Core", "owner_record": True, "cost": 120000, "domain": "Finance"},
    {"name": "Payments Hub", "owner_text": "Dana Reed", "cost": 80000, "domain": "Finance"},
]
CONTRACTS = [("Northbeam", 200000, 8)]


@pytest.fixture(scope="module")
def providers():
    google = FakeOIDCProvider("google")
    microsoft = FakeOIDCProvider("microsoft")
    yield {"google": google, "microsoft": microsoft}
    google.close()
    microsoft.close()


@pytest.fixture(scope="module")
def server(request, ai_protocol_stub, app, providers):
    port = smoke._free_port()
    env = {
        "GOOGLE_OAUTH_CLIENT_ID": CLIENT_ID, "GOOGLE_OAUTH_CLIENT_SECRET": CLIENT_SECRET,
        "MICROSOFT_OAUTH_CLIENT_ID": CLIENT_ID, "MICROSOFT_OAUTH_CLIENT_SECRET": CLIENT_SECRET,
        "OAUTH_REDIRECT_HOSTS": "127.0.0.1:%d" % port,
        "OAUTH_TEST_METADATA_URL_GOOGLE": providers["google"].metadata_url,
        "OAUTH_TEST_METADATA_URL_MICROSOFT": providers["microsoft"].metadata_url,
    }
    with mock.patch.object(smoke, "_free_port", return_value=port):
        yield boot_live_server(request, ai_protocol_stub, app, extra_env=env)


@pytest.fixture(scope="module")
def organisation(server):
    """A company that already holds linked applications, owners and a contract cost, with its owner's password account."""
    info = seed_overview_org("social", APPS, CONTRACTS)
    yield info
    purge_org(info["org_id"])


def _shoot(page, name, width):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)


def _text(page):
    return " ".join(page.locator("body").inner_text().split())


def _arrive(page, pattern):
    """Wait for the browser to reach a page; when it does not, say where it is and what it shows."""
    try:
        page.wait_for_url(pattern, timeout=PAGE_TIMEOUT)
    except Exception:
        raise AssertionError("never reached %s; the browser is at %s and shows: %s" % (pattern, page.url, _text(page)[:300]))


def _count(text):
    return int("".join(ch for ch in text if ch.isdigit()))


def test_j1_a_new_cto_signs_up_with_google_and_lands_on_the_welcome_steps(browser, server, providers, organisation):
    """J-1: register, Continue with Google, welcome steps, signed in, alone in an organisation of their own."""
    email = "cto.%s@example.com" % uuid.uuid4().hex[:8]
    providers["google"].queue(providers["google"].google_claims("g-" + uuid.uuid4().hex, email, given_name="Ada", family_name="Okafor"))
    context, page = _page(browser, FOLD)
    try:
        page.goto(server + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("social-sign-in-google")).to_be_visible()
        expect(page.get_by_test_id("social-sign-in-microsoft")).to_be_visible()
        expect(page.get_by_text("By continuing, you agree to the").first).to_be_visible()
        _shoot(page, "j1-register", 1440)
        page.get_by_test_id("social-sign-in-google").click()
        _arrive(page, "**/account/welcome**")

        body = _text(page)
        assert "check your email" not in body.lower() and "confirm your email" not in body.lower(), body[:300]
        assert "/account/unconfirmed" not in page.url
        _shoot(page, "j1-welcome", 1440)

        # Signed in as the person who signed up: the team page lists only them, as administrator.
        page.goto(server + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        team = _text(page)
        assert email in team and "Ada" in team, team[:400]
        people = [r.inner_text() for r in page.locator("table tbody tr").all() if "@" in r.inner_text()]
        assert len(people) == 1 and email in people[0], people
        assert "admin" in people[0].lower(), people
        _shoot(page, "j1-team", 1440)

        # None of the other company's records are theirs to see.
        page.goto(server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        shown = _text(page)
        for name in ("Flow Tracker", "Ledger Core", "Payments Hub"):
            assert name not in shown, name
    finally:
        context.close()

    from app import create_app
    from app.models.sign_in_identity import SignInIdentity
    from app.models.user import User

    with create_app("testing").app_context():
        person = User.find_by_email(email)
        assert person is not None and person.confirmed is True
        assert person.organization_id != organisation["org_id"]
        assert [i.provider for i in SignInIdentity.query.filter_by(user_id=person.id).all()] == ["google"]


def test_j2_a_password_user_connects_microsoft_and_then_signs_in_with_it(browser, server, providers, organisation):
    """J-2: Continue with Microsoft asks for the password once; afterwards it signs straight in to the same company."""
    email = organisation["emails"]["owner"]
    claims = providers["microsoft"].microsoft_claims(uuid.uuid4().hex, email=email, tid=PERSONAL_TENANT)
    context, page = _page(browser, FOLD)
    try:
        page.goto(server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _shoot(page, "j2-login", 1440)
        providers["microsoft"].queue(claims)
        page.get_by_test_id("social-sign-in-microsoft").click()
        _arrive(page, "**/account/login**")

        assert "An Entelim account already uses %s. Sign in to it once to connect Microsoft." % email in _text(page)
        expect(page.locator("#email")).to_have_value(email)
        assert "dashboard" not in page.url
        _shoot(page, "j2-connect-message", 1440)

        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)

        page.goto(server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("sign-in-methods-card")
        expect(card).to_contain_text("Microsoft")
        expect(card).to_contain_text(email)
        _shoot(page, "j2-sign-in-methods", 1440)

        page.goto(server + "/account/logout", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.goto(server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        providers["microsoft"].queue(claims)
        page.get_by_test_id("social-sign-in-microsoft").click()
        _arrive(page, "**/dashboard/**")

        # The company's own figures: the dashboard counts what the applications list holds.
        page.goto(server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        counted = _count(page.get_by_test_id("welcome-applications").locator("span").first.inner_text())
        assert counted >= 1, counted
        _shoot(page, "j2-dashboard", 1440)
        page.get_by_test_id("welcome-applications").click()
        _arrive(page, "**/applications/**")
        expect(page.get_by_text("Ledger Core", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        # Each application is two rows (the record and its evidence), so count the applications by name.
        shown = _text(page)
        assert counted == len(APPS), counted
        for spec in APPS:
            assert shown.count(spec["name"]) >= 1, spec["name"]
    finally:
        context.close()


def test_j2_phone_width_buttons_are_usable(browser, server, providers):
    """J-2 (phone): at 390 wide both buttons are on screen, full width, and the page does not scroll sideways."""
    context, page = _page(browser, PHONE)
    try:
        page.goto(server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for key in ("google", "microsoft"):
            button = page.get_by_test_id("social-sign-in-" + key)
            expect(button).to_be_visible()
            box = button.bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"], box
            assert box["width"] > PHONE["width"] * 0.6, box
        assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
        _shoot(page, "j2-login", 390)
    finally:
        context.close()


def test_j3_an_unverified_google_email_is_refused_in_plain_words(browser, server, providers):
    """J-3: back on sign-in with the message and the email form; no account exists for that address."""
    email = "unverified.%s@example.com" % uuid.uuid4().hex[:8]
    providers["google"].queue(providers["google"].google_claims("g-" + uuid.uuid4().hex, email, verified=False))
    context, page = _page(browser, FOLD)
    try:
        page.goto(server + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("social-sign-in-google").click()
        _arrive(page, "**/account/login**")
        assert "Google did not confirm this email address. Use Continue with Microsoft, or sign up with email and password." in _text(page)
        expect(page.locator("#email")).to_be_visible()
        expect(page.locator("#password")).to_be_visible()
        _shoot(page, "j3-refused", 1440)
    finally:
        context.close()

    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        assert User.find_by_email(email) is None
