"""Journey: a CTO adds a unified capability to the roadmap from the capability map and finds it there.

Goal: "put this capability's gap on the roadmap". The capability map's Add to roadmap control sends the
capability's real kind, the roadmap lists the planned work under the capability's name, and a person from
another organisation never sees it.
"""
import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from . import fresh_org
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PERSONA = "cto"
NAME = "Plant scheduling"
SECOND_NAME = "Line changeover"
WALK = os.environ.get("CAPABILITY_MAP_UNIFIED_ROADMAP_WALK_DIR")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id, name):
    from app import create_app, db
    from app.models.manufacturing_capability import ManufacturingCapability
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        cap = UnifiedCapability(name=name, organization_id=org_id, level=1,
                                code="UR%s" % uuid.uuid4().hex[:6])
        db.session.add(cap)
        db.session.flush()
        db.session.add(ManufacturingCapability(unified_capability_id=cap.id, manufacturing_domain="production"))
        db.session.commit()
        db.session.remove()


@pytest.fixture
def orgs():
    first = fresh_org.create_fresh_org(PERSONA, org_admin=False)
    second = fresh_org.create_fresh_org(PERSONA, org_admin=False)
    _seed(first["org_id"], NAME)
    _seed(first["org_id"], SECOND_NAME)
    return first, second


def _add_from_map(page, live_server):
    page.goto(live_server + "/capability-map/dashboard", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.locator("#capability-lens").select_option("unified")
    row = page.locator("tr[data-cap-name]").filter(has_text=NAME).first
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    row.locator("[data-cap-actions-toggle]").click()
    row.locator("[data-action='add-roadmap']").click()
    modal = page.locator("#add-roadmap-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(modal).to_contain_text(NAME)
    modal.get_by_label("Create default work packages").check()
    with page.expect_response(lambda r: "gaps/add-from-capability" in r.url) as sent:
        modal.get_by_role("button", name="Submit roadmap addition").click()
    assert sent.value.status == 200, sent.value.text()


def _roadmap_links(page, live_server):
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    return page.get_by_test_id("roadmap-wp-link")


def test_cto_adds_a_unified_capability_to_the_roadmap_and_sees_it_there(page, live_server, orgs):
    first, second = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    fresh_org.sign_in(page, live_server, first["emails"][PERSONA])
    _add_from_map(page, live_server)
    links = _roadmap_links(page, live_server)
    expect(links.filter(has_text=NAME).first).to_be_visible(timeout=PAGE_TIMEOUT)
    _shot(page, "unified-roadmap-1440-light")
    links.filter(has_text=NAME).first.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("main")).to_contain_text(NAME)

    # A person from another organisation sees nothing of it.
    page.context.clear_cookies()
    fresh_org.sign_in(page, live_server, second["emails"][PERSONA])
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).not_to_contain_text(NAME)
    page.goto(live_server + "/capability-map/dashboard", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).not_to_contain_text(NAME)


def test_the_same_journey_works_at_phone_width_in_dark(page, live_server, orgs):
    first, _ = orgs
    page.set_viewport_size({"width": 390, "height": 844})
    fresh_org.sign_in(page, live_server, first["emails"][PERSONA])
    page.goto(live_server + "/capability-map/dashboard", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.evaluate("try { localStorage.setItem('theme', 'dark'); } catch (e) {} document.documentElement.classList.add('dark');")
    _add_from_map(page, live_server)
    links = _roadmap_links(page, live_server)
    page.evaluate("document.documentElement.classList.add('dark');")
    expect(links.filter(has_text=NAME).first).to_be_visible(timeout=PAGE_TIMEOUT)
    _shot(page, "unified-roadmap-390-dark")


def test_cto_adds_two_selected_capabilities_to_the_roadmap_in_one_step(page, live_server, orgs):
    """Select two capabilities, press Add selected to roadmap, and find each under its own name."""
    first, second = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    fresh_org.sign_in(page, live_server, first["emails"][PERSONA])
    page.goto(live_server + "/capability-map/dashboard", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.locator("#capability-lens").select_option("unified")
    for name in (NAME, SECOND_NAME):
        expect(page.locator("tr[data-cap-name]").filter(has_text=name).first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.locator("#unified-table-body").get_by_label("Select " + name).check()
    bulk = page.locator("#unified-bulk-add-btn")
    expect(bulk).to_be_enabled()
    with page.expect_response(lambda r: "gaps/add-from-capability" in r.url):
        bulk.click()
    expect(page.get_by_text("Added 2 capabilities to roadmap").first).to_be_visible(timeout=PAGE_TIMEOUT)
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    for name in (NAME, SECOND_NAME):
        expect(page.locator("main")).to_contain_text(name, timeout=PAGE_TIMEOUT)
    page.get_by_test_id("roadmap-wp-link").filter(has_text=SECOND_NAME).first.scroll_into_view_if_needed()
    _shot(page, "unified-bulk-1440-light")
    # What was just recorded is not also offered as a gap that is not recorded.
    signals = page.request.get(live_server + "/capability-map/api/roadmap/gaps?capability_type=business")
    assert NAME not in signals.text() and SECOND_NAME not in signals.text(), signals.text()[:400]

    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.documentElement.classList.add('dark');")
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.evaluate("document.documentElement.classList.add('dark');")
    expect(page.locator("main")).to_contain_text(NAME, timeout=PAGE_TIMEOUT)
    page.get_by_test_id("roadmap-wp-link").filter(has_text=SECOND_NAME).first.scroll_into_view_if_needed()
    _shot(page, "unified-bulk-390-dark")

    page.context.clear_cookies()
    fresh_org.sign_in(page, live_server, second["emails"][PERSONA])
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).not_to_contain_text(NAME)


def test_adding_planned_work_with_the_roadmap_form_links_it_to_the_unified_gap(page, live_server):
    """An architect fills the roadmap's Add Work Package form, links the capability's gap, and sees the work."""
    architect = "enterprise_architect"
    first = fresh_org.create_fresh_org(architect, org_admin=False)
    _seed(first["org_id"], NAME)
    page.set_viewport_size({"width": 1440, "height": 900})
    fresh_org.sign_in(page, live_server, first["emails"][architect])
    _add_from_map(page, live_server)
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Add Work Package").click()
    page.locator("#rdm-wp-name").wait_for(state="visible", timeout=PAGE_TIMEOUT)
    modal = page.locator("#rdm-create-wp-modal")
    page.fill("#rdm-wp-name", "Schedule the plant")
    page.fill("#rdm-wp-start", "2026-12-01")
    page.fill("#rdm-wp-target", "2027-03-01")
    page.get_by_test_id("wp-modal-gap-input").fill(NAME)
    modal.get_by_role("button", name=NAME).first.click()
    with page.expect_response(lambda r: "/work-packages" in r.url and r.request.method == "POST") as sent:
        modal.get_by_role("button", name="Create Work Package").click()
    assert sent.value.status in (200, 201), sent.value.text()
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    expect(_roadmap_links(page, live_server).filter(has_text="Schedule the plant").first).to_be_visible(timeout=PAGE_TIMEOUT)
