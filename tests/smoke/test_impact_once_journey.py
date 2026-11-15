"""A new owner asks the chat what breaks if Payments service fails and is told about each
affected element once, with every way it was found on that one row."""

import os
import re

import pytest
from playwright.sync_api import expect

from .chat_ask import open_chat
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("IMPACT_ONCE_WALK_DIR")
AFFECTED = 11
QUESTION = "What breaks if Payments service fails?"


def _shot(answer, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        answer.screenshot(path=os.path.join(WALK_DIR, name + ".png"))


def _ask_on_sample_company(page, base):
    org = create_fresh_org("solution_architect")
    sign_in(page, base, org["emails"]["solution_architect"])
    cont = page.get_by_role("button", name="Continue")
    if cont.count() and cont.first.is_visible():
        cont.first.click()
        page.wait_for_timeout(2000)
    open_chat(page, base)
    sample = page.get_by_test_id("chat-first-sample")
    expect(sample.first).to_be_visible(timeout=PAGE_TIMEOUT)
    sample.first.click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    open_chat(page, base)
    box = page.locator("#user-input")
    box.fill(QUESTION)
    box.press("Enter")
    answer = page.get_by_test_id("estate-answer").last
    expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(answer.locator("[data-answer-row]").first).to_be_visible(timeout=PAGE_TIMEOUT)
    return answer


def _check_answer(page, answer):
    rows = answer.locator("[data-answer-row]")
    names = [rows.nth(i).locator("a").first.inner_text().strip() for i in range(rows.count())]
    assert len(names) == AFFECTED, names
    assert len(set(names)) == AFFECTED, "an element is listed twice: %s" % names

    source = rows.nth(names.index("Cache layer")).locator("xpath=following-sibling::dl[1]").locator("[data-citation]")
    text = re.sub(r"\s+", " ", source.inner_text())
    assert "Imported from Sample company" in text and "Worked out from the model" in text, text
    assert text.index("Imported from Sample company") < text.index("Worked out from the model"), text

    summary = re.sub(r"\s+", " ", answer.locator("[data-source-summary]").inner_text())
    assert re.match(r"^%d connections in your records" % AFFECTED, summary), summary
    for count in re.findall(r"(\d+) (?:other )?(?:connections?|elements?)", summary):
        assert int(count) == AFFECTED, summary
    return names


def test_what_breaks_lists_each_element_once_at_1440_light(page, live_server):
    page.set_viewport_size({"width": 1440, "height": 900})
    answer = _ask_on_sample_company(page, live_server)
    _check_answer(page, answer)
    _shot(answer, "1440-light-what-breaks")

    # The link the answer offers opens the same answer, with the same number of rows.
    href = answer.get_by_test_id("answer-open").get_attribute("href")
    page.goto(live_server + href)
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    table = page.locator("[data-answer-rows] tr[data-element-id]")
    expect(table.first).to_be_visible(timeout=PAGE_TIMEOUT)
    assert table.count() == AFFECTED, table.count()
    assert page.locator("[data-row-total]").inner_text().strip() == str(AFFECTED)
    assert page.locator("[data-answer-rows] tr", has_text=re.compile("cache layer", re.I)).count() == 1


def test_what_breaks_lists_each_element_once_at_390_dark(browser, live_server):
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        answer = _ask_on_sample_company(page, live_server)
        _check_answer(page, answer)
        _shot(answer, "390-dark-what-breaks")
    finally:
        page.close()
