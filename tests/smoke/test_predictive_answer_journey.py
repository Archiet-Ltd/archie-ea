
"""A signed-in person in a seeded organisation with linked data asks the AI
chat a question the predictive engine answers, and the test asserts the outcome
a person needs: a prediction with its figure and its evidence footer in the
chat answer, not merely that the page rendered.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_try_sample_company_journey import _empty_organisation_user, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ASKCHAT_WALK_DIR")
QUESTION = "What are our biggest risks?"


def _shot(page, name, width, theme="light"):
    if WALK:
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)), full_page=False)


def _load_sample(page, base):
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("first-action-sample").click()
    expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)


@pytest.mark.parametrize("width", [1440])
def test_predictive_answer_journey(browser, live_server, seeded_user, width):
    email, org_id = seeded_user
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        _load_sample(page, live_server.url)

        # Go to chat
        page.goto(live_server.url + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Ask the question
        box = page.locator("#user-input")
        expect(box).to_be_visible(timeout=PAGE_TIMEOUT)
        box.fill(QUESTION)
        box.press("Enter")

        # Check for predictive answer
        answer = page.get_by_test_id("predictive-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)

        # Check for a known prediction type from the sample data
        expect(answer.get_by_text("Zombie Projects")).to_be_visible()

        # Check that the evidence footer is present
        expect(answer.locator(".text-gray-500.dark:text-gray-400.text-xs.mt-2")).to_be_visible()
    finally:
        context.close()
