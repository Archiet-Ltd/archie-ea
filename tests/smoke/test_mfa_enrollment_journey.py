"""Two-factor enrolment journey for a startup CTO who has just signed up.

The person: the new owner of a workspace, signed in as themselves (never as
the platform administrator). The goal: turn on two-factor authentication, keep
the recovery codes, and get back to work; then, having lost the phone, sign in
with a recovery code, once.

Steps assert what the person needs, not that a page rendered.
"""

import os
import pyotp
import pytest
import re
import time
import uuid

from playwright.sync_api import expect

from .conftest import boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PASSWORD = "StartupCto!Mfa2026"
SHORT = 8000  # ms


class Journey:
    """One person in one browser, carried from step to step."""

    def __init__(self, page, base, tag):
        self.page = page
        self.base = base
        self.tag = tag
        self.started = None
        self.n = 0
        root = os.environ.get("JOURNEY_SHOT_DIR") or os.path.join(
            os.path.dirname(__file__), "..", "..", "design-audit", "journeys", "mfa-enrollment"
        )
        self.dir = os.path.abspath(os.path.join(root, tag))
        os.makedirs(self.dir, exist_ok=True)
        self.record = open(os.path.join(self.dir, "walk-record.md"), "w", encoding="utf-8")
        self.record.write("# MFA Enrollment walk (%s)\n\n" % tag)

    def shot(self, name):
        self.n += 1
        self.page.screenshot(path=os.path.join(self.dir, "%02d-%s.png" % (self.n, name)), full_page=True)

    def saw(self, step, text=None):
        """Record, in plain words, what the person sees at this step."""
        if text is None:
            try:
                text = self.page.locator("main").first.inner_text(timeout=2000)
            except Exception:
                text = ""
        text = " | ".join(t.strip() for t in text.splitlines() if t.strip())[:700]
        self.record.write("## %s\n%s\n\n%s\n\n" % (step, self.page.url, text))
        self.record.flush()

    def main_text(self):
        return self.page.locator("main").first.inner_text()


@pytest.fixture(scope="module")
def mfa_server(request, ai_protocol_stub, app):
    """A server with the real administrator two-factor gate (the shared smoke
    server skips it so other journeys can sign in as administrators)."""
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"FLASK_CONFIG": "testing"})


@pytest.fixture(scope="module", params=[(1440, "light"), (390, "dark")], ids=["desktop-light", "phone-dark"])
def journey(request, browser, mfa_server):
    live_server = mfa_server
    width, theme = request.param
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(30000)
    context.set_default_navigation_timeout(60000)
    if theme == "dark":
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    page = context.new_page()
    j = Journey(page, live_server, "%d-%s" % (width, theme))
    yield j
    elapsed = time.time() - j.started if j.started else 0
    print("\n[mfa-enrollment %s] sign-up to end of journey: %.0f s" % (j.tag, elapsed))
    j.record.close()
    context.close()


def _dismiss_welcome(j):
    """The first-run dialog the way the walk took it: pick the role, continue, skip."""
    page = j.page
    dialog = page.locator("[x-show='showOnboarding']").first
    if not (dialog.count() and dialog.is_visible()):
        return
    page.get_by_text("CTO / CIO", exact=True).first.click()
    for _ in range(5):
        if not dialog.is_visible():
            break
        button = page.locator(
            "[x-show='showOnboarding'] button:visible",
            has_text=re.compile("Continue|Skip|Get started|Done|Finish", re.I),
        )
        if not button.count():
            break
        button.last.click()
        page.wait_for_timeout(500)
    expect(dialog).to_be_hidden(timeout=SHORT)


def _go_home(j):
    j.page.get_by_role("link", name="Home").first.click()
    j.page.wait_for_url(re.compile(r".*/dashboard/overview.*"), timeout=30000)


# ---------------------------------------------------------------------------
# The journey
# ---------------------------------------------------------------------------

_STATE = {}


def _sign_out(j):
    page = j.page
    page.context.clear_cookies()
    page.goto(j.base + "/account/login", wait_until="domcontentloaded")


def _sign_in(j):
    page = j.page
    page.fill("#email", _STATE["email"])
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(re.compile(r".*/account/mfa-challenge.*"), timeout=60000)


def test_01_a_new_cto_signs_up_as_the_workspace_owner(journey, app):
    j, page = journey, journey.page
    j.started = time.time()
    page.goto(j.base + "/", wait_until="domcontentloaded")
    page.get_by_role("link", name="Start free").first.click()
    page.wait_for_url(re.compile(r".*/account/register.*"), timeout=30000)
    email = "startup.cto.%s@gmail.com" % uuid.uuid4().hex[:10]
    _STATE["email"] = email
    page.fill("#first_name", "Chidi")
    page.fill("#last_name", "Okeke")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    j.shot("signed-up")

    # The persona is the workspace owner, not the platform administrator.
    from app.models.user import User

    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.is_platform_admin is False, "the new owner must not be a platform administrator"
        assert user.enterprise_role != "platform_admin"


def test_02_signing_back_in_asks_for_two_factor_on_one_screen(journey):
    j, page = journey, journey.page
    _sign_out(j)
    _sign_in(j)
    j.saw("Two-factor set-up")
    j.shot("scan-and-verify")
    text = j.main_text()
    assert "Set up two-factor authentication" in text
    assert "Remind me later" not in text and "days remaining" not in text
    # The QR code is drawn from the page itself, not fetched from another site.
    qr = page.locator("img[alt*='QR code']")
    expect(qr).to_be_visible(timeout=SHORT)
    assert qr.get_attribute("src").startswith("data:image/png;base64,")
    secret = page.locator("#setup-key").inner_text().strip()
    assert len(secret) >= 16
    _STATE["secret"] = secret
    expect(page.locator("#mfa-code")).to_be_visible()


def test_03_a_wrong_code_explains_itself_and_keeps_the_qr_code(journey):
    j, page = journey, journey.page
    page.fill("#mfa-code", "000000")
    page.get_by_role("button", name="Verify and continue").click()
    page.wait_for_load_state("domcontentloaded")
    j.shot("wrong-code")
    assert "did not match" in j.main_text()
    expect(page.locator("#setup-key")).to_have_text(_STATE["secret"])


def test_04_the_right_code_shows_eight_recovery_codes_once(journey):
    j, page = journey, journey.page
    page.fill("#mfa-code", pyotp.TOTP(_STATE["secret"]).now())
    page.get_by_role("button", name="Verify and continue").click()
    expect(page.locator("#recovery-codes code").first).to_be_visible(timeout=SHORT)
    codes = [c.strip() for c in page.locator("#recovery-codes code").all_inner_texts()]
    assert len(codes) == 8 and len(set(codes)) == 8
    _STATE["codes"] = codes
    j.saw("Recovery codes")
    j.shot("recovery-codes")
    expect(page.get_by_role("button", name="Finish")).to_be_disabled()
    with page.expect_download() as download_info:
        page.get_by_role("button", name="Download").click()
    assert download_info.value.suggested_filename == "entelim-recovery-codes.txt"
    page.locator("#recovery-confirmed").check()
    expect(page.get_by_role("button", name="Finish")).to_be_enabled()


def test_05_finish_lands_on_the_dashboard_and_the_codes_are_not_shown_again(journey, app):
    j, page = journey, journey.page
    page.get_by_role("button", name="Finish").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    j.shot("dashboard")

    from app import db
    from app.models.user import User

    with app.app_context():
        stored = db.session.execute(
            db.text("SELECT mfa_recovery_codes FROM users WHERE email = :e"), {"e": _STATE["email"]}
        ).scalar()
        assert stored and not any(code in stored for code in _STATE["codes"])


def test_06_a_lost_phone_signs_in_with_a_recovery_code_once(journey):
    j, page = journey, journey.page
    code = _STATE["codes"][0]
    _sign_out(j)
    _sign_in(j)
    assert "Enter your verification code" in j.main_text()
    page.get_by_role("link", name="Lost your device? Use a recovery code").click()
    page.wait_for_url(re.compile(r".*recovery=1.*"))
    assert "Use a recovery code" in j.main_text()
    j.shot("use-recovery-code")
    page.fill("#mfa-code", code)
    page.get_by_role("button", name="Verify").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    j.shot("signed-in-with-recovery-code")

    # The same code a second time is refused, and the person is told so.
    _sign_out(j)
    _sign_in(j)
    page.get_by_role("link", name="Lost your device? Use a recovery code").click()
    page.fill("#mfa-code", code)
    page.get_by_role("button", name="Verify").click()
    page.wait_for_load_state("domcontentloaded")
    j.shot("used-code-refused")
    assert "not accepted" in j.main_text()
    assert "/dashboard" not in page.url

    # A different unused code still works: the person is not locked out.
    page.fill("#mfa-code", _STATE["codes"][1])
    page.get_by_role("button", name="Verify").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
