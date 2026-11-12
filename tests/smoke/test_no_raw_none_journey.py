"""A person reading any page in the demonstration organisation never meets the word "None" on its own.

Capability health, the capability tree, the decision form, vendors, contracts, solutions and the other pages
that once printed a raw "None" say what is missing in the product's own words. The gap board's unclosed
count is the number the Gaps page shows as open.
"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_archetype_journeys import _login

SHOTS = os.environ.get("NONE_TEXT_SHOTS")

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGES = [
    "/applications/vendors",
    "/architecture/data-entities",
    "/architecture/decisions/new",
    "/enterprise/analysis/ai-architecture",
    "/enterprise/strategic/capability-health",
    "/genome/coverage/generate",
    "/industry-apqc/",
    "/procurement/contracts",
    "/solutions/",
    "/solutions/architect/workspace",
    "/value-streams/",
    "/vendors/data",
    "/vendors/data/import",
    "/vendors/data/quality",
    "/vendors/data/reconciliation",
    "/vendors/integration",
    "/vendors/integration/patterns",
    "/vendors/selection",
    "/vendors/selection/analysis",
    "/vendors/selection/discovery",
    "/vendors/selection/requirements",
    "/vendors/technical",
    "/vendors/technical/comparison",
    "/vendors/technical/scenarios",
    "/capability-map/tree",
]

# JavaScript: every visible text node, and the visible text of every option, that holds the word on its own.
FIND_NONE = """() => {
  const hits = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const p = n.parentElement;
    if (!p || ['SCRIPT','STYLE','NOSCRIPT','TEMPLATE'].includes(p.tagName)) continue;
    if (!p.getClientRects().length && p.tagName !== 'OPTION') continue;
    const t = n.textContent;
    if (/\\bNone\\b/.test(t)) hits.push(t.replace(/\\s+/g, ' ').trim().slice(0, 120));
  }
  return hits;
}"""


@pytest.fixture(scope="module")
def world(request, ai_protocol_stub, live_server):
    return _seed_standard_org(request, ai_protocol_stub)


def _shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / (name + ".png")), full_page=True)
        if page.viewport_size["width"] == 390:
            page.evaluate("document.documentElement.classList.add('dark')")
            page.screenshot(path=str(Path(SHOTS) / (name + "-dark.png")), full_page=True)
            page.evaluate("document.documentElement.classList.remove('dark')")


def test_no_page_prints_a_raw_none(browser, live_server, world):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    found = {}
    try:
        _login(page, live_server, world["emails"]["portfolio_manager"])
        for path in PAGES:
            page.goto(live_server + path, timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("load")
            hits = page.evaluate(FIND_NONE)
            if hits:
                found[path] = hits
    finally:
        page.close()
    assert not found, "\n" + "\n".join("%s: %s" % (k, v) for k, v in found.items())


def test_the_tree_all_button_raises_no_console_error(browser, live_server, world):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _login(page, live_server, world["emails"]["portfolio_manager"])
        page.goto(live_server + "/capability-map/tree/capability", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("load")
        button = page.get_by_role("button", name="All", exact=True)
        expect(button).to_be_visible(timeout=PAGE_TIMEOUT)
        del errors[:]
        button.click()
        page.wait_for_timeout(1000)
        _shot(page, "tree-all")
    finally:
        page.close()
    assert not errors, errors


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_the_boards_unclosed_count_is_the_gaps_page_count(browser, live_server, viewport):
    from .fresh_org import sign_in
    from .test_adm_board_gap_figure_journey import _figure, _open, _org_with_gaps

    mine = _org_with_gaps(["Slow onboarding", "Manual billing", "No audit trail"], covered_name="Manual billing",
                          with_signal=False)
    theirs = _org_with_gaps(["Their only gap"], covered_name="Their only gap", with_signal=False)
    for org, expected in ((mine, 2), (theirs, 0)):
        context = browser.new_context(ignore_https_errors=True, viewport=viewport)
        page = context.new_page()
        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            _open(page, live_server, "/adm-kanban/gap-analysis", "[data-testid='gap-unclosed']")
            board = _figure(page, "gap-unclosed")
            _open(page, live_server, "/enterprise/implementation/gap-analysis", "[data-testid='gap-unclosed-count']")
            gaps_page = _figure(page, "gap-unclosed-count")
            assert board == gaps_page == expected, (board, gaps_page, expected)
            sentence = page.locator("[data-testid='gap-unclosed-count-line']").inner_text()
            assert "work package" in sentence, sentence
            _shot(page, "gaps-%d-%d" % (expected, viewport["width"]))
        finally:
            context.close()
