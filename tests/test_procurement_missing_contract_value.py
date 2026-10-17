"""The Contracts register and the Spend page agree about a contract's amounts.

A contract with no annual cost is a dash on the register. The Spend page used to
add it into the annual total as zero and print "GBP 0.00" for it, so the two
screens told a procurement lead different things about the same record.

This file asks the screens rather than the code: it seeds one tenant, signs in
as the procurement user over HTTP, reads the figures off both rendered pages and
asserts they agree, that a missing amount is a dash on both, and that a total
says how many contracts it leaves out.

Uses the shared fixtures in tests/conftest.py (db_session rolls everything back).
"""

from __future__ import annotations

import datetime as _dt
import uuid

import pytest
from bs4 import BeautifulSoup

pytestmark = pytest.mark.usefixtures("db_session")

DASH = "—"


def _usd_org(make_org, db_session, label):
    """An organisation reporting in USD, the currency these contracts are recorded in."""
    org = make_org(label)
    org.reporting_currency = "USD"
    db_session.flush()
    return org


def _procurement_user(db_session, org):
    from app.models.user import User

    user = User(
        email=f"spend-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Procurement",
        last_name="Lead",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="procurement",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _contract(db_session, org, name, contract_value=None, annual_cost=None, **extra):
    from app.models.application_portfolio import VendorContract

    contract = VendorContract(
        organization_id=org.id,
        contract_name=f"{name} {uuid.uuid4().hex[:6]}",
        status="active",
        start_date=_dt.date.today(),
        contract_value=contract_value,
        annual_cost=annual_cost,
        **extra,
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _soup(client, login_as, user, path):
    login_as(client, user)
    response = client.get(path)
    assert response.status_code == 200, response.get_data(as_text=True)[:1500]
    return BeautifulSoup(response.get_data(as_text=True), "html.parser")


def _register_annual_cost(soup, contract):
    """What the Annual Cost cell says on the register row for this contract."""
    for row in soup.select("tbody tr"):
        cells = row.find_all("td")
        if contract.contract_name in cells[0].get_text():
            return " ".join(cells[2].get_text().split())
    raise AssertionError(f"the register has no row for {contract.contract_name!r}")


def _tile(soup, label):
    """The figure and the note beneath it in the Spend summary tile with this label."""
    label_div = soup.find("div", string=lambda s: s and s.strip() == label)
    assert label_div is not None, f"the Spend page has no {label!r} tile"
    tile = label_div.find_parent("div", class_="rounded-lg")
    figure = " ".join(tile.find("div", class_="text-3xl").get_text().split())
    note = tile.find("p")
    return figure, (" ".join(note.get_text().split()) if note else None)


def _is_zero_amount(text):
    """True for a printed amount that is a zero, whatever its symbol or code."""
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    return digits != "" and float(digits) == 0


def _by_type_amounts(soup):
    """The amount printed on each Spend by Contract Type row."""
    heading = soup.find("h2", string=lambda s: s and s.strip() == "Spend by Contract Type")
    panel = heading.find_parent("div", class_="rounded-lg")
    return [
        " ".join(span.get_text().split())
        for span in panel.find_all("span", class_="font-medium")
    ]


def test_a_contract_without_an_annual_cost_is_a_dash_on_both_screens(
    client, login_as, db_session, make_org
):
    org = _usd_org(make_org, db_session, "spend-missing")
    user = _procurement_user(db_session, org)
    with_cost = _contract(db_session, org, "Priced", contract_value=50000, annual_cost=12000)
    without_cost = _contract(db_session, org, "Unpriced", contract_value=100000)

    register = _soup(client, login_as, user, "/procurement/contracts")
    spend = _soup(client, login_as, user, "/procurement/spend")

    assert _register_annual_cost(register, without_cost) == DASH

    # The contract that states a cost shows one figure, formatted one way, and
    # the Spend page's annual total is that figure: the unpriced contract adds
    # nothing to it, not a zero.
    figure = _register_annual_cost(register, with_cost)
    assert figure != DASH and "12,000.00" in figure
    annual_tile, annual_note = _tile(spend, "Annual Recurring Cost")
    assert "12,000.00" in annual_tile and annual_tile.endswith("USD"), annual_tile
    assert _by_type_amounts(spend) == ["12,000.00 USD/yr"]

    assert annual_note == "excludes 1 contract without an annual cost"
    contract_value_tile, contract_value_note = _tile(spend, "Total Contract Value")
    assert "150,000.00" in contract_value_tile
    assert contract_value_note is None

    # No zero amount anywhere in the figures those rows and tiles print.
    printed = [annual_tile, contract_value_tile, *_by_type_amounts(spend)]
    assert not [text for text in printed if _is_zero_amount(text)], printed


def test_totals_name_how_many_contracts_they_leave_out(client, login_as, db_session, make_org):
    org = _usd_org(make_org, db_session, "spend-excluded")
    user = _procurement_user(db_session, org)
    _contract(db_session, org, "Priced", contract_value=50000, annual_cost=12000)
    _contract(db_session, org, "No cost one", contract_value=1000)
    _contract(db_session, org, "No cost two")

    spend = _soup(client, login_as, user, "/procurement/spend")

    assert _tile(spend, "Annual Recurring Cost")[1] == "excludes 2 contracts without an annual cost"
    assert _tile(spend, "Total Contract Value")[1] == "excludes 1 contract without a contract value"


def test_a_total_nothing_states_is_a_dash_not_a_zero(client, login_as, db_session, make_org):
    org = _usd_org(make_org, db_session, "spend-none-stated")
    user = _procurement_user(db_session, org)
    unpriced = _contract(db_session, org, "Unpriced")

    register = _soup(client, login_as, user, "/procurement/contracts")
    spend = _soup(client, login_as, user, "/procurement/spend")

    assert _register_annual_cost(register, unpriced) == DASH
    assert _tile(spend, "Annual Recurring Cost") == (
        DASH,
        "excludes 1 contract without an annual cost",
    )
    assert _tile(spend, "Total Contract Value") == (
        DASH,
        "excludes 1 contract without a contract value",
    )
    assert _by_type_amounts(spend) == [DASH]


def test_a_stated_zero_is_an_amount_on_both_screens(client, login_as, db_session, make_org):
    org = _usd_org(make_org, db_session, "spend-stated-zero")
    user = _procurement_user(db_session, org)
    free = _contract(db_session, org, "Free tier", contract_value=0, annual_cost=0)

    register = _soup(client, login_as, user, "/procurement/contracts")
    spend = _soup(client, login_as, user, "/procurement/spend")

    figure = _register_annual_cost(register, free)
    assert figure != DASH and figure.endswith("0.00")
    annual_tile, annual_note = _tile(spend, "Annual Recurring Cost")
    assert annual_tile == "0.00 USD"
    assert annual_note is None
    assert _tile(spend, "Total Contract Value")[1] is None


def test_vendor_rows_do_not_rank_or_total_a_missing_cost_as_zero(
    client, login_as, db_session, make_org
):
    from app.models.vendor.vendor_organization import VendorOrganization

    org = _usd_org(make_org, db_session, "spend-vendors")
    user = _procurement_user(db_session, org)
    priced_vendor = VendorOrganization(name=f"Priced Vendor {uuid.uuid4().hex[:6]}")
    unpriced_vendor = VendorOrganization(name=f"Unpriced Vendor {uuid.uuid4().hex[:6]}")
    db_session.add_all([priced_vendor, unpriced_vendor])
    db_session.flush()
    _contract(db_session, org, "Priced", annual_cost=12000, vendor_id=priced_vendor.id)
    _contract(db_session, org, "Unpriced", vendor_id=unpriced_vendor.id)

    spend = _soup(client, login_as, user, "/procurement/spend")

    table = spend.select_one("table[data-testid='spend-by-vendor']")
    rows = [
        [" ".join(td.get_text().split()) for td in tr.find_all("td")]
        for tr in table.select("tbody tr")
    ]
    assert [row[0] for row in rows] == [priced_vendor.name, unpriced_vendor.name]
    # as recorded, then in the reporting currency
    assert rows[0][2] == "12,000.00 USD" and rows[0][3] == "12,000.00 USD"
    assert rows[1][2] == DASH and rows[1][3] == DASH


def test_both_screens_read_a_contracts_amounts_from_one_function(db_session, make_org):
    from app.modules.procurement.services import get_contract_amounts, total_of_stated

    org = make_org("spend-source")
    unpriced = _contract(db_session, org, "Unpriced", contract_value=100000)
    zero = _contract(db_session, org, "Zero", annual_cost=0)

    assert get_contract_amounts(unpriced) == {"contract_value": 100000, "annual_cost": None}
    assert get_contract_amounts(zero) == {"contract_value": None, "annual_cost": 0}

    assert total_of_stated([None, None]) == (None, 2)
    assert total_of_stated([0, None]) == (0, 1)
    assert total_of_stated([5, None, 7]) == (12, 1)
    assert total_of_stated([]) == (None, 0)
