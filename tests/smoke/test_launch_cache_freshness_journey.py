"""A business architect sees a record they just created answered in the chat at once.

The chat answers a question about something in the workspace from the records. Those
records used to be counted from a cache held for five minutes in each server worker, so
right after adding the first application the page still said nothing was modelled. The live
server runs two workers; the chat is asked about the application several times before the
write so that each worker has read the empty workspace, and several times after it so that
every worker is asked again.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, role_of
from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LOADS_PER_CHECK = 6


def _new_page(browser):
    context = browser.new_context()
    # The first-run card is a separate concern; keep it from covering controls.
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    return context, context.new_page()


def _chat_has_nothing_to_answer_with(page, base, name):
    """Ask the chat's records lookup from the signed-in page, as the chat does: an empty
    workspace has no record named so, and the question goes on to the model."""
    page.goto(base + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status, body = api(page, "POST", "/ai-chat/estate-answer", {"message": "What breaks if %s fails" % name})
    assert status == 200, (status, body)
    return body["found"] is False


def test_a_new_application_is_counted_on_the_next_page_load(browser, live_server):
    first = create_fresh_org("business_architect")
    other = create_fresh_org("business_architect")
    name = "Freshness Portal %s" % uuid.uuid4().hex[:6]

    context, page = _new_page(browser)
    try:
        assert role_of(first["emails"]["business_architect"]) == "business_architect"
        sign_in(page, live_server, first["emails"]["business_architect"])

        # Empty workspace: every worker finds nothing recorded under that name.
        for _ in range(LOADS_PER_CHECK):
            assert _chat_has_nothing_to_answer_with(page, live_server, name), (
                "a new organisation should have nothing to answer with")

        # Create the first application through the portfolio's own dialog.
        assert page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT).status == 200
        page.get_by_test_id("btn-add-application").first.click()
        dialog = page.locator("#modal-create")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        dialog.locator("#ca-name").fill(name)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            dialog.locator("button[type=submit]").click()
        row = page.locator("tr", has_text=name).first
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        # The type was not recorded, so it reads as a dash, not as a value.
        expect(row.locator("td", has_text="—").first).to_be_visible()

        # Immediately afterwards, whichever worker answers, the chat answers about it.
        for attempt in range(LOADS_PER_CHECK):
            answer = ask(page, live_server, "What breaks if %s fails" % name)
            expect(answer.locator("[data-answer-cards]")).to_contain_text(
                "What breaks if %s fails" % name), "load %d after the create did not answer" % (attempt + 1)
            expect(answer).to_contain_text("Nothing is recorded as connected to this yet")

        # Persisted: a reload of the portfolio still lists it.
        page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT)
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        expect(page.locator("tr", has_text=name).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    # Another organisation neither sees the application nor loses its empty state.
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, other["emails"]["business_architect"])
        for _ in range(LOADS_PER_CHECK):
            assert _chat_has_nothing_to_answer_with(page, live_server, name), (
                "another organisation's application was answered in this workspace"
            )
        assert page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT).status == 200
        expect(page.locator("tr", has_text=name)).to_have_count(0)
    finally:
        context.close()
