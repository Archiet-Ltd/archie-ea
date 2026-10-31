"""A platform administrator sets up a webhook and learns whether it works.

* Every field on the page has a label a person can find it by.
* A saved subscription is still listed after a reload, and the signing secret
  is never written back onto the page.
* A web address the platform refuses is refused in words, and the form keeps
  what the administrator typed so it can be corrected, not retyped.
* The test control on a subscription answers in words.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SECRET = "journey-signing-secret-4417"


def test_administrator_saves_a_webhook_and_it_survives_a_reload(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/webhook-settings", timeout=PAGE_TIMEOUT)
        marker = "journey-" + uuid.uuid4().hex[:8]
        url = "https://hooks.example.com/services/" + marker

        # Every field is found by its label, as a person finds it.
        page.get_by_label("Webhook Type").select_option("slack")
        page.get_by_label("Webhook URL").fill(url)
        page.get_by_label("Description").fill("Architecture alerts " + marker)
        page.get_by_label("Signing Secret").fill(SECRET)
        # The event choices are optional; where the page offers them, one is chosen.
        events = page.get_by_role("checkbox", name="solution.approved")
        if events.count():
            events.click()
        page.get_by_role("button", name=re.compile("save (webhook )?subscription", re.I)).click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Webhook subscription created", timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        body = page.locator("body")
        expect(body).to_contain_text(marker)
        expect(body).to_contain_text("Slack")
        if events.count():
            expect(body).to_contain_text("Events: solution.approved")
        assert SECRET not in page.content(), "the signing secret was written back onto the page"
    finally:
        page.close()


def test_a_refused_address_is_refused_in_words_and_kept(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/webhook-settings", timeout=PAGE_TIMEOUT)
        bad = "ftp://hooks.example.com/hook"
        page.get_by_label("Webhook URL").fill(bad)
        page.get_by_label("Description").fill("kept description")
        page.get_by_role("button", name=re.compile("save (webhook )?subscription", re.I)).click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("refused", timeout=PAGE_TIMEOUT)
        assert page.get_by_label("Webhook URL").input_value() == bad
        assert page.get_by_label("Description").input_value() == "kept description"
    finally:
        page.close()


def test_the_test_control_answers_in_words(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/webhook-settings", timeout=PAGE_TIMEOUT)
        page.get_by_label("Webhook URL").fill("https://hooks.example.com/journey-test")
        page.get_by_role("button", name=re.compile("save (webhook )?subscription", re.I)).click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Send test payload to https://hooks.example.com/journey-test").first.click(no_wait_after=True)
        # A delivery attempt waits on the network, so the answer can take a while.
        expect(page.locator("body")).to_contain_text("Test delivery", timeout=180000)
    finally:
        page.close()
