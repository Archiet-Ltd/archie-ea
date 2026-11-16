"""Every exposed governance Add button must open a usable, dismissible editor."""
import pytest
import uuid
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.parametrize("label", ["+ Exception", "+ Compliance", "+ Change", "+ Review"])
def test_governance_editor_open_cancel_and_escape(browser, live_server, seeded, label):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        response = page.goto(live_server + "/solutions/" + str(seeded["ids"]["solution"]) + "?edit=blueprint", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        trigger = page.get_by_role("button", name=label, exact=True)
        trigger.click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("button", name="Cancel", exact=True)).to_be_visible()
        assert dialog.get_by_role("textbox").count() > 0, "An empty modal is not an entity editor"
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
        trigger.click()
        expect(dialog).to_be_visible()
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
    finally:
        page.close()


@pytest.mark.parametrize(
    "label,resource,fields,selects,stored_field",
    [
        ("+ Exception", "governance-exceptions", {"Exception description *": "marker"}, {}, "exception_description"),
        ("+ Compliance", "compliance-mappings", {"Framework *": "QA framework", "Control ID *": "marker"}, {}, "control_id"),
        ("+ Change", "change-requests", {"Title *": "marker"}, {"Change type *": "scope"}, "title"),
        ("+ Review", "feasibility-reviews", {"Notes": "marker"}, {"Review type *": "technical", "Feasible": "false"}, "notes"),
    ],
)
def test_governance_editor_saves_to_real_api_and_survives_reload(
    browser, live_server, seeded, label, resource, fields, selects, stored_field
):
    """No network doubles: UI POST, a fresh page, then a real persisted list read.

    Only the exact row created in this disposable smoke database is cleaned up.
    This does not prove role restrictions or cross-tenant authorization.
    """
    page = browser.new_page()
    marker = "QA governance " + uuid.uuid4().hex[:12]
    api_url = live_server + "/solutions/" + str(seeded["ids"]["solution"]) + "/" + resource
    created_id = None
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        response = page.goto(live_server + "/solutions/" + str(seeded["ids"]["solution"]) + "?edit=blueprint", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        page.get_by_role("button", name=label, exact=True).click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        for field, value in fields.items():
            dialog.get_by_label(field, exact=True).fill(marker if value == "marker" else value)
        for field, value in selects.items():
            dialog.get_by_label(field, exact=True).select_option(value)
        with page.expect_response(lambda result: result.url == api_url and result.request.method == "POST") as saved:
            dialog.get_by_role("button", name="Save", exact=True).click()
        result = saved.value
        assert result.status == 201, result.text()
        body = result.json()
        created_id = body["item"]["id"]
        assert body["success"] is True
        assert body["item"][stored_field] == marker
        if resource == "feasibility-reviews":
            assert body["item"]["feasible"] is False
        expect(dialog).not_to_be_visible()
        response = page.reload(timeout=PAGE_TIMEOUT)
        assert response.status == 200
        persisted = page.request.get(api_url)
        assert persisted.status == 200, persisted.text()
        matches = [row for row in persisted.json()["items"] if row["id"] == created_id]
        assert len(matches) == 1
        assert matches[0][stored_field] == marker
        if resource == "feasibility-reviews":
            assert matches[0]["feasible"] is False
        else:
            expect(page.get_by_text(marker, exact=True)).to_be_visible()
    finally:
        try:
            if created_id is not None:
                csrf = page.locator('meta[name="csrf-token"]').get_attribute("content")
                cleaned = page.request.delete(api_url + "/" + str(created_id), headers={"X-CSRFToken": csrf or ""})
                assert cleaned.status == 200, "Failed to clean up exact QA governance row: " + cleaned.text()
        finally:
            page.close()
