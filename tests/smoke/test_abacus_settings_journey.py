"""A platform administrator sets up the Abacus integration one section at a time.

* Every field has a label a person can find it by.
* The connection and the sync schedule are separate sections, each with its own Save and a saved
  message in words; saving one leaves the other alone.
* Saved values are there after a reload and the client secret is never written back onto the page.
* An address the platform refuses is refused in words and the form keeps what was typed.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SECRET = "journey-abacus-client-secret-7741"


def _open(browser, live_server, seeded):
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["platform_admin"])
    page.goto(live_server + "/admin/abacus-settings", timeout=PAGE_TIMEOUT)
    return page


def _save_connection(page, url="https://example.com", client_id="journey-client"):
    card = page.get_by_test_id("abacus-connection")
    card.get_by_label("Base URL").fill(url)
    card.get_by_label("Client ID").fill(client_id)
    card.get_by_label("Client Secret").fill(SECRET)
    card.get_by_role("button", name=re.compile("save connection", re.I)).click()
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def test_connection_and_schedule_are_saved_separately_and_read_back(browser, live_server, seeded):
    page = _open(browser, live_server, seeded)
    try:
        _save_connection(page)
        expect(page.get_by_test_id("abacus-message")).to_contain_text(re.compile("connection saved", re.I), timeout=PAGE_TIMEOUT)

        schedule = page.get_by_test_id("abacus-schedule")
        schedule.get_by_label("Sync interval").select_option("720")
        schedule.get_by_role("button", name=re.compile("save sync schedule", re.I)).click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("abacus-message")).to_contain_text(re.compile("sync schedule saved", re.I), timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("abacus-schedule").get_by_label("Sync interval").input_value() == "720"
        connection = page.get_by_test_id("abacus-connection")
        assert connection.get_by_label("Base URL").input_value() == "https://example.com"
        assert connection.get_by_label("Client ID").input_value() == "journey-client"
        assert connection.get_by_label("Client Secret").input_value() == ""
        assert SECRET not in page.content(), "the client secret was written back onto the page"
    finally:
        page.close()


def test_a_refused_address_is_refused_in_words_and_kept(browser, live_server, seeded):
    page = _open(browser, live_server, seeded)
    try:
        _save_connection(page, url="ftp://example.com/abacus", client_id="kept-client")
        expect(page.get_by_test_id("abacus-message")).to_contain_text(re.compile("not an allowed address|not allowed|refused", re.I), timeout=PAGE_TIMEOUT)
        connection = page.get_by_test_id("abacus-connection")
        assert connection.get_by_label("Base URL").input_value() == "ftp://example.com/abacus"
        assert connection.get_by_label("Client ID").input_value() == "kept-client"
        assert SECRET not in page.content()
    finally:
        page.close()
