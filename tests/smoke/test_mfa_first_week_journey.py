"""The first week without two-factor sign-in, as the startup CTO who signs up lives it.

The person: the founder-CTO of a new workspace, signed in as themselves, alone in it.
Goal one: reach a first answer with no authenticator, be asked once afterwards to
secure the account, do it, and find sign-in now asks for a code.
Goal two: invite a colleague, be taken to set up two-factor first, and come back to
the invite form to finish the job.
"""

import os
import re
import time
import uuid
from datetime import datetime, timedelta

import pyotp
import pytest
from playwright.sync_api import expect

from .conftest import boot_live_server
from .test_mfa_enrollment_journey import Journey

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PASSWORD = "StartupCto!Week1-2026"
SHORT = 8000  # ms
_STATE = {}


@pytest.fixture(scope="module")
def week_server(request, ai_protocol_stub, app):
    """A server with the real administrator rule (the shared smoke server skips it)."""
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"FLASK_CONFIG": "testing"})


@pytest.fixture(scope="module", params=[(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")],
                ids=["desktop-light", "desktop-dark", "phone-light", "phone-dark"])
def journey(request, browser, week_server):
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
    j = Journey(page, week_server, "first-week-%d-%s" % (width, theme))
    yield j
    j.record.close()
    context.close()


def _sign_up(j, label):
    page = j.page
    page.context.clear_cookies()
    page.goto(j.base + "/account/register", wait_until="domcontentloaded")
    assert "Your first week: you can start without a phone app." in j.main_text().replace("’", "'")
    j.shot(label + "-sign-up-page")
    email = "startup.cto.%s@gmail.com" % uuid.uuid4().hex[:10]
    page.fill("#first_name", "Chidi")
    page.fill("#last_name", "Okeke")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)
    return email


def _seed_first_answer(app, email):
    """The first cited answer, recorded the way the chat records one."""
    from app import db
    from app.models.conversation import ConversationMessageRecord, ConversationThreadRecord
    from app.models.user import User

    with app.app_context():
        user = User.query.filter_by(email=email).one()
        now = datetime.utcnow()
        thread_id = uuid.uuid4().hex
        db.session.add(ConversationThreadRecord(
            id=thread_id, user_id=user.id, organization_id=user.organization_id,
            title="What should we own first?", model="stub", created_at=now, updated_at=now))
        db.session.flush()
        db.session.add(ConversationMessageRecord(
            id=uuid.uuid4().hex, thread_id=thread_id, role="assistant",
            content="Start with the billing service [record 1].", created_at=now))
        db.session.commit()
        return user.signup_at


def _enrol(j, expect_return):
    """Scan step to recovery codes to Finish, the way a person does it."""
    page = j.page
    secret = page.locator("#setup-key").inner_text().strip()
    page.fill("#mfa-code", "000000")
    page.get_by_role("button", name="Verify and continue").click()
    page.wait_for_load_state("domcontentloaded")
    assert "did not match" in j.main_text()
    page.fill("#mfa-code", pyotp.TOTP(secret).now())
    page.get_by_role("button", name="Verify and continue").click()
    expect(page.locator("#recovery-codes code").first).to_be_visible(timeout=SHORT)
    assert len(page.locator("#recovery-codes code").all_inner_texts()) == 8
    j.shot("recovery-codes")
    page.locator("#recovery-confirmed").check()
    page.get_by_role("button", name="Finish").click()
    page.wait_for_url(re.compile(expect_return), timeout=60000)
    return secret


# -- goal one ----------------------------------------------------------------------------------


def test_01_sign_up_reaches_the_product_with_no_authenticator(journey, app):
    j, page = journey, journey.page
    j.started = time.time()
    _STATE["email"] = _sign_up(j, "one")
    j.saw("After sign-up")
    j.shot("one-after-sign-up")
    assert "/account/mfa" not in page.url, page.url
    # Confirm in a fresh browser: no second factor is asked for before an answer.
    assert "Set up two-factor authentication" not in j.page.content()


def test_02_a_fresh_sign_in_before_any_answer_is_not_stopped_for_a_code(journey):
    j, page = journey, journey.page
    page.context.clear_cookies()
    page.goto(j.base + "/account/login", wait_until="domcontentloaded")
    page.fill("#email", _STATE["email"])
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)
    j.shot("one-second-sign-in")
    assert "/account/mfa" not in page.url, page.url


def test_03_after_the_first_answer_a_notice_offers_two_factor_with_the_date(journey, app):
    j, page = journey, journey.page
    signed_up = _seed_first_answer(app, _STATE["email"])
    page.goto(j.base + "/ai-chat/", wait_until="domcontentloaded")
    notice = page.get_by_test_id("two-factor-notice")
    expect(notice).to_be_visible(timeout=SHORT)
    j.shot("one-notice")
    text = notice.inner_text()
    assert "Protect your workspace" in text
    ends = (signed_up + timedelta(days=7)).strftime("%-d %B %Y")
    assert "Required from %s, or before you invite your team." % ends in text
    assert "Later (7 days left)" in text


def test_04_set_it_up_enrols_and_returns_to_the_chat(journey):
    j, page = journey, journey.page
    page.get_by_test_id("two-factor-set-up").click()
    page.wait_for_url(re.compile(r".*/account/mfa-enrol.*"))
    j.saw("Set up from the notice")
    j.shot("one-enrol")
    assert "Remind me later (7 days left)" in j.main_text()
    _STATE["secret"] = _enrol(j, r".*/ai-chat/?$")
    j.shot("one-back-in-chat")
    expect(page.get_by_test_id("two-factor-notice")).to_have_count(0)


def test_05_signing_out_and_in_now_asks_for_a_code(journey):
    j, page = journey, journey.page
    page.context.clear_cookies()
    page.goto(j.base + "/account/login", wait_until="domcontentloaded")
    page.fill("#email", _STATE["email"])
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(re.compile(r".*/account/mfa-challenge.*"), timeout=60000)
    assert "Enter your verification code" in j.main_text()
    j.shot("one-asks-for-code")


# -- goal two ----------------------------------------------------------------------------------


def test_06_inviting_someone_sends_the_owner_to_set_up_and_back_to_the_form(journey, app):
    j, page = journey, journey.page
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    _STATE["email2"] = _sign_up(j, "two")
    colleague = "colleague.%s@gmail.com" % uuid.uuid4().hex[:8]
    with app.app_context():
        other = Organization(name="Colleague Co %s" % uuid.uuid4().hex[:6], slug="colleague-%s" % uuid.uuid4().hex[:8])
        db.session.add(other)
        db.session.flush()
        existing = User(first_name="Ada", last_name="Lovelace", email=colleague, password=PASSWORD,
                        confirmed=True, organization_id=other.id)
        db.session.add(existing)
        db.session.commit()
    _STATE["colleague"] = colleague

    page.goto(j.base + "/admin/team", wait_until="domcontentloaded")
    page.fill("#email", colleague)
    j.shot("two-invite-form")
    page.get_by_role("button", name="Send invitation").click()
    page.wait_for_url(re.compile(r".*/account/mfa-enrol.*"), timeout=60000)
    j.saw("Sent to set up first")
    j.shot("two-trigger-line")
    assert "Before you invite someone, set up two-factor sign-in." in j.main_text().replace("’", "'")
    _enrol(j, r".*/admin/team/?$")
    j.shot("two-back-at-invite-form")
    assert page.locator("#email").input_value() == ""


def test_07_the_invitation_is_sent_and_listed_after_a_reload(journey):
    j, page = journey, journey.page
    page.fill("#email", _STATE["colleague"])
    page.get_by_role("button", name="Send invitation").click()
    page.wait_for_load_state("domcontentloaded")
    page.goto(j.base + "/admin/team", wait_until="domcontentloaded")
    j.shot("two-invitation-listed")
    row = page.get_by_test_id("invitation-row")
    expect(row.first).to_be_visible(timeout=SHORT)
    assert _STATE["colleague"] in row.first.inner_text()
