"""A platform administrator connects Jira for pushing the portfolio and learns whether it works.

* Every field has a label a person can find it by.
* Saved settings are still there after a reload and the API token is never written back onto the page.
* An address the platform refuses is refused in words, beside the field, and the form keeps what was typed.
* The Test Connection control answers in words.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TOKEN = "journey-jira-api-token-5521"
SAVE = re.compile("save settings", re.I)


def _open(browser, live_server, seeded):
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["platform_admin"])
    page.goto(live_server + "/admin/jira-settings", timeout=PAGE_TIMEOUT)
    return page


def _fill(page, base_url, project):
    page.get_by_label("Jira Base URL").fill(base_url)
    page.get_by_label("Username / Email").fill("journey@example.com")
    page.get_by_label("API Token").fill(TOKEN)
    page.get_by_label("Project Key").fill(project)
    page.get_by_label("Issue Type").fill("Story")
    page.get_by_label("Country Filter").fill("Ireland")


def test_saved_jira_settings_survive_a_reload(browser, live_server, seeded):
    page = _open(browser, live_server, seeded)
    try:
        project = "J" + uuid.uuid4().hex[:4].upper()
        _fill(page, "https://example.com", project)
        page.get_by_role("button", name=SAVE).click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Jira settings", timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        assert page.get_by_label("Jira Base URL").input_value() == "https://example.com"
        assert page.get_by_label("Project Key").input_value() == project
        assert page.get_by_label("Issue Type").input_value() == "Story"
        assert page.get_by_label("Country Filter").input_value() == "Ireland"
        assert page.get_by_label("API Token").input_value() == ""
        assert TOKEN not in page.content(), "the API token was written back onto the page"
        expect(page.get_by_label("API Token")).to_have_attribute("placeholder", re.compile("keep", re.I))
    finally:
        page.close()


def test_a_refused_address_is_refused_in_words_and_kept(browser, live_server, seeded):
    page = _open(browser, live_server, seeded)
    try:
        _fill(page, "ftp://example.com/jira", "KEEP")
        page.get_by_role("button", name=SAVE).click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        body = page.locator("main")
        expect(body).to_contain_text(re.compile("not an allowed address|not allowed|refused", re.I), timeout=PAGE_TIMEOUT)
        assert page.get_by_label("Jira Base URL").input_value() == "ftp://example.com/jira"
        assert page.get_by_label("Project Key").input_value() == "KEEP"
        assert TOKEN not in page.content()
    finally:
        page.close()


def test_test_connection_answers_in_words(browser, live_server, seeded):
    page = _open(browser, live_server, seeded)
    try:
        if not page.get_by_role("button", name=re.compile("test connection", re.I)).count():
            _fill(page, "https://example.com", "TEST")
            page.get_by_role("button", name=SAVE).click()
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name=re.compile("test connection", re.I)).first.click()
        expect(page.locator("body")).to_contain_text(re.compile("Connection (failed|successful)|Failed to connect|Error:", re.I), timeout=150000)
    finally:
        page.close()
