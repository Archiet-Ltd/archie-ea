"""Tests for the application-link half of ``risk_for_element`` (L6).

A risk recorded against an application (a ``RiskEntityLink`` of type
``application`` whose ``entity_id`` is the component the picked element
resolves to) is listed beside the risks mirrored on the element, each with how
it was reached. Solution and programme links cannot be followed to an element,
so the answer names them by type instead of dropping them.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/intelligence/tests/conftest.py.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import event

BASE_KEYS = {
    "risk_id", "title", "status", "likelihood", "impact", "risk_score", "risk_level",
    "owner", "mitigation_plan", "affected_rows", "affected_summary",
}
BLOCK_KEYS = {"application_links_resolved", "unresolvable_entity_types", "reason"}


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _component(db_session, org_id, element):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name=element.name, organization_id=org_id, archimate_element_id=element.id)
    db_session.add(comp)
    db_session.flush()
    return comp


def _relationship(db_session, org_id, source, target):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _risk(db_session, org_id, element=None, *, title="A risk", likelihood=3, impact=4):
    from app.models.risk import Risk

    row = Risk(
        organization_id=org_id,
        archimate_element_id=element.id if element is not None else None,
        title=title,
        likelihood=likelihood,
        impact=impact,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _link(db_session, org_id, risk, entity_type, entity_id):
    from app.models.risk_entity_link import RiskEntityLink

    row = RiskEntityLink(
        organization_id=org_id, risk_id=risk.id, entity_type=entity_type, entity_id=entity_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _answer(app, org_id, element_id, **kwargs):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.risk_for_element(element_id, **kwargs)


def _make_user(db_session, org):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"risklink-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        is_org_admin=True,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


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
    """Organisation A: an application, its component, a direct risk, a linked
    risk with no element mirror, and a solution-type link."""
    org = make_org(slug)
    app_a = _element(db_session, org.id, "AppA")
    tech = _element(db_session, org.id, "TechA", type_="Node", layer="technology")
    _relationship(db_session, org.id, app_a, tech)
    comp = _component(db_session, org.id, app_a)
    direct = _risk(db_session, org.id, app_a, title="Direct")
    linked = _risk(db_session, org.id, None, title="Linked")
    link = _link(db_session, org.id, linked, "application", comp.id)
    other = _risk(db_session, org.id, None, title="Solution risk")
    _link(db_session, org.id, other, "solution", 9001)
    db_session.commit()
    return org, app_a, comp, direct, linked, link


# (1) both risks, how each was reached, and what the answer cannot resolve
def test_direct_and_linked_risks_are_listed_with_how_they_were_reached(app, db_session, make_org):
    org, app_a, comp, direct, linked, link = _world(db_session, make_org, "rl-1")

    result = _answer(app, org.id, app_a.id)

    assert [r["risk_id"] for r in result["risks"]] == [direct.id, linked.id]
    assert [r["seeded_via"] for r in result["risks"]] == ["element", "application_link"]
    assert "link_id" not in result["risks"][0]
    assert result["risks"][1]["link_id"] == link.id
    assert all(r["affected_rows"] for r in result["risks"])
    assert result["reasons"] == []
    assert result["link_resolution"] == {
        "application_links_resolved": 1,
        "unresolvable_entity_types": [{"entity_type": "solution", "count": 1}],
        "reason": "risk_link_unresolvable",
    }


# (2) a foreign link never seeds a listing; the red run proves the predicate does the work
def test_foreign_link_to_this_component_lists_nothing(app, db_session, make_org):
    org, app_a, comp, direct, linked, link = _world(db_session, make_org, "rl-2a")
    org_b = make_org("rl-2b")
    b_risk = _risk(db_session, org_b.id, None, title="B risk")
    _link(db_session, org_b.id, b_risk, "application", comp.id)
    db_session.commit()

    result = _answer(app, org.id, app_a.id)

    listed = {r["risk_id"] for r in result["risks"]}
    assert b_risk.id not in listed
    assert listed == {direct.id, linked.id}
    assert result["link_resolution"]["application_links_resolved"] == 1


class _AlwaysTrue:
    """Stands in for a tenant column whose predicate has been dropped."""

    def __eq__(self, other):  # noqa: D105
        from app import db

        return db.true()

    __hash__ = None


def _link_columns(real, tenant_column):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=real.id,
        risk_id=real.risk_id,
        entity_type=real.entity_type,
        entity_id=real.entity_id,
        organization_id=tenant_column,
    )


def _foreign_link_to_an_own_risk(db_session, make_org, slug):
    org, app_a, comp, direct, linked, link = _world(db_session, make_org, slug)
    org_b = make_org(f"{slug}-b")
    own_unlinked = _risk(db_session, org.id, None, title="Reachable only through B's link")
    _link(db_session, org_b.id, own_unlinked, "application", comp.id)
    db_session.commit()
    return org, app_a, own_unlinked


def _service_without_orm_fence(app, monkeypatch, org_id, element_id):
    """Run the service with no request tenant context (so the ORM and session
    fences are off) and the service's own organisation set explicitly, so only
    the query predicates stand between the caller and another tenant's rows."""
    from app.modules.intelligence.services import query_service
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    monkeypatch.setattr(query_service, "current_org_id", lambda: org_id)
    with app.test_request_context("/"):
        return IntelligenceQueryService.risk_for_element(element_id)


def test_mutation_proof_link_predicate_keeps_foreign_link_out(app, db_session, make_org, monkeypatch):
    from app.models import risk_entity_link

    org, app_a, own_unlinked = _foreign_link_to_an_own_risk(db_session, make_org, "rl-2m")

    # Control: with the explicit predicate in place the foreign link seeds nothing.
    control = _service_without_orm_fence(app, monkeypatch, org.id, app_a.id)
    assert own_unlinked.id not in {r["risk_id"] for r in control["risks"]}

    # Mutation: the explicit predicate on the link select is dropped.
    real = risk_entity_link.RiskEntityLink
    monkeypatch.setattr(risk_entity_link, "RiskEntityLink", _link_columns(real, _AlwaysTrue()))
    mutated = _service_without_orm_fence(app, monkeypatch, org.id, app_a.id)
    with pytest.raises(AssertionError):
        assert own_unlinked.id not in {r["risk_id"] for r in mutated["risks"]}


def test_link_to_a_risk_owned_by_another_organisation_lists_nothing(app, db_session, make_org, monkeypatch):
    org, app_a, comp, direct, linked, link = _world(db_session, make_org, "rl-3a")
    org_b = make_org("rl-3b")
    b_risk = _risk(db_session, org_b.id, None, title="B risk")
    _link(db_session, org.id, b_risk, "application", comp.id)
    db_session.commit()

    result = _service_without_orm_fence(app, monkeypatch, org.id, app_a.id)

    assert b_risk.id not in {r["risk_id"] for r in result["risks"]}


def test_element_of_another_organisation_answers_not_found(app, db_session, make_org):
    org, app_a, comp, direct, linked, link = _world(db_session, make_org, "rl-3c")
    org_b = make_org("rl-3d")
    db_session.commit()

    result = _answer(app, org_b.id, app_a.id)

    assert result["risks"] == []
    assert result["reasons"] == ["element_not_found"]
    assert result["link_resolution"] == {
        "application_links_resolved": None,
        "unresolvable_entity_types": None,
        "reason": "element_not_found",
    }


# (4) (5) (6) (7) nothing recorded, and what an unrecorded answer still says
def test_no_risk_and_no_links_is_a_measured_empty(app, db_session, make_org):
    org = make_org("rl-4")
    el = _element(db_session, org.id, "Lonely")
    db_session.commit()

    result = _answer(app, org.id, el.id)

    assert result["risks"] == []
    assert result["reasons"] == ["no_risk_recorded"]
    assert result["link_resolution"] == {
        "application_links_resolved": 0,
        "unresolvable_entity_types": [],
        "reason": None,
    }


def test_no_risk_but_a_solution_link_in_the_tenant_is_still_named(app, db_session, make_org):
    org = make_org("rl-5")
    el = _element(db_session, org.id, "Lonely")
    other = _risk(db_session, org.id, None, title="Solution risk")
    _link(db_session, org.id, other, "solution", 42)
    db_session.commit()

    result = _answer(app, org.id, el.id)

    assert result["reasons"] == ["no_risk_recorded"]
    assert result["link_resolution"]["reason"] == "risk_link_unresolvable"
    assert result["link_resolution"]["unresolvable_entity_types"] == [
        {"entity_type": "solution", "count": 1}
    ]


def test_risk_both_mirrored_and_linked_is_listed_once_as_element(app, db_session, make_org):
    org = make_org("rl-6")
    app_a = _element(db_session, org.id, "AppA")
    comp = _component(db_session, org.id, app_a)
    both = _risk(db_session, org.id, app_a, title="Both")
    _link(db_session, org.id, both, "application", comp.id)
    db_session.commit()

    result = _answer(app, org.id, app_a.id)

    assert [r["risk_id"] for r in result["risks"]] == [both.id]
    assert result["risks"][0]["seeded_via"] == "element"
    assert "link_id" not in result["risks"][0]
    assert result["link_resolution"]["application_links_resolved"] == 0


def test_non_component_element_makes_no_link_lookup_but_still_names_other_types(
    app, db_session, make_org, counter
):
    org = make_org("rl-7")
    process = _element(db_session, org.id, "Process", type_="BusinessProcess", layer="business")
    direct = _risk(db_session, org.id, process, title="Direct")
    other = _risk(db_session, org.id, None, title="Programme risk")
    _link(db_session, org.id, other, "programme", 7)
    db_session.commit()

    counter.statements.clear()
    result = _answer(app, org.id, process.id)

    assert [r["risk_id"] for r in result["risks"]] == [direct.id]
    assert result["link_resolution"]["application_links_resolved"] == 0
    assert result["link_resolution"]["unresolvable_entity_types"] == [
        {"entity_type": "programme", "count": 1}
    ]
    assert counter.touching("risk_entity_links") == 1


# (8) shape
def test_payload_keys_and_the_three_key_block_on_every_branch(app, db_session, make_org):
    org, app_a, comp, direct, linked, link = _world(db_session, make_org, "rl-8")
    empty = _element(db_session, org.id, "Lonely")
    db_session.commit()

    computed = _answer(app, org.id, app_a.id)
    assert set(computed["risks"][0].keys()) == BASE_KEYS | {"seeded_via"}
    assert set(computed["risks"][1].keys()) == BASE_KEYS | {"seeded_via", "link_id"}

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        no_context = IntelligenceQueryService.risk_for_element(app_a.id)

    branches = [
        computed,
        _answer(app, org.id, empty.id),
        _answer(app, org.id, 999999999),
        no_context,
    ]
    for answer in branches:
        assert set(answer["link_resolution"].keys()) == BLOCK_KEYS


# (9) a constant number of the task's own selects
def _own_selects(app, db_session, make_org, counter, slug, linked_count):
    org = make_org(slug)
    app_a = _element(db_session, org.id, "AppA")
    comp = _component(db_session, org.id, app_a)
    for index in range(linked_count):
        risk = _risk(db_session, org.id, None, title=f"Linked {index}")
        _link(db_session, org.id, risk, "application", comp.id)
    db_session.commit()

    counter.statements.clear()
    result = _answer(app, org.id, app_a.id)
    assert result["link_resolution"]["application_links_resolved"] == linked_count
    return counter.touching("risk_entity_links"), counter.touching("risks")


def test_own_selects_are_the_same_for_one_linked_risk_and_for_twenty(app, db_session, make_org, counter):
    one = _own_selects(app, db_session, make_org, counter, "rl-9a", 1)
    twenty = _own_selects(app, db_session, make_org, counter, "rl-9b", 20)

    assert one == twenty
    assert one[0] == 2  # the link select and the grouped count


# (10) nothing the answer already said changes
def test_a_direct_risk_reads_identically_with_and_without_links(app, db_session, make_org):
    org = make_org("rl-10")
    app_a = _element(db_session, org.id, "AppA")
    tech = _element(db_session, org.id, "TechA", type_="Node", layer="technology")
    _relationship(db_session, org.id, app_a, tech)
    comp = _component(db_session, org.id, app_a)
    direct = _risk(db_session, org.id, app_a, title="Direct", likelihood=4, impact=5)
    db_session.commit()

    def direct_row(answer):
        row = next(r for r in answer["risks"] if r["risk_id"] == direct.id)
        summary = {k: v for k, v in row["affected_summary"].items() if k != "latency_ms"}
        return row["affected_rows"], summary, row["risk_score"], row["risk_level"]

    before = direct_row(_answer(app, org.id, app_a.id))

    linked = _risk(db_session, org.id, None, title="Linked")
    _link(db_session, org.id, linked, "application", comp.id)
    other = _risk(db_session, org.id, None, title="Programme risk")
    _link(db_session, org.id, other, "programme", 3)
    db_session.commit()

    assert direct_row(_answer(app, org.id, app_a.id)) == before


# (11) nothing invented
def test_fabrication_branch_blocks_carry_no_values_beside_their_reason(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org, app_a, comp, direct, linked, link = _world(db_session, make_org, "rl-11a")

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        no_context = IntelligenceQueryService.risk_for_element(app_a.id)

    for answer in (no_context, _answer(app, org.id, 999999999)):
        block = answer["link_resolution"]
        assert block["reason"] in REASON_CODES
        assert block["application_links_resolved"] is None
        assert block["unresolvable_entity_types"] is None


def test_fabrication_no_measured_looking_zero_no_duplicate_no_invented_word(app, db_session, make_org):
    org, app_a, comp, direct, linked, link = _world(db_session, make_org, "rl-11b")

    not_found = _answer(app, org.id, 999999999)
    assert '"application_links_resolved": 0' not in json.dumps(not_found)

    computed = _answer(app, org.id, app_a.id)
    ids = [r["risk_id"] for r in computed["risks"]]
    assert len(ids) == len(set(ids))
    assert {r["seeded_via"] for r in computed["risks"]} <= {"element", "application_link"}


# (12) route
def test_route_carries_link_resolution_and_a_foreign_id_is_the_absent_404(
    app, db_session, make_org, client, login_as
):
    org, app_a, comp, direct, linked, link = _world(db_session, make_org, "rl-12a")
    org_b = make_org("rl-12b")
    user_b = _make_user(db_session, org_b)
    user_a = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user_a)
    ok = client.get(f"/api/v1/intelligence/risk/{app_a.id}")
    assert ok.status_code == 200
    data = ok.get_json()["data"]
    assert set(data["link_resolution"].keys()) == BLOCK_KEYS
    assert data["link_resolution"]["application_links_resolved"] == 1

    login_as(client, user_b)
    foreign = client.get(f"/api/v1/intelligence/risk/{app_a.id}")
    absent = client.get("/api/v1/intelligence/risk/999999999")
    assert foreign.status_code == absent.status_code == 404
    # Only the per-request meta (request id, timestamp) may differ.
    assert foreign.get_json()["error"] == absent.get_json()["error"]
    assert foreign.get_json()["success"] is absent.get_json()["success"] is False
