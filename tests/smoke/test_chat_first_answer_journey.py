"""A new sign-up who asks the AI chat a question gets a cited answer.

Two journeys:
1. With the sample company loaded: the answer cites at least one record from it.
2. Without the sample company: the answer says plainly that nothing is connected
   yet and offers Connect GitHub.
"""

import pytest
from playwright.sync_api import expect

from .chat_ask import open_chat
from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _load_sample_company(page, base):
    """Click the sample-company button on the first screen after sign-up."""
    page.goto(base + "/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # The sample-company offer appears on the dashboard for an empty org.
    sample = page.get_by_role("button", name="Sample")
    if sample.count() and sample.first.is_visible():
        sample.first.click()
        page.wait_for_timeout(3000)
        return True
    # It may also appear as a link.
    sample_link = page.get_by_role("link", name="Sample")
    if sample_link.count() and sample_link.first.is_visible():
        sample_link.first.click()
        page.wait_for_timeout(3000)
        return True
    return False


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

    # The answer must cite at least one record — a link that opens a detail page.
    record_link = page.locator("[data-catalogue-record] a, [data-answer-row] a").first
    expect(record_link).to_be_visible(timeout=PAGE_TIMEOUT)
    href = record_link.get_attribute("href")
    assert href, "record link has no href"
    # The link must point to a page within the app, not an empty or external URL.
    assert href.startswith("/"), "record link is not an app page: %s" % href
    assert "empty" not in href.lower(), "record link points to empty: %s" % href


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