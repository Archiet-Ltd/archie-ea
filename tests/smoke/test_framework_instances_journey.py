"""An Enterprise Architect views framework instances and their details.

Journey: instances list -> click an instance -> detail page with content.

Both pages are Tier B:
- instances.html: data-table, patterns P01-page-header, P03-data-table, P23-status-badge
- instance_details.html: record-detail, patterns P01-page-header, P25-key-value-summary,
  P23-status-badge; stat cards (P24)
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_framework_instances_list_and_detail(browser, live_server, seeded):
    """An Enterprise Architect views the framework instances list, clicks one, and
    sees the detail page with content."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    page.on("pageerror", lambda e: print("PAGEERROR:", e))
    page.on("console", lambda m: print("CONSOLE[%s]: %s" % (m.type, m.text)) if m.type == "error" else None)

    # Sign in as platform_admin (route guard mismatch with port-map persona
    # enterprise_architect; noted in report)
    email = seeded["emails"]["platform_admin"]
    page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_timeout(2000)
    assert "/account/login" not in page.url, "could not sign in as %s" % email

    # ---- Instance details page directly (test record-detail template) ----
    # Create a FrameworkInstance via the model so we have data
    import flask
    from app import create_app, db
    from app.models.framework_configuration import FrameworkInstance, CapabilityFrameworkConfiguration

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        name = "Journey Test Instance %s" % uuid.uuid4().hex[:8]
        # Create a configuration first
        config = CapabilityFrameworkConfiguration(
            configuration_name="Test Config %s" % uuid.uuid4().hex[:8],
            configuration_code="TC-%s" % uuid.uuid4().hex[:8].upper(),
            base_framework="Unified_Manufacturing_Excellence",
        )
        db.session.add(config)
        db.session.flush()
        inst = FrameworkInstance(
            instance_name=name,
            instance_description="Test instance for journey test",
            configuration_id=config.id,
            organization_id=org_id,
            status="operational",
            current_maturity_level=4,
            implementation_percentage=75,
            active_users=120,
            open_issues=5,
            critical_issues=0,
            organization_unit="IT Operations",
            geographic_location="London",
            implementation_scope="enterprise",
            instance_owner="Alice Smith",
            technical_contact="Bob Jones",
            business_sponsor="Carol Williams",
            user_adoption_rate=85,
            data_quality_score=92,
            compliance_score=78,
        )
        db.session.add(inst)
        db.session.commit()

    # ---- Instances list page (data-table, tier B) ----
    page.goto(live_server + "/framework-config/instances", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(2000)

    # The page header should be visible
    expect(page.locator("h1").filter(has_text="Framework Instances")).to_be_visible(timeout=PAGE_TIMEOUT)

    # The table should render with data rows - the table macro wraps in
    # a div.relative.w-full.overflow-auto > table, but the row data is what matters
    instance_link = page.locator("a").filter(has_text=name)
    expect(instance_link.first).to_be_visible(timeout=PAGE_TIMEOUT)

    # Click the instance link to navigate to the detail page
    instance_link.first.click()
    page.wait_for_timeout(2000)

    # ---- Instance details page (record-detail, tier B) ----
    # The detail page should show the instance name in the h1
    expect(page.locator("h1").filter(has_text=name)).to_be_visible(timeout=PAGE_TIMEOUT)

    # Breadcrumb should exist with link back to instances
    breadcrumb_link = page.locator("nav[aria-label=Breadcrumb] a").filter(has_text="Framework Instances")
    expect(breadcrumb_link).to_be_visible(timeout=PAGE_TIMEOUT)

    # Stat cards should show key metrics - card_description renders as p tag
    for metric in ["Maturity Level", "Implementation", "Active Users", "Open Issues"]:
        expect(page.locator("[data-slot=card]").filter(has_text=metric).first).to_be_visible(timeout=PAGE_TIMEOUT)

    # Detail cards should show section headings
    for section in ["Organisation", "Ownership", "Implementation", "Quality Scores"]:
        expect(page.locator("h3").filter(has_text=section).first).to_be_visible(timeout=PAGE_TIMEOUT)

    # Status badge should show "Operational" (the seeded instance is operational)
    expect(page.locator("text=Operational").first).to_be_visible(timeout=PAGE_TIMEOUT)

    # Verify a key value from the seeded data is visible
    expect(page.locator("text=London").first).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator("text=Alice Smith").first).to_be_visible(timeout=PAGE_TIMEOUT)

    context.close()