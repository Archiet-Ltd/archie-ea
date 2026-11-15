"""Continue with Google and Continue with Microsoft, as a startup CTO would use them.

A stand-in provider plays Google and Microsoft. The application runs as a real server and a real browser
clicks the real buttons.

- A new CTO on the register page clicks Continue with Google and lands on the welcome steps signed in,
  in an organisation of their own, never seeing a "check your email" page; the team page lists only them,
  as administrator, and none of the other organisation's records show.
- Someone with a password account clicks Continue with Microsoft on the sign-in page, is asked to prove
  the account first, and afterwards Continue with Microsoft signs them straight in; the dashboard's
  application count equals the applications list.
- A Google account whose email Google has not verified is told so, in plain words, and no account exists.
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


def test_a_new_cto_signs_up_with_google_and_lands_on_the_welcome_steps(browser, server, providers, organisation):
    """Register, Continue with Google, welcome steps, signed in, alone in an organisation of their own."""
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


def test_a_password_user_connects_microsoft_and_then_signs_in_with_it(browser, server, providers, organisation):
    """Continue with Microsoft asks for the password once; afterwards it signs straight in to the same company."""
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


def test_phone_width_buttons_are_usable(browser, server, providers):
    """Phone: at 390 wide both buttons are on screen, full width, and the page does not scroll sideways."""
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


def test_an_unverified_google_email_is_refused_in_plain_words(browser, server, providers):
    """Back on sign-in with the message and the email form; no account exists for that address."""
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


def _person_with_google(org_id):
    """A member of ``org_id`` who signs in with a password and has connected Google. Returns the email."""
    from datetime import datetime, timezone

    from app import create_app, db
    from app.models.sign_in_identity import SignInIdentity
    from app.models.user import Role, User

    email = "connected.%s@example.com" % uuid.uuid4().hex[:8]
    with create_app("testing").app_context():
        user = User(email=email, first_name="Chris", last_name="Linked", organization_id=org_id,
                    enterprise_role="enterprise_architect", confirmed=True)
        user.role = Role.query.filter_by(name="Administrator").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.flush()
        now = datetime.now(timezone.utc)
        db.session.add(SignInIdentity(user_id=user.id, provider="google", issuer="https://accounts.google.com",
                                      subject="g-" + uuid.uuid4().hex, email_at_link=email, created_at=now, last_used_at=now))
        db.session.commit()
    return email


def _google_rows(email):
    from app import create_app
    from app.models.sign_in_identity import SignInIdentity
    from app.models.user import User

    with create_app("testing").app_context():
        person = User.find_by_email(email)
        return SignInIdentity.query.filter_by(user_id=person.id, provider="google").count()


def test_a_person_with_a_password_disconnects_google_and_it_stays_gone(browser, server, providers, organisation):
    """Cancel keeps Google connected; confirm removes it, and after a reload the card and the database agree."""
    email = _person_with_google(organisation["org_id"])
    context, page = _page(browser, FOLD)
    try:
        page.goto(server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", email)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)

        page.goto(server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_test_id("sign-in-method-google")
        expect(row).to_contain_text(email)
        _shoot(page, "disconnect-before", 1440)

        row.get_by_role("button", name="Disconnect Google").click()
        dialog = page.locator("[role='dialog']:visible").first
        expect(dialog).to_contain_text("Disconnect Google from your account?")
        dialog.get_by_role("button", name="Cancel").click()
        expect(page.locator("[role='dialog']:visible")).to_have_count(0)
        assert _google_rows(email) == 1

        row.get_by_role("button", name="Disconnect Google").click()
        _shoot(page, "disconnect-confirm", 1440)
        page.locator("[role='dialog']:visible").first.get_by_role("button", name="Confirm").click()
        _arrive(page, "**/account/manage**")
        expect(page.get_by_text("Google sign-in was disconnected from your account.")).to_be_visible()

        page.reload(wait_until="domcontentloaded")
        card = page.get_by_test_id("sign-in-methods-card")
        expect(card).to_be_visible()
        expect(page.get_by_test_id("sign-in-method-google")).to_have_count(0)
        expect(card).to_contain_text("No Google or Microsoft sign-in is connected")
        assert _google_rows(email) == 0
        _shoot(page, "disconnect-after", 1440)
    finally:
        context.close()


def test_a_person_whose_only_way_in_is_google_is_refused_in_plain_words(browser, server, providers, organisation):
    """No password and no other provider: Disconnect is refused with a sentence, and Google is still listed after a reload."""
    from app import create_app
    from app.models.user import User

    email = "only.%s@example.com" % uuid.uuid4().hex[:8]
    providers["google"].queue(providers["google"].google_claims("g-" + uuid.uuid4().hex, email, given_name="Sam", family_name="Solo"))
    context, page = _page(browser, FOLD)
    try:
        page.goto(server + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("social-sign-in-google").click()
        _arrive(page, "**/account/welcome**")
        with create_app("testing").app_context():
            assert User.find_by_email(email).password_hash is None

        page.goto(server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_test_id("sign-in-method-google")
        expect(row).to_contain_text(email)
        row.get_by_role("button", name="Disconnect Google").click()
        page.locator("[role='dialog']:visible").first.get_by_role("button", name="Confirm").click()
        _arrive(page, "**/account/manage**")
        refusal = ("Google is your only way to sign in, so it cannot be disconnected yet. "
                   "Set a password first: use Forgot password on the sign-in page.")
        expect(page.get_by_text(refusal)).to_be_visible()
        page.wait_for_timeout(800)  # the notice slides in; photograph it at rest
        box = page.get_by_text(refusal).bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= FOLD["width"], box
        _shoot(page, "disconnect-refused", 1440)

        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("sign-in-method-google")).to_contain_text(email)
        assert _google_rows(email) == 1
    finally:
        context.close()


def test_phone_width_dark_sign_in_methods_card_and_confirmation_fit_the_screen(browser, server, providers, organisation):
    """At 390 wide in the dark theme the card, its Disconnect button and the confirmation all sit inside the screen."""
    email = _person_with_google(organisation["org_id"])
    context, page = _page(browser, PHONE)
    try:
        page.goto(server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.evaluate("() => document.documentElement.classList.add('dark')")
        page.fill("#email", email)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
        page.goto(server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.evaluate("() => document.documentElement.classList.add('dark')")

        row = page.get_by_test_id("sign-in-method-google")
        expect(row).to_contain_text(email)
        button = row.get_by_role("button", name="Disconnect Google")
        box = button.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"], box
        assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
        _shoot(page, "disconnect-card-dark", 390)

        button.click()
        dialog = page.locator("[role='dialog']:visible").first
        expect(dialog).to_contain_text("Disconnect Google from your account?")
        dbox = dialog.bounding_box()
        assert dbox["x"] >= 0 and dbox["x"] + dbox["width"] <= PHONE["width"], dbox
        _shoot(page, "disconnect-confirm-dark", 390)
        dialog.get_by_role("button", name="Cancel").click()
        assert _google_rows(email) == 1
    finally:
        context.close()
