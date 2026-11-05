"""Journeys for the capability map's edit form, Assess and Map-an-application controls.

A CTO edits a capability and saves once; assesses a capability from the drawer; maps an application from the drawer
and finds it on the mapping page and on the application's own page. Seeded the way the demonstration company is: the
category is stored capitalised, and the mappings sit in the unified mapping table the map reads.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.models.application_layer import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        out = {}
        for key, name in (("cal", "Sensor Calibration"), ("log", "Field Logistics"), ("gov", "Quality Governance")):
            bc = BusinessCapability(name=name, code="J3-%s-%s" % (key, org_id), level=1, category="Strategic",
                                    organization_id=org_id)
            db.session.add(bc)
            db.session.flush()
            out[key] = bc.id
        comps = {}
        for key, name in (("ledger", "Calibration Ledger"), ("bench", "Test Bench Console"), ("route", "Route Planner")):
            comp = ApplicationComponent(name=name, lifecycle_status="active", organization_id=org_id)
            db.session.add(comp)
            db.session.flush()
            comps[key] = comp.id
        unified = UnifiedCapability.query.filter_by(source_table="business_capability", source_id=str(out["cal"])).one()
        for key in ("ledger", "bench"):
            db.session.add(UnifiedApplicationCapabilityMapping(
                application_component_id=comps[key], unified_capability_id=unified.id, support_level="full"))
        db.session.commit()
        out["apps"] = comps
        db.session.remove()
        return out


def _open_drawer(page, base, name):
    page.goto(base + "/capability-map/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    page.locator('[role="tabpanel"][aria-label="Map view"] [role="button"]', has_text=name).first.click()
    drawer = page.locator('#drawer-capability-detail [role="dialog"]')
    expect(drawer).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(drawer.get_by_role("heading", name=name)).to_be_visible()
    return drawer


@pytest.fixture
def world(browser, live_server):
    org = create_fresh_org("cto", org_admin=True)
    ids = _seed(org["org_id"])
    page = browser.new_page()
    sign_in(page, live_server, org["emails"]["cto"])
    yield page, live_server, ids
    page.close()


def test_edit_a_capability_saves_on_the_first_save(world):
    """The goal: a CTO raises a capability's maturity and sees it after a reload, with one save."""
    page, base, ids = world
    page.goto(base + "/enterprise/capability-map/capabilities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Edit Sensor Calibration").click()
    # The Category box opens on the capability's category, not blank.
    expect(page.locator("#cap-category")).to_have_value("strategic")
    page.select_option("#cap-maturity", "2")
    page.locator("#cap-save").click()
    page.wait_for_url(re.compile(r"capabilities"), timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    # The change persisted: reopen the form after the reload.
    page.get_by_role("button", name="Edit Sensor Calibration").click()
    expect(page.locator("#cap-maturity")).to_have_value("2")
    expect(page.locator("#cap-category")).to_have_value("strategic")


def test_assess_from_the_drawer_shows_on_the_drawer_map_and_record(world):
    page, base, ids = world
    drawer = _open_drawer(page, base, "Field Logistics")
    drawer.get_by_test_id("assess-capability").click()
    drawer.locator("#assess-level").select_option("3")
    drawer.get_by_test_id("assess-save").click()
    # The drawer reopens on the fresh capability and shows the level.
    expect(drawer.get_by_text("L3", exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(drawer.get_by_test_id("assess-capability")).to_have_count(0)
    # The heat map tile carries the level.
    page.keyboard.press("Escape")
    tile = page.locator('[role="tabpanel"][aria-label="Map view"] [role="button"]', has_text="Field Logistics").first
    expect(tile).to_contain_text(re.compile(r"Current\s*L3"))
    # The record shows the same level.
    page.goto(base + "/enterprise/capabilities/%s/fact-sheet" % ids["log"], wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    expect(page.locator("body")).to_contain_text(re.compile(r"Maturity\s*3\s*/"))


def test_map_an_application_from_the_drawer(world):
    page, base, ids = world
    drawer = _open_drawer(page, base, "Quality Governance")
    drawer.get_by_test_id("map-application").click()
    # Only applications not already mapped are offered, and the foreign/other ones are never listed.
    drawer.locator("#map-application-id").select_option(label="Route Planner")
    drawer.get_by_test_id("map-save").click()
    link = drawer.get_by_test_id("coverage-application").filter(has_text="Route Planner")
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(drawer.get_by_test_id("coverage-count")).to_have_text("1")
    # The mapping page shows it.
    page.goto(base + "/enterprise/capability-map/mapping", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator(".cap-row", has_text="Quality Governance")
    expect(row.get_by_test_id("mapped-application")).to_have_text("Route Planner")
    # The link lands on the application's own page, which lists the capability.
    href = row.get_by_test_id("mapped-application").get_attribute("href")
    page.goto(base + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("body")).to_contain_text("Route Planner")


def test_drawer_names_the_applications_and_opens_the_capability_record(world):
    page, base, ids = world
    drawer = _open_drawer(page, base, "Sensor Calibration")
    links = drawer.get_by_test_id("coverage-application")
    expect(links).to_have_count(2)
    names = sorted(links.all_text_contents())
    assert names == ["Calibration Ledger", "Test Bench Console"]
    for i in range(2):
        href = links.nth(i).get_attribute("href")
        assert href
        reply = page.request.get(base + href)
        assert reply.status == 200 and names[i] in reply.text(), (href, reply.status)
    drawer.get_by_test_id("open-record").click()
    page.wait_for_url(re.compile(r"/enterprise/capabilities/%s/fact-sheet" % ids["cal"]), timeout=PAGE_TIMEOUT)
    expect(page.locator("body")).to_contain_text("Sensor Calibration")
    expect(page.locator("body")).to_contain_text("Calibration Ledger")
    expect(page.locator("body")).to_contain_text("Test Bench Console")
