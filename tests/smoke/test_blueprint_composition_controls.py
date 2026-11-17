"""Composition controls must work through the real UI, API and smoke database."""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login


def open_section_editors(page, live_server, solution_id):
    """Reach the section editors the way a person does: the record, then More, then Edit design sections."""
    response = page.goto(live_server + "/solutions/" + str(solution_id), timeout=PAGE_TIMEOUT)
    assert response.status == 200
    page.get_by_role("button", name="More", exact=True).click()
    page.get_by_role("menuitem", name="Edit design sections", exact=True).click()
    page.wait_for_url(re.compile(r"\?edit=blueprint$"), timeout=PAGE_TIMEOUT)
    expect(page.locator("#application_cooperation")).to_be_attached()

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_composition_editor_open_cancel_and_escape(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        open_section_editors(page, live_server, seeded["ids"]["solution"])
        page.locator('#application_cooperation').get_by_role(
            'button', name=re.compile(r'^5\. Application Cooperation')
        ).click()
        trigger = page.get_by_role("button", name="+ Component", exact=True)
        trigger.click()
        dialog = page.get_by_role("dialog", name="Add Component", exact=True)
        expect(dialog).to_be_visible()
        expect(dialog.get_by_label("Application *", exact=True)).to_be_visible()
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


def test_composition_add_edit_and_delete_survive_reload(browser, live_server, seeded):
    """No network doubles. Clean up only the exact composition created here.

    This proves one admin application-component journey, not role restrictions
    or the alternative ArchiMate picker, which need their own qualification.
    """
    page = browser.new_page()
    marker = "QA composition " + uuid.uuid4().hex[:12]
    url = live_server + "/solutions/" + str(seeded["ids"]["solution"])
    api_url = url + "/composition"
    created_id = None
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        open_section_editors(page, live_server, seeded["ids"]["solution"])
        page.locator('#application_cooperation').get_by_role(
            'button', name=re.compile(r'^5\. Application Cooperation')
        ).click()
        page.get_by_role("button", name="+ Component", exact=True).click()
        dialog = page.get_by_role("dialog", name="Add Component", exact=True)
        expect(dialog).to_be_visible()
        dialog.get_by_label("Application *", exact=True).fill("Smoke Payroll")
        choice = dialog.get_by_role("button", name=re.compile(r"^Smoke Payroll "))
        expect(choice).to_have_count(1)
        component_name = choice.inner_text().strip()
        choice.click()
        dialog.get_by_label("Notes", exact=True).fill(marker)
        dialog.get_by_label("Role", exact=True).select_option("core")
        with page.expect_response(lambda r: r.url == api_url and r.request.method == "POST") as saved:
            dialog.get_by_role("button", name="Save", exact=True).click()
        result = saved.value
        assert result.status == 201, result.text()
        payload = result.json()
        created_id = payload["item"]["id"]
        assert payload["success"] is True
        assert payload["item"]["component_id"] == seeded["ids"]["application"]
        assert payload["item"]["component_type"] == "application"
        expect(dialog).not_to_be_visible()
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        row = page.get_by_role("row").filter(has=page.get_by_role("cell", name=component_name, exact=True))
        expect(row).to_have_count(1)
        expect(row.get_by_role("cell", name="core", exact=True)).to_be_visible()
        row.get_by_role("button", name="Edit", exact=True).click()
        editor = page.get_by_role("dialog", name="Edit Component", exact=True)
        expect(editor.get_by_label("Notes", exact=True)).to_have_value(marker)
        editor.get_by_label("Notes", exact=True).fill(marker + " edited")
        editor.get_by_label("Role", exact=True).select_option("supporting")
        item_url = api_url + "/" + str(created_id)
        with page.expect_response(lambda r: r.url == item_url and r.request.method == "PUT") as updated:
            editor.get_by_role("button", name="Save changes", exact=True).click()
        assert updated.value.status == 200, updated.value.text()
        assert updated.value.json()["success"] is True
        expect(editor).not_to_be_visible()
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        stored = page.request.get(api_url)
        assert stored.status == 200, stored.text()
        matches = [item for item in stored.json()["items"] if item["id"] == created_id]
        assert len(matches) == 1
        assert matches[0]["notes"] == marker + " edited"
        assert matches[0]["role"] == "supporting"
        assert matches[0]["component_id"] == seeded["ids"]["application"]
        expect(row.get_by_role("cell", name="supporting", exact=True)).to_be_visible()
        delete_trigger = row.get_by_role("button", name="Delete", exact=True)
        delete_trigger.click()
        confirmation = page.get_by_role("dialog")
        expect(confirmation).to_be_visible()
        expect(confirmation.get_by_text(component_name, exact=False)).to_be_visible()
        confirmation.get_by_role("button", name="Cancel", exact=True).click()
        expect(confirmation).not_to_be_visible()
        expect(delete_trigger).to_be_focused()
        still_present = page.request.get(api_url)
        assert still_present.status == 200, still_present.text()
        assert any(item["id"] == created_id for item in still_present.json()["items"])
        delete_trigger.click()
        expect(confirmation).to_be_visible()
        deleted_id = created_id
        with page.expect_response(lambda r: r.url == item_url and r.request.method == "DELETE") as deleted:
            confirmation.get_by_role("button", name="Delete", exact=True).click()
        assert deleted.value.status == 200, deleted.value.text()
        assert deleted.value.json()["success"] is True
        created_id = None  # The exact test row is already removed; do not delete twice.
        expect(confirmation).not_to_be_visible()
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        after_delete = page.request.get(api_url)
        assert after_delete.status == 200, after_delete.text()
        assert not any(item["id"] == deleted_id for item in after_delete.json()["items"])
        expect(row).to_have_count(0)
    finally:
        try:
            if created_id is not None:
                csrf = page.locator('meta[name="csrf-token"]').get_attribute("content")
                cleaned = page.request.delete(api_url + "/" + str(created_id), headers={"X-CSRFToken": csrf or ""})
                assert cleaned.status == 200, cleaned.text()
        finally:
            page.close()
