"""An enterprise architect reads the compliance, capability health and capability map screens in an organisation
with linked data: figures are explained, one word names one thing, and the owner column names a person."""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = "test-results/visible-lows/"
os.makedirs(WALK, exist_ok=True)


def _seed(org, noun):
    """A capability supported by an application whose every mapped control is in place, with an owner."""
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.unified_capability import UnifiedCapability
    from app.models.user import User
    from app.modules.compliance.services.applicability_service import ApplicabilityService
    from app.services.capability_ownership_service import set_capability_owner

    app = create_app("testing")
    with app.app_context():
        user = db.session.get(User, org["user_ids"]["enterprise_architect"])
        framework = RegulatoryFramework(code="T-%s" % noun, name="Standard %s" % noun, status="active")
        db.session.add(framework)
        db.session.flush()
        control = ComplianceControl(framework_id=framework.id, control_code="X.1-%s" % noun, title="Access %s" % noun)
        db.session.add(control)
        db.session.flush()
        ApplicabilityService.adopt_framework(org["org_id"], framework.id, adopted_by_id=user.id)
        capability = BusinessCapability(name="Billing %s" % noun, organization_id=org["org_id"],
                                        strategic_importance="critical")
        application = ApplicationComponent(name="Ledger %s" % noun, organization_id=org["org_id"])
        db.session.add_all([capability, application])
        db.session.flush()
        db.session.add(ApplicationCapabilityMapping(
            organization_id=org["org_id"], application_component_id=application.id,
            business_capability_id=capability.id, support_level="full", coverage_percentage=80, is_active=True))
        ApplicabilityService.link_requirement(org["org_id"], application.id, control.id, "implemented")
        for link in ApplicationComplianceControl.query.filter_by(organization_id=org["org_id"]):
            link.implementation_status = "implemented"
        db.session.commit()
        unified = UnifiedCapability.query.filter_by(organization_id=org["org_id"], name=capability.name).one()
        set_capability_owner(capability_id=unified.id, user_id=user.id, organization_id=org["org_id"])
        name = "%s %s" % (user.first_name, user.last_name)
        db.session.remove()
    return name


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_zero_tiles_one_word_one_meaning_and_a_named_owner(live_server, browser, width, scheme):
    noun = uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    owner = _seed(org, noun)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000},
                                  color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    if scheme == "dark":
        context.add_init_script("localStorage.setItem('theme', 'dark');"
                                "document.documentElement.classList.add('dark');")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        page.goto(live_server + "/strategic/compliance-tracking", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        missing = page.locator('[data-slot="card"]').filter(has_text="Missing Controls").first
        expect(missing).to_contain_text("No control is missing")
        value = missing.locator('[data-slot="card-title"]')
        assert "text-destructive" not in (value.get_attribute("class") or "")
        expect(page.get_by_text("No capability is below the compliance threshold")).to_be_visible()
        expect(page.get_by_text("Billing %s" % noun).first).to_be_visible()
        page.screenshot(path=WALK + "tracking-%d-%s.png" % (width, scheme), full_page=True)

        page.goto(live_server + "/strategic/capability-health", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("High-importance Capabilities")).to_be_visible()
        expect(page.get_by_text("At risk", exact=True).first).to_be_visible()
        assert "Critical" not in page.locator("main").inner_text()
        page.screenshot(path=WALK + "health-%d-%s.png" % (width, scheme), full_page=True)

        page.goto(live_server + "/capability-map/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="List").click()
        row = page.get_by_test_id("lens-owner").first
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(row).to_have_text(owner)
        page.screenshot(path=WALK + "capability-map-%d-%s.png" % (width, scheme), full_page=True)
    finally:
        context.close()
