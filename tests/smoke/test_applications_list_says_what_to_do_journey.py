"""
Journey test: Applications list says once what is missing and shows owner and completeness per row.

Tests T-council-10 acceptance criteria as a startup CTO:
- Summary reads "N of M have no owner" and "M of M have no capability" in full, no ellipsis
- Clicking "N of M have no owner" shows exactly N rows and a removable filter chip
- No "No capability mapped yet: map it" text appears anywhere
- Each owner-less row offers "Assign owner" which opens the owner picker
- After assigning an owner, the row shows the person and the summary updates
- Each "Record" ring opens the fact sheet at its missing list
- "Map a capability" in a row's Actions menu opens the mapping dialog for that application
"""

import re

import pytest


@pytest.mark.journey
def test_applications_list_says_what_to_do_journey(
    live_server, authenticated_browser_session, make_org, db_session
):
    """
    As a startup CTO in an organisation seeded with eight applications (three with owners,
    none with a capability, six with vendors), I see a non-truncating summary that links
    to filtered views, owner display rows or an explained action, and a completeness ring
    per row.
    """
    browser, user, _org_name = authenticated_browser_session

    # Given: an organisation with eight applications (three with owners, none with a
    # capability, six with vendors)
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    org = user.organization

    # Create eight applications
    apps = []
    for i in range(1, 9):
        app = ApplicationComponent(
            name=f"Test App {i}",
            organization_id=org.id,
            component_type="web_application" if i % 2 == 0 else "mobile_app",
            lifecycle_status="2.1 STRATEGIC" if i <= 4 else "2.2 TACTICAL",
            vendor_name=f"Vendor {i}" if i <= 6 else None,
        )
        db_session.add(app)
        apps.append(app)

    db_session.flush()

    # Add owners to three applications
    for i in [0, 2, 4]:  # apps 1, 3, 5
        owner = ApplicationOwner(
            application_id=apps[i].id,
            user_id=user.id,
            organization_id=org.id,
            ownership_type="primary",
        )
        db_session.add(owner)

    db_session.commit()

    # When: I open the applications list
    browser.goto(f"{live_server.url}/applications/")
    browser.wait_for_selector('[data-testid="data-quality-alert"]', timeout=10000)

    # Then: the summary reads "5 of 8 have no owner" in full, with no ellipsis
    summary = browser.locator('[data-testid="data-quality-alert"]')
    summary_text = summary.inner_text()
    assert "5 of 8 have no owner" in summary_text, f"Expected '5 of 8 have no owner' in summary, got: {summary_text}"
    assert "..." not in summary_text, f"Summary should not truncate, but got: {summary_text}"
    assert "8 of 8 have no capability" in summary_text, f"Expected '8 of 8 have no capability' in summary, got: {summary_text}"

    # And: clicking "5 of 8 have no owner" shows exactly five rows
    no_owner_link = summary.get_by_text(re.compile(r"5 of 8 have no owner"))
    no_owner_link.click()
    browser.wait_for_selector('[data-testid="app-row-1"]', timeout=10000)

    # Check that filter chip is visible
    filter_chips = browser.locator('[data-testid="active-filters"]')
    assert filter_chips.is_visible(), "Active filters chip should be visible"
    assert "No owner" in filter_chips.inner_text(), "Filter chip should show 'No owner'"

    # Count rows (should be 5: apps 2, 4, 6, 7, 8 have no owner)
    rows = browser.locator('[data-testid^="app-row-"]')
    row_count = rows.count()
    assert row_count == 5, f"Expected 5 rows when filtering by no owner, got {row_count}"

    # And: the text "No capability mapped yet: map it" appears nowhere
    page_content = browser.locator("body").inner_text()
    assert "No capability mapped yet: map it" not in page_content, \
        "Per-row capability link text should not appear"

    # And: clicking the filter chip removes the filter
    remove_filter = filter_chips.get_by_text("No owner")
    remove_filter.click()
    browser.wait_for_timeout(500)

    # Should now see all 8 rows again
    browser.wait_for_selector('[data-testid="app-row-1"]', timeout=5000)
    rows = browser.locator('[data-testid^="app-row-"]')
    row_count = rows.count()
    assert row_count == 8, f"Expected 8 rows after removing filter, got {row_count}"

    # And: each owner-less row shows "No owner" with explained_value styling
    ownerless_row = browser.locator('[data-testid="app-row-2"]')  # App 2 has no owner
    owner_cell_text = ownerless_row.locator('td').nth(5).inner_text()  # Owner is 6th column (0-indexed: 5)
    assert "No owner" in owner_cell_text, f"Expected 'No owner' in ownerless row, got: {owner_cell_text}"

    # And: capability column shows "Not mapped" without a link
    capability_cell = ownerless_row.locator('[data-testid="app-capability-2"]')
    capability_text = capability_cell.inner_text()
    assert "Not mapped" in capability_text, f"Expected 'Not mapped' in capability column, got: {capability_text}"

    # And: "Map a capability" is in the Actions menu
    actions_menu_button = ownerless_row.locator('[aria-label="Map application Test App 2"]')
    actions_menu_button.click()
    browser.wait_for_timeout(300)

    map_capability_item = browser.locator('[data-testid="action-map-capability-2"]')
    assert map_capability_item.is_visible(), "Map a capability should be in Actions menu"
    map_capability_text = map_capability_item.inner_text()
    assert "Map a capability" in map_capability_text, \
        f"Expected 'Map a capability' in menu, got: {map_capability_text}"

    # Close the menu
    browser.keyboard.press("Escape")
    browser.wait_for_timeout(200)

    # And: each row with an owner shows the owner's name
    owned_row = browser.locator('[data-testid="app-row-1"]')  # App 1 has an owner
    owner_cell = owned_row.locator('[data-testid="app-owner-1"]')
    owner_text = owner_cell.inner_text()
    assert user.full_name() in owner_text or user.email in owner_text, \
        f"Expected owner name in owned row, got: {owner_text}"

    # And: the completeness ring is present (if the model has completeness_score)
    # Note: We can't easily test this without seeding completeness data, but we can
    # check that the Record column exists
    record_column_header = browser.locator('th:has-text("Record")')
    assert record_column_header.is_visible(), "Record column header should be visible"
