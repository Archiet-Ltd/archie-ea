"""Full vendor create -> edit -> persist -> reload journey through the real UI.

test_vendor_dialog_controls.py already covers the create-dialog's cancel/escape
paths (no write on dismissal). This file covers the write path itself: a
platform_admin creates a real vendor through the "New Vendor" modal, confirms
it survives a hard page reload, then edits it through the "Edit Vendor" modal
and confirms the edit also survives a reload -- the "Done means DEMONSTRATED"
bar from CLAUDE.md, not just an unchecked POST.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_vendor_create_edit_persists_across_reload(browser, live_server, seeded):
    page = browser.new_page()
    vendor_name = f"QA Journey Vendor {uuid.uuid4().hex[:8]}"
    updated_name = f"{vendor_name} (Updated)"
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        response = page.goto(live_server + '/applications/vendors', timeout=PAGE_TIMEOUT)
        assert response.status == 200

        # ---- Create ----
        page.get_by_role('button', name='Add vendor', exact=True).click()
        dialog = page.locator('#create-vendor')
        expect(dialog).to_be_visible()
        dialog.get_by_label('Vendor name *', exact=True).fill(vendor_name)
        dialog.locator('#cv-type').select_option('software_vendor')
        dialog.locator('#cv-website').fill('https://qa-journey-vendor.example.com')
        dialog.get_by_role('button', name='Create vendor', exact=True).click()
        expect(dialog).not_to_be_visible(timeout=PAGE_TIMEOUT)

        # The modal's own success path reloads the page after a toast; wait for
        # that reload rather than racing it, then do our own hard reload so the
        # assertion is about persisted server state, not client-side Alpine state.
        page.wait_for_timeout(1200)
        page.reload(timeout=PAGE_TIMEOUT)
        row_name = page.get_by_text(vendor_name, exact=True).first
        expect(row_name).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- Edit ---- (the row menu opens the vendor's edit page)
        page.get_by_role('button', name=f'Actions for {vendor_name}', exact=True).click()
        page.locator('[data-testid=vendor-edit]:visible').click()
        page.wait_for_url('**/edit', timeout=PAGE_TIMEOUT)
        name_input = page.get_by_label('Vendor Name', exact=True)
        expect(name_input).to_have_value(vendor_name)
        name_input.fill('')
        name_input.fill(updated_name)
        page.get_by_role('button', name='Update Vendor', exact=True).click()
        page.wait_for_load_state('networkidle', timeout=PAGE_TIMEOUT)

        # Confirm the edit persisted server-side: back on the list after a reload.
        page.goto(live_server + '/applications/vendors', timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(updated_name, exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(vendor_name, exact=True)).to_have_count(0)
    finally:
        page.close()
