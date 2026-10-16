"""The spend page in a real browser, as the finance and procurement personas: a vendor with one
contract in pounds and one in dollars is shown in both currencies; with no dollar rate on file
the converted figure is a dash that names the missing rate, and nothing states the two added
together. Reloading shows the same figures.
"""

import datetime
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_mixed_vendor(org_id):
    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.cost_fact import ExchangeRate
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    label = uuid.uuid4().hex[:6]
    with app.app_context():
        # No dollar-to-pound rate on file, in either direction.
        ExchangeRate.query.filter(
            ExchangeRate.from_currency.in_(["USD", "GBP"]), ExchangeRate.to_currency.in_(["USD", "GBP"])
        ).delete(synchronize_session=False)
        vendor = VendorOrganization(name="Spend Mixed %s" % label)
        db.session.add(vendor)
        db.session.flush()
        for name, currency in (("Pounds", "GBP"), ("Dollars", "USD")):
            db.session.add(VendorContract(
                organization_id=org_id, contract_name="%s deal %s" % (name, label), status="active",
                start_date=datetime.date.today(), annual_cost=1000, currency=currency, vendor_id=vendor.id,
            ))
        db.session.commit()
        return {"vendor": vendor.name, "label": label}


@pytest.fixture(scope="module")
def mixed_vendor(seeded, live_server):
    return _seed_mixed_vendor(seeded["ids"]["org"])


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _read_vendor_row(page, live_server, vendor_name):
    page.goto(live_server + "/procurement/spend", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator('[data-testid="spend-vendor-row"]', has_text=vendor_name)
    expect(row).to_be_visible()
    return (
        row.locator('[data-testid="vendor-stated"]').inner_text(),
        row.locator('[data-testid="vendor-converted"]').inner_text(),
    )


@pytest.mark.parametrize("persona", ["finance", "procurement"])
def test_the_spend_page_states_each_currency_and_names_the_missing_rate(
    persona, page, live_server, seeded, mixed_vendor
):
    _login(page, live_server, seeded["emails"][persona])
    stated, converted = _read_vendor_row(page, live_server, mixed_vendor["vendor"])
    assert "1,000.00 GBP" in stated and "1,000.00 USD" in stated
    assert converted.strip().startswith("—") and "No USD to GBP rate on file" in converted

    contracts = page.locator('[data-testid="spend-contract-row"]', has_text=mixed_vendor["label"])
    expect(contracts).to_have_count(2)
    expect(contracts.locator('[data-testid="contract-converted"]').filter(has_text="No USD to GBP rate")).to_have_count(1)

    # Nothing on the page adds the two currencies together, and a reload shows the same figures.
    assert "2,000.00" not in page.content()
    assert _read_vendor_row(page, live_server, mixed_vendor["vendor"]) == (stated, converted)
