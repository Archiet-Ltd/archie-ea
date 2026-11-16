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


def test_adding_a_unified_gap_from_the_roadmap_form_resolves_its_id(page, live_server, orgs):
    """The roadmap's own form posts the derived id of a unified gap; it must become planned work."""
    first, _ = orgs
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        cap_id = UnifiedCapability.query.filter_by(organization_id=first["org_id"], name=NAME).first().id
        db.session.remove()
    page.set_viewport_size({"width": 1440, "height": 900})
    fresh_org.sign_in(page, live_server, first["emails"][PERSONA])
    page.goto(live_server + "/capability-roadmap", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    token = page.evaluate("document.querySelector('meta[name=csrf-token]')?.content || ''")
    reply = page.evaluate(
        """async ([id, token]) => {
            const r = await fetch('/capability-map/api/roadmap/gaps/unified-' + id + '/work-packages', {
                method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                body: JSON.stringify({name: 'Schedule the plant', start_date: '2026-12-01', target_date: '2027-03-01'})});
            return {status: r.status, body: await r.text()};
        }""", [cap_id, token])
    assert reply["status"] in (200, 201), reply
    links = _roadmap_links(page, live_server)
    expect(links.filter(has_text="Schedule the plant").first).to_be_visible(timeout=PAGE_TIMEOUT)
