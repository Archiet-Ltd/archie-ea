"""An enterprise architect opens Decision tables from the sidebar, sees it marked as the current page, reads a
trail whose every crumb lands on a page of that name, and reads the table on a phone.

The decision is written through New decision and its rules through the rule editor, in an organisation of its
own; a second organisation proves the sidebar entry is there for a person with nothing yet written.
"""

import os
import re
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("DMN_WALK_DIR")
NAME = "Calibration interval by instrument class"


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _context(browser, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    return context


def _open_from_sidebar(page):
    side = page.locator('[data-testid="sidebar"]')
    layer = side.locator('[data-slot="sidebar-menu-button"][data-nav-key="layer_business"]')
    if layer.get_attribute("aria-expanded") != "true":
        layer.click()
    link = side.locator('a[data-slot="sidebar-menu-sub-button"][data-nav-key="decision_tables"]')
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    link.click()
    page.wait_for_url(re.compile(r"/architecture/decision-tables/?$"), timeout=PAGE_TIMEOUT)
    return side


def _create_with_rules(page, live_server):
    page.goto(live_server + "/architecture/decision-tables/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="New decision").click()
    page.fill("#decision-name", NAME)
    page.get_by_test_id("decision-create-submit").click()
    page.wait_for_url(re.compile(r"/architecture/decision-tables/\d+$"), timeout=PAGE_TIMEOUT)
    page.get_by_test_id("column-add").click()
    page.select_option("#column-kind", "input")
    page.fill("#column-label", "Instrument class")
    page.select_option("#column-type", "string")
    page.get_by_test_id("column-save").click()
    expect(page.get_by_test_id("dmn-input-head").filter(has_text="Instrument class")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.get_by_test_id("column-add").click()
    page.select_option("#column-kind", "output")
    page.fill("#column-label", "Months")
    page.select_option("#column-type", "number")
    page.get_by_test_id("column-save").click()
    expect(page.get_by_test_id("dmn-output-head").filter(has_text="Months")).to_be_visible(timeout=PAGE_TIMEOUT)
    for count, (cls, months) in enumerate((('"torque"', "6"), ('"pressure"', "12")), start=1):
        page.get_by_test_id("rule-add").click()
        page.fill("#rule-in-0", cls)
        page.fill("#rule-out-0", months)
        page.get_by_test_id("rule-save").click()
        expect(page.get_by_test_id("dmn-rule")).to_have_count(count, timeout=PAGE_TIMEOUT)


def _below_banner(page):
    """The trail starts below everything the page shows above the heading, and above the heading."""
    crumbs = page.locator('main nav[aria-label="Breadcrumb"]').first
    box = crumbs.bounding_box()
    heading = page.get_by_role("heading", level=1).first.bounding_box()
    banner = page.get_by_test_id("two-factor-banner")
    for i in range(banner.count()):
        if banner.nth(i).is_visible():
            assert box["y"] >= banner.nth(i).bounding_box()["y"] + banner.nth(i).bounding_box()["height"] - 1
    assert box["y"] < heading["y"]
    return crumbs


def _follow_crumbs(page, crumbs, here, minimum):
    links = crumbs.locator("a")
    seen = [(links.nth(i).inner_text().strip(), links.nth(i).get_attribute("href")) for i in range(links.count())]
    assert len(seen) >= minimum, "the trail links only %r" % seen
    # every separator is drawn: one per gap between crumbs
    assert crumbs.locator("li").count() >= 3
    assert crumbs.locator("svg, [data-lucide]").count() >= crumbs.locator("li").count() - 1
    assert "Architecture" not in crumbs.inner_text()
    for label, href in seen:
        page.goto(urljoin(page.url, href), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        heading = page.get_by_role("heading", level=1).first
        expect(heading).to_be_visible(timeout=PAGE_TIMEOUT)
        if label == "Home":
            assert len(page.locator("main").first.inner_text().strip()) > 40
        else:
            assert label.lower() in heading.inner_text().lower(), "crumb %r landed on %r" % (label, heading.inner_text())
    page.goto(here, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


def test_open_from_sidebar_read_the_trail_and_the_rules(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    page = _context(browser, 1440).new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    _create_with_rules(page, live_server)

    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    side = _open_from_sidebar(page)
    expect(page.get_by_role("heading", name="Decision tables", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
    current = side.locator('a[data-nav-key="decision_tables"][aria-current="page"]')
    expect(current).to_have_count(1)
    expect(side.locator('a[aria-current="page"]')).to_have_count(1)
    crumbs = _below_banner(page)
    _shot(page, "decision-tables-1440")
    _follow_crumbs(page, crumbs, live_server + "/architecture/decision-tables/", 1)

    # open the decision by its name; its rules are the two written
    page.get_by_role("link", name=NAME).click()
    page.wait_for_url(re.compile(r"/architecture/decision-tables/\d+$"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name=NAME, level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("dmn-rule")).to_have_count(2)
    expect(page.get_by_test_id("dmn-rule").first).to_contain_text("torque")
    expect(page.get_by_test_id("dmn-rule").nth(1)).to_contain_text("pressure")
    expect(page.locator('a[data-nav-key="decision_tables"][aria-current="page"]')).to_have_count(1)
    crumbs = _below_banner(page)
    names = [crumbs.locator("li").nth(i).inner_text().strip() for i in range(crumbs.locator("li").count())]
    assert names[-1] == NAME and "Decision tables" in names, names
    _shot(page, "decision-record-1440")
    _follow_crumbs(page, crumbs, page.url, 2)

    # a second organisation sees none of it, and says why
    page2 = _context(browser, 1440).new_page()
    sign_in(page2, live_server, other["emails"]["enterprise_architect"])
    page2.goto(live_server + "/architecture/decision-tables/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page2.get_by_text("No decision tables yet.")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page2.get_by_text(NAME)).to_have_count(0)


def test_decision_and_rules_are_readable_on_a_phone(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    page = _context(browser, 1440).new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    _create_with_rules(page, live_server)
    phone = _context(browser, 390).new_page()
    sign_in(phone, live_server, org["emails"]["enterprise_architect"])
    phone.goto(live_server + "/architecture/decision-tables/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    for head in ("Decision", "Rules"):
        cell = phone.locator("table thead th", has_text=re.compile("^\\s*%s\\s*$" % head)).first
        expect(cell).to_be_visible()
        box = cell.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390, (head, box)
    row = phone.get_by_test_id("decision-row").first
    link = row.get_by_test_id("decision-link")
    assert link.inner_text().strip() == NAME
    assert phone.evaluate("e => e.scrollWidth <= e.clientWidth", link.element_handle()), "the name is cut off"
    last = row.locator("td").nth(2) if row.locator("td:visible").count() > 2 else None
    shown = [row.locator("td:visible").nth(i).inner_text().strip() for i in range(row.locator("td:visible").count())]
    assert "2" in shown, shown
    _shot(phone, "decision-tables-390")
