"""The capability roadmap's tiles and plateau timeline agree with the roadmap board.

An architect plans three plateaus and six work packages, two of them inside another
(a package broken into parts). On the capability roadmap the Work Packages tile, the
"Work packages" line under the tiles and the roadmap board's own count are the same
number, and the timeline names every plateau the board shows. Another organisation's
plateaus and packages are on neither screen.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/capability-roadmap"
BASE = "/capability-map/api/roadmap"


def _page(browser, width, theme):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": 1000}, color_scheme=theme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _frame(page, label, width, theme):
    out = os.environ.get("ROADMAP_FRAMES_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path="%s/roadmap-%s-%d-%s.png" % (out, label, width, theme), full_page=True)


def _plan(browser, live_server, email, suffix):
    """Plan three plateaus and six work packages (two are parts of another); return the plateau names."""
    context, page = _page(browser, 1440, "light")
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        names, plateau_ids = [], []
        for order, word in enumerate(("Foundation", "Consolidation", "Target"), start=1):
            name = "%s %s" % (word, suffix)
            status, made = api(page, "POST", BASE + "/plateaus", {"name": name, "sequence_order": order})
            assert status == 201, made
            names.append(name)
            plateau_ids.append(made["plateau"]["id"])
        parents = []
        for index in range(4):
            status, made = api(page, "POST", BASE + "/work-packages", {
                "name": "Package %d %s" % (index + 1, suffix), "plateau_id": plateau_ids[index % 3],
                "start_date": "2026-11-01", "target_date": "2027-01-31"})
            assert status == 201, made
            parents.append((made.get("work_package") or made)["id"])
        for index in range(2):
            status, made = api(page, "POST", "%s/work-packages/%d/children" % (BASE, parents[0]), {
                "name": "Part %d %s" % (index + 1, suffix)})
            assert status in (200, 201), made
        return names
    finally:
        context.close()


def _board(page, live_server):
    page.goto(live_server + "/genome/roadmap/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    text = page.get_by_test_id("roadmap-totals").inner_text()
    found = re.search(r"(\d+) plateaus? and (\d+) work packages? shown", text)
    assert found, text
    return page.get_by_test_id("roadmap-totals").locator("xpath=..").inner_text(), int(found.group(2))


def test_tiles_and_timeline_agree_with_the_roadmap_board(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    suffix, other_suffix = org["suffix"], other["suffix"]
    names = _plan(browser, live_server, org["emails"]["enterprise_architect"], suffix)
    _plan(browser, live_server, other["emails"]["enterprise_architect"], other_suffix)

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)
        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            board_text, board_packages = _board(page, live_server)
            assert board_packages == 6, board_packages

            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            for name in names:
                expect(page.get_by_test_id("rdm-plateau-name").filter(has_text=name)).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("rdm-plateau")).to_have_count(len(names))
            assert page.locator("[data-testid=rdm-plateau-name]", has_text=other_suffix).count() == 0

            expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text(str(board_packages))
            for name in names:
                assert name in board_text, board_text
            expect(page.get_by_test_id("rdm-plateaus-tile")).to_have_text(str(len(names)))
            line = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Work packages: %d" % board_packages in line, line
            assert "Plateaus: %d" % len(names) in line, line
            assert other_suffix not in page.content()
            _frame(page, "figures", width, theme)
        finally:
            context.close()
