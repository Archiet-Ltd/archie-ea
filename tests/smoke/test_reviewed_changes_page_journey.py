"""The one person in a small workspace asks the assistant for a change and finds it on Reviewed changes.

The person (an application manager) asks the assistant in the chat to move an application to
production. They open Reviewed changes from the sidebar and see their own request, applied, with
nothing on the page that waits for a person: no Approve, no Reject. Every link on the page opens a
page with content. The page is checked at 1440 and 390 wide.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_change_reviewed_and_applied_journey import (  # noqa: F401  (fixtures are used by name)
    _ask,
    _one_person_workspace,
    _shot,
    _sign_in,
    deepseek_server,
    shots,
    stub,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.parametrize("width", [1440, 390])
def test_a_one_person_workspace_sees_its_own_request_on_reviewed_changes_with_nothing_to_approve(
    browser, deepseek_server, stub, shots, width
):
    base = deepseek_server
    ws = _one_person_workspace()
    _, atlas_name = ws["atlas"]
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        _sign_in(page, base, ws["email"])
        _ask(page, base, stub, {
            "application_name": atlas_name, "new_status": "production",
            "rationale": "It is live for customers now.",
        }, "Move %s to production, it is live for customers now." % atlas_name)

        # The sidebar item is called Reviewed changes and opens the page.
        page.goto(base + "/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if width < 768:
            page.get_by_test_id("sidebar-trigger").first.click()
        page.get_by_role("link", name=re.compile(r"^Reviewed changes")).first.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Reviewed changes").first).to_be_visible(timeout=PAGE_TIMEOUT)

        # The person's own request is listed as applied.
        main = page.locator("main")
        expect(main).to_contain_text(atlas_name)
        decided = page.get_by_test_id("approval-decided")
        expect(decided).to_contain_text("Applied")
        _shot(page, shots, "reviewed-changes")

        # Nothing here waits for a person.
        assert page.get_by_role("button", name=re.compile(r"Approve|Reject")).count() == 0
        assert page.locator("main summary", has_text=re.compile("Reject")).count() == 0
        assert re.search(r"/approvals/\d+/(approve|reject)", page.content()) is None

        # Every link in the content lands on a page with content.
        hrefs = sorted({
            h for h in main.locator("a[href]").evaluate_all("els => els.map(e => e.getAttribute('href'))")
            if h and not h.startswith(("#", "javascript:", "mailto:"))
        })
        assert hrefs, "the page offers links to follow"
        for href in hrefs:
            response = page.request.get(base + href if href.startswith("/") else href)
            assert response.status == 200, (href, response.status)
            assert len(response.text().strip()) > 500, href
        record_link = main.get_by_text("See how it was checked").first
        record_link.click()
        expect(page.get_by_test_id("review-record")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
