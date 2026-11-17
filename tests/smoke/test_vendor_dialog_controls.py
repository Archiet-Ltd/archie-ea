"""Vendor creation cancellation through the real authenticated application."""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.parametrize('dismissal', ['Cancel', 'Close', 'Escape'])
def test_vendor_create_dialog_dismisses_without_write(browser, live_server, seeded, dismissal):
    page = browser.new_page()
    writes = []
    errors = []
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        response = page.goto(live_server + '/applications/vendors', timeout=PAGE_TIMEOUT)
        assert response.status == 200
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: writes.append((request.method, request.url))
                if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
                and 'vendor' in request.url else None)
        trigger = page.get_by_role('button', name='Add vendor', exact=True)
        trigger.click()
        dialog = page.locator('#create-vendor')
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role('heading', name='New vendor', exact=True)).to_be_visible()
        name = dialog.get_by_label('Vendor name *', exact=True)
        expect(name).to_be_visible()
        name.fill('Unsaved QA vendor')
        if dismissal == 'Escape':
            page.keyboard.press('Escape')
        else:
            dialog.get_by_role('button', name=dismissal, exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
        assert writes == [], 'Dismissing an unsaved vendor form must not write vendor data'
        assert errors == []
    finally:
        page.close()
