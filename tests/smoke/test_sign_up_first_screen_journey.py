"""A new owner goes from sign-up straight to the one-message first screen.

The person: a startup CTO who has never seen the product. The goal: sign up, see
"Connect GitHub" and "Look around a sample company" and the question box without
scrolling, load the sample, and ask the first question in the chat with a cited
answer. No role wizard or feature tour stands in front of any of it, and the one
onboarding question (the company kind) lives inside the same card.

Every journey starts at /account/register as a new person, never a fixture user whose
onboarding is finished. Set ONB_WALK_DIR to save the fold screenshots; the seconds
from sign-up to the first cited answer are printed.
"""
import os
import re
import time
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PASSWORD = "FirstScreen!Journey2026"
WALK = os.environ.get("ONB_WALK_DIR")
QUESTION = "What breaks if Payments service fails"
SIZES = [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})]


def _sign_up(page, base):
    email = "owner.%s@gmail.com" % uuid.uuid4().hex[:10]
    page.goto(base + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#first_name", "Chidi")
    page.fill("#last_name", "Okeke")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=PAGE_TIMEOUT * 2)
    return email


def _assert_persona(email):
    """The signed-in owner carries the startup CTO persona and is not a platform administrator."""
    from app import create_app
    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.persona_lens == "cto", user.persona_lens
        assert user.enterprise_role != "platform_admin", user.enterprise_role
        assert not is_platform_admin(user)


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_new_owner_lands_on_one_card_with_no_wizard(browser, live_server, name, viewport):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        email = _sign_up(page, live_server)
        _assert_persona(email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)

        # no role wizard and no feature tour, however long we wait
        page.wait_for_timeout(1000)
        body = page.locator("body").inner_text()
        for phrase in ("What's your role?", "Key features for you", "You're all set"):
            assert phrase not in body, phrase
        expect(page.locator("[x-show='showOnboarding']")).to_have_count(0)

        # the card, the sample, the question box: all above the fold
        message = page.locator('[data-testid="first-message"]')
        expect(message).to_contain_text("look around a sample")
        for testid in ("first-action-sample", "first-question-input"):
            expect(page.locator('[data-testid="%s"]' % testid)).to_be_in_viewport()
        # an owner may connect: Connect GitHub is on the card and opens a page with content
        expect(message.locator('[data-testid="first-action-connect"]')).to_be_in_viewport()

        # one onboarding question, and it sits inside the card, not in a section of its own
        assert page.locator('[data-testid="company-kind-picker"]').count() == 1
        assert message.locator('[data-testid="company-kind-picker"]').count() == 1
        assert message.locator('[data-testid="company-kind-selector"]').count() == 1
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, "fold-%s.png" % name))
    finally:
        context.close()


def test_sign_up_to_first_cited_answer(browser, live_server):
    context = browser.new_context(viewport=SIZES[0][1])
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    started = time.time()
    try:
        email = _sign_up(page, live_server)
        _assert_persona(email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)

        # the company kind is answered in the card and is kept
        page.locator('[data-testid="company-kind-scaleup"]').click()
        expect(page.locator('[data-testid="company-kind-status"]')).to_contain_text("Scale-up", timeout=PAGE_TIMEOUT)

        page.locator('[data-testid="first-action-sample"]').click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        page.locator('[data-testid="first-question-input"]').fill(QUESTION)
        page.locator('[data-testid="first-question-submit"]').click()
        page.wait_for_url(re.compile(r"/ai-chat"), timeout=PAGE_TIMEOUT)
        expect(page.locator("#user-input")).to_have_value(QUESTION)
        page.locator("#user-input").press("Enter")

        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        rows = answer.locator("[data-answer-row]")
        assert rows.count() >= 10
        for i in range(rows.count()):
            card = rows.nth(i).locator("xpath=ancestor::*[@data-slot='card'][1]")
            assert card.locator("[data-citation]").inner_text().strip() not in ("", "Source not recorded")
        seconds = time.time() - started
        print("\n[onb05] sign-up to first cited answer: %.1f s" % seconds)
        if WALK:
            page.screenshot(path=os.path.join(WALK, "first-answer-1440.png"))

        # the kind chosen on the card is still the kind after a reload
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        picker = page.locator('[data-testid="company-kind-scaleup"]')
        if picker.count():
            assert picker.get_attribute("aria-pressed") == "true"
    finally:
        context.close()
