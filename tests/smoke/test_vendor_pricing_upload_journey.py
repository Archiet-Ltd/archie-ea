"""platform_admin: upload the vendor pricing page's own CSV template and see
its rows staged for review (currencies included); an unreadable file gives a
plain error and the page stays usable; a non-administrator is refused."""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

URL = '/admin/vendor-pricing/import'


def _page_template_csv(page):
    text = page.locator('pre').first.inner_text()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith('vendor_name,'), lines
    return '\n'.join(lines) + '\n'


def test_admin_uploads_the_pages_own_csv_template_and_sees_rows_staged(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
        csv_text = _page_template_csv(page)
        page.set_input_files('input[name="file"]', files=[{
            'name': 'pricing.csv', 'mimeType': 'text/csv', 'buffer': csv_text.encode(),
        }])
        page.get_by_role('button', name=re.compile('Upload')).click()
        expect(page.get_by_text('2 items ready for review')).to_be_visible(timeout=PAGE_TIMEOUT)
        table = page.locator('table')
        expect(table.get_by_text('SAP', exact=True)).to_be_visible()
        expect(table.get_by_text('EUR', exact=True)).to_be_visible()
        expect(table.get_by_text('USD', exact=True)).to_be_visible()
        expect(page.get_by_text('Parse error')).to_have_count(0)
        expect(page.locator('i[data-lucide="alert-circle"]')).to_have_count(0)
    finally:
        page.close()


def test_unreadable_file_shows_plain_error_and_page_stays_usable(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
        page.set_input_files('input[name="file"]', files=[{
            'name': 'broken.csv', 'mimeType': 'text/csv', 'buffer': b'\xff\xfe\x00\x80\x81',
        }])
        page.get_by_role('button', name=re.compile('Upload')).click()
        expect(page.locator('span', has_text=re.compile('could not|Parse error|no data', re.I)).first
               ).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator('input[name="file"]')).to_be_visible()
    finally:
        page.close()


def test_non_administrator_is_still_refused(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['solution_architect'])
        resp = page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
        assert resp.status in (302, 401, 403) or '/admin/vendor-pricing' not in page.url or resp.status == 403
        expect(page.get_by_text('Upload Pricing Data')).to_have_count(0)
    finally:
        page.close()
