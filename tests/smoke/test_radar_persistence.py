"""Technology classification must survive reload and a new authenticated session."""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _assert_persisted_ring(page, element_name, ring):
    """Assert the element shows the expected ring badge."""
    row = page.get_by_test_id("radar-row").filter(has_text=element_name)
    expect(row).to_have_count(1)
    # The ring badge's test id carries the element id, so find it inside the row.
    badge = row.locator("[data-testid^='radar-ring-']").first
    expect(badge).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(badge).to_contain_text(ring.capitalize())


def _get_element_name(element_id):
    """Get the element name from the database."""
    from app import create_app
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        element = ArchiMateElement.query.get(element_id)
        return element.name if element else None


def _classify_element(page, element_name, ring):
    """Classify an element by opening its row menu and choosing a ring."""
    row = page.get_by_test_id("radar-row").filter(has_text=element_name)
    expect(row).to_have_count(1)
    # Open the row menu
    row.locator("[aria-haspopup='menu']").first.click()
    page.wait_for_timeout(150)
    # Click the move item for the desired ring
    move_button = page.locator(f"[data-testid^='radar-move-'][data-testid$='-{ring}']:visible").first
    expect(move_button).to_be_visible(timeout=PAGE_TIMEOUT)
    with page.expect_navigation(timeout=PAGE_TIMEOUT) as submitted:
        move_button.click()
    assert submitted.value.status == 200
    expect(page.get_by_role("heading", name="Technology radar", exact=True)).to_be_visible()


def test_cto_classification_and_reclassification_persist(browser, live_server, seeded):
    element_id = seeded["ids"]["radar_element"]
    element_name = _get_element_name(element_id)
    assert element_name, "Radar element not found in database"
    context = browser.new_context()
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["cto"])
        response = page.goto(live_server + "/technology/radar/", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        for ring in ("trial", "hold"):
            _classify_element(page, element_name, ring)
            _assert_persisted_ring(page, element_name, ring)
            response = page.reload(timeout=PAGE_TIMEOUT)
            assert response.status == 200
            _assert_persisted_ring(page, element_name, ring)
    finally:
        context.close()

    # A fresh session rules out an optimistic display retained by the first page.
    context = browser.new_context()
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["cto"])
        response = page.goto(live_server + "/technology/radar/", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        _assert_persisted_ring(page, element_name, "hold")
    finally:
        context.close()
