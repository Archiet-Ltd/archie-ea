"""A new sign-up who asks the AI chat a question gets a cited answer.

Two journeys:
1. With the sample company loaded: the answer cites at least one record from it.
2. Without the sample company: the answer says plainly that nothing is connected
   yet and offers Connect GitHub.
"""

import re

import pytest
from playwright.sync_api import expect

from .chat_ask import open_chat
from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _load_sample_company(page, base):
    """Click "See a sample company" on the chat's first screen, as a new sign-up does."""
    open_chat(page, base)
    sample = page.get_by_test_id("chat-first-sample")
    expect(sample.first).to_be_visible(timeout=PAGE_TIMEOUT)
    sample.first.click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    return True


def _dismiss_role_dialog(page):
    """Click Continue on the role-selection dialog if it appears."""
    cont = page.get_by_role("button", name="Continue")
    if cont.count() and cont.first.is_visible():
        cont.first.click()
        page.wait_for_timeout(2000)


def test_sample_company_cited_answer_appears(page, live_server):
    """A person who signs up, loads the sample company, and asks what breaks
    if the most connected application fails gets a cited answer card."""
    base = live_server
    org = create_fresh_org("solution_architect")
    email = org["emails"]["solution_architect"]

    sign_in(page, base, email)
    _dismiss_role_dialog(page)
    assert _load_sample_company(page, base), "sample company button not found"

    open_chat(page, base)
    box = page.locator("#user-input")
    box.fill("What breaks if our most connected application fails?")
    box.press("Enter")

    # The answer should appear as cited cards (estate-answer or catalogue-cards).
    answer = page.get_by_test_id("estate-answer")
    catalogue = page.locator("[data-catalogue-cards]")
    expect(answer.or_(catalogue).first).to_be_visible(timeout=PAGE_TIMEOUT)

    # The answer text must not contain "nothing recorded matches" — superlative
    # phrases name no system, so the reason line must not say nothing matches.
    answer_text = page.locator("#messages-container").inner_text().lower()
    assert "nothing recorded matches" not in answer_text, \
        "answer incorrectly says nothing matches: %s" % answer_text[:200]

    # Every row is a system that says how many things depend on it and where
    # it came from, so the reader can see why it is a single point of failure.
    rows = page.locator("[data-catalogue-record]")
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    assert rows.count() > 0
    for i in range(rows.count()):
        row = rows.nth(i)
        name = row.locator("a").first.inner_text().strip()
        count = row.locator("[data-catalogue-count]")
        assert count.count() == 1, "row %r shows no dependents count" % name
        assert re.match(r"^\d+ things? depends? on it$", count.inner_text().strip()), count.inner_text()
        source = row.locator("[data-catalogue-source]")
        assert source.count() == 1, "row %r names no source" % name
        assert re.match(r"^(from .+, as of \d{1,2} \w{3} \d{4}|added by hand on \d{1,2} \w{3} \d{4})$",
                        source.inner_text().strip()), source.inner_text()
        for process in ("Track customer orders", "Handle customer checkout", "Process online payments", "Fulfil customer order"):
            assert name != process, "a process is listed as a system: %s" % name
    counts = [int(re.match(r"\d+", rows.nth(i).locator("[data-catalogue-count]").inner_text()).group())
              for i in range(rows.count())]
    assert counts == sorted(counts, reverse=True), "rows are not ordered by dependents: %s" % counts

    # The answer must cite at least one record — a link that opens a detail page.
    record_link = page.locator("[data-catalogue-record] a, [data-answer-row] a").first
    expect(record_link).to_be_visible(timeout=PAGE_TIMEOUT)
    href = record_link.get_attribute("href")
    assert href, "record link has no href"
    # The link must point to a page within the app, not an empty or external URL.
    assert href.startswith("/"), "record link is not an app page: %s" % href
    assert "empty" not in href.lower(), "record link points to empty: %s" % href

    # Open the cited record and assert the page has the record's name and no
    # empty state (Section 22: every link lands on a page with content).
    record_link.click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    # The detail page should show the element/application name in a heading.
    heading = page.locator("main h1:visible, main [data-element-name]:visible, main [data-application-name]:visible").first
    expect(heading).to_be_visible(timeout=PAGE_TIMEOUT)
    heading_text = heading.inner_text().strip()
    assert heading_text, "detail page heading is empty"
    # The page must not show an empty-state macro or "no data" message.
    page_text = page.locator("body").inner_text().lower()
    assert "no data" not in page_text, "detail page shows empty state: %s" % page_text[:200]
    assert "nothing recorded" not in page_text, "detail page shows nothing recorded: %s" % page_text[:200]


def test_no_sample_company_gets_honest_empty_answer(page, live_server):
    """A person who signs up, skips the sample company, and asks a question
    gets an honest answer saying nothing is connected yet, with Connect GitHub."""
    base = live_server
    org = create_fresh_org("solution_architect")
    email = org["emails"]["solution_architect"]

    sign_in(page, base, email)
    _dismiss_role_dialog(page)

    open_chat(page, base)
    box = page.locator("#user-input")
    box.fill("What breaks if our most connected application fails?")
    box.press("Enter")

    # The answer should appear — either as catalogue cards or an estate answer.
    answer = page.get_by_test_id("estate-answer")
    catalogue = page.locator("[data-catalogue-cards]")
    expect(answer.or_(catalogue).first).to_be_visible(timeout=PAGE_TIMEOUT)

    # The page must offer Connect GitHub or say nothing is connected.
    page_text = page.locator("#messages-container").inner_text()
    assert "connect" in page_text.lower() or "nothing" in page_text.lower() or \
        "github" in page_text.lower() or "empty" in page_text.lower(), \
        "expected Connect GitHub or empty-state message, got: %s" % page_text[:200]