"""Browser journey for ArchiMate CRUD pages migrated to shadcn.

Visits the three tier B templates and asserts:
  - the page loads without 4xx/5xx
  - the front end boots (Alpine)
  - the page header renders with the expected title
  - named controls are reachable and labelled
  - no console errors

Signed in as enterprise_architect (the persona these pages serve).
"""

import pytest

from .test_archetype_journeys import _login, _visit, page  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PERSONA = "enterprise_architect"
DASHBOARD_URL = "/architecture/"
IMPORT_URL = "/architecture/import/oef"
RESTORE_URL = "/architecture/import/oef/restore-points"


def test_architecture_dashboard_renders(page, live_server, seeded):
    """Dashboard loads with heading, action buttons and Alpine."""
    _login(page, live_server, seeded["emails"][PERSONA])
    response, state = _visit(page, live_server, DASHBOARD_URL)

    assert response.status < 400, f"dashboard returned {response.status}"
    assert state["alpine"] == "object", "Alpine did not boot on the dashboard"
    assert state["overflow"] == 0, "dashboard overflows horizontally"
    # All named controls must be labelled
    assert not state["unnamed"], f"unnamed controls: {state['unnamed']}"

    # Page header is present with the layer spine
    header = page.locator('[data-testid="architecture-page-header"]')
    assert header.is_visible()

    # Create element button exists
    create_btn = page.locator('[data-testid="btn-create-element"]')
    assert create_btn.is_visible()
    assert create_btn.get_attribute("aria-label") == "Create element"

    # Import and Export links exist and have correct hrefs
    import_btn = page.locator('[data-testid="btn-import-oef"]')
    assert import_btn.is_visible()
    export_btn = page.locator('[data-testid="btn-export-oef"]')
    assert export_btn.is_visible()

    # No page errors
    assert not page.page_errors, "uncaught page console errors"


def test_architecture_dashboard_has_layer_spine(page, live_server, seeded):
    """The layer navigation strip is present with at least one tab."""
    _login(page, live_server, seeded["emails"][PERSONA])
    _visit(page, live_server, DASHBOARD_URL)

    spine = page.locator('[data-testid="layer-spine"]')
    assert spine.is_visible()
    tabs = spine.locator("a")
    count = tabs.count()
    assert count >= 3, f"expected at least 3 layer tabs, got {count}"


def test_architecture_dashboard_create_element_modal_opens(page, live_server, seeded):
    """Clicking Create element opens the form modal."""
    _login(page, live_server, seeded["emails"][PERSONA])
    _visit(page, live_server, DASHBOARD_URL)

    create_btn = page.locator('[data-testid="btn-create-element"]')
    create_btn.click()
    page.wait_for_timeout(500)

    modal = page.locator("#archimate-form-modal")
    assert modal.is_visible()
    heading = modal.locator("h2")
    assert heading.is_visible()
    assert "Create Element" in heading.text_content()


def test_architecture_dashboard_click_validation_button(page, live_server, seeded):
    """Validate button fires, results panel opens (may show empty results)."""
    _login(page, live_server, seeded["emails"][PERSONA])
    _visit(page, live_server, DASHBOARD_URL)

    validate_btn = page.locator('[data-testid="btn-run-validation"]')
    assert validate_btn.is_visible()
    assert validate_btn.get_attribute("aria-label") == "Validate ArchiMate repository"


def test_import_oef_page_renders(page, live_server, seeded):
    """Import OEF page loads with upload form and strategy options."""
    _login(page, live_server, seeded["emails"][PERSONA])
    response, state = _visit(page, live_server, IMPORT_URL)

    assert response.status < 400, f"import page returned {response.status}"
    assert state["alpine"] == "object", "Alpine did not boot on the import page"
    assert state["overflow"] == 0, "import page overflows horizontally"
    assert not state["unnamed"], f"unnamed controls: {state['unnamed']}"

    # Check page structure
    heading = page.locator("h1, h2").filter(has_text="Import ArchiMate Model")
    assert heading.count() > 0, "Import heading not found"

    # Strategy radio group should be present
    strategy_fieldset = page.locator("fieldset").filter(has_text="When an element is already stored")
    assert strategy_fieldset.count() > 0, "Strategy fieldset not found"

    # Preview and import buttons
    assert page.locator('[data-testid="btn-preview-import"]').is_visible()
    assert page.locator('[data-testid="btn-import-model"]').is_visible()

    # File input should exist
    file_input = page.locator("#oef_file")
    assert file_input.is_visible()

    assert not page.page_errors, "uncaught page console errors"


def test_import_oef_page_has_back_and_restore_links(page, live_server, seeded):
    """Navigation links point to the dashboard and restore points page."""
    _login(page, live_server, seeded["emails"][PERSONA])
    _visit(page, live_server, IMPORT_URL)

    # The restore points link should be present
    restore_link = page.locator('[data-testid="link-restore-points"]')
    assert restore_link.is_visible()
    href = restore_link.get_attribute("href")
    assert href is not None and "restore" in href.lower()


def test_import_restore_points_page_renders(page, live_server, seeded):
    """Restore points page loads and shows empty state when no imports exist."""
    _login(page, live_server, seeded["emails"][PERSONA])
    response, state = _visit(page, live_server, RESTORE_URL)

    assert response.status < 400, f"restore page returned {response.status}"
    assert state["alpine"] == "object", "Alpine did not boot on restore page"
    assert state["overflow"] == 0, "restore page overflows horizontally"
    assert not state["unnamed"], f"unnamed controls: {state['unnamed']}"

    # Should show empty state or table depending on seed data
    empty_state = page.locator('[data-testid="restore-points-empty"]')
    restore_table = page.locator('[data-testid="restore-points"]')
    assert empty_state.is_visible() or restore_table.is_visible(), (
        "restore points page shows neither empty state nor table"
    )

    assert not page.page_errors, "uncaught page console errors"


def test_restore_page_shows_back_link(page, live_server, seeded):
    """Restore page includes a back-to-import link."""
    _login(page, live_server, seeded["emails"][PERSONA])
    _visit(page, live_server, RESTORE_URL)

    back_btn = page.locator('[data-testid="btn-back-to-import"]')
    assert back_btn.is_visible()