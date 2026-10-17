"""Finance and procurement open the spend page and read spend by vendor and by
contract, each amount in the currency it was recorded in, a dash and the missing
rate where no rate is on file, and the same figures after a reload."""

import datetime as dt
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DASH = "—"


def _seed_spend(org_id):
    """Two contracts for one vendor, in the reporting currency and in another
    with no rate on file. Returns the names and the ids to clean up."""
    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.cost_fact import ExchangeRate
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org = db.session.get(Organization, org_id)
        # What the journey disturbs, kept so teardown can put it back.
        original_currency = org.reporting_currency
        original_rates = [
            (r.from_currency, r.to_currency, r.rate, r.effective_date, r.source)
            for r in ExchangeRate.query.filter(
                ExchangeRate.from_currency.in_(["CHF", "GBP"]),
                ExchangeRate.to_currency.in_(["CHF", "GBP"]),
            ).all()
        ]
        org.reporting_currency = "GBP"
        # The rate table is platform reference data; make sure the pair this
        # journey depends on is genuinely absent.
        for row in ExchangeRate.query.filter(
            ExchangeRate.from_currency.in_(["CHF", "GBP"]),
            ExchangeRate.to_currency.in_(["CHF", "GBP"]),
        ).all():
            db.session.delete(row)
        vendor = VendorOrganization(name="Spend Vendor %s" % suffix)
        db.session.add(vendor)
        db.session.flush()
        contracts = []
        for name, cost, currency in (("Pounds", 1000, "GBP"), ("Francs", 700, "CHF")):
            contract = VendorContract(
                contract_name="%s Contract %s" % (name, suffix), organization_id=org_id,
                vendor_id=vendor.id, annual_cost=cost, currency=currency, status="active",
                start_date=dt.date.today(),
            )
            db.session.add(contract)
            db.session.flush()
            contracts.append(contract)
        db.session.commit()
        return {
            "vendor": vendor.name, "vendor_id": vendor.id,
            "contracts": [c.contract_name for c in contracts],
            "contract_ids": [c.id for c in contracts],
            "org_id": org_id,
            "original_currency": original_currency,
            "original_rates": original_rates,
        }


def _cleanup(seed):
    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.cost_fact import ExchangeRate
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    with app.app_context():
        VendorContract.query.filter(VendorContract.id.in_(seed["contract_ids"])).delete(
            synchronize_session=False)
        VendorOrganization.query.filter_by(id=seed["vendor_id"]).delete(synchronize_session=False)
        # Put the reference data and the organisation back as the seed found them.
        db.session.get(Organization, seed["org_id"]).reporting_currency = seed["original_currency"]
        for frm, to, rate, effective, source in seed["original_rates"]:
            if not ExchangeRate.query.filter_by(
                    from_currency=frm, to_currency=to, effective_date=effective).first():
                db.session.add(ExchangeRate(from_currency=frm, to_currency=to, rate=rate,
                                            effective_date=effective, source=source))
        db.session.commit()


def _table_rows(page, testid):
    rows = page.locator("table[data-testid='%s'] tbody tr" % testid)
    return [[" ".join(c.inner_text().split()) for c in rows.nth(i).locator("td").all()]
            for i in range(rows.count())]


@pytest.mark.parametrize("persona", ["finance", "procurement"])
def test_spend_by_vendor_and_contract_in_each_currency(browser, live_server, seeded, persona):
    seed = _seed_spend(seeded["ids"]["org"])
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"][persona])
        figures = []
        for _ in range(2):  # open, then reload
            response = page.goto(live_server + "/procurement/spend",
                                 wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 200
            page.locator("table[data-testid='spend-by-vendor']").wait_for(
                state="visible", timeout=PAGE_TIMEOUT)

            vendor_row = next(r for r in _table_rows(page, "spend-by-vendor")
                              if r[0] == seed["vendor"])
            # recorded amounts, each in its own currency, never one sum
            assert "1,000.00 GBP" in vendor_row[2] and "700.00 CHF" in vendor_row[2]
            assert "1,700" not in " ".join(vendor_row)
            # the converted column is a dash, and the missing rate is named
            assert vendor_row[3].startswith(DASH)
            assert "No CHF to GBP rate on file" in vendor_row[3]

            contract_rows = {r[0]: r for r in _table_rows(page, "spend-by-contract")}
            pounds, francs = (contract_rows[n] for n in seed["contracts"])
            assert pounds[2] == "1,000.00 GBP" and pounds[3] == "1,000.00 GBP"
            assert francs[2] == "700.00 CHF" and francs[3].startswith(DASH)
            assert page.get_by_test_id("spend-total-converted").inner_text().strip() == DASH
            figures.append((vendor_row, pounds, francs))
        assert figures[0] == figures[1], "the figures changed after a reload"
    finally:
        page.close()
        _cleanup(seed)


def _rates_and_currency(org_id):
    from app import create_app, db
    from app.models.cost_fact import ExchangeRate
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        rates = sorted(
            (r.from_currency, r.to_currency, str(r.rate), r.effective_date)
            for r in ExchangeRate.query.filter(
                ExchangeRate.from_currency.in_(["CHF", "GBP"]),
                ExchangeRate.to_currency.in_(["CHF", "GBP"]),
            ).all()
        )
        return rates, db.session.get(Organization, org_id).reporting_currency


def _plant_rate_and_currency(org_id):
    """Give the reference data something for the journey's seed to disturb."""
    from app import create_app, db
    from app.models.cost_fact import ExchangeRate
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        db.session.get(Organization, org_id).reporting_currency = "EUR"
        if not ExchangeRate.query.filter_by(
                from_currency="CHF", to_currency="GBP", effective_date=dt.date(2020, 1, 1)).first():
            db.session.add(ExchangeRate(from_currency="CHF", to_currency="GBP", rate=1.1,
                                        effective_date=dt.date(2020, 1, 1)))
        db.session.commit()


def test_the_journey_seed_leaves_rates_and_reporting_currency_as_it_found_them(seeded):
    org_id = seeded["ids"]["org"]
    _plant_rate_and_currency(org_id)
    before = _rates_and_currency(org_id)
    assert before[1] == "EUR" and before[0]

    seed = _seed_spend(org_id)
    assert _rates_and_currency(org_id) != before  # the seed does disturb them
    _cleanup(seed)
    assert _rates_and_currency(org_id) == before
