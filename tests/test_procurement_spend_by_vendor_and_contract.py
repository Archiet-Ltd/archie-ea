"""Spend by vendor and by contract: each amount in its own currency, converted
to the organisation's reporting currency at a dated rate, never added across
currencies.

Real organisations, real rows, no mocked tenant context. The rate table is a
platform reference table, so each test clears the pairs it depends on inside
its own rolled-back transaction before it records the rate it wants.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from decimal import Decimal

import pytest
from bs4 import BeautifulSoup

pytestmark = pytest.mark.usefixtures("db_session")

DASH = "—"
TODAY = _dt.date.today()


def _org(make_org, db_session, reporting_currency="GBP"):
    org = make_org("spendcur")
    org.reporting_currency = reporting_currency
    db_session.flush()
    return org


def _user(db_session, org, role="procurement"):
    from app.models.user import User

    user = User(
        email=f"spendcur-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Spend",
        last_name="Reader",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _vendor(db_session, label="Vendor"):
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"{label} {uuid.uuid4().hex[:6]}")
    db_session.add(vendor)
    db_session.flush()
    return vendor


def _contract(db_session, org, name, *, annual_cost=None, currency="USD", vendor=None,
              contract_value=None, **extra):
    from app.models.application_portfolio import VendorContract

    contract = VendorContract(
        organization_id=org.id,
        contract_name=f"{name} {uuid.uuid4().hex[:6]}",
        status=extra.pop("status", "active"),
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


def _app(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    app_row = ApplicationComponent(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(app_row)
    db_session.flush()
    return app_row


def _link(db_session, org, contract, application):
    from app.models.contract_application import ContractApplication

    db_session.add(
        ContractApplication(
            contract_id=contract.id, application_id=application.id, organization_id=org.id
        )
    )
    db_session.flush()


def _no_rates(db_session, *pairs):
    from app.models.cost_fact import ExchangeRate

    for frm, to in pairs:
        for row in ExchangeRate.query.filter(
            ExchangeRate.from_currency.in_([frm, to]), ExchangeRate.to_currency.in_([frm, to])
        ).all():
            db_session.delete(row)
    db_session.flush()


def _rate(db_session, frm, to, rate, on=TODAY):
    from app.models.cost_fact import ExchangeRate

    db_session.add(
        ExchangeRate(from_currency=frm, to_currency=to, rate=Decimal(str(rate)), effective_date=on)
    )
    db_session.flush()


def _summary(org, **kw):
    from app.modules.procurement.services import get_spend_summary

    return get_spend_summary(org.id, **kw)


def _vendor_row(summary, vendor):
    rows = [r for r in summary["by_vendor"] if r["vendor_name"] == vendor.name]
    assert len(rows) == 1, summary["by_vendor"]
    return rows[0]


def _page(client, login_as, user):
    login_as(client, user)
    response = client.get("/procurement/spend")
    assert response.status_code == 200, response.get_data(as_text=True)[:1500]
    return BeautifulSoup(response.get_data(as_text=True), "html.parser")


# 1 ---------------------------------------------------------------------------


def test_a_vendor_with_two_currencies_shows_both_and_a_converted_figure(db_session, make_org):
    org = _org(make_org, db_session)
    vendor = _vendor(db_session)
    _contract(db_session, org, "Pounds", annual_cost=1000, currency="GBP", vendor=vendor)
    _contract(db_session, org, "Dollars", annual_cost=1000, currency="USD", vendor=vendor)
    _no_rates(db_session, ("USD", "GBP"))
    _rate(db_session, "USD", "GBP", "0.8")

    summary = _summary(org)
    row = _vendor_row(summary, vendor)

    assert summary["reporting_currency"] == "GBP"
    assert row["by_currency"] == {"GBP": 1000, "USD": 1000}
    assert float(row["converted"]) == pytest.approx(1800)
    assert row["missing_rates"] == []
    assert row["contract_count"] == 2
    assert float(summary["total"]["converted"]) == pytest.approx(1800)


def test_without_the_rate_the_converted_figure_is_a_dash_naming_the_currency(
    db_session, make_org, client, login_as
):
    org = _org(make_org, db_session)
    user = _user(db_session, org)
    vendor = _vendor(db_session)
    _contract(db_session, org, "Pounds", annual_cost=1000, currency="GBP", vendor=vendor)
    _contract(db_session, org, "Dollars", annual_cost=1000, currency="USD", vendor=vendor)
    _no_rates(db_session, ("USD", "GBP"))

    row = _vendor_row(_summary(org), vendor)
    assert row["by_currency"] == {"GBP": 1000, "USD": 1000}
    assert row["converted"] is None
    assert [pair[0] for pair in row["missing_rates"]] == ["USD"]

    soup = _page(client, login_as, user)
    text = " ".join(soup.get_text().split())
    assert "No USD to GBP rate on file" in text
    assert "2,000" not in text, "the page added GBP and USD together"
    vendor_cells = next(
        tr for tr in soup.select("table[data-testid='spend-by-vendor'] tbody tr")
        if vendor.name in tr.get_text()
    ).find_all("td")
    assert DASH in [" ".join(td.get_text().split()) for td in vendor_cells]


# 2 ---------------------------------------------------------------------------


def test_the_page_total_is_convert_total_and_no_figure_is_the_raw_cross_currency_sum(
    db_session, make_org, client, login_as
):
    from app.services.currency_service import convert_total

    org = _org(make_org, db_session)
    user = _user(db_session, org)
    vendor = _vendor(db_session)
    c1 = _contract(db_session, org, "Pounds", annual_cost=1234, currency="GBP", vendor=vendor)
    c2 = _contract(db_session, org, "Dollars", annual_cost=2000, currency="USD", vendor=vendor)
    c3 = _contract(db_session, org, "Euros", annual_cost=100, currency="EUR", vendor=vendor)
    _no_rates(db_session, ("USD", "GBP"), ("EUR", "GBP"))
    _rate(db_session, "USD", "GBP", "0.8")
    _rate(db_session, "EUR", "GBP", "0.9")

    class _Fact:
        def __init__(self, c):
            self.amount, self.currency = c.annual_cost, c.currency
            self.period_start, self.period_end = None, TODAY

    expected = convert_total([_Fact(c) for c in (c1, c2, c3)], "GBP")
    summary = _summary(org)
    assert summary["total"]["converted"] == expected.amount
    assert float(expected.amount) == pytest.approx(1234 + 1600 + 90)

    text = " ".join(_page(client, login_as, user).get_text().split())
    assert "2,924.00" in text  # the converted figure
    for raw_sum in ("3,334", "3,334.00"):  # 1234 + 2000 + 100 across currencies
        assert raw_sum not in text


def test_the_by_type_figure_is_not_a_cross_currency_sum(db_session, make_org):
    org = _org(make_org, db_session)
    _contract(db_session, org, "Pounds", annual_cost=1000, currency="GBP", contract_type="license")
    _contract(db_session, org, "Dollars", annual_cost=1000, currency="USD", contract_type="license")
    _no_rates(db_session, ("USD", "GBP"))

    by_type = _summary(org)["by_type"]["license"]
    assert by_type["count"] == 2
    assert by_type["by_currency"] == {"GBP": 1000, "USD": 1000}
    assert by_type["annual_cost"] is None and by_type["converted"] is None


# 3 ---------------------------------------------------------------------------


def _two_orgs(db_session, make_org):
    org_a, org_b = _org(make_org, db_session), _org(make_org, db_session)
    vendor_a, vendor_b = _vendor(db_session, "VendorA"), _vendor(db_session, "VendorB")
    app_a, app_b = _app(db_session, org_a, "AppA"), _app(db_session, org_b, "AppB")
    contract_a = _contract(db_session, org_a, "ContractA", annual_cost=100, currency="GBP",
                           vendor=vendor_a, contract_category="software", contract_value=500)
    contract_b = _contract(db_session, org_b, "ContractB", annual_cost=7777, currency="GBP",
                           vendor=vendor_b, contract_category="hardware", contract_value=9999)
    _link(db_session, org_a, contract_a, app_a)
    _link(db_session, org_b, contract_b, app_b)
    return org_a, org_b, vendor_a, vendor_b, app_a, app_b, contract_a, contract_b


def test_one_organisations_figures_never_include_the_others(db_session, make_org):
    from app.modules.procurement.services import get_spend_by_category

    org_a, org_b, vendor_a, vendor_b, app_a, app_b, contract_a, contract_b = _two_orgs(
        db_session, make_org
    )

    summary = _summary(org_a)
    assert [r["vendor_name"] for r in summary["by_vendor"]] == [vendor_a.name]
    assert [r["contract_name"] for r in summary["by_contract"]] == [contract_a.contract_name]
    assert summary["by_contract"][0]["applications"] == [app_a.name]
    assert summary["total"]["by_currency"] == {"GBP": 100}
    assert summary["total_contracts"] == 1
    blob = repr(summary)
    for foreign in (vendor_b.name, contract_b.contract_name, app_b.name, "7777"):
        assert foreign not in blob

    assert list(get_spend_by_category(org_a.id)) == ["software"]
    assert list(get_spend_by_category(org_b.id)) == ["hardware"]


def test_a_user_switched_into_another_organisation_sees_that_organisations_spend(
    db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole

    org_a, org_b, vendor_a, vendor_b, *_ = _two_orgs(db_session, make_org)
    visitor = _user(db_session, org_b)  # home organisation is B
    db_session.add(OrgRole(organization_id=org_a.id, user_id=visitor.id, role="viewer"))
    db_session.flush()

    login_as(client, visitor)
    home = client.get("/procurement/spend").get_data(as_text=True)
    assert vendor_b.name in home and vendor_a.name not in home

    switched = client.post("/account/switch-organization", data={"organization_id": str(org_a.id)},
                           follow_redirects=True)
    assert switched.status_code == 200
    login_as(client, visitor)  # drop cached request state; the switch lives in the session
    page = client.get("/procurement/spend").get_data(as_text=True)
    assert vendor_a.name in page, "the spend page read the home organisation"
    assert vendor_b.name not in page and "7,777" not in page


def test_with_no_active_organisation_both_functions_answer_empty(db_session, make_org):
    from app.modules.procurement.services import get_spend_by_category, get_spend_summary

    _two_orgs(db_session, make_org)  # data exists; none of it may be returned

    summary = get_spend_summary(None)
    assert summary["total_contracts"] == 0
    assert summary["by_vendor"] == [] and summary["by_contract"] == [] and summary["by_type"] == {}
    assert summary["total"]["by_currency"] == {} and summary["total"]["converted"] is None
    assert get_spend_by_category(None) == {}


def test_the_spend_page_for_an_empty_organisation_shows_the_empty_state(
    db_session, make_org, client, login_as
):
    org = _org(make_org, db_session)
    user = _user(db_session, org)
    text = " ".join(_page(client, login_as, user).get_text().split())
    assert "No active contracts with a stated annual cost" in text


# 4 ---------------------------------------------------------------------------


def test_covered_applications_merge_the_links_and_the_legacy_link_without_repeats(
    db_session, make_org, client, login_as
):
    org = _org(make_org, db_session)
    user = _user(db_session, org)
    vendor = _vendor(db_session)
    first, second = _app(db_session, org, "First"), _app(db_session, org, "Second")
    covered = _contract(db_session, org, "Covered", annual_cost=10, currency="GBP", vendor=vendor,
                        application_id=first.id)
    alone = _contract(db_session, org, "Alone", annual_cost=5, currency="GBP", vendor=vendor)
    _link(db_session, org, covered, first)
    _link(db_session, org, covered, second)

    summary = _summary(org)
    rows = {r["contract_name"]: r for r in summary["by_contract"]}
    assert sorted(rows[covered.contract_name]["applications"]) == sorted([first.name, second.name])
    assert rows[alone.contract_name]["applications"] == []
    assert _vendor_row(summary, vendor)["covered_application_count"] == 2

    table = _page(client, login_as, user).select_one("table[data-testid='spend-by-contract']")
    cells = {
        tr.find_all("td")[0].get_text().strip(): [" ".join(td.get_text().split()) for td in tr.find_all("td")]
        for tr in table.select("tbody tr")
    }
    alone_cells = next(v for k, v in cells.items() if k.startswith("Alone"))
    covered_cells = next(v for k, v in cells.items() if k.startswith("Covered"))
    assert DASH in alone_cells
    assert any(first.name in c and second.name in c for c in covered_cells)


def test_each_contract_row_names_its_source_and_truth_class(db_session, make_org):
    from app.modules.intelligence.services.query_catalogue import truth_class_for

    org = _org(make_org, db_session)
    contract = _contract(db_session, org, "Sourced", annual_cost=1, currency="GBP")
    row = _summary(org)["by_contract"][0]
    assert row["source_table"] == "vendor_contracts"
    assert row["truth_class"] == truth_class_for("vendor_contracts")
    assert row["contract_name"] == contract.contract_name


# 5 ---------------------------------------------------------------------------


def test_a_contract_with_no_value_is_left_out_of_the_category_and_counted(db_session, make_org):
    from app.modules.procurement.services import get_spend_by_category

    org = _org(make_org, db_session)
    _contract(db_session, org, "Valued", contract_value=500, currency="GBP",
              contract_category="software")
    _contract(db_session, org, "Unvalued", currency="GBP", contract_category="software")

    software = get_spend_by_category(org.id)["software"]
    assert software["by_currency"] == {"GBP": 500}
    assert software["contracts_without_value"] == 1
    assert software["converted"] == Decimal(500)


def test_a_category_with_no_stated_value_has_no_figure_not_zero(db_session, make_org):
    from app.modules.procurement.services import get_spend_by_category

    org = _org(make_org, db_session)
    _contract(db_session, org, "Unvalued", currency="GBP", contract_category="services")

    services = get_spend_by_category(org.id)["services"]
    assert services["by_currency"] == {}
    assert services["converted"] is None
    assert services["contracts_without_value"] == 1


# 6 ---------------------------------------------------------------------------


def test_the_recommendations_context_never_adds_two_currencies(db_session, make_org):
    from app.modules.procurement.procurement_ai_service import _build_spend_context

    org = _org(make_org, db_session)
    vendor = _vendor(db_session)
    _contract(db_session, org, "Pounds", annual_cost=1234, currency="GBP", vendor=vendor,
              contract_value=4321, contract_type="license", contract_category="software")
    _contract(db_session, org, "Dollars", annual_cost=2000, currency="USD", vendor=vendor,
              contract_value=1111, contract_type="license", contract_category="software")
    _no_rates(db_session, ("USD", "GBP"))

    context = _build_spend_context(org.id)
    flat = repr(context)
    for cross_sum in ("3234", "5432"):  # 1234+2000 and 4321+1111
        assert cross_sum not in flat
    assert context["reporting_currency"] == "GBP"
    assert context["total_annual_cost"]["by_currency"] == {"GBP": 1234, "USD": 2000}
    assert context["total_annual_cost"]["converted"] is None
    assert context["top_vendors"][0]["by_currency"] == {"GBP": 1234, "USD": 2000}

    _rate(db_session, "USD", "GBP", "0.5")
    assert _build_spend_context(org.id)["total_annual_cost"]["converted"] == pytest.approx(2234)


# 5 ---------------------------------------------------------------------------


def test_recommendations_use_the_active_organisation_after_a_switch(
    db_session, make_org, client, login_as, monkeypatch
):
    from app.models.org_role import OrgRole
    from app.modules.procurement import ai_routes

    org_a, org_b, *_ = _two_orgs(db_session, make_org)
    visitor = _user(db_session, org_b)  # home organisation is B
    db_session.add(OrgRole(organization_id=org_a.id, user_id=visitor.id, role="viewer"))
    db_session.flush()

    asked = []

    def fake(organization_id):
        asked.append(organization_id)
        return {"recommendations": [], "summary": ""}

    monkeypatch.setattr(ai_routes, "generate_spend_recommendations", fake)
    monkeypatch.setattr(ai_routes.FeatureFlagService, "require_ai_for_route",
                        staticmethod(lambda *a, **k: None))

    login_as(client, visitor)
    switched = client.post("/account/switch-organization", data={"organization_id": str(org_a.id)},
                           follow_redirects=True)
    assert switched.status_code == 200
    login_as(client, visitor)
    response = client.post("/procurement/api/spend/ai-recommendations")
    assert response.status_code == 200, response.get_data(as_text=True)[:500]
    assert asked == [org_a.id], "recommendations were built for the home organisation"


# 6 ---------------------------------------------------------------------------


def test_rate_lookups_do_not_grow_with_the_number_of_contracts(db_session, make_org):
    from sqlalchemy import event

    org = _org(make_org, db_session)
    vendor = _vendor(db_session)
    _no_rates(db_session, ("USD", "GBP"), ("EUR", "GBP"))
    _rate(db_session, "USD", "GBP", "0.8")
    _rate(db_session, "EUR", "GBP", "0.9")

    def statements_for(contract_count):
        for i in range(contract_count):
            _contract(db_session, org, "Many%d" % i, annual_cost=10 + i,
                      currency="USD" if i % 2 else "EUR", vendor=vendor,
                      contract_value=20 + i, contract_type="license")
        seen = []

        def count(conn, cursor, statement, *a):
            if "exchange_rates" in statement:
                seen.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", count)
        try:
            summary = _summary(org)
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert summary["total"]["converted"] is not None
        return len(seen)

    few = statements_for(4)
    many = statements_for(40)
    assert many == few, "rate queries grew from %d to %d with the contract count" % (few, many)
    assert many <= 8


# 7 ---------------------------------------------------------------------------


def test_two_distinct_applications_with_one_name_count_as_two(db_session, make_org):
    org = _org(make_org, db_session)
    vendor = _vendor(db_session)
    first, second = _app(db_session, org, "Same"), _app(db_session, org, "Same")
    second.name = first.name
    db_session.flush()
    one = _contract(db_session, org, "One", annual_cost=10, currency="GBP", vendor=vendor)
    two = _contract(db_session, org, "Two", annual_cost=10, currency="GBP", vendor=vendor)
    _link(db_session, org, one, first)
    _link(db_session, org, two, second)

    summary = _summary(org)
    assert _vendor_row(summary, vendor)["covered_application_count"] == 2
    rows = {r["contract_name"]: r for r in summary["by_contract"]}
    assert rows[one.contract_name]["applications"] == [first.name]
