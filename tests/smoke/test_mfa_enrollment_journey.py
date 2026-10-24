"""MFA enrollment journey for a startup CTO after their first answer.

The person: a CTO who has just received their first cited answer and is now
prompted to set up two-factor authentication. The goal: complete the three-step
enrollment flow (scan QR, enter code, save recovery codes) and land back on
the dashboard.

Steps assert what the person needs, not that a page rendered.
"""

import os
import pyotp
import pytest
import re
import time
import uuid

from playwright.sync_api import expect

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


@pytest.fixture(scope="module", params=[(1440, "light"), (390, "dark")], ids=["desktop-light", "phone-dark"])
def journey(request, browser, live_server):
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
# Sign-up as an administrator (CTO)
# ---------------------------------------------------------------------------


def test_01_a_new_cto_signs_up_from_the_landing_page(journey):
    j, page = journey, journey.page
    j.started = time.time()
    page.goto(j.base + "/", wait_until="domcontentloaded")
    j.shot("landing")
    page.get_by_role("link", name="Start free").first.click()
    page.wait_for_url(re.compile(r".*/account/register.*"), timeout=30000)
    email = "startup.cto.%s@gmail.com" % uuid.uuid4().hex[:10]
    page.fill("#first_name", "Chidi")
    page.fill("#last_name", "Okeke")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    j.shot("sign-up-form")
    page.locator("#submit").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    page.wait_for_timeout(1500)
    j.saw("Signed up")
    j.shot("signed-up")
    assert "/dashboard" in page.url, "signing up should land the person in their new workspace"


def test_02_sign_up_ends_on_dashboard_not_job_title_question(journey):
    j, page = journey, journey.page
    dialog = page.locator("[x-show='showOnboarding']").first
    asked_a_role_question = bool(dialog.count() and dialog.is_visible())
    j.saw("First screen after sign-up", dialog.inner_text() if asked_a_role_question else None)
    try:
        _dismiss_welcome(j)
    finally:
        j.shot("after-first-run-dialog")
    assert not asked_a_role_question, (
        "a startup CTO should land on the dashboard, not an eleven-way job-title question"
    )


# ---------------------------------------------------------------------------
# Trigger MFA enrollment by signing out and back in
# ---------------------------------------------------------------------------


def test_03_sign_out_and_back_in_triggers_mfa_enrollment(journey):
    j, page = journey, journey.page
    _go_home(j)
    # Sign out
    page.get_by_role("button", name=re.compile(r"user|account|menu", re.I)).first.click()
    page.get_by_role("link", name=re.compile(r"sign out|log out", re.I)).first.click()
    page.wait_for_url(re.compile(r".*/account/login.*"), timeout=30000)
    j.saw("Signed out, on login page")
    j.shot("signed-out")

    # Sign back in - this should trigger MFA enrollment for admin
    page.fill("#email", page.locator("#email").input_value() or "startup.cto.*.gmail.com")
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(re.compile(r".*/account/mfa-challenge.*"), timeout=60000)
    j.saw("MFA enrollment step 1 - scan QR code")
    j.shot("mfa-step1-scan")

    # Verify step 1 content
    text = j.main_text()
    assert "Set up two-factor authentication" in text
    assert "So nobody else can sign in as you" in text
    assert "Scan the QR code" in text or "scan this QR code" in text.lower()
    assert "Remind me later" in text
    assert "7 days remaining" in text


def test_04_step1_has_qr_code_and_secret_with_copy_button(journey):
    j, page = journey, journey.page
    # Check QR code image exists
    qr_img = page.locator("img[src*='qrserver']").first
    expect(qr_img).to_be_visible(timeout=SHORT)

    # Check setup key is displayed
    setup_key = page.locator("#setup-key").first
    expect(setup_key).to_be_visible(timeout=SHORT)
    secret = setup_key.inner_text()
    assert len(secret) > 0, "Setup key should be displayed"

    # Check copy button exists
    copy_btn = page.locator("button:has-text('Copy')").first
    expect(copy_btn).to_be_visible(timeout=SHORT)

    # Check "I've scanned the code" button
    next_btn = page.locator("button:has-text(\"I've scanned the code\")").first
    expect(next_btn).to_be_visible(timeout=SHORT)

    j.saw("Step 1 - QR code and secret verified")
    j.shot("mfa-step1-verified")


def test_05_click_next_to_step2_enter_code(journey):
    j, page = journey, journey.page
    # Click "I've scanned the code" to go to step 2
    page.locator("button:has-text(\"I've scanned the code\")").first.click()
    page.wait_for_timeout(1000)
    j.saw("MFA enrollment step 2 - enter code")
    j.shot("mfa-step2-enter-code")

    # Verify step 2 content
    text = j.main_text()
    assert "Enter the 6-digit code" in text
    assert "Remind me later" in text
    assert "7 days remaining" in text

    # Check input field exists
    code_input = page.locator("#mfa-code").first
    expect(code_input).to_be_visible(timeout=SHORT)
    expect(code_input).to_have_attribute("maxlength", "6")
    expect(code_input).to_have_attribute("inputmode", "numeric")
    expect(code_input).to_have_attribute("autocomplete", "one-time-code")


def test_06_enter_valid_totp_code_advances_to_recovery_codes(journey):
    j, page = journey, journey.page
    # Get the secret from step 1 (we need to extract it from the page)
    # The secret is in the setup-key element
    secret = page.locator("#setup-key").first.inner_text().strip().replace(" ", "")
    totp = pyotp.TOTP(secret)
    code = totp.now()

    # Enter the code
    page.fill("#mfa-code", code)
    page.locator("button:has-text('Verify and continue')").first.click()
    page.wait_for_url(re.compile(r".*/account/mfa-challenge.*"), timeout=30000)
    page.wait_for_timeout(1500)
    j.saw("MFA enrollment step 3 - recovery codes")
    j.shot("mfa-step3-recovery-codes")

    # Verify step 3 content
    text = j.main_text()
    assert "Two-factor authentication is now enabled" in text
    assert "recovery codes" in text.lower()
    assert "Save these recovery codes" in text
    assert "Copy all" in text
    assert "Download" in text
    assert "I have saved these codes" in text
    assert "Finish" in text

    # Check recovery codes are displayed (8 codes)
    recovery_codes = page.locator("#recovery-codes code")
    expect(recovery_codes.first).to_be_visible(timeout=SHORT)
    count = recovery_codes.count()
    assert count == 8, f"Expected 8 recovery codes, got {count}"

    # Check Finish button is initially disabled
    finish_btn = page.locator("button:has-text('Finish')").first
    expect(finish_btn).to_be_disabled(timeout=SHORT)


def test_07_copy_all_recovery_codes_works(journey):
    j, page = journey, journey.page
    # Click Copy all button
    page.locator("button:has-text('Copy all')").first.click()
    page.wait_for_timeout(500)
    # Button should show "Copied!" temporarily
    copied_btn = page.locator("button:has-text('Copied!')").first
    expect(copied_btn).to_be_visible(timeout=SHORT)
    j.saw("Copy all recovery codes")
    j.shot("mfa-copy-all")


def test_08_download_recovery_codes_works(journey):
    j, page = journey, journey.page
    # Click Download button - this triggers a download
    with page.expect_download() as download_info:
        page.locator("button:has-text('Download')").first.click()
    download = download_info.value
    assert download.suggested_filename == "entelim-recovery-codes.txt"
    j.saw("Download recovery codes")
    j.shot("mfa-download")


def test_09_checking_checkbox_enables_finish_button(journey):
    j, page = journey, journey.page
    # Check the checkbox
    page.locator("#recovery-confirmed").first.check()
    page.wait_for_timeout(200)
    # Finish button should now be enabled
    finish_btn = page.locator("button:has-text('Finish')").first
    expect(finish_btn).to_be_enabled(timeout=SHORT)
    j.saw("Checkbox checked, Finish enabled")
    j.shot("mfa-checkbox-checked")


def test_10_clicking_finish_completes_enrollment_and_lands_on_dashboard(journey):
    j, page = journey, journey.page
    # Click Finish
    page.locator("button:has-text('Finish')").first.click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    page.wait_for_timeout(1500)
    j.saw("MFA enrollment complete, on dashboard")
    j.shot("mfa-complete-dashboard")

    # Verify we're on the dashboard
    assert "/dashboard" in page.url, "should land on dashboard after MFA enrollment"
    text = j.main_text()
    assert "Welcome" in text or "Dashboard" in text or "Applications" in text


def test_11_wrong_code_shows_error_and_stays_on_step2(journey):
    """Test that entering a wrong code shows an error and keeps the user on step 2."""
    j, page = journey, journey.page
    # Sign out and back in to trigger a new enrollment
    _go_home(j)
    page.get_by_role("button", name=re.compile(r"user|account|menu", re.I)).first.click()
    page.get_by_role("link", name=re.compile(r"sign out|log out", re.I)).first.click()
    page.wait_for_url(re.compile(r".*/account/login.*"), timeout=30000)

    # Sign in again
    page.fill("#email", "startup.cto.%s@gmail.com" % uuid.uuid4().hex[:10])
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(re.compile(r".*/account/mfa-challenge.*"), timeout=60000)

    # Go to step 2
    page.locator("button:has-text(\"I've scanned the code\")").first.click()
    page.wait_for_timeout(1000)

    # Enter wrong code
    page.fill("#mfa-code", "000000")
    page.locator("button:has-text('Verify and continue')").first.click()
    page.wait_for_timeout(1500)

    # Should show error and stay on step 2
    text = j.main_text()
    assert "That code did not match" in text or "not accepted" in text.lower() or "wrong" in text.lower()
    assert "Enter the 6-digit code" in text
    j.saw("Wrong code shows error")
    j.shot("mfa-wrong-code-error")


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def test_12_sign_up_to_mfa_complete_under_budget(journey):
    elapsed = time.time() - journey.started
    print("\n[mfa-enrollment %s] elapsed since sign-up: %.0f s" % (journey.tag, elapsed))
    journey.record.write("## Elapsed\n%.0f seconds from landing page to MFA complete.\n" % elapsed)
    assert elapsed < 300, "sign-up to MFA complete took %.0f s; budget is 300 s" % elapsed