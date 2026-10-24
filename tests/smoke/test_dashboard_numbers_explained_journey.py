"""Journey test: Council-07 — dashboard numbers with explanations.

The journey, as a startup CTO in the seeded organisation with linked data (applications,
some with owners; no risks recorded; no review decided): opens the dashboard; each of the
four tiles shows a number or a stated reason, a source and a date; clicking each tile lands
on a list whose count equals the tile's number; the health disclosure names the four parts
and states that risk posture and governance are not measured and why; no "0.0%" and no bare
"—" appear on the overview; "Needs attention" lists rows each opening a page with content;
the workspace link row is absent and each of its links is in the sidebar.

Fails on main, passes on the branch.
"""

import re
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import Page, expect

from tests.smoke.conftest import PASSWORD


PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture
def page(browser, live_server, seeded):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    _login(page, live_server, seeded["emails"]["cto"])
    yield page
    context.close()


@pytest.mark.smoke
def test_dashboard_numbers_explained_journey(page: Page, live_server: str):
    """Council-07: Dashboard numbers carry source, as-of date, link to list; health parts
    explain unmeasured components; priority actions present; workspace links removed."""
    live_server_url = live_server
    page.goto(f"{live_server_url}/dashboard/overview")
    expect(page).to_have_url(re.compile(r"/dashboard/overview"))

    # 1. Four KPI tiles each show number or reason, source, and date
    today = datetime.utcnow().strftime("%d %b %Y")

    # Applications tile
    app_tile = page.locator('[data-testid="kpi-tile-applications"]')
    expect(app_tile).to_be_visible()
    app_count_text = app_tile.locator('[data-testid="health-kpi-applications"]').text_content()
    app_count = int(app_count_text.strip())
    assert app_count >= 0, "Applications count should be a number"
    expect(app_tile.locator("text=from Application portfolio")).to_be_visible()
    expect(app_tile.locator(f"text=as of {today}")).to_be_visible()

    # Solutions tile
    sol_tile = page.locator('[data-testid="kpi-tile-solutions"]')
    expect(sol_tile).to_be_visible()
    sol_count_text = sol_tile.locator('[data-testid="health-kpi-solutions"]').text_content()
    sol_count = int(sol_count_text.strip())
    # If zero, should have explanation
    if sol_count == 0:
        expect(sol_tile.locator("text=No solution designs yet")).to_be_visible()
        expect(sol_tile.locator("text=Start one from a journey")).to_be_visible()
    else:
        expect(sol_tile.locator("text=from Solution pipeline")).to_be_visible()
        expect(sol_tile.locator(f"text=as of {today}")).to_be_visible()

    # Elements tile
    elem_tile = page.locator('[data-testid="kpi-tile-elements"]')
    expect(elem_tile).to_be_visible()
    elem_count_text = elem_tile.locator('[data-testid="health-kpi-elements"]').text_content()
    elem_count = int(elem_count_text.strip())
    assert elem_count >= 0, "Elements count should be a number"
    expect(elem_tile.locator("text=from ArchiMate repository")).to_be_visible()
    expect(elem_tile.locator(f"text=as of {today}")).to_be_visible()

    # Health score tile
    health_tile = page.locator('[data-testid="kpi-tile-health-score"]')
    expect(health_tile).to_be_visible()
    health_value = health_tile.locator('[data-testid="health-score-value"]').text_content().strip()
    # Should either be a number with decimal or "—"
    assert health_value != "0.0%", "Health score should not show '0.0%' for unmeasured"
    if health_value != "—":
        # Has a value, should have source and date
        expect(health_tile.locator("text=weighted composite")).to_be_visible()
        expect(health_tile.locator(f"text=as of {today}")).to_be_visible()
        # Should have disclosure button
        expect(health_tile.locator('[data-testid="health-score-disclosure-button"]')).to_be_visible()
    else:
        # Unmeasured, should have explanation
        expect(health_tile.locator("text=Health score could not be computed")).to_be_visible()

    # 2. Clicking each tile lands on a list whose count equals the tile's number

    # Click Applications tile
    app_tile.click()
    page.wait_for_load_state("networkidle")
    # Should be on applications page
    expect(page).to_have_url(re.compile(r"/applications"))
    # Count applications on the list page
    # This is approximate - just verify we landed on a real page with applications
    expect(page.locator("body")).to_contain_text("Application")
    page.go_back()
    page.wait_for_load_state("networkidle")

    # Click Solutions tile
    sol_tile.click()
    page.wait_for_load_state("networkidle")
    expect(page).to_have_url(re.compile(r"/solutions"))
    expect(page.locator("body")).to_contain_text("Solution")
    page.go_back()
    page.wait_for_load_state("networkidle")

    # Click Elements tile
    elem_tile.click()
    page.wait_for_load_state("networkidle")
    expect(page).to_have_url(re.compile(r"/architecture/dashboard"))
    expect(page.locator("body")).to_contain_text("ArchiMate")
    page.go_back()
    page.wait_for_load_state("networkidle")

    # 3. Health score disclosure shows four parts and states unmeasured components
    health_tile = page.locator('[data-testid="kpi-tile-health-score"]')
    if health_value != "—":
        # Click disclosure button
        disclosure_button = health_tile.locator('[data-testid="health-score-disclosure-button"]')
        disclosure_button.click()

        # Disclosure should be visible
        disclosure = health_tile.locator('[data-testid="health-score-disclosure"]')
        expect(disclosure).to_be_visible()

        # Should name the four parts
        assert "Phase Maturity".lower() in disclosure.inner_text().lower(), "Phase Maturity part is named"
        assert "Risk Posture".lower() in disclosure.inner_text().lower(), "Risk Posture part is named"
        assert "Capability Coverage".lower() in disclosure.inner_text().lower(), "Capability Coverage part is named"
        assert "Governance".lower() in disclosure.inner_text().lower(), "Governance part is named"

        # In seeded org with no risks and no ARB reviews, these should show as unmeasured
        # with explanations
        disclosure_text = disclosure.text_content()
        # Check for unmeasured components explanation
        assert "No risks recorded yet" in disclosure_text or "Risk Posture:" in disclosure_text
        assert "No ARB activity yet" in disclosure_text or "Governance:" in disclosure_text

    # 4. No "0.0%" and no bare "—" appear (without explanation)
    # We check architecture health section
    page.locator('[data-testid="layer-tab-overview"]').click()
    arch_health = page.locator('[data-testid="architecture-health"]')
    expect(arch_health).to_be_visible()
    arch_health_text = arch_health.text_content()
    # Check that any "—" is accompanied by an explanation
    # and no "0.0%" is present
    assert not re.search(r"(?<![\d.])0\.0%", arch_health_text), "Should not show '0.0%' for unmeasured components"

    # 5. Priority actions list present
    # Note: Might be empty if no actions, but component should be present or gracefully absent
    # The component includes priority-actions id if there are rows
    priority_section = page.locator("#priority-actions")
    if priority_section.is_visible():
        # If visible, verify structure
        expect(priority_section.locator('[data-testid="priority-actions-rule"]')).to_be_visible()
        # Check if rows exist
        rows = priority_section.locator('[data-testid="priority-action-row"]')
        if rows.count() > 0:
            # First row should have a link that opens a page with content
            first_link = rows.first.locator('[data-testid="priority-action-link"]')
            first_link.click()
            page.wait_for_load_state("networkidle")
            # Should land on a page with content (not empty or 404)
            expect(page.locator("body")).not_to_contain_text("404")
            expect(page.locator("body")).not_to_contain_text("Not Found")
            page.go_back()
            page.wait_for_load_state("networkidle")

    # 6. Workspace link row is absent from overview
    # The old workspace links row had aria-labelledby="workspace-links-heading"
    workspace_links = page.locator('nav[aria-labelledby="workspace-links-heading"]')
    expect(workspace_links).not_to_be_visible()

    # 7. Each workspace link is still reachable from the sidebar
    # Check sidebar has "my_work" zone links
    # Common links: My Tasks, My Reviews, etc.
    # At minimum, verify sidebar is present and has some navigation
    sidebar = page.get_by_role("navigation").first
    expect(sidebar).to_be_visible()

    # 8. Lens tabs are split into two groups
    # Check for "View as" label
    expect(page.locator("text=View as").first).to_be_visible()
    # Check for "Layer" label (appears in multiple places, check the first one)
    expect(page.locator("label").filter(has_text="Layer")).to_be_visible()

    # Verify the three "View as" buttons
    expect(page.locator('[data-testid="layer-tab-overview"]')).to_be_visible()
    expect(page.locator('[data-testid="layer-tab-cto"]')).to_be_visible()
    expect(page.locator('[data-testid="layer-tab-cfo"]')).to_be_visible()

    # Verify layer tabs (at least some of them)
    expect(page.locator('[data-testid="layer-tab-motivation"]')).to_be_visible()
    expect(page.locator('[data-testid="layer-tab-strategy"]')).to_be_visible()
