"""A visitor follows the review board, renewals and applications stories to the pages they name.

Anonymous throughout: these are public pages, so no one is signed in and no role applies.
"""

import os

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]
WALK = os.environ.get("MKT03_WALK_DIR")


def _page(browser, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    return context, context.new_page()


def _main_text(page):
    return page.locator("main, #main-content").first.inner_text()


def _shot(page, name, width):
    if WALK:
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, width)), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_the_review_board_story_lands_on_the_board_module(browser, live_server, width):
    context, page = _page(browser, width)
    try:
        page.goto(live_server + "/use-cases/architecture-review-board", wait_until="domcontentloaded")
        assert "with its evidence pinned to the decision" in page.get_by_role("heading", level=1).inner_text()
        assert "Impact analysis sits beside the review" in _main_text(page)
        _shot(page, "board", width)
        page.locator("main a[href='/modules/arb']").first.click()
        page.wait_for_url("**/modules/arb")
        assert "review" in _main_text(page).lower() and len(_main_text(page)) > 500
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_the_renewals_story_sends_the_reader_to_the_chat_not_ask(browser, live_server, width):
    context, page = _page(browser, width)
    try:
        page.goto(live_server + "/use-cases/contract-renewals", wait_until="domcontentloaded")
        text = _main_text(page)
        assert "Entelim does not join the two" not in text
        assert "AI chat" in text
        _shot(page, "renewals", width)
        page.locator("main a[href='/modules/vendors']").first.click()
        page.wait_for_url("**/modules/vendors")
        assert len(_main_text(page)) > 500
        hrefs = page.eval_on_selector_all("main a", "els => els.map(e => e.getAttribute('href'))")
        assert not any("/intelligence/ask" in (h or "") for h in hrefs)
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_the_applications_story_starts_by_connecting_a_source(browser, live_server, width):
    context, page = _page(browser, width)
    try:
        page.goto(live_server + "/modules/applications", wait_until="domcontentloaded")
        text = _main_text(page)
        assert "built from your repositories or imported, not typed in" in text
        assert "Connect GitHub or import a file" in text
        _shot(page, "applications", width)
        page.locator("main a[href='/use-cases/import-archimate-model']").first.click()
        page.wait_for_url("**/use-cases/import-archimate-model")
        assert len(_main_text(page)) > 500
    finally:
        context.close()
