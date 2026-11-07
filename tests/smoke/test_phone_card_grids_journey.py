"""Journey, in a browser, on a phone: a person reading the governance figures, the ArchiMate repository, the
diagram import preview and the capability roadmap sees each figure card whole, one to a row.

These pages laid their figure cards out two (or three, or six) to a row at every width, so on a 390 or 360 wide
screen a card was 90 to 170 px wide and its label ran past the border. Each grid now starts at one column on a
phone and widens from 640 px; at 1440 the grids keep their columns.
"""

import json
import os
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from . import fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROLE = "enterprise_architect"
MIN_CARD = 260

# What a person reads as a card, per page: the direct children of the grid marked in the template.
CARDS = "[data-testid=figure-cards] > *:not(template)"

DIAGRAM = {
    "title": "Phone card grid landscape",
    "pages": [{"id": "p1", "title": "P1", "items": {
        "shapes": [
            {"id": "a%d" % i, "class": "ArchiMate3ComponentBoxBlock",
             "textAreas": [{"label": "Text", "text": name}],
             "boundingBox": {"x": i * 200, "y": 0, "w": 160, "h": 60}}
            for i, name in enumerate(("Business Central", "MuleSoft", "Data warehouse"))],
        "lines": [{"id": "l1", "endpoint1": {"connectedTo": "a0", "style": "None"},
                   "endpoint2": {"connectedTo": "a1", "style": "Arrow"}}]}}],
}


def _seed(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Plateau

    app = create_app("testing")
    with app.app_context():
        for name, kind, layer in (("Customer ordering", "BusinessProcess", "Business"),
                                  ("Order service", "ApplicationComponent", "Application"),
                                  ("Order database", "Node", "Technology")):
            db.session.add(ArchiMateElement(name="%s %s" % (name, uuid.uuid4().hex[:4]), type=kind, layer=layer,
                                            organization_id=org_id))
        for i, name in enumerate(("Today", "Consolidated", "Target")):
            db.session.add(Plateau(name="%s plateau" % name, sequence_order=i + 1, organization_id=org_id))
        db.session.commit()
        db.session.remove()


@pytest.fixture
def org():
    o = fresh_org.create_fresh_org(ROLE, org_admin=True)
    _seed(o["org_id"])
    return o


def _seed_change_request(org_id, title, status="open"):
    from app import create_app, db
    from app.models.architecture_decision import ArchitectureChangeRequest

    app = create_app("testing")
    with app.app_context():
        db.session.add(ArchitectureChangeRequest(
            acr_reference="ACR-%s" % uuid.uuid4().hex[:8], title=title, description="Replaced by the order service.",
            trigger_type="business_event", status=status, organization_id=org_id))
        db.session.commit()
        db.session.remove()


def _measure(page, selector):
    return page.evaluate("""(sel) => {
      const cards = Array.from(document.querySelectorAll(sel)).filter(c => c.getBoundingClientRect().width > 0);
      return cards.map(c => {
        const r = c.getBoundingClientRect();
        const spill = Array.from(c.querySelectorAll('*')).filter(e => e.scrollWidth > e.clientWidth + 1
            && e.clientWidth > 0 && !['svg', 'path', 'i'].includes(e.tagName.toLowerCase()))
          .map(e => e.tagName + ':' + (e.textContent || '').trim().slice(0, 30));
        return {text: (c.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 40), width: Math.round(r.width),
                grid: Array.from(document.querySelectorAll('[data-testid=figure-cards]')).indexOf(c.parentElement),
                right: Math.round(r.right), spill, own: c.scrollWidth > c.clientWidth + 1};
      });
    }""", selector)


def _assert_whole(page, label, expected_cards, shot):
    cards = _measure(page, CARDS)
    assert len(cards) >= expected_cards, "%s: %d cards found, expected %d: %s" % (label, len(cards), expected_cards, cards)
    viewport = page.viewport_size["width"]
    for c in cards:
        assert c["width"] >= MIN_CARD, "%s: card '%s' is %d px wide at %d" % (label, c["text"], c["width"], viewport)
        assert c["right"] <= viewport, "%s: card '%s' runs off the screen" % (label, c["text"])
        assert not c["spill"] and not c["own"], "%s: text runs past the card '%s': %s" % (label, c["text"], c["spill"])
    # One column: within each grid every card ends at the same right edge.
    for grid in {c["grid"] for c in cards}:
        edges = {c["right"] for c in cards if c["grid"] == grid}
        assert len(edges) == 1, "%s: cards are not one column: %s" % (label, cards)
    os.makedirs("test-results/phonegrid", exist_ok=True)
    page.screenshot(path="test-results/phonegrid/%s.png" % shot, full_page=True)


def _open(page, live_server, path, ready):
    page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(ready, state="attached", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def _preview_diagram(page):
    page.set_input_files("#diagram-file", files=[{"name": "landscape.json", "mimeType": "application/json",
                                                  "buffer": json.dumps(DIAGRAM).encode()}])
    page.get_by_role("button", name="Preview").click()
    page.wait_for_selector(CARDS, state="attached", timeout=PAGE_TIMEOUT)


PAGES = [
    ("arb-dashboard", "/arb/", None, 4),
    ("arb-change-requests", "/arb/change-requests", None, 4),
    ("repository-workspace", "/architecture/", None, 4),
    ("elements-overview", "/architecture/elements", None, 3),
    ("import-diagram", "/archimate/import/diagram", _preview_diagram, 4),
    ("capability-roadmap", "/capability-roadmap", None, 6),
]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [390, 360])
@pytest.mark.parametrize("name,path,step,count", PAGES, ids=[p[0] for p in PAGES])
def test_figure_cards_are_whole_and_one_to_a_row_on_a_phone(browser, live_server, org, name, path, step, count,
                                                            width, scheme):
    page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    try:
        fresh_org.sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-testid=figure-cards], #diagram-file", state="attached", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        if step:
            step(page)
        elif name == "capability-roadmap":
            page.wait_for_selector("[data-testid=rdm-plateau]", state="attached", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(300)
        if name == "capability-roadmap":
            # Both card rows on this page: the figures and the plateaus.
            _assert_whole(page, name, 6, "%s-%d-%s" % (name, width, scheme))
        else:
            _assert_whole(page, name, count, "%s-%d-%s" % (name, width, scheme))
    finally:
        page.close()


def test_the_figure_grids_keep_their_columns_on_a_desktop(browser, live_server, org):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        fresh_org.sign_in(page, live_server, org["emails"][ROLE])
        for path, per_row in (("/arb/", 4), ("/arb/change-requests", 4), ("/architecture/", 4)):
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_selector("[data-testid=figure-cards]", state="attached", timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
            tops = {c["top"] for c in page.evaluate("""(sel) => Array.from(document.querySelectorAll(sel))
                .map(c => ({top: Math.round(c.getBoundingClientRect().top)}))""", CARDS)}
            assert len(tops) == 1, "%s: the %d cards no longer sit in one row at 1440" % (path, per_row)
    finally:
        page.close()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_a_raised_change_request_is_listed_counted_and_opens(browser, live_server, org, width, scheme):
    """An architect finds a raised change request in the list, counted as open, and opens it."""
    title = "Retire the legacy order gateway %s" % uuid.uuid4().hex[:6]
    _seed_change_request(org["org_id"], title)
    page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    try:
        fresh_org.sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/arb/change-requests", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-testid=change-request-row]", timeout=PAGE_TIMEOUT)
        row = page.locator("[data-testid=change-request-row]", has_text=title)
        assert row.count() == 1
        assert "Open" in row.inner_text()
        figures = page.locator("[data-testid=figure-cards]").inner_text()
        assert "Total" in figures and "Open" in figures
        open_card = page.locator("[data-testid=figure-cards] > *", has_text="Open").first.inner_text()
        assert "1" in open_card.split(), open_card
        assert "2" not in open_card.split() and "0" not in open_card.split(), open_card
        page.screenshot(path="test-results/phonegrid/change-requests-rows-%d-%s.png" % (width, scheme),
                        full_page=True)
        row.get_by_role("link", name="View").click()
        page.wait_for_url(lambda url: url.rstrip("/").split("/")[-1].isdigit(), timeout=PAGE_TIMEOUT)
        assert title in page.locator("body").inner_text()
    finally:
        page.close()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_a_person_narrows_the_change_requests_by_status_and_widens_again(browser, live_server, org, width, scheme):
    """An architect with requests in three statuses chooses Closed, sees only Closed, then All, and sees every one."""
    tag = uuid.uuid4().hex[:6]
    titles = {status: "Request %s %s" % (status, tag) for status in ("open", "assessing", "closed")}
    for status, title in titles.items():
        _seed_change_request(org["org_id"], title, status)
    page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    try:
        fresh_org.sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/arb/change-requests", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-testid=change-request-row]", timeout=PAGE_TIMEOUT)
        rows = page.locator("[data-testid=change-request-row]:visible")
        assert rows.count() == 3
        picker = page.get_by_test_id("change-request-status-filter")
        picker.select_option("closed")
        assert rows.count() == 1
        assert titles["closed"] in rows.first.inner_text() and "Closed" in rows.first.inner_text()
        page.screenshot(path="test-results/phonegrid/change-requests-closed-%d-%s.png" % (width, scheme),
                        full_page=True)
        picker.select_option("")
        assert rows.count() == 3
        for title in titles.values():
            assert page.locator("[data-testid=change-request-row]:visible", has_text=title).count() == 1
        picker.select_option("assessing")
        assert rows.count() == 1 and titles["assessing"] in rows.first.inner_text()
    finally:
        page.close()


def test_the_change_request_table_starts_where_the_figure_cards_start(browser, live_server, org):
    _seed_change_request(org["org_id"], "Align %s" % uuid.uuid4().hex[:6])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        fresh_org.sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/arb/change-requests", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-testid=change-request-row]", timeout=PAGE_TIMEOUT)
        cards = page.locator("[data-testid=figure-cards]").bounding_box()
        table = page.locator("[data-testid=change-requests-table-pager]").bounding_box()
        assert abs(cards["x"] - table["x"]) <= 1, (cards, table)
        assert abs((cards["x"] + cards["width"]) - (table["x"] + table["width"])) <= 1, (cards, table)
    finally:
        page.close()


TRIAGE = {"triage": {"summary": "Two reviews wait on the board.", "suggested_order": ["ARB-1", "ARB-2"], "items": [
    {"review_number": "ARB-1", "title": "Retire the order gateway", "reason": "Blocks the Q1 release.",
     "complexity": "High"},
    {"review_number": "ARB-2", "title": "Adopt the event bus", "reason": "Needs a security opinion.",
     "complexity": "Medium"}]}}


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_the_triage_list_shows_each_review_it_returns(browser, live_server, org, width, scheme):
    page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    try:
        page.route("**/arb/api/queue/ai-triage", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(TRIAGE)))
        fresh_org.sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Triage pending queue").click()
        entries = page.get_by_role("list", name="Triage by review").get_by_role("listitem")
        entries.first.wait_for(timeout=PAGE_TIMEOUT)
        assert entries.count() == 2
        for item in TRIAGE["triage"]["items"]:
            text = page.get_by_role("list", name="Triage by review").inner_text()
            assert item["review_number"] in text and item["title"] in text and item["reason"] in text
        for entry in (entries.nth(0), entries.nth(1)):
            box = entry.bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
            assert entry.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
        page.screenshot(path="test-results/phonegrid/triage-%d-%s.png" % (width, scheme), full_page=True)
    finally:
        page.close()
