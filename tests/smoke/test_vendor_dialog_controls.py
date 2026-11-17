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
        name.press_sequentially('Unsaved QA vendor')
        if dismissal == 'Escape':
            page.keyboard.press('Escape')
        else:
            dialog.get_by_role('button', name=dismissal, exact=True).click()
        ask = page.get_by_role('dialog', name='Discard what you typed?')
        expect(ask).to_be_visible()
        ask.get_by_role('button', name='Discard', exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
        assert writes == [], 'Dismissing an unsaved vendor form must not write vendor data'
        assert errors == []
    finally:
        page.close()


def test_vendor_dialog_keeps_focus_where_the_person_put_it(browser, live_server, seeded):
    """Typing starts the moment the dialog is up; the dialog's own move of focus to its first
    control must not take the rest of the keystrokes (a space would press Close)."""
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        page.goto(live_server + '/applications/vendors', timeout=PAGE_TIMEOUT)
        page.get_by_role('button', name='Add vendor', exact=True).click()
        dialog = page.locator('#create-vendor')
        name = dialog.get_by_label('Vendor name *', exact=True)
        expect(name).to_be_visible()
        text = 'Typed straight away with spaces'
        name.press_sequentially(text, delay=40)
        expect(name).to_have_value(text)
        expect(name).to_be_focused()
        expect(dialog).to_be_visible()
        expect(page.get_by_role('dialog', name='Discard what you typed?')).to_have_count(0)
    finally:
        page.close()
