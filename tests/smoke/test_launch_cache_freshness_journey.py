"""A business architect sees a record they just created counted at once.

The Ask page decides between "Nothing is modelled yet" and its question picker
from the workspace counts. Those counts used to be held for five minutes in
each server worker, so right after adding the first application the page still
said nothing was modelled. The live server runs two workers; the Ask page is
loaded several times before the write so that each worker has read the empty
counts, and several times after it so that every worker is asked again.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

EMPTY_HEADING = "Nothing is modelled yet"
LOADS_PER_CHECK = 6


def _new_page(browser):
    context = browser.new_context()
    # The first-run card is a separate concern; keep it from covering controls.
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    return context, context.new_page()


def _ask_says_empty(page, base):
    response = page.goto(base + "/intelligence/ask", wait_until="domcontentloaded",
                         timeout=PAGE_TIMEOUT)
    assert response.status == 200
    return page.locator("main").get_by_role("heading", name=EMPTY_HEADING).count() == 1


def test_a_new_application_is_counted_on_the_next_page_load(browser, live_server):
    first = create_fresh_org("business_architect")
    other = create_fresh_org("business_architect")
    name = "Freshness Portal %s" % uuid.uuid4().hex[:6]

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, first["emails"]["business_architect"])

        # Empty workspace: every worker answers "nothing is modelled".
        for _ in range(LOADS_PER_CHECK):
            assert _ask_says_empty(page, live_server), "a new organisation should see the empty state"

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

        # Immediately afterwards, whichever worker answers, the page has moved on.
        for attempt in range(LOADS_PER_CHECK):
            assert not _ask_says_empty(page, live_server), (
                "load %d after the create still said nothing is modelled" % (attempt + 1)
            )
            expect(page.locator("#ask-question-impact")).to_be_attached(timeout=PAGE_TIMEOUT)

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
            assert _ask_says_empty(page, live_server), (
                "another organisation's application was counted in this workspace"
            )
        assert page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT).status == 200
        expect(page.locator("tr", has_text=name)).to_have_count(0)
    finally:
        context.close()
