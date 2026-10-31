"""The Contracts register and the Spend page agree about a contract's amounts.

A contract with no annual cost says "Not set" on the register. The Spend page
used to add it into the annual total as zero and print "GBP 0.00" for it, so the
two screens told a procurement lead different things about the same record.

This file asks the screens rather than the code: it seeds one tenant, signs in
as the procurement user over HTTP, reads the figures off both rendered pages and
asserts they agree, that a missing amount is stated in words on both (never a
zero), and that a total says how many contracts it leaves out.

The register prints an amount in full ("£12,000.00"); the Spend page's headline
figures are abbreviated the way the Overview writes them ("£12k"). The two are
compared as amounts, so they must still be the same figure.

Uses the shared fixtures in tests/conftest.py (db_session rolls everything back).
"""

from __future__ import annotations

import datetime as _dt
import re
import uuid

import pytest
from bs4 import BeautifulSoup

pytestmark = pytest.mark.usefixtures("db_session")

NOT_SET = "Not set"
NO_ANNUAL_COST = "No annual cost stated"


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


def _text(node):
    return " ".join(node.get_text().split()) if node is not None else None


def _register_annual_cost(soup, contract):
    """What the Annual cost cell says on the register row for this contract."""
    for row in soup.select('[data-testid="contracts-table"] tbody tr[data-row]'):
        cells = row.find_all("td")
        if contract.contract_name in cells[0].get_text():
            return _text(cells[2])
    raise AssertionError(f"the register has no row for {contract.contract_name!r}")


def _tile(soup, testid):
    """The Spend stat card with this test id: (label, figure or None, sentence)."""
    card = soup.select_one(f'[data-testid="{testid}"]')
    assert card is not None, f"the Spend page has no {testid!r} card"
    figure = card.select_one(f'[data-testid="{testid}-value"]')
    spans = card.select("[data-slot=card-content] > span")
    label = _text(spans[1] if figure is not None else spans[0])
    sentence = _text(card.select_one("span.text-muted-foreground"))
    return label, _text(figure), sentence


def _amount(text):
    """The amount a printed figure states, whether written in full or as 12k / 1.5m."""
    match = re.search(r"(-?[\d,]+(?:\.\d+)?)\s*([km])?", text)
    assert match, f"no amount in {text!r}"
    value = float(match.group(1).replace(",", ""))
    return value * {"k": 1000, "m": 1000000}.get(match.group(2), 1)


def _by_type_amounts(soup):
    """The amount printed on each Spend by contract type row."""
    return [_text(row.find_all("span", recursive=False)[-1])
            for row in soup.select('[data-testid="spend-type-row"]')]


def test_a_contract_without_an_annual_cost_is_not_set_on_both_screens(
    client, login_as, db_session, make_org
):
    org = make_org("spend-missing")
    user = _procurement_user(db_session, org)
    with_cost = _contract(db_session, org, "Priced", contract_value=50000, annual_cost=12000)
    without_cost = _contract(db_session, org, "Unpriced", contract_value=100000)

    register = _soup(client, login_as, user, "/procurement/contracts")
    spend = _soup(client, login_as, user, "/procurement/spend")

    assert _register_annual_cost(register, without_cost) == NOT_SET

    # The contract that states a cost shows one figure, and the Spend page's
    # annual total is that figure: the unpriced contract adds nothing to it,
    # not a zero.
    figure = _register_annual_cost(register, with_cost)
    assert figure != NOT_SET and "12,000.00" in figure
    label, annual_figure, annual_note = _tile(spend, "spend-annual-cost")
    assert label == "Annual recurring cost"
    assert _amount(annual_figure) == _amount(figure) == 12000
    assert [_amount(a) for a in _by_type_amounts(spend)] == [12000]
    assert all(a.endswith("a year") for a in _by_type_amounts(spend))

    assert annual_note == "Leaves out 1 contract with no annual cost"
    label, value_figure, value_note = _tile(spend, "spend-total-value")
    assert label == "Total contract value"
    assert _amount(value_figure) == 150000
    assert value_note == "The full value of every active contract"

    # No zero amount anywhere in the figures those rows and cards print.
    printed = [annual_figure, value_figure, *_by_type_amounts(spend)]
    assert not [text for text in printed if _amount(text) == 0], printed


def test_totals_name_how_many_contracts_they_leave_out(client, login_as, db_session, make_org):
    org = make_org("spend-excluded")
    user = _procurement_user(db_session, org)
    _contract(db_session, org, "Priced", contract_value=50000, annual_cost=12000)
    _contract(db_session, org, "No cost one", contract_value=1000)
    _contract(db_session, org, "No cost two")

    spend = _soup(client, login_as, user, "/procurement/spend")

    assert _tile(spend, "spend-annual-cost")[2] == "Leaves out 2 contracts with no annual cost"
    assert _tile(spend, "spend-total-value")[2] == "Leaves out 1 contract with no stated value"


def test_a_total_nothing_states_is_said_in_words_not_a_zero(client, login_as, db_session, make_org):
    org = make_org("spend-none-stated")
    user = _procurement_user(db_session, org)
    unpriced = _contract(db_session, org, "Unpriced")

    register = _soup(client, login_as, user, "/procurement/contracts")
    spend = _soup(client, login_as, user, "/procurement/spend")

    assert _register_annual_cost(register, unpriced) == NOT_SET
    assert _tile(spend, "spend-annual-cost") == (
        "No annual cost recorded yet",
        None,
        "Add the annual cost to a contract and the yearly total shows here",
    )
    assert _tile(spend, "spend-total-value") == (
        "No contract value recorded yet",
        None,
        "Add the total value to a contract and the sum shows here",
    )
    assert _by_type_amounts(spend) == [NO_ANNUAL_COST]


def test_a_stated_zero_is_an_amount_on_both_screens(client, login_as, db_session, make_org):
    org = make_org("spend-stated-zero")
    user = _procurement_user(db_session, org)
    free = _contract(db_session, org, "Free tier", contract_value=0, annual_cost=0)

    register = _soup(client, login_as, user, "/procurement/contracts")
    spend = _soup(client, login_as, user, "/procurement/spend")

    figure = _register_annual_cost(register, free)
    assert figure != NOT_SET and figure.endswith("0.00")
    label, annual_figure, annual_note = _tile(spend, "spend-annual-cost")
    assert label == "Annual recurring cost"
    assert annual_figure is not None and _amount(annual_figure) == _amount(figure) == 0
    assert annual_note == "Every active contract states its annual cost"
    assert _tile(spend, "spend-total-value")[2] == "The full value of every active contract"


def test_vendor_rows_do_not_rank_or_total_a_missing_cost_as_zero(
    client, login_as, db_session, make_org
):
    from app.models.vendor.vendor_organization import VendorOrganization

    org = make_org("spend-vendors")
    user = _procurement_user(db_session, org)
    priced_vendor = VendorOrganization(name=f"Priced Vendor {uuid.uuid4().hex[:6]}")
    unpriced_vendor = VendorOrganization(name=f"Unpriced Vendor {uuid.uuid4().hex[:6]}")
    db_session.add_all([priced_vendor, unpriced_vendor])
    db_session.flush()
    _contract(db_session, org, "Priced", annual_cost=12000, vendor_id=priced_vendor.id)
    _contract(db_session, org, "Unpriced", vendor_id=unpriced_vendor.id)

    spend = _soup(client, login_as, user, "/procurement/spend")

    rows = []
    for row in spend.select('[data-testid="spend-top-vendors"] [data-testid="spend-vendor-row"]'):
        spans = row.find_all("span", recursive=False)
        rows.append([_text(span) for span in spans][:2])
    assert [name for name, _ in rows] == [priced_vendor.name, unpriced_vendor.name]
    assert rows[0][1].endswith("a year") and _amount(rows[0][1]) == 12000
    assert rows[1][1] == NO_ANNUAL_COST


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
