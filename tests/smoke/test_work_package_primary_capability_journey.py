"""A work package generated for an application names the capability that application is the primary enabler of.

The goal, as an enterprise architect: "I generate the modernisation work for the Ledger App, which supports two
capabilities, and the timeline shows the work against the capability it is the primary enabler of". The
organisation holds linked data (two capabilities, one marked primary); a second organisation holds nothing, so the
empty timeline is walked too and must not carry the first organisation's capability.
"""
import os
import pathlib
import tempfile
import uuid
from datetime import date

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("WORK_PACKAGE_WALK_DIR") or tempfile.gettempdir()) / "work-package-walk"
PACKAGE = "Modernize Ledger App"
PRIMARY = "Zeta Payments"
OTHER = "Alpha Billing"


@pytest.fixture(scope="module")
def world():
    """One organisation with an application mapped to a primary and a non-primary capability; one empty."""
    full = create_fresh_org("enterprise_architect", org_admin=True)
    empty = create_fresh_org("enterprise_architect", org_admin=True)
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    with app.app_context():
        component = ApplicationComponent(
            name="Ledger App", application_code="LED%s" % uuid.uuid4().hex[:5].upper(),
            organization_id=full["org_id"], criticality="high")
        db.session.add(component)
        db.session.flush()
        for name, primary in ((PRIMARY, True), (OTHER, False)):
            capability = BusinessCapability(
                name=name, code="BIZ%s" % uuid.uuid4().hex[:6].upper(), level=1, category="core",
                business_domain="test", organization_id=full["org_id"], strategic_importance="high")
            db.session.add(capability)
            db.session.flush()
            db.session.add(ApplicationCapabilityMapping(
                organization_id=full["org_id"], application_component_id=component.id,
                business_capability_id=capability.id, is_primary_enabler=primary, support_level="full",
                coverage_percentage=100))
        db.session.commit()
        db.session.remove()
    return {"full": full, "empty": empty}


def _shot(page, name, width):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%d.png" % (name, width))), full_page=True)


def _generated_package_id(org_id):
    from app import create_app, db
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    with app.app_context():
        found = UnifiedWorkPackage.query.filter_by(organization_id=org_id, name=PACKAGE).one()
        db.session.remove()
        return found.id


@pytest.mark.parametrize("width", [1440, 390])
def test_generated_work_package_names_the_primary_capability(browser, live_server, world, width):
    full = world["full"]
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, full["emails"]["enterprise_architect"])
        page.goto(live_server + "/gantt-demo", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        status, reply = api(page, "POST", "/api/roadmap/sync/applications", {})
        assert status == 200, (status, reply)
        assert reply["sync_result"]["errors"] == [], reply

        # The generated package is undated, so give it a start date the way the work package screen does.
        package_id = _generated_package_id(full["org_id"])
        status, reply = api(page, "PATCH", "/enterprise/api/work-packages/%d" % package_id,
                            {"start_date": date.today().isoformat(),
                             "target_date": date.today().replace(year=date.today().year + 1).isoformat()})
        assert status == 200, (status, reply)

        # The timeline reads this feed; its row for the package carries the capability.
        page.goto(live_server + "/gantt-demo", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        status, feed = api(page, "GET", "/enterprise/api/work-packages/gantt")
        assert status == 200, (status, feed)
        shown = [w["business_capability"] for w in feed["work_packages"] if w["name"] == PACKAGE]
        assert shown == [PRIMARY], shown
        _shot(page, "timeline", width)

        # The package is in the work package list, and its link opens a page with content.
        page.goto(live_server + "/enterprise/implementation/work-packages", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        page.get_by_test_id("work-package-row").filter(has_text=PACKAGE).get_by_test_id("work-package-link").click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name=PACKAGE).first).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "work-package", width)
    finally:
        context.close()


def test_other_organisation_timeline_does_not_show_the_capability(browser, live_server, world):
    empty = world["empty"]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, empty["emails"]["enterprise_architect"])
        page.goto(live_server + "/gantt-demo", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        body = page.inner_text("body")
        assert PACKAGE not in body and PRIMARY not in body and OTHER not in body
        status, feed = api(page, "GET", "/enterprise/api/work-packages/gantt")
        assert status == 200 and feed["work_packages"] == [], (status, feed)
        _shot(page, "timeline-empty", 1440)
    finally:
        context.close()
