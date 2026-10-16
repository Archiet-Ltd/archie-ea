"""Spend by vendor and by contract: each amount in the currency it was recorded in and
converted to the reporting currency at a dated rate, never added across currencies.

Two real organisations in the isolation tests; the page tests read what a signed-in user
sees, including a user whose home organisation is not the one they are working in.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from decimal import Decimal

import pytest
from bs4 import BeautifulSoup

from app.modules.procurement.services import get_spend_by_category, get_spend_summary

TODAY = _dt.date.today()


def _org(make_org, label, currency="GBP"):
    org = make_org(label)
    org.reporting_currency = currency
    return org


def _vendor(db_session, label):
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"{label} {uuid.uuid4().hex[:6]}")
    db_session.add(vendor)
    db_session.flush()
    return vendor


def _application(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    app_row = ApplicationComponent(name=f"{name} {uuid.uuid4().hex[:5]}", organization_id=org.id)
    db_session.add(app_row)
    db_session.flush()
    return app_row


def _contract(db_session, org, name, *, annual_cost=None, currency="GBP", vendor=None, contract_value=None, **extra):
    from app.models.application_portfolio import VendorContract

    contract = VendorContract(
        organization_id=org.id,
        contract_name=f"{name} {uuid.uuid4().hex[:6]}",
        status="active",
        start_date=TODAY,
        annual_cost=annual_cost,
        contract_value=contract_value,
        currency=currency,
        vendor_id=vendor.id if vendor else None,
        **extra,
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _rate(db_session, frm, to, rate, day=TODAY):
    from app.models.cost_fact import ExchangeRate

    db_session.query(ExchangeRate).filter_by(from_currency=frm, to_currency=to, effective_date=day).delete()
    db_session.add(ExchangeRate(from_currency=frm, to_currency=to, rate=Decimal(str(rate)), effective_date=day))
    db_session.flush()


def _user(db_session, org, enterprise_role="finance"):
    from app.models.user import Role, User

    user = User(
        email=f"spend-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Finance",
        last_name="Partner",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
        role=Role.query.filter_by(name="Architect").first(),
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _page(client, login_as, user, session_org=None):
    login_as(client, user)
    if session_org is not None:
        with client.session_transaction() as sess:
            sess["current_org_id"] = session_org
    response = client.get("/procurement/spend")
    assert response.status_code == 200, response.get_data(as_text=True)[:1500]
    return response.get_data(as_text=True), BeautifulSoup(response.get_data(as_text=True), "html.parser")


def _mixed_vendor(db_session, org):
    vendor = _vendor(db_session, "Mixed")
    _contract(db_session, org, "Pounds", annual_cost=1000, currency="GBP", vendor=vendor)
    _contract(db_session, org, "Dollars", annual_cost=1000, currency="USD", vendor=vendor)
    return vendor


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


def test_a_vendor_in_two_currencies_is_stated_in_both_and_converted_at_the_rate(app, db_session, make_org):
    org = _org(make_org, "spend-mixed")
    _mixed_vendor(db_session, org)
    _rate(db_session, "USD", "GBP", "0.8")

    summary = get_spend_summary(org.id)

    [vendor] = summary["by_vendor"]
    assert vendor["by_currency"] == {"GBP": Decimal("1000"), "USD": Decimal("1000")}
    assert vendor["converted"] == Decimal("1800.0")
    assert vendor["missing_rates"] == []
    assert summary["reporting_currency"] == "GBP"
    assert summary["total"]["converted"] == Decimal("1800.0")


def test_without_the_rate_the_converted_figure_is_a_dash_and_names_the_missing_rate(app, db_session, make_org):
    org = _org(make_org, "spend-norate")
    _mixed_vendor(db_session, org)
    from app.models.cost_fact import ExchangeRate

    db_session.query(ExchangeRate).filter(
        ExchangeRate.from_currency.in_(["USD", "GBP"]), ExchangeRate.to_currency.in_(["USD", "GBP"])
    ).delete(synchronize_session=False)

    summary = get_spend_summary(org.id)

    [vendor] = summary["by_vendor"]
    assert vendor["by_currency"] == {"GBP": Decimal("1000"), "USD": Decimal("1000")}
    assert vendor["converted"] is None
    assert vendor["missing_rates"] == [("USD", TODAY)]
    assert summary["total"]["converted"] is None and summary["total_annual_cost"] is None
    # Nothing on the summary is the cross-currency sum of 2,000.
    assert Decimal("2000") not in _all_numbers(summary)


def _all_numbers(value):
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float, Decimal)):
        return [Decimal(str(value))]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _all_numbers(v)]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in _all_numbers(v)]
    return []


def test_the_rate_in_effect_on_the_date_asked_is_the_one_used(app, db_session, make_org):
    org = _org(make_org, "spend-dated")
    vendor = _vendor(db_session, "Dated")
    _contract(db_session, org, "Dollars", annual_cost=100, currency="USD", vendor=vendor)
    _rate(db_session, "USD", "GBP", "0.5", day=TODAY - _dt.timedelta(days=30))
    _rate(db_session, "USD", "GBP", "0.9", day=TODAY - _dt.timedelta(days=1))

    assert get_spend_summary(org.id, as_of=TODAY - _dt.timedelta(days=10))["total"]["converted"] == Decimal("50.0")
    assert get_spend_summary(org.id, as_of=TODAY)["total"]["converted"] == Decimal("90.0")


def test_the_top_figure_is_what_convert_total_gives_for_the_same_contracts(app, db_session, make_org):
    from app.services.currency_service import convert_total

    org = _org(make_org, "spend-parity")
    _mixed_vendor(db_session, org)
    other = _vendor(db_session, "Euro")
    _contract(db_session, org, "Euros", annual_cost=500, currency="EUR", vendor=other)
    _rate(db_session, "USD", "GBP", "0.8")
    _rate(db_session, "EUR", "GBP", "0.85")

    class Fact:
        def __init__(self, amount, currency):
            self.amount, self.currency, self.period_start, self.period_end = Decimal(amount), currency, None, TODAY

    expected = convert_total([Fact(1000, "GBP"), Fact(1000, "USD"), Fact(500, "EUR")], "GBP").amount
    assert get_spend_summary(org.id)["total"]["converted"] == expected == Decimal("2225.0")


def test_amounts_nobody_entered_are_left_out_and_counted_never_added_as_zero(app, db_session, make_org):
    org = _org(make_org, "spend-missing")
    vendor = _vendor(db_session, "Partial")
    _contract(db_session, org, "Priced", annual_cost=700, vendor=vendor, contract_value=7000)
    _contract(db_session, org, "Unpriced", vendor=vendor)

    summary = get_spend_summary(org.id)
    assert summary["total"]["converted"] == Decimal("700") and summary["contracts_without_annual_cost"] == 1
    assert summary["by_vendor"][0]["contracts_without_annual_cost"] == 1


def test_category_figures_leave_a_contract_with_no_value_out_and_count_it(app, db_session, make_org):
    org = _org(make_org, "spend-cat")
    _contract(db_session, org, "Valued", contract_value=1000, contract_category="Cloud")
    _contract(db_session, org, "No value", contract_category="Cloud")
    _contract(db_session, org, "Dollar", contract_value=1000, currency="USD", contract_category="Cloud")
    _rate(db_session, "USD", "GBP", "0.8")

    categories = get_spend_by_category(org.id)

    cloud = categories["Cloud"]
    assert cloud["by_currency"] == {"GBP": Decimal("1000"), "USD": Decimal("1000")}
    assert cloud["converted"] == Decimal("1800.0")
    assert cloud["contracts_without_value"] == 1 and cloud["count"] == 3


def test_vendors_are_ranked_by_converted_amount_and_those_with_nothing_stated_come_last(app, db_session, make_org):
    org = _org(make_org, "spend-rank")
    small, big, none = _vendor(db_session, "Small"), _vendor(db_session, "Big"), _vendor(db_session, "None")
    _contract(db_session, org, "s", annual_cost=10, vendor=small)
    _contract(db_session, org, "b", annual_cost=500, vendor=big)
    _contract(db_session, org, "n", vendor=none)
    names = [v["vendor_name"] for v in get_spend_summary(org.id)["by_vendor"]]
    assert names[0].startswith("Big") and names[1].startswith("Small") and names[2].startswith("None")


def test_covered_applications_are_the_linked_ones_once_each_and_a_dash_when_there_are_none(app, db_session, make_org):
    from app.models.contract_application import ContractApplication

    org = _org(make_org, "spend-cover")
    vendor = _vendor(db_session, "Cover")
    first, second = _application(db_session, org, "First"), _application(db_session, org, "Second")
    contract = _contract(db_session, org, "Linked", annual_cost=10, vendor=vendor, application_id=first.id)
    alone = _contract(db_session, org, "Alone", annual_cost=10, vendor=vendor)
    for application in (first, second):
        db_session.add(ContractApplication(contract_id=contract.id, application_id=application.id, organization_id=org.id))
    db_session.flush()

    summary = get_spend_summary(org.id)
    rows = {r["contract_id"]: r for r in summary["by_contract"]}
    assert rows[contract.id]["covered_applications"] == sorted([first.name, second.name])
    assert len(rows[contract.id]["covered_applications"]) == 2
    assert rows[alone.id]["covered_applications"] == []


def test_each_contract_row_carries_its_source_and_truth_class(app, db_session, make_org):
    org = _org(make_org, "spend-prov")
    _contract(db_session, org, "Row", annual_cost=10, vendor=_vendor(db_session, "Prov"))
    [row] = get_spend_summary(org.id)["by_contract"]
    assert row["source_table"] == "vendor_contracts" and row["truth_class"] in {"drawn", "derived", "synchronised"}


def test_another_organisations_contracts_vendors_and_applications_never_appear(app, db_session, make_org):
    org_a, org_b = _org(make_org, "iso-a"), _org(make_org, "iso-b")
    vendor_b = _vendor(db_session, "OnlyB")
    app_b = _application(db_session, org_b, "BApp")
    _contract(db_session, org_b, "B contract", annual_cost=999, vendor=vendor_b, application_id=app_b.id)
    vendor_a = _vendor(db_session, "OnlyA")
    _contract(db_session, org_a, "A contract", annual_cost=1, vendor=vendor_a)

    summary_a = get_spend_summary(org_a.id)
    assert [v["vendor_name"][:5] for v in summary_a["by_vendor"]] == ["OnlyA"]
    assert summary_a["total"]["converted"] == Decimal("1")
    assert [c["contract_name"][:10] for c in summary_a["by_contract"]] == ["A contract"]
    assert all(app_b.name not in c["covered_applications"] for c in summary_a["by_contract"])
    categories = get_spend_by_category(org_a.id)
    assert sum(row["count"] for row in categories.values()) == 1


def test_with_no_organisation_both_functions_return_the_empty_shape(app, db_session, make_org):
    org = _org(make_org, "spend-none")
    _contract(db_session, org, "Hidden", annual_cost=5, vendor=_vendor(db_session, "H"), contract_value=5)
    summary = get_spend_summary(None)
    assert summary["by_vendor"] == [] and summary["by_contract"] == [] and summary["total_contracts"] == 0
    assert summary["total"]["converted"] is None
    assert get_spend_by_category(None) == {}


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #


def test_the_page_shows_both_currencies_and_the_converted_figure_per_vendor_and_contract(
    app, db_session, make_org, client, login_as
):
    org = _org(make_org, "page-mixed")
    vendor = _mixed_vendor(db_session, org)
    _rate(db_session, "USD", "GBP", "0.8")
    user = _user(db_session, org)

    html, soup = _page(client, login_as, user)

    [row] = [r for r in soup.select('[data-testid="spend-vendor-row"]') if vendor.name in r.get_text()]
    assert "1,000.00 GBP" in row.select_one('[data-testid="vendor-stated"]').get_text()
    assert "1,000.00 USD" in row.select_one('[data-testid="vendor-stated"]').get_text()
    assert "1,800.00" in row.select_one('[data-testid="vendor-converted"]').get_text()
    contracts = soup.select('[data-testid="spend-contract-row"]')
    assert len(contracts) == 2
    assert {c.select_one('[data-testid="contract-stated"]').get_text().strip() for c in contracts} == {
        "1,000.00 GBP", "1,000.00 USD"
    }
    # Nothing on the page states the cross-currency sum.
    assert "2,000.00" not in html


def test_the_page_names_the_missing_rate_and_shows_a_dash(app, db_session, make_org, client, login_as):
    org = _org(make_org, "page-norate")
    vendor = _mixed_vendor(db_session, org)
    from app.models.cost_fact import ExchangeRate

    db_session.query(ExchangeRate).filter(
        ExchangeRate.from_currency.in_(["USD", "GBP"]), ExchangeRate.to_currency.in_(["USD", "GBP"])
    ).delete(synchronize_session=False)
    user = _user(db_session, org)

    html, soup = _page(client, login_as, user)

    [row] = [r for r in soup.select('[data-testid="spend-vendor-row"]') if vendor.name in r.get_text()]
    converted = row.select_one('[data-testid="vendor-converted"]').get_text(" ", strip=True)
    assert converted.startswith("—") and "No USD to GBP rate on file" in converted
    assert soup.select_one('[data-testid="spend-total-annual"]').get_text(strip=True) == "—"
    assert "No USD to GBP rate on file" in soup.select_one('[data-testid="spend-total-missing"]').get_text()
    assert "2,000.00" not in html


def test_a_user_switched_into_another_organisation_sees_that_organisations_spend(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole

    home, working = _org(make_org, "home"), _org(make_org, "working")
    _contract(db_session, home, "Home contract", annual_cost=111, vendor=_vendor(db_session, "HomeVendor"))
    _contract(db_session, working, "Working contract", annual_cost=222, vendor=_vendor(db_session, "WorkingVendor"))
    user = _user(db_session, home)
    db_session.add(OrgRole(user_id=user.id, organization_id=working.id, role="admin"))
    db_session.commit()

    html, soup = _page(client, login_as, user, session_org=working.id)

    assert "WorkingVendor" in html and "HomeVendor" not in html
    assert "Working contract" in html and "Home contract" not in html


def test_an_organisation_with_no_active_contracts_sees_the_empty_state(app, db_session, make_org, client, login_as):
    org = _org(make_org, "page-empty")
    html, _soup = _page(client, login_as, _user(db_session, org))
    assert "No active contracts with a stated annual cost" in html


# --------------------------------------------------------------------------- #
# The AI recommendations context
# --------------------------------------------------------------------------- #


def test_the_recommendation_context_never_adds_two_currencies(app, db_session, make_org):
    import json

    from app.modules.procurement.procurement_ai_service import _build_spend_context

    org = _org(make_org, "spend-ai")
    _mixed_vendor(db_session, org)
    from app.models.cost_fact import ExchangeRate

    db_session.query(ExchangeRate).filter(
        ExchangeRate.from_currency.in_(["USD", "GBP"]), ExchangeRate.to_currency.in_(["USD", "GBP"])
    ).delete(synchronize_session=False)

    text = json.dumps(_build_spend_context(org.id), default=str)
    context = json.loads(text)
    assert context["reporting_currency"] == "GBP"
    assert context["total_annual_cost"]["converted"] is None
    assert set(context["total_annual_cost"]["by_currency"]) == {"GBP", "USD"}
    assert "2000" not in text

    _rate(db_session, "USD", "GBP", "0.8")
    with_rate = _build_spend_context(org.id)
    assert Decimal(with_rate["total_annual_cost"]["converted"]) == Decimal("1800.0")
