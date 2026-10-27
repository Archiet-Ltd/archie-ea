"""The same application's cost is the same figure on every screen that shows it.

Three applications: one with its cost broken down, one with a line a person added, one recorded before the cost
store existed. Each screen is opened as a person would open it (or, where a screen has no page of its own, through
the service it renders) and its figure is compared with the Cost tab's. A screen that reads anything else fails here.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

EXPECTED = {"Ledger": Decimal("72000.00"), "Payments": Decimal("10400.00"), "Archive": Decimal("5000.00")}


@pytest.fixture
def portfolio(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.cost_fact import ELEMENT_APPLICATION, CostFact
    from app.models.user import Permission, Role, User
    from app.services.cost_fact_store import upsert_fact, year_period

    org = make_org("costs")
    org.reporting_currency = "GBP"
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    user = User(email=f"cost-{uuid.uuid4().hex[:8]}@example.com", first_name="Ada", last_name="Okafor",
                organization_id=org.id, role=role, confirmed=True)
    db_session.add(user)
    apps = {
        "Ledger": ApplicationComponent(name="Ledger", organization_id=org.id, lifecycle_status="operational",
                                       license_cost_annual=Decimal("60000"), infrastructure_cost_monthly=Decimal("1000"),
                                       total_cost_of_ownership=Decimal("72000")),
        "Payments": ApplicationComponent(name="Payments", organization_id=org.id, lifecycle_status="operational",
                                         total_cost_of_ownership=Decimal("10000")),
        "Archive": ApplicationComponent(name="Archive", organization_id=org.id, lifecycle_status="operational",
                                        total_cost_of_ownership=Decimal("5000")),
    }
    db_session.add_all(apps.values())
    db_session.flush()
    # Each was scored for rationalisation before the person's line was added, so the stored score holds the old
    # figure; the dossier must still show today's.
    from app.models.application_rationalization import ApplicationRationalizationScore

    for app in apps.values():
        db_session.add(ApplicationRationalizationScore(
            application_component_id=app.id, organization_id=org.id, overall_health_score=60,
            rationalization_action="TOLERATE",
            total_cost_of_ownership=app.total_cost_of_ownership))
    # A person's line, as the Cost tab's Add cost writes it.
    start, end = year_period()
    upsert_fact(org.id, ELEMENT_APPLICATION, apps["Payments"].id, Decimal("400"), "GBP", "person",
                category="support", period="annual", period_start=start, period_end=end,
                source_table="users", source_id=f"{user.id}:agree")
    # Archive was recorded before the store existed: its fields hold the cost, no fact does.
    CostFact.query.filter_by(organization_id=org.id, element_type=ELEMENT_APPLICATION,
                             element_id=apps["Archive"].id).delete()
    db_session.flush()
    return org, user, apps


def _money(text):
    return Decimal(text.replace(",", ""))


def test_the_cost_tab_shows_the_three_figures(portfolio, client, login_as):
    org, user, apps = portfolio
    for name, app in apps.items():
        login_as(client, user)
        html = client.get(f"/applications/{app.id}?tab=cost").get_data(as_text=True)
        found = re.search(r'data-testid="cost-total"[^>]*>\s*([\d,]+\.\d{2}) GBP', html)
        assert found, f"{name}: no total on the Cost tab"
        assert _money(found.group(1)) == EXPECTED[name], name


def test_the_accessor_every_screen_reads_gives_the_cost_tab_figure(portfolio):
    from app.services.application_cost_accessor import (
        get_annual_cost,
        get_annual_cost_with_source,
        get_annual_costs,
    )
    from app.services.cost_view import application_cost

    org, _, apps = portfolio
    batch = get_annual_costs(org.id, list(apps.values()))
    for name, app in apps.items():
        tab = application_cost(org.id, app.id).total
        assert tab == EXPECTED[name], name
        assert get_annual_cost(app) == tab, name
        assert batch[app.id] == tab, name
        assert Decimal(str(get_annual_cost_with_source(app)[0])) == tab, name


def test_the_fact_sheet_agrees(portfolio, client, login_as):
    _, user, apps = portfolio
    for name, app in apps.items():
        login_as(client, user)
        html = client.get(f"/applications/{app.id}/fact-sheet").get_data(as_text=True)
        assert f"{EXPECTED[name]:,.0f}" in html, f"{name}: fact sheet does not show {EXPECTED[name]:,.0f}"


def _find(payload, key):
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = _find(value, key)
            if found is not None:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _find(value, key)
            if found is not None:
                return found
    return None


def test_the_rationalisation_dossier_agrees(portfolio, client, login_as):
    _, user, apps = portfolio
    for name, app in apps.items():
        login_as(client, user)
        resp = client.get(f"/applications/rationalization/api/decision-dossier/{app.id}")
        assert resp.status_code == 200, (name, resp.status_code)
        financial = _find(resp.get_json(), "financial")
        assert Decimal(str(financial["total_cost_of_ownership"])) == EXPECTED[name], name


def test_the_consolidation_list_agrees(portfolio, client, login_as):
    _, user, apps = portfolio
    login_as(client, user)
    resp = client.post("/consolidation-list/api/add", json={"application_ids": [a.id for a in apps.values()]})
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)[:300]
    login_as(client, user)
    entries = _find(client.get("/consolidation-list/api/entries").get_json(), "entries") or []
    by_app = {e["application_id"]: e for e in entries}
    for name, app in apps.items():
        entry = by_app[app.id]
        assert Decimal(str(entry["estimated_savings"])) == EXPECTED[name], f"{name}: added with another cost"
        login_as(client, user)
        detail = client.get(f"/consolidation-list/api/entry/{entry['id']}/detail").get_json()
        assert Decimal(str(detail["financial"]["total_annual_cost"])) == EXPECTED[name], name


def test_the_application_picker_agrees(portfolio, client, login_as):
    _, user, apps = portfolio
    login_as(client, user)
    rows = {r["id"]: r for r in client.get("/capability-map/api/applications").get_json()["applications"]}
    for name, app in apps.items():
        assert Decimal(str(rows[app.id]["total_cost_of_ownership"])) == EXPECTED[name], name


def test_the_cost_tiers_count_the_same_figures(portfolio, client, login_as):
    _, user, _ = portfolio
    login_as(client, user)
    summary = client.get("/dashboard/api/tco/cost-tiers").get_json()["summary"]
    assert Decimal(str(summary["total_tco"])) == sum(EXPECTED.values())
    assert summary["apps_with_tco"] == 3


def test_vendors_and_the_canvas_agree(portfolio):
    from app.services import vendor_register
    from app.services.cost_view import organisation_cost

    org, _, apps = portfolio
    for name, app in apps.items():
        assert Decimal(str(vendor_register.annual_cost([app]))) == EXPECTED[name], name
    canvas = {a["id"]: a["total"] for a in organisation_cost(org.id).applications}
    for name, app in apps.items():
        assert canvas[app.id] == EXPECTED[name], name
    assert organisation_cost(org.id).total == sum(EXPECTED.values())


def test_a_cost_field_changed_anywhere_reaches_every_screen(portfolio, db_session):
    """A route that writes the column directly (not through the accessor) still moves the Cost tab's figure."""
    from app.services.application_cost_accessor import get_annual_cost

    org, _, apps = portfolio
    ledger = apps["Ledger"]
    ledger.license_cost_annual = Decimal("66000")
    ledger.total_cost_of_ownership = Decimal("78000")
    db_session.flush()
    assert get_annual_cost(ledger) == Decimal("78000.00")
