"""A review board member finds a decision by search, opens the review queue from the same place, and records an
outcome that removes the decision from the queue. A second organisation whose decisions carry no review date sees
the queue explain why it is empty and link to a decision's edit page.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("DECISIONS_WALK_DIR")
LIST = "/architecture/decisions/"
TITLES = [
    ("AD-2-001", "Calibration service runs in the shared cluster", "accepted"),
    ("AD-2-002", "Calibration data is kept for seven years", "proposed"),
    ("AD-2-003", "Standardise on one message broker", "accepted"),
    ("AD-2-004", "Retire the legacy file transfer", "deprecated"),
    ("AD-2-005", "Single sign-on for supplier portals", "superseded"),
    ("AD-2-006", "Use managed databases by default", "proposed"),
]


def _seed(org_id, tag):
    from app import create_app, db
    from app.models.architecture_decision import ArchitectureDecision

    app = create_app("testing")
    with app.app_context():
        for ref, title, status in TITLES:
            db.session.add(ArchitectureDecision(
                decision_id=ref.replace("AD-2", "AD-" + tag), title=title, status=status, organization_id=org_id,
            ))
        db.session.commit()
        db.session.remove()


def _text(page):
    return re.sub(r"\s+", " ", page.locator("body").inner_text())


def _shot(page, name, width):
    if not WALK:
        return
    os.makedirs(WALK, exist_ok=True)
    for theme in ("light", "dark"):
        page.evaluate("t => localStorage.setItem('theme', t)", theme)
        page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.screenshot(path=os.path.join(WALK, "%s-%d-%s.png" % (name, width, theme)), full_page=True)
    page.evaluate("() => localStorage.setItem('theme', 'light')")
    page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)


def _no_jargon(page):
    text = _text(page)
    for word in ("ADM", "ARB", "ArchiMate"):
        assert not re.search(r"\b%s\b" % word, text), "%s appears on %s" % (word, page.url)


def _current_entry(page):
    return page.locator('[data-sidebar="menu-button"][data-active="true"], [aria-current="page"][data-sidebar]')


def test_find_open_review_and_record_outcome(browser, live_server):
    org = create_fresh_org("arb_member")
    other = create_fresh_org("arb_member")
    _seed(org["org_id"], org["suffix"])
    _seed(other["org_id"], other["suffix"])
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["arb_member"])
    REF = "AD-%s-001" % org["suffix"]

    page.goto(live_server + LIST, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Decisions", exact=True)).to_be_visible()
    expect(page.locator('nav[aria-label="Breadcrumb"]')).to_contain_text("Decisions")
    assert "Decisions" in _current_entry(page).all_inner_texts()[0] or page.get_by_test_id("decision-views").count()
    page.get_by_role("link", name="Decisions").first.wait_for()
    # Only this organisation's decisions are listed.
    assert page.get_by_test_id("decision-row").count() == 6
    assert "AD-%s-001" % other["suffix"] not in _text(page)
    page.get_by_placeholder("Search decisions...").fill("Calibration")
    page.wait_for_function("() => [...document.querySelectorAll('[data-testid=decision-row]')].filter(r => !r.hidden).length === 2")
    expect(page.get_by_test_id("decisions-table-pager")).to_contain_text("Rows per page")
    _no_jargon(page)
    _shot(page, "decisions-list", 1440)

    page.get_by_placeholder("Search decisions...").fill("")
    page.get_by_role("link", name=REF).click()
    page.wait_for_load_state("networkidle")
    assert REF in page.title()
    expect(page.locator("body")).to_contain_text("Calibration service runs in the shared cluster")

    # Give the first decision a past review date through its edit form.
    page.goto(live_server + LIST, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    href = page.get_by_role("link", name=REF).get_attribute("href")
    page.goto(live_server + href + "/edit", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.fill("#review_date", "2026-11-10")
    page.get_by_role("button", name="Save Changes").click()
    page.wait_for_load_state("networkidle")

    # Open the queue from the list.
    page.goto(live_server + LIST, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    list_badge = page.locator('[data-testid=decision-row]', has_text=REF).get_by_test_id("decision-status").inner_text()
    page.get_by_test_id("decision-views").get_by_role("link", name="Due for review").click()
    page.wait_for_url(re.compile(r"due-for-review"))
    expect(page.locator('nav[aria-label="Breadcrumb"]')).to_contain_text("Decisions")
    expect(page.locator('nav[aria-label="Breadcrumb"]')).to_contain_text("Due for review")
    assert "Governance" not in page.locator('nav[aria-label="Breadcrumb"]').inner_text()
    assert _current_entry(page).count() >= 1, "the sidebar marks no entry on the queue"
    assert "Decisions" in " ".join(_current_entry(page).all_inner_texts())
    row = page.locator('[data-testid=due-row]', has_text=REF)
    expect(row).to_have_count(1)
    assert row.get_by_test_id("decision-status").inner_text() == list_badge
    assert "10 Nov 2026" in row.inner_text()
    _no_jargon(page)
    _shot(page, "decisions-queue", 1440)

    # 390 wide: the title and the row menu are on screen without sideways scrolling.
    phone = browser.new_context(viewport={"width": 390, "height": 844})
    phone.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    small = phone.new_page()
    sign_in(small, live_server, org["emails"]["arb_member"])
    small.goto(live_server + LIST, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert small.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    small.locator("[data-testid=decision-row] a", has_text="Calibration service").first.scroll_into_view_if_needed()
    _shot(small, "decisions-list", 390)
    small.goto(live_server + LIST + "due-for-review", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(small.locator("[data-testid=due-row] a", has_text="Calibration service")).to_be_in_viewport()
    expect(small.get_by_role("button", name="Actions for " + REF)).to_be_in_viewport()
    _shot(small, "decisions-queue", 390)
    assert small.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")

    row.get_by_role("button", name="Actions for " + REF).click()
    page.get_by_test_id("record-outcome").click()
    page.wait_for_url(re.compile(r"record-outcome"))
    page.fill("#review_outcome", "Still right; no change needed.")
    page.get_by_role("button", name="Record outcome").click() if page.get_by_role("button", name="Record outcome").count() else page.locator("form button[type=submit]").last.click()
    page.wait_for_load_state("networkidle")
    page.goto(live_server + LIST + "due-for-review", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert page.locator('[data-testid=due-row]', has_text=REF).count() == 0
    expect(page.locator("body")).to_contain_text("No decision has a review date")


def test_queue_with_no_review_dates_says_so_and_links_to_an_edit_page(browser, live_server):
    org = create_fresh_org("arb_member")
    _seed(org["org_id"], org["suffix"])
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["arb_member"])
    page.goto(live_server + LIST + "due-for-review", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("No decision has a review date")).to_be_visible()
    link = page.get_by_test_id("set-review-date")
    expect(link).to_be_visible()
    link.click()
    page.wait_for_url(re.compile(r"/edit$"))
    expect(page.locator("#review_date")).to_be_visible()
    expect(page.locator("#title, input[name=title]").first).to_have_value(re.compile(r".+"))
    _shot(page, "decisions-queue-undated", 1440)
