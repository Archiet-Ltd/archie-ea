"""Browser journey for the model-import screen at /architecture/import/oef,
reached from the element catalog's "Import model file" action.

"Done means DEMONSTRATED": this clicks the real controls -- Import model
file, the file picker, Preview import, the strategy choice, Import Model --
and then reloads and reads what persisted, rather than calling the engine or
the JSON API directly.

The fixture (tests/fixtures/oef/archiet_shaped.xml) has 15 elements and 13
relationships, one of which is an ArchiMate-invalid composition. The preview
must say so before anything is written, and the import must store 12
relationships and refuse that one -- never store it.
"""
import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "oef", "archiet_shaped.xml"
)


def _int(page, testid):
    return int(page.locator(f"[data-testid='{testid}']").first.inner_text().strip())


def test_oef_import_preview_then_import_persists_valid_relationships_only(browser, live_server, seeded):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        email = seeded["emails"]["solution_architect"]
        _login(page, live_server, email)

        page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.click("[data-testid='import-oef-link']")
        page.wait_for_url("**/architecture/import/oef", timeout=PAGE_TIMEOUT)
        expect(page.locator("h1, h2", has_text="Import ArchiMate Model")).to_be_visible(timeout=PAGE_TIMEOUT)

        page.set_input_files("#oef_file", FIXTURE_PATH)

        # Preview: classified against the store, relationships checked
        # against the ArchiMate matrix, nothing written yet.
        page.click("[data-testid='btn-preview-import']")
        preview = page.locator("[data-testid='import-preview']")
        expect(preview).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("[data-testid='preview-total']")).to_have_text("15", timeout=PAGE_TIMEOUT)
        assert _int(page, "preview-relationships-valid") == 12
        assert _int(page, "preview-relationships-invalid") == 1
        expect(preview).to_contain_text("composition")

        # The outcome line follows the strategy the user picks.
        page.check("input[name='strategy'][value='update_existing']")
        expect(page.locator("[data-testid='preview-outcome']")).to_contain_text("Update existing")
        page.check("input[name='strategy'][value='skip_duplicates']")
        expect(page.locator("[data-testid='preview-outcome']")).to_contain_text("Skip duplicates")

        page.click("[data-testid='btn-import-model']")
        result = page.locator("[data-testid='import-result']")
        expect(result).to_be_visible(timeout=PAGE_TIMEOUT)

        # 12 stored (created now, or already stored by an earlier run against
        # this database), 1 refused -- the invalid composition.
        rel_panel = page.locator("[data-testid='relationship-import-result']")
        expect(rel_panel).to_be_visible(timeout=PAGE_TIMEOUT)
        stored = _int(page, "result-relationships-created") + _int(page, "result-relationships-skipped")
        assert stored == 12, rel_panel.inner_text()
        assert _int(page, "result-relationships-refused") == 1, rel_panel.inner_text()
        expect(rel_panel).to_contain_text("composition")
        assert _int(page, "result-elements-created") + _int(page, "result-elements-skipped") == 15

        # Reload a fresh page and confirm the import persisted server-side,
        # not just in this response.
        page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)

        resp = page.request.get(live_server + "/archimate/api/elements/search?q=M-CON-10G-FREE-PILOTS")
        assert resp.ok, "element search API failed: %s" % resp.status
        results = resp.json()
        matches = results.get("data") or results.get("elements") or results.get("results") or (
            results if isinstance(results, list) else []
        )
        assert isinstance(matches, list) and len(matches) >= 1, (
            "M-CON-10G-FREE-PILOTS was not found after reload: %r" % results
        )
        element_id = matches[0]["id"]

        # Open the element detail drawer for real and read the rendered
        # properties panel -- the imported OEF properties must be visible on
        # the page, not only in the API.
        #
        # The page also carries a sidebar "Search navigation..." box and a
        # hidden command-palette search that match a looser selector; the
        # element list's own search box has a placeholder nothing else uses.
        search_box = page.locator("input[placeholder='Search by name...']")
        search_box.fill("M-CON-10G-FREE-PILOTS")
        target_row = page.locator("tr[data-testid='element-row']", has_text="M-CON-10G-FREE-PILOTS")
        expect(target_row).to_have_count(1, timeout=PAGE_TIMEOUT)
        target_row.click()

        props_panel = page.locator("[data-testid='element-properties']")
        expect(props_panel).to_be_visible(timeout=PAGE_TIMEOUT)
        props_text = props_panel.inner_text()
        assert "status" in props_text and "RULED" in props_text
        assert "layer" in props_text and "Motivation" in props_text

        detail_resp = page.request.get(
            live_server + "/archimate/api/elements/%s/detail" % element_id
        )
        assert detail_resp.ok
        detail = detail_resp.json()
        assert detail["custom_properties"]["status"] == "RULED"
        assert detail["custom_properties"]["layer"] == "Motivation"
        assert "archie:imported_at" in detail["custom_properties"]

        rel_resp = page.request.get(live_server + "/archimate/api/relationships")
        assert rel_resp.ok
        rel_data = rel_resp.json()
        # success_response() may wrap the payload (CLAUDE.md "Impact scoring"
        # convention: unwrap with json.data ?? json).
        payload = rel_data.get("data", rel_data)
        relationships = payload.get("relationships", [])
        total = payload.get("total", len(relationships))
        assert total >= 12, (
            "expected at least the fixture's 12 valid relationships to persist, got %r" % rel_data
        )
    finally:
        context.close()
