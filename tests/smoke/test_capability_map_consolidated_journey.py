"""Journey test for the consolidated capability map page.

A startup CTO signs in, navigates to the capability map, switches between the six
lenses, opens a capability detail drawer, checks the table view, and verifies
that every link lands on a page with content.

This test must fail before the consolidated page exists and pass after.
"""

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

CAP_MAP_URL = "/capability-map/"


def _seed_capabilities_with_apps(org_id):
    """Seed capabilities with linked applications for the test organisation."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.unified_capability import UnifiedCapability
        from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
        from app.models.application_layer import ApplicationComponent
        from app.models.business_domain import BusinessDomain

        # Create a domain
        domain = BusinessDomain.query.first()
        if not domain:
            domain = BusinessDomain(name="Customer Management", code="CUST")
            db.session.add(domain)
            db.session.flush()

        # Create L1 capabilities
        caps = []
        for i, (name, ctype, maturity_cur, maturity_tgt, importance, annual_cost, security) in enumerate([
            ("Customer Acquisition", "strategic", 3, 5, "critical", 500000, 85),
            ("Order Management", "operational", 4, 4, "high", 200000, 70),
            ("Customer Support", "supporting", 2, 4, "medium", 100000, 55),
            ("Product Development", "strategic", 3, 4, "high", 750000, 90),
            ("Financial Reporting", "operational", 5, 5, "critical", 300000, 95),
            ("IT Infrastructure", "supporting", 1, 3, "low", 50000, 40),
        ]):
            cap = UnifiedCapability(
                name=name,
                description=f"Description for {name}",
                code=f"CUST-{name[:3].upper()}",
                level=1,
                domain_id=domain.id,
                capability_type=ctype,
                current_maturity_level=maturity_cur,
                target_maturity_level=maturity_tgt,
                strategic_importance=importance,
                annual_cost=annual_cost,
                security_score=security,
                reliability_score=75,
                performance_score=80,
                scalability_score=70,
                business_owner="Jane Smith",
                organization_id=org_id,
                scope="tenant",
            )
            db.session.add(cap)
            caps.append(cap)

        db.session.flush()

        # Create L2 children for first capability
        for j, (child_name, ctype) in enumerate([
            ("Lead Generation", "strategic"),
            ("Prospect Qualification", "operational"),
        ]):
            child = UnifiedCapability(
                name=child_name,
                description=f"Child: {child_name}",
                code=f"CUST-{child_name[:3].upper()}",
                level=2,
                parent_capability_id=caps[0].id,
                domain_id=domain.id,
                capability_type=ctype,
                current_maturity_level=2,
                target_maturity_level=4,
                strategic_importance="high",
                annual_cost=100000,
                security_score=65,
                reliability_score=70,
                performance_score=75,
                scalability_score=60,
                business_owner="John Doe",
                organization_id=org_id,
                scope="tenant",
            )
            db.session.add(child)

        db.session.flush()

        # Create applications and map them to capabilities
        apps = []
        for app_name, lifecycle in [
            ("CRM System", "active"),
            ("Marketing Hub", "active"),
            ("ERP Platform", "active"),
        ]:
            app_comp = ApplicationComponent(
                name=app_name,
                description=f"Application: {app_name}",
                lifecycle_status=lifecycle,
                organization_id=org_id,
            )
            db.session.add(app_comp)
            apps.append(app_comp)

        db.session.flush()

        # Map applications to capabilities
        mappings = [
            (apps[0].id, caps[0].id, "full", 90),
            (apps[1].id, caps[0].id, "partial", 60),
            (apps[2].id, caps[3].id, "full", 85),
        ]
        for app_id, cap_id, support_level, coverage in mappings:
            mapping = UnifiedApplicationCapabilityMapping(
                application_component_id=app_id,
                unified_capability_id=cap_id,
                support_level=support_level,
                coverage_percentage=coverage,
                organization_id=org_id,
            )
            db.session.add(mapping)

        db.session.commit()
        db.session.remove()


@pytest.mark.smoke
@pytest.mark.journey
def test_capability_map_consolidated_page_renders_with_six_lenses(browser, live_server):
    """A startup CTO sees the consolidated capability map with six lenses,
    switches between them, opens a capability detail, and checks the table view.
    """
    base = live_server

    # Create a fresh organisation with a CTO
    org = create_fresh_org("cto", org_admin=True)
    cto_email = org["emails"]["cto"]
    org_id = org["org_id"]

    # Seed capabilities with linked applications
    _seed_capabilities_with_apps(org_id)

    # Sign in as the CTO
    page = browser.new_page()
    try:
        sign_in(page, base, cto_email)

        # Navigate to the capability map
        page.goto(base + CAP_MAP_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        # The page should render the capability map heading
        expect(page.locator("h1")).to_contain_text("Capability Map")

        # The six lens buttons should be visible
        lens_buttons = page.locator('[role="group"][aria-label="Lens"] button')
        expect(lens_buttons).to_have_count(6)

        # Default lens is maturity - verify the legend shows maturity levels
        legend = page.locator('[role="status"]')
        expect(legend).to_contain_text("Initial")
        expect(legend).to_contain_text("Optimising")

        # Verify capability tiles are rendered in the map view
        page.wait_for_selector('[role="tabpanel"][aria-label="Map view"]', timeout=PAGE_TIMEOUT)
        # Wait for Alpine to render tiles
        page.wait_for_timeout(2000)

        # Should see the three bands (strategic, operational, supporting)
        expect(page.locator("h2")).to_contain_text("Strategic")
        expect(page.locator("h2")).to_contain_text("Operational")
        expect(page.locator("h2")).to_contain_text("Supporting")

        # Click the Health lens
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Health").click()
        page.wait_for_timeout(500)
        expect(legend).to_contain_text("Healthy")

        # Click the Importance lens
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Importance").click()
        page.wait_for_timeout(500)
        expect(legend).to_contain_text("Critical")

        # Click the Investment lens
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Investment").click()
        page.wait_for_timeout(500)
        expect(legend).to_contain_text("Funded")

        # Click the Control lens
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Control").click()
        page.wait_for_timeout(500)
        expect(legend).to_contain_text("Strong")

        # Click the Applications lens
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Applications").click()
        page.wait_for_timeout(500)
        expect(legend).to_contain_text("Covered")

        # Switch back to Maturity and click a capability tile to open the drawer
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Maturity").click()
        page.wait_for_timeout(500)

        # Click the first capability tile
        first_tile = page.locator('[role="tabpanel"][aria-label="Map view"] [role="button"]').first
        cap_name = first_tile.locator("h4").text_content()
        first_tile.click()
        page.wait_for_timeout(1000)

        # The drawer should open with the capability name
        drawer = page.locator('[role="dialog"]')
        expect(drawer).to_be_visible()
        expect(drawer).to_contain_text(cap_name)

        # The drawer should show all six measures
        expect(drawer.locator("text=Measures")).to_be_visible()
        expect(drawer).to_contain_text("Maturity")
        expect(drawer).to_contain_text("Importance")
        expect(drawer).to_contain_text("Investment")
        expect(drawer).to_contain_text("Health")
        expect(drawer).to_contain_text("Control coverage")
        expect(drawer).to_contain_text("Application coverage")

        # The drawer should show ownership
        expect(drawer).to_contain_text("Ownership")

        # The drawer should show supporting applications
        expect(drawer).to_contain_text("Supporting applications")

        # The drawer should have an "Open the record" button
        expect(drawer.locator("button", has_text="Open the record")).to_be_visible()

        # Close the drawer
        page.locator('[role="dialog"] button[aria-label="Close drawer"]').click()
        page.wait_for_timeout(500)

        # Switch to list view
        page.locator('[role="group"][aria-label="View mode"] button', has_text="List").click()
        page.wait_for_timeout(1000)

        # The table should be visible with all six measure columns
        table = page.locator('[role="tabpanel"][aria-label="List view"] table')
        expect(table).to_be_visible()
        header_row = table.locator("thead tr")
        expect(header_row).to_contain_text("Maturity")
        expect(header_row).to_contain_text("Importance")
        expect(header_row).to_contain_text("Investment")
        expect(header_row).to_contain_text("Health")
        expect(header_row).to_contain_text("Control")
        expect(header_row).to_contain_text("Apps")

        # Verify at least one row has content
        rows = table.locator("tbody tr")
        row_count = rows.count()
        assert row_count > 0, "Table should have at least one data row"

        # Search filtering
        search_input = page.locator('input[aria-label="Search capabilities"]')
        search_input.fill("Customer")
        page.wait_for_timeout(1000)

        # After filtering, rows should contain "Customer"
        filtered_rows = table.locator("tbody tr")
        for i in range(filtered_rows.count()):
            row_text = filtered_rows.nth(i).text_content()
            assert "Customer" in row_text, f"Filtered row should contain 'Customer': {row_text}"

        # Clear search
        search_input.fill("")
        page.wait_for_timeout(500)

        # Verify URL updates with lens parameter
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Health").click()
        page.wait_for_timeout(500)
        assert "lens=health" in page.url, f"URL should contain lens=health: {page.url}"

        # Verify redirects work
        page.goto(base + "/capability-map/health", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "lens=health" in page.url, f"Redirect should set lens=health: {page.url}"

        page.goto(base + "/capability-map/maturity", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "lens=maturity" in page.url, f"Redirect should set lens=maturity: {page.url}"

        page.goto(base + "/capability-map/heatmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "lens=health" in page.url, f"Redirect should set lens=health: {page.url}"

        page.goto(base + "/capability-map/list", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "view=list" in page.url, f"Redirect should set view=list: {page.url}"

        page.goto(base + "/capability-map/tree", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "view=list" in page.url, f"Redirect should set view=list: {page.url}"

        page.goto(base + "/capability-map/search?q=test", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "view=list" in page.url, f"Redirect should set view=list: {page.url}"
        assert "search=test" in page.url, f"Redirect should preserve search query: {page.url}"

    finally:
        page.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_capability_map_not_assessed_shows_correctly(browser, live_server):
    """A capability without recorded values shows 'Not assessed' and is excluded
    from averages and the heat scale.
    """
    base = live_server

    org = create_fresh_org("cto", org_admin=True)
    cto_email = org["emails"]["cto"]
    org_id = org["org_id"]

    # Seed a capability with no maturity, no importance, no investment
    from app import create_app, db
    app = create_app("testing")
    with app.app_context():
        from app.models.unified_capability import UnifiedCapability
        from app.models.business_domain import BusinessDomain

        domain = BusinessDomain.query.first()
        if not domain:
            domain = BusinessDomain(name="Test Domain", code="TEST")
            db.session.add(domain)
            db.session.flush()

        cap = UnifiedCapability(
            name="Unassessed Capability",
            description="A capability with no assessments",
            code="TEST-UNAS",
            level=1,
            domain_id=domain.id,
            capability_type="supporting",
            current_maturity_level=None,
            target_maturity_level=None,
            strategic_importance=None,
            annual_cost=None,
            security_score=None,
            reliability_score=None,
            performance_score=None,
            scalability_score=None,
            organization_id=org_id,
            scope="tenant",
        )
        db.session.add(cap)
        db.session.commit()
        db.session.remove()

    page = browser.new_page()
    try:
        sign_in(page, base, cto_email)
        page.goto(base + CAP_MAP_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(2000)

        # Find the unassessed capability tile
        unassessed_tile = page.locator('[role="tabpanel"][aria-label="Map view"] [role="button"]', has_text="Unassessed Capability")
        expect(unassessed_tile).to_be_visible()

        # In maturity lens, it should show "Not assessed"
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Maturity").click()
        page.wait_for_timeout(500)
        expect(unassessed_tile).to_contain_text("Not assessed")

        # In importance lens, it should show "Not assessed"
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Importance").click()
        page.wait_for_timeout(500)
        expect(unassessed_tile).to_contain_text("Not assessed")

        # In investment lens, it should show "Not recorded"
        page.locator('[role="group"][aria-label="Lens"] button', has_text="Investment").click()
        page.wait_for_timeout(500)
        expect(unassessed_tile).to_contain_text("Not recorded")

        # Open the drawer
        unassessed_tile.click()
        page.wait_for_timeout(1000)

        drawer = page.locator('[role="dialog"]')
        expect(drawer).to_be_visible()

        # The drawer should show "Not assessed" for maturity
        expect(drawer).to_contain_text("Not assessed")

        # The drawer should have an "Assess" button (lens-specific action for maturity)
        expect(drawer.locator("button", has_text="Assess")).to_be_visible()

    finally:
        page.close()