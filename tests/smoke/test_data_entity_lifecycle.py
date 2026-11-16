"""Real data-architect form lifecycle; no request interception or database doubles."""

import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]
CATALOG = "/architecture/data-entities"


def _owned_entities(page, base, marker):
    response = page.request.get(base + "/architecture/api/data-entities", params={"search": marker})
    assert response.status == 200
    return [row for row in response.json() if row["name"] in (marker, marker + " edited")]


def _submit(page, button, url):
    """Observe the actual HTML form submission, including its CSRF token."""
    with page.expect_response(lambda response: response.url == url and response.request.method == "POST") as saved:
        button.click()
    response = saved.value
    assert response.status == 302
    assert parse_qs(response.request.post_data or "").get("csrf_token", [""])[0]
    expect(page).to_have_url(re.compile(re.escape(url.split(CATALOG)[0] + CATALOG) + r"$"))


def test_data_architect_entity_create_filter_edit_cancel_delete(browser, live_server, seeded):
    """Persist and independently read back only this run's synthetic entity."""
    page = browser.new_page()
    marker = "QA data entity " + uuid.uuid4().hex
    created_id = None
    attempted_create = False
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        _login(page, live_server, seeded["emails"]["data_architect"])
        assert page.goto(live_server + CATALOG, timeout=PAGE_TIMEOUT).status == 200
        page.get_by_role("link", name="Create entity", exact=True).click()
        expect(page.get_by_role("heading", name="New data entity", exact=True)).to_be_visible()
        assert page.get_by_test_id("data-entity-form").locator('input[name="csrf_token"]').input_value()
        page.locator("#name").fill(marker)
        page.get_by_label("Business name", exact=True).fill("Synthetic lifecycle fixture")
        page.get_by_label("Description", exact=True).fill("Created by an isolated browser qualification run.")
        # Use the shared synthetic domain: never cause the form's implicit General seed.
        page.get_by_label("Domain", exact=True).select_option(str(seeded["ids"]["data_domain"]))
        page.get_by_label("Entity type", exact=True).select_option("master")
        page.get_by_label("Data classification", exact=True).select_option("confidential")
        page.get_by_label("Contains personal data (PII)", exact=True).check()
        page.get_by_label("Master data", exact=True).check()
        attempted_create = True
        _submit(page, page.get_by_role("button", name="Create entity", exact=True), live_server + CATALOG + "/create")
        rows = _owned_entities(page, live_server, marker)
        assert len(rows) == 1
        created_id = rows[0]["id"]
        assert rows[0]["entity_type"] == "master"
        assert rows[0]["data_classification"] == "confidential"
        assert rows[0]["contains_pii"] is True

        page.goto(live_server + CATALOG, timeout=PAGE_TIMEOUT)
        page.get_by_label("Classification", exact=True).select_option("restricted")
        page.get_by_role("button", name="Apply", exact=True).click()
        expect(page.get_by_role("heading", name="No data entities match these filters", exact=True)).to_be_visible()
        page.get_by_label("Classification", exact=True).select_option("confidential")
        page.get_by_label("Type", exact=True).select_option("master")
        page.get_by_role("button", name="Apply", exact=True).click()
        row = page.get_by_role("row").filter(has=page.get_by_text(marker, exact=True))
        expect(row).to_have_count(1)
        row.get_by_role("button", name=re.compile("^Actions for ")).click()
        page.get_by_role("menuitem", name="Edit", exact=True).click()
        edit_url = live_server + CATALOG + f"/{created_id}/edit"
        expect(page).to_have_url(edit_url)
        expect(page.locator("#name")).to_have_value(marker)
        page.locator("#name").fill(marker + " edited")
        page.get_by_label("Business name", exact=True).fill("Synthetic fixture revised")
        page.get_by_label("Entity type", exact=True).select_option("reference")
        page.get_by_label("Data classification", exact=True).select_option("internal")
        page.get_by_label("Contains personal data (PII)", exact=True).uncheck()
        _submit(page, page.get_by_role("button", name="Save changes", exact=True), edit_url)
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        rows = _owned_entities(page, live_server, marker)
        assert len(rows) == 1
        assert rows[0]["id"] == created_id
        assert rows[0]["name"] == marker + " edited"
        assert rows[0]["business_name"] == "Synthetic fixture revised"
        assert rows[0]["entity_type"] == "reference"
        assert rows[0]["data_classification"] == "internal"
        assert rows[0]["contains_pii"] is False
        row = page.get_by_role("row").filter(has=page.get_by_text(marker + " edited", exact=True))
        expect(row.get_by_role("cell", name="reference", exact=True)).to_be_visible()
        expect(row.get_by_role("cell", name="internal", exact=True)).to_be_visible()
        row.get_by_role("button", name=re.compile("^Actions for ")).click()
        page.get_by_role("menuitem", name="Edit", exact=True).click()
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        expect(page.locator("#name")).to_have_value(marker + " edited")
        expect(page.get_by_label("Contains personal data (PII)", exact=True)).not_to_be_checked()

        delete_url = live_server + CATALOG + f"/{created_id}/delete"
        deletes = []
        page.on("request", lambda request: deletes.append(request.url)
                if request.method == "POST" and request.url == delete_url else None)
        trigger = page.get_by_role("button", name="Delete Entity", exact=True)
        trigger.click()
        dialog = page.get_by_role("dialog", name="Confirm", exact=True)
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text(marker + " edited")
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
        assert _owned_entities(page, live_server, marker)[0]["id"] == created_id
        assert deletes == [], "Cancel submitted a deletion"
        trigger.click()
        expect(dialog).to_be_visible()
        _submit(page, dialog.get_by_role("button", name="Confirm", exact=True), delete_url)
        assert deletes == [delete_url], "Confirmation must submit exactly once"
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        assert _owned_entities(page, live_server, marker) == []
        expect(page.get_by_text(marker + " edited", exact=True)).to_have_count(0)
        assert errors == [], errors
    finally:
        try:
            if attempted_create:
                # Recover identity after an interrupted redirect, but never touch other rows.
                for owned in _owned_entities(page, live_server, marker):
                    assert created_id is None or owned["id"] == created_id
                    edit_url = live_server + CATALOG + f"/{owned['id']}/edit"
                    assert page.goto(edit_url, timeout=PAGE_TIMEOUT).status == 200
                    csrf = page.locator('#delete-entity-form input[name="csrf_token"]').input_value()
                    response = page.request.post(
                        live_server + CATALOG + f"/{owned['id']}/delete",
                        form={"csrf_token": csrf}, max_redirects=0,
                    )
                    assert response.status == 302, "Exact test entity cleanup failed"
                assert _owned_entities(page, live_server, marker) == []
        finally:
            page.close()


@pytest.mark.parametrize("path", [CATALOG, CATALOG + "/create"])
def test_data_entity_forms_require_login(browser, live_server, path):
    """The routes explicitly require login; no invented role/tenant policy claims."""
    context = browser.new_context()
    try:
        response = context.request.get(live_server + path, max_redirects=0)
        assert response.status == 302
        assert "login" in urlparse(response.headers["location"]).path
    finally:
        context.close()
