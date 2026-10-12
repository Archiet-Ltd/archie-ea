"""Tests for the contract window on the impact answer (L1).

Rows whose element is an application component carry the organisation's
contracts for it whose renewal or end date falls in the caller's window, as
recorded, and the answer carries a whole-answer flags block. Nothing
unrecorded is rendered as a date, a cost or a flag, and the cost figure is
redacted at the route for a caller without budget authority.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/intelligence/tests/conftest.py.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import event

ENTRY_KEYS = {
    "contract_id", "contract_name", "renewal_date", "end_date", "renewal_status", "auto_renewal",
    "notice_period_days", "annual_cost", "currency", "contract_risk", "vendor_risk",
    "exit_complexity", "cost_reason", "truth_class",
}
FLAG_KEYS = {"renewing_element_ids", "reason", "window_days"}
TODAY = date.today()


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _component(db_session, org_id, element):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name=element.name, organization_id=org_id, archimate_element_id=element.id)
    db_session.add(comp)
    db_session.flush()
    return comp


def _contract(db_session, org_id, component, *, name="Support", renewal=None, end=None, cost=None, **kw):
    from app.models.application_portfolio import VendorContract

    row = VendorContract(
        organization_id=org_id,
        application_id=component.id,
        contract_name=name,
        contract_number=f"C-{uuid.uuid4().hex[:10]}",
        start_date=TODAY - timedelta(days=400),
        renewal_date=renewal,
        end_date=end,
        annual_cost=cost,
        **kw,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_user(db_session, org, *, enterprise_role=None):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"contract-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        is_org_admin=True,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _impact(app, org_id, element_id, **kwargs):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    kwargs.setdefault("include_derived", False)
    kwargs.setdefault("with_owner", False)
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.cross_layer_impact(element_id, **kwargs)


class _Counter:
    def __init__(self):
        self.statements = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def touching(self, table):
        pattern = re.compile(rf"\bFROM {table}\b")
        return sum(1 for s in self.statements if pattern.search(s))


@pytest.fixture
def counter(app):
    from app import db

    c = _Counter()
    event.listen(db.engine, "before_cursor_execute", c)
    try:
        yield c
    finally:
        event.remove(db.engine, "before_cursor_execute", c)


def _world(db_session, make_org, slug):
    """Organisation A: app_a serves app_b; app_b's component has a contract
    renewing in 30 days with a recorded cost."""
    org = make_org(slug)
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _component(db_session, org.id, app_a)
    comp_b = _component(db_session, org.id, app_b)
    contract = _contract(
        db_session, org.id, comp_b, renewal=TODAY + timedelta(days=30), cost=1200.0,
        renewal_status="renewing", contract_risk="high", vendor_risk="medium",
        exit_complexity="low", auto_renewal=True, notice_period_days=60, currency="GBP",
    )
    db_session.commit()
    return org, app_a, app_b, comp_b, contract


def _row_for(result, element):
    return next(r for r in result["rows"] if r["element_id"] == element.id)


# (1) the renewing contract and the flags
def test_renewing_contract_is_listed_on_its_row_and_flagged(app, db_session, make_org):
    org, app_a, app_b, comp_b, contract = _world(db_session, make_org, "ic-1")

    result = _impact(app, org.id, app_a.id)

    row = _row_for(result, app_b)
    assert row["contracts_reason"] is None
    (entry,) = row["contracts"]
    assert entry["contract_id"] == contract.id
    assert entry["renewal_date"] == (TODAY + timedelta(days=30)).isoformat()
    assert entry["annual_cost"] == 1200.0 and entry["currency"] == "GBP"
    assert entry["renewal_status"] == "renewing" and entry["contract_risk"] == "high"
    assert entry["auto_renewal"] is True and entry["notice_period_days"] == 60
    assert entry["truth_class"] == "authoritative_fact" and entry["cost_reason"] is None
    assert result["contract_flags"] == {
        "renewing_element_ids": [app_b.id], "reason": None, "window_days": 90,
    }


# (2) a foreign contract naming this component never appears; the red run proves the predicate
def _foreign_contract(db_session, make_org, slug):
    org, app_a, app_b, comp_b, contract = _world(db_session, make_org, slug)
    org_b = make_org(f"{slug}-b")
    foreign = _contract(
        db_session, org_b.id, comp_b, name="B contract", renewal=TODAY + timedelta(days=1), cost=1.0
    )
    db_session.commit()
    return org, app_a, app_b, contract, foreign


def _impact_without_orm_fence(app, monkeypatch, org_id, element_id):
    from app.modules.intelligence.services import query_service
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    monkeypatch.setattr(query_service, "current_org_id", lambda: org_id)
    with app.test_request_context("/"):
        return IntelligenceQueryService.cross_layer_impact(
            element_id, include_derived=False, with_owner=False
        )


def test_foreign_contract_naming_this_component_is_absent(app, db_session, make_org):
    org, app_a, app_b, contract, foreign = _foreign_contract(db_session, make_org, "ic-2a")

    row = _row_for(_impact(app, org.id, app_a.id), app_b)

    assert [c["contract_id"] for c in row["contracts"]] == [contract.id]


def test_mutation_proof_contract_predicate_keeps_the_foreign_contract_out(
    app, db_session, make_org, monkeypatch
):
    from app import db
    from app.modules.intelligence.services import query_service

    org, app_a, app_b, contract, foreign = _foreign_contract(db_session, make_org, "ic-2m")

    def listed():
        row = _row_for(_impact_without_orm_fence(app, monkeypatch, org.id, app_a.id), app_b)
        return {c["contract_id"] for c in row["contracts"]}

    assert foreign.id not in listed()  # control

    monkeypatch.setattr(query_service, "_contract_tenant_predicate", lambda model, org_id: db.true())
    with pytest.raises(AssertionError):
        assert foreign.id not in listed()


def test_element_of_another_organisation_answers_not_found_with_null_flags(app, db_session, make_org):
    org, app_a, app_b, comp_b, contract = _world(db_session, make_org, "ic-3a")
    org_b = make_org("ic-3b")
    db_session.commit()

    result = _impact(app, org_b.id, app_a.id)

    assert result["contract_flags"] == {
        "renewing_element_ids": None, "reason": "element_not_found", "window_days": 90,
    }


# (4) (5) (6) (7) (8) not recorded and the window
def test_component_with_no_contract_row_says_so_and_is_not_renewing(app, db_session, make_org):
    org = make_org("ic-4")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _component(db_session, org.id, app_b)
    db_session.commit()

    result = _impact(app, org.id, app_a.id)

    row = _row_for(result, app_b)
    assert row["contracts"] is None
    assert row["contracts_reason"] == "no_contract_recorded"
    assert result["contract_flags"] == {"renewing_element_ids": [], "reason": None, "window_days": 90}


def test_window_boundary_is_inclusive_at_the_far_end(app, db_session, make_org):
    org = make_org("ic-5")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    comp_b = _component(db_session, org.id, app_b)
    _contract(db_session, org.id, comp_b, renewal=TODAY + timedelta(days=91))
    db_session.commit()

    row = _row_for(_impact(app, org.id, app_a.id), app_b)
    assert row["contracts"] == [] and row["contracts_reason"] is None

    widened = _row_for(_impact(app, org.id, app_a.id, renewal_window_days=91), app_b)
    assert len(widened["contracts"]) == 1


def test_a_past_renewal_is_out_and_an_end_date_in_the_window_brings_it_in(app, db_session, make_org):
    org = make_org("ic-6")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    comp_b = _component(db_session, org.id, app_b)
    _contract(db_session, org.id, comp_b, name="Past only", renewal=TODAY - timedelta(days=1))
    through_end = _contract(
        db_session, org.id, comp_b, name="Through end",
        renewal=TODAY - timedelta(days=1), end=TODAY + timedelta(days=10),
    )
    db_session.commit()

    row = _row_for(_impact(app, org.id, app_a.id), app_b)

    assert [c["contract_id"] for c in row["contracts"]] == [through_end.id]


def test_null_cost_stays_null_and_two_contracts_order_by_their_earlier_date(app, db_session, make_org):
    org = make_org("ic-7")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    comp_b = _component(db_session, org.id, app_b)
    later = _contract(db_session, org.id, comp_b, name="Later", renewal=TODAY + timedelta(days=40), cost=None)
    sooner = _contract(db_session, org.id, comp_b, name="Sooner", renewal=TODAY + timedelta(days=10), cost=5.0)
    db_session.commit()

    entries = _row_for(_impact(app, org.id, app_a.id), app_b)["contracts"]

    assert [e["contract_id"] for e in entries] == [sooner.id, later.id]
    assert entries[1]["annual_cost"] is None and entries[1]["cost_reason"] is None


# (9) shape
def test_non_component_rows_carry_no_contract_keys_and_shapes_are_exact(app, db_session, make_org):
    org, app_a, app_b, comp_b, contract = _world(db_session, make_org, "ic-9")
    process = _element(db_session, org.id, "Process", type_="BusinessProcess", layer="business")
    _relationship(db_session, org.id, app_a, process)
    db_session.commit()

    result = _impact(app, org.id, app_a.id)

    plain = _row_for(result, process)
    assert "contracts" not in plain and "contracts_reason" not in plain
    assert set(_row_for(result, app_b)["contracts"][0].keys()) == ENTRY_KEYS
    assert set(result["contract_flags"].keys()) == FLAG_KEYS

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        no_context = IntelligenceQueryService.cross_layer_impact(app_a.id)
    assert no_context["contract_flags"] == {
        "renewing_element_ids": None, "reason": "no_tenant_context", "window_days": 90,
    }


# (10) a constant number of contract selects
def _contract_selects(app, db_session, make_org, counter, slug, elements):
    org = make_org(slug)
    root = _element(db_session, org.id, "Root")
    for index in range(elements):
        el = _element(db_session, org.id, f"App {index}")
        _relationship(db_session, org.id, root, el)
        comp = _component(db_session, org.id, el)
        _contract(db_session, org.id, comp, renewal=TODAY + timedelta(days=5))
    db_session.commit()

    counter.statements.clear()
    result = _impact(app, org.id, root.id)
    assert len(result["rows"]) == elements
    return counter.touching("vendor_contracts")


def test_one_contract_select_for_one_row_and_for_twenty(app, db_session, make_org, counter):
    one = _contract_selects(app, db_session, make_org, counter, "ic-10a", 1)
    twenty = _contract_selects(app, db_session, make_org, counter, "ic-10b", 20)

    assert one == twenty == 1

    org = make_org("ic-10c")
    lonely = _element(db_session, org.id, "Lonely")
    db_session.commit()
    counter.statements.clear()
    _impact(app, org.id, lonely.id)
    assert counter.touching("vendor_contracts") == 0


# (11) nothing the answer already said changes
def test_counts_depths_and_positions_are_the_same_with_and_without_contracts(app, db_session, make_org):
    org = make_org("ic-11")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    comp_b = _component(db_session, org.id, app_b)
    db_session.commit()

    def shape(result):
        summary = {k: v for k, v in result["summary"].items() if k != "latency_ms"}
        return (
            [(r["element_id"], r["relation"]["depth"], r["relation"]["kind"]) for r in result["rows"]],
            summary, result["reasons"],
        )

    before = shape(_impact(app, org.id, app_a.id))
    _contract(db_session, org.id, comp_b, renewal=TODAY + timedelta(days=5), cost=10.0)
    db_session.commit()

    assert shape(_impact(app, org.id, app_a.id)) == before


# (12) nothing invented
def test_no_invented_cost_flag_or_default_for_a_component_without_a_contract(app, db_session, make_org):
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org = make_org("ic-12")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _component(db_session, org.id, app_b)
    db_session.commit()

    result = _impact(app, org.id, app_a.id)
    row = _row_for(result, app_b)
    text = json.dumps(row)

    assert row["contracts_reason"] in REASON_CODES and row["contracts"] is None
    for invented in ('"annual_cost": 0', '"auto_renewal": false', '"notice_period_days": 90'):
        assert invented not in text

    missing = _impact(app, org.id, 999999999)
    assert missing["contract_flags"]["renewing_element_ids"] is None
    assert missing["contract_flags"]["reason"] in REASON_CODES


def test_a_bad_window_is_rejected_by_the_service(app, db_session, make_org):
    org = make_org("ic-12b")
    el = _element(db_session, org.id, "AppA")
    db_session.commit()

    for bad in (0, 3651):
        with pytest.raises(ValueError):
            _impact(app, org.id, el.id, renewal_window_days=bad)


# (13) (14) route and redaction
def test_route_carries_flags_and_validates_the_window(app, db_session, make_org, client, login_as):
    org, app_a, app_b, comp_b, contract = _world(db_session, make_org, "ic-13")
    user = _make_user(db_session, org, enterprise_role="cto")
    db_session.commit()

    login_as(client, user)
    ok = client.get(f"/api/v1/intelligence/impact/{app_a.id}")
    assert ok.status_code == 200
    data = ok.get_json()["data"]
    assert data["contract_flags"]["renewing_element_ids"] == [app_b.id]
    assert any(row.get("contracts") for row in data["rows"])

    for bad in ("0", "abc", "4000"):
        resp = client.get(f"/api/v1/intelligence/impact/{app_a.id}?renewal_window_days={bad}")
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "INVALID_PARAMETER"


def test_route_redacts_the_cost_figure_for_a_restricted_role_only(
    app, db_session, make_org, client, login_as
):
    org, app_a, app_b, comp_b, contract = _world(db_session, make_org, "ic-14")
    restricted = _make_user(db_session, org, enterprise_role="solution_architect")
    budget_holder = _make_user(db_session, org, enterprise_role="cto")
    db_session.commit()

    def first_contract(user):
        login_as(client, user)
        rows = client.get(f"/api/v1/intelligence/impact/{app_a.id}").get_json()["data"]["rows"]
        return next(c for r in rows for c in (r.get("contracts") or []))

    hidden = first_contract(restricted)
    assert hidden["annual_cost"] is None
    assert hidden["cost_reason"] == "financial_data_restricted"
    assert hidden["renewal_date"] is not None and hidden["renewal_status"] == "renewing"
    assert hidden["contract_risk"] == "high"

    shown = first_contract(budget_holder)
    assert shown["annual_cost"] == 1200.0 and shown["cost_reason"] is None
