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
        # Each amount is shown in the row's own currency.
        eur_row = table.locator('tr', has_text='SAP')
        usd_row = table.locator('tr', has_text='Okta')
        expect(eur_row).to_contain_text('€')
        expect(eur_row).not_to_contain_text('$')
        expect(usd_row).to_contain_text('$14,400')
        expect(usd_row).not_to_contain_text('€')
        # No upload-error banner (the banner is the only div with this border).
        expect(page.locator('div.border-destructive\\/50')).to_have_count(0)
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
        expect(page.locator('span', has_text="This file isn't UTF-8 encoded. Save it as CSV UTF-8 and upload again.").first
               ).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator('input[name="file"]')).to_be_visible()
    finally:
        page.close()


def test_non_administrator_is_still_refused(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['solution_architect'])
        resp = page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
        assert resp.status == 403
        expect(page.get_by_text('Upload Pricing Data')).to_have_count(0)
    finally:
        page.close()


def _xlsx_bytes():
    import datetime
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['vendor_name', 'product_name', 'tier', 'annual_cost', 'unit_type', 'unit_quantity',
               'currency', 'contract_term_months', 'discount_percent', 'expiry_date'])
    ws.append(['SAP', 'S/4HANA Cloud', 'Professional', 4800, 'user', 100, 'EUR', 36, 15,
               datetime.datetime(2028, 12, 31)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_upload_stages_the_expiry_as_an_iso_date(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
        page.set_input_files('input[name="file"]', files=[{
            'name': 'pricing.xlsx',
            'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'buffer': _xlsx_bytes(),
        }])
        page.get_by_role('button', name=re.compile('Upload')).click()
        expect(page.get_by_text('1 item ready for review')).to_be_visible(timeout=PAGE_TIMEOUT)
        staged = page.evaluate(
            "() => Alpine.$data(document.querySelector(\"[x-data^='vendorPricingImport']\")).items")
        assert staged[0]['expiry_date'] == '2028-12-31', staged
    finally:
        page.close()


def test_confirmed_xlsx_row_keeps_its_expiry_date(browser, live_server, seeded, app):
    from app import db
    from app.models.vendor.vendor_organization import (
        VendorOrganization, VendorProduct, VendorProductPricing)

    with app.app_context():
        vendor = VendorOrganization.query.filter_by(name='SAP').first()
        if vendor is None:
            vendor = VendorOrganization(name='SAP')
            db.session.add(vendor)
            db.session.flush()
        product = VendorProduct.query.filter_by(
            vendor_organization_id=vendor.id, name='S/4HANA Cloud').first()
        if product is None:
            product = VendorProduct(vendor_organization_id=vendor.id, name='S/4HANA Cloud')
            db.session.add(product)
        db.session.flush()
        # Start from no pricing row so the saved date can only come from this confirm.
        VendorProductPricing.query.filter_by(product_id=product.id, tier_name='Professional').delete()
        db.session.commit()
        product_id = product.id

    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
        page.set_input_files('input[name="file"]', files=[{
            'name': 'pricing.xlsx',
            'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'buffer': _xlsx_bytes(),
        }])
        page.get_by_role('button', name=re.compile('Upload')).click()
        expect(page.get_by_text('1 item ready for review')).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_role('button', name=re.compile('Confirm All')).click()
        expect(page.get_by_text('Saved:')).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        page.close()

    with app.app_context():
        row = VendorProductPricing.query.filter_by(
            product_id=product_id, tier_name='Professional').first()
        assert row is not None and str(row.expiry_date) == '2028-12-31'


def test_missing_column_file_shows_a_plain_message(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
        page.set_input_files('input[name="file"]', files=[{
            'name': 'pricing.csv', 'mimeType': 'text/csv',
            'buffer': b'vendor_name,product_name,annual_cost\nSAP,S4,1\n',
        }])
        page.get_by_role('button', name=re.compile('Upload')).click()
        expect(page.get_by_text('Missing required columns: tier')).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        page.close()


def test_uppercase_extension_uploads(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
        csv_text = _page_template_csv(page)
        page.set_input_files('input[name="file"]', files=[{
            'name': 'PRICING.CSV', 'mimeType': 'text/csv', 'buffer': csv_text.encode(),
        }])
        page.get_by_role('button', name=re.compile('Upload')).click()
        expect(page.get_by_text('2 items ready for review')).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        page.close()


def test_non_utf8_csv_and_legacy_xls_get_plain_sentences(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])
        for name, body, sentence in [
            ('latin.csv', 'vendor_name,product_name,tier,annual_cost\nSoci\xe9t\xe9,X,Y,1\n'.encode('latin-1'),
             "This file isn't UTF-8 encoded. Save it as CSV UTF-8 and upload again."),
            ('old.xls', b'\xd0\xcf\x11\xe0', 'Save the file as .xlsx'),
            ('bad.xlsx', b'not a workbook', "This doesn't look like an Excel workbook."),
        ]:
            page.goto(live_server + URL, timeout=PAGE_TIMEOUT)
            page.set_input_files('input[name="file"]', files=[{'name': name, 'mimeType': 'application/octet-stream', 'buffer': body}])
            page.get_by_role('button', name=re.compile('Upload')).click()
            expect(page.get_by_text(sentence)).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(page.get_by_text('codec')).to_have_count(0)
    finally:
        page.close()
