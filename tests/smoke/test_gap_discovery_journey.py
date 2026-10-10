"""Gap discovery, saved to the one gap register, refusing a duplicate on a
repeat run -- walked as the enterprise architect would use it, from
the "Run gap discovery" control on the Implementation Planning dashboard.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _run_discovery(page):
    """Click the real "Run gap discovery" control, answer the confirm modal,
    and return the endpoint's parsed JSON response."""
    with page.expect_response(
        lambda r: "/implementation/gaps/discover" in r.url and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as response_info:
        page.get_by_role("button", name="Run gap discovery").click()
        confirm_dialog = page.locator('[id^="modal-confirm-"]:visible').first
        expect(confirm_dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        confirm_dialog.get_by_role("button", name=re.compile("^Confirm$")).dispatch_event("click")
    return response_info.value.json()


@pytest.mark.smoke
def test_gap_discovery_saves_and_refuses_duplicates_on_reload(browser, live_server, seeded):
    email = seeded["emails"]["enterprise_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())  # Platform.modal.confirm may fall back to native
    _login(page, live_server, email)

    page.goto(live_server + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("button", name="Run gap discovery")).to_be_visible(timeout=PAGE_TIMEOUT)

    first = _run_discovery(page)
    assert first["success"] is True
    assert first["found"] == first["saved"] + first["duplicates"]

    # Wait for the panel to refresh from the freshly-saved data, then reload
    # the page for real -- the acceptance floor's "reload and assert
    # persisted", not just an in-page state change.
    page.wait_for_timeout(500)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Running discovery again against the same, unchanged organisation must
    # find the same gaps and refuse to save them a second time.
    second = _run_discovery(page)
    assert second["success"] is True
    assert second["saved"] == 0, (
        f"a repeat run must not create new rows for gaps already in the register: {second}"
    )
    assert second["duplicates"] == first["saved"], (
        "every gap saved on the first run must be recognised as a duplicate on the second"
    )

    # The visible list did not grow across the repeat run -- reload once more
    # and confirm the same critical gaps are still there, not duplicated.
    page.wait_for_timeout(500)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # Wait for the critical gaps list to be populated (it loads via JS)
    expect(page.locator('[data-testid="critical-gaps-list"]')).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator('[data-testid="critical-gaps-list"] li').first).to_be_visible(timeout=PAGE_TIMEOUT)
    list_items = page.locator('[data-testid="critical-gaps-list"] li')
    count_after = list_items.count()
    names_after = set(list_items.all_inner_texts())
    assert len(names_after) == count_after, "no gap name should appear more than once in the panel"
