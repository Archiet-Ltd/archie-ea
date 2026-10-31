"""A platform administrator changes a governance gate, saves it, reloads and sees it saved.

* Every field has a label a person can find it by.
* Each gate is its own section with its own Save; saving one leaves the others alone.
* A saved gate says so in words and is still there, with the new value, after a reload.
* A wrong value is refused in words and the form keeps what was typed.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _open(browser, live_server, seeded):
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["platform_admin"])
    page.goto(live_server + "/admin/governance-gates", timeout=PAGE_TIMEOUT)
    return page


def _add(page, name, completeness="70", decisions="1"):
    add = page.get_by_test_id("gate-add")
    add.get_by_label("Gate name").fill(name)
    add.get_by_label("Description").fill("Journey gate " + name)
    add.get_by_label("Required sections").fill("vision_motivation\nbusiness_process_view")
    add.get_by_label("Minimum completeness (%)").fill(completeness)
    add.get_by_label("Required approved decisions").fill(decisions)
    add.get_by_role("button", name=re.compile("add gate", re.I)).click()
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def test_a_changed_gate_is_saved_and_read_back_after_a_reload(browser, live_server, seeded):
    page = _open(browser, live_server, seeded)
    try:
        name = "journey_" + uuid.uuid4().hex[:6]
        other = "other_" + uuid.uuid4().hex[:6]
        _add(page, name)
        _add(page, other, completeness="55")
        expect(page.get_by_test_id("gate-message")).to_contain_text(re.compile("added|created", re.I), timeout=PAGE_TIMEOUT)

        card = page.get_by_test_id("gate-" + name)
        card.get_by_label("Minimum completeness (%)").fill("85")
        card.get_by_role("button", name=re.compile("save", re.I)).click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("gate-message")).to_contain_text(re.compile("saved", re.I), timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("gate-" + name)
        assert card.get_by_label("Minimum completeness (%)").input_value() == "85"
        assert card.get_by_label("Required sections").input_value() == "vision_motivation\nbusiness_process_view"
        assert page.get_by_test_id("gate-" + other).get_by_label("Minimum completeness (%)").input_value() == "55"
    finally:
        page.close()


def test_a_wrong_value_is_refused_in_words_and_kept(browser, live_server, seeded):
    page = _open(browser, live_server, seeded)
    try:
        name = "bad_" + uuid.uuid4().hex[:6]
        _add(page, name)
        card = page.get_by_test_id("gate-" + name)
        card.get_by_label("Minimum completeness (%)").fill("150")
        card.get_by_label("Description").fill("typed and kept")
        card.get_by_role("button", name=re.compile("save", re.I)).click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("gate-message")).to_contain_text(re.compile("between 0 and 100", re.I), timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("gate-" + name)
        assert card.get_by_label("Minimum completeness (%)").input_value() == "150"
        assert card.get_by_label("Description").input_value() == "typed and kept"
        # Open the page again (a reload would send the refused form a second time).
        page.goto(live_server + "/admin/governance-gates", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("gate-" + name).get_by_label("Minimum completeness (%)").input_value() == "70"
    finally:
        page.close()
