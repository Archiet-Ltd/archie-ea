"""An administrator reviewing staged vendor prices sees each price in the currency it was recorded in.

The goal, as the platform administrator: "check what the import found before I confirm it, without a euro
price passing as dollars". The staged page is rendered by the product's own template for a euro row, a pound
row, a real zero and a row with no price, and the browser reads what the table shows.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROWS = [
    {"vendor_name": "Acme GmbH", "product_name": "Euro Suite", "tier": "Pro", "annual_cost": 4800, "currency": "EUR"},
    {"vendor_name": "Crown Ltd", "product_name": "Pound Desk", "tier": "Pro", "annual_cost": 1200, "currency": "GBP"},
    {"vendor_name": "Free Co", "product_name": "Free Tier", "tier": "Free", "annual_cost": 0, "currency": "USD"},
    {"vendor_name": "Quiet Inc", "product_name": "Unpriced", "tier": "Std", "annual_cost": None, "currency": "EUR"},
    {"vendor_name": "Typo Corp", "product_name": "Garbled", "tier": "Std", "annual_cost": "abc", "currency": "EUR"},
]


def _staged_page_html(app, user_id):
    from flask import render_template
    from flask_login import login_user

    from app.models.user import User

    with app.test_request_context("/admin/vendor-pricing/import"):
        from app import db
        login_user(db.session.get(User, user_id))
        return render_template("admin/vendor_pricing_import.html", staging_items=ROWS, upload_error=None)


def _stage(page, base, app, user_id):
    html = _staged_page_html(app, user_id)
    page.goto(base + "/admin/vendor-pricing/import", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.route("**/admin/vendor-pricing/import", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.evaluate("() => { const f = document.createElement('form'); f.method = 'POST'; f.action = '/admin/vendor-pricing/import'; document.body.appendChild(f); f.submit(); }")
    expect(page.get_by_text("Euro Suite")).to_be_visible(timeout=PAGE_TIMEOUT)


def test_staged_prices_read_in_their_own_currency(browser, live_server, app):
    info = create_fresh_org("platform_admin", org_admin=True)
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    sign_in(page, live_server, info["emails"]["platform_admin"])
    _stage(page, live_server, app, info["user_ids"]["platform_admin"])
    rows = {r: page.locator("tr", has_text=r) for r in ("Euro Suite", "Pound Desk", "Free Tier", "Unpriced")}
    price = {name: row.locator("td").nth(3).inner_text().strip() for name, row in rows.items()}
    assert price["Euro Suite"] == "€4,800.00", price
    assert price["Pound Desk"] == "£1,200.00", price
    assert price["Free Tier"] == "$0.00", price
    assert price["Unpriced"] == "Not recorded", price


def test_non_numeric_price_reads_not_recorded_without_a_console_error(browser, live_server, app):
    info = create_fresh_org("platform_admin", org_admin=True)
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    sign_in(page, live_server, info["emails"]["platform_admin"])
    _stage(page, live_server, app, info["user_ids"]["platform_admin"])
    expect(page.locator("tr", has_text="Garbled").locator("td").nth(3)).to_have_text("Not recorded", timeout=PAGE_TIMEOUT)
    assert not [e for e in errors if "NaN" in e or "TypeError" in e or "RangeError" in e], errors
