"""Journeys for the one capability store: a CTO whose mappings sit in the older tables, and Remove.

Seeded the way production holds mappings: one through a path that writes the store, one only in the frozen
coverage table, one only in the frozen legacy table. Nothing may be lost, shown twice, or survive Remove.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from sqlalchemy import text

    from app import create_app, db
    from app.models.application_layer import ApplicationComponent
    from app.models.business_capabilities import ApplicationCapabilityCoverage, BusinessCapability

    app = create_app("testing")
    with app.app_context():
        caps, comps = {}, {}
        for key, name in (("cal", "Sensor Calibration"), ("log", "Field Logistics"), ("gov", "Quality Governance")):
            bc = BusinessCapability(name=name, code="J3B-%s-%s" % (key, org_id), level=1, category="Strategic",
                                    organization_id=org_id)
            db.session.add(bc)
            db.session.flush()
            caps[key] = bc.id
        for key, name in (("ledger", "Calibration Ledger"), ("bench", "Test Bench Console"), ("route", "Route Planner")):
            comp = ApplicationComponent(name=name, lifecycle_status="active", organization_id=org_id)
            db.session.add(comp)
            db.session.flush()
            comps[key] = comp.id
        # Written by a path that reaches the store (the model layer mirrors it).
        cov = ApplicationCapabilityCoverage(application_component_id=comps["ledger"], capability_id=caps["cal"],
                                            support_level="full")
        db.session.add(cov)
        db.session.flush()
        # Held only in the frozen tables, as production holds them.
        db.session.execute(text("""INSERT INTO application_capability_coverage
            (application_component_id, capability_id, support_level, coverage_percentage, confidence_score,
             is_strategic, investment_priority) VALUES (:a, :c, 'partial', 55, 0.5, FALSE, 'medium')"""),
            {"a": comps["bench"], "c": caps["gov"]})
        db.session.execute(text("""INSERT INTO application_capability_mapping
            (organization_id, application_component_id, business_capability_id, support_level, coverage_percentage,
             relationship_type, gap_status, is_active, specialization_type)
            VALUES (:o, :a, :c, 'full', 70, 'supports', 'unknown', TRUE, 'APPLICATION')"""),
            {"o": org_id, "a": comps["route"], "c": caps["log"]})
        db.session.commit()
        out = {"org_id": org_id, "caps": caps, "apps": comps, "cov": cov.id}
        db.session.remove()
        return out


def _backfill(org_id):
    from app import create_app, db
    from app.commands import backfill_capability_mappings as bf
    from app.jobs.tenant_safe_job import platform_scope

    app = create_app("testing")
    with app.app_context(), platform_scope("test: backfill"):
        inserted = bf.apply(bf.plan(org_id))
        db.session.commit()
        db.session.remove()
        return inserted


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
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    sign_in(page, live_server, org["emails"]["cto"])
    yield page, live_server, ids, errors
    page.close()


def test_backfill_brings_older_mappings_onto_the_drawer_and_every_screen(world):
    page, base, ids, errors = world
    # Before: the mappings held only in the frozen tables are not on the drawer.
    drawer = _open_drawer(page, base, "Quality Governance")
    expect(drawer.get_by_test_id("coverage-application")).to_have_count(0)
    page.keyboard.press("Escape")
    assert _backfill(ids["org_id"]) == 2
    assert _backfill(ids["org_id"]) == 0, "a second apply must insert nothing"

    # After: each application once, with its name; one per application; the record opens; mapping works.
    drawer = _open_drawer(page, base, "Quality Governance")
    links = drawer.get_by_test_id("coverage-application")
    expect(links).to_have_count(1)
    expect(links.first).to_have_text("Test Bench Console")
    expect(drawer.get_by_test_id("coverage-count")).to_have_text("1")
    page.keyboard.press("Escape")
    drawer = _open_drawer(page, base, "Field Logistics")
    expect(drawer.get_by_test_id("coverage-application")).to_have_text(["Route Planner"])
    page.keyboard.press("Escape")

    drawer = _open_drawer(page, base, "Sensor Calibration")
    expect(drawer.get_by_test_id("coverage-application")).to_have_text(["Calibration Ledger"])
    drawer.get_by_test_id("map-application").click()
    panel = drawer.get_by_test_id("map-panel")
    expect(panel).to_be_in_viewport(timeout=PAGE_TIMEOUT)
    expect(drawer.locator("#map-application-id")).to_be_focused()
    drawer.locator("#map-application-id").select_option(label="Route Planner")
    drawer.get_by_test_id("map-save").click()
    expect(drawer.get_by_test_id("coverage-application").filter(has_text="Route Planner")).to_be_visible(
        timeout=PAGE_TIMEOUT)
    expect(drawer.get_by_test_id("coverage-count")).to_have_text("2")
    drawer.get_by_test_id("open-record").click()
    page.wait_for_url(re.compile(r"/enterprise/capabilities/%s/fact-sheet" % ids["caps"]["cal"]), timeout=PAGE_TIMEOUT)
    for name in ("Calibration Ledger", "Route Planner"):
        expect(page.locator("body")).to_contain_text(name)

    # The mapping page lists the backfilled mappings too.
    page.goto(base + "/enterprise/capability-map/mapping", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator(".cap-row", has_text="Quality Governance").get_by_test_id("mapped-application")).to_have_text(
        "Test Bench Console")
    expect(page.locator(".cap-row", has_text="Field Logistics").get_by_test_id("mapped-application")).to_have_text(
        "Route Planner")
    assert not [e for e in errors if "favicon" not in e], errors


def test_remove_removes_the_mapping_from_every_screen_and_it_stays_gone(world):
    page, base, ids, errors = world
    _backfill(ids["org_id"])
    cal = ids["caps"]["cal"]
    page.goto(base + "/capability-map/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    page.evaluate("openMappingModal(%r, 'Sensor Calibration')" % str(cal))
    page.locator('[data-cm-action="delete-mapping"]').first.click()
    page.locator('[data-modal-btn="1"]', has_text="Remove").click()
    expect(page.get_by_text("Mapping removed successfully")).to_be_visible(timeout=PAGE_TIMEOUT)

    def gone():
        drawer = _open_drawer(page, base, "Sensor Calibration")
        expect(drawer.get_by_test_id("coverage-application")).to_have_count(0)
        expect(drawer.get_by_test_id("coverage-count")).to_have_count(0)
        page.keyboard.press("Escape")
        page.goto(base + "/enterprise/capability-map/mapping", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator(".cap-row", has_text="Sensor Calibration").get_by_test_id("mapped-application")).to_have_count(0)
        page.goto(base + "/enterprise/capabilities/%s/fact-sheet" % cal, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).not_to_contain_text("Calibration Ledger")

    gone()
    page.reload()
    gone()
