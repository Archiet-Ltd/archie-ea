"""A work package generated for an application names the capability that application is the primary enabler of.

The goal, as an enterprise architect: "I generate the modernisation work for the Ledger App, which supports two
capabilities, open it from the work package list, and its page names the capability it is the primary enabler
of". The organisation holds linked data (two capabilities, one marked primary); a second organisation holds
nothing, so its list is walked too and must carry none of the first organisation's work.
"""
import os
import pathlib
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

LIST = "/enterprise/implementation/work-packages"

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


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_generated_work_package_names_the_primary_capability(browser, live_server, world, width, scheme):
    full = world["full"]
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, full["emails"]["enterprise_architect"])
        page.goto(live_server + LIST, timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        status, reply = api(page, "POST", "/api/roadmap/sync/applications", {})
        assert status == 200, (status, reply)
        assert reply["sync_result"]["errors"] == [], reply

        # The package is in the work package list, and its link opens its page.
        page.goto(live_server + LIST, timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        page.get_by_test_id("work-package-row").filter(has_text=PACKAGE).get_by_test_id("work-package-link").click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name=PACKAGE).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # The page names the capability the application is the primary enabler of, as a link that lands on content.
        shown = page.get_by_test_id("wp-capability")
        expect(shown).to_have_text(PRIMARY, timeout=PAGE_TIMEOUT)
        assert OTHER not in page.inner_text("body")
        assert "No capability linked" not in page.inner_text("body")
        _shot(page, "work-package-%s" % scheme, width)
        shown.get_by_role("link", name=PRIMARY).click()
        page.wait_for_load_state("networkidle")
        assert "/work-packages/" not in page.url.split("#")[0]
        assert PRIMARY in page.inner_text("body")
    finally:
        context.close()


def test_other_organisation_sees_none_of_the_work(browser, live_server, world):
    empty = world["empty"]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, empty["emails"]["enterprise_architect"])
        page.goto(live_server + LIST, timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        body = page.inner_text("body")
        assert PACKAGE not in body and PRIMARY not in body and OTHER not in body
        package_id = _generated_package_id(world["full"]["org_id"])
        response = page.goto(live_server + "%s/%d" % (LIST, package_id), timeout=PAGE_TIMEOUT)
        assert response.status == 404, response.status
        _shot(page, "work-packages-empty", 1440)
    finally:
        context.close()
