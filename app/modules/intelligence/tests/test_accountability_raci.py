"""Tests for the RACI half of ``accountability_for_element`` (L4).

For a ``Capability`` element the answer gains a ``raci`` block: who is recorded
against the organisation's capability that mirrors the element, as recorded.
The ownership read stays withdrawn, so every other element type answers
exactly as before and carries no ``raci`` key. A capability with rows but no
``A`` is a listed absence, not a defect and not a score.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/intelligence/tests/conftest.py.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import event

WITHDRAWN = {
    "owners": [],
    "capacity_not_available": True,
    "reasons": ["ownership_reader_not_built", "capacity_not_available"],
}
BLOCK_KEYS = {"capability_id", "rows", "accountable_count", "no_accountable", "reason", "source"}
ENTRY_KEYS = {"assignment_id", "stakeholder_type", "stakeholder_id", "stakeholder_name", "raci"}


def _element(db_session, org_id, name, type_="Capability", layer="strategy"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _capability(db_session, org_id, element, name="Cap", code=None):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name=name,
        code=code or f"RACI-{uuid.uuid4().hex[:8]}",
        organization_id=org_id,
        scope="tenant" if org_id is not None else "reference",
        level=1,
        archimate_element_id=element.id,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _raci(db_session, org_id, capability, letter, name, stakeholder_type="role", stakeholder_id=None):
    from app.models.organization_model import EnterpriseRaciAssignment

    row = EnterpriseRaciAssignment(
        organization_id=org_id,
        capability_id=capability.id,
        raci=letter,
        stakeholder_name=name,
        stakeholder_type=stakeholder_type,
        stakeholder_id=stakeholder_id if stakeholder_id is not None else uuid.uuid4().int % 100000,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_user(db_session, org):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"raci-{uuid.uuid4().hex[:8]}@example.com",
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


def _answer(app, org_id, element_id):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.accountability_for_element(element_id)


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
    """Organisation A: a Capability element, the capability that mirrors it,
    and three assignments recorded as R, A and I."""
    org = make_org(slug)
    cap_el = _element(db_session, org.id, "Cap A")
    cap = _capability(db_session, org.id, cap_el)
    r = _raci(db_session, org.id, cap, "R", "Ops lead")
    a = _raci(db_session, org.id, cap, "A", "Head of Ops")
    i = _raci(db_session, org.id, cap, "I", "Finance")
    db_session.commit()
    return org, cap_el, cap, r, a, i


# (1) rows ordered A, I, R; the withdrawn half unchanged
def test_capability_answer_lists_rows_and_leaves_the_withdrawn_half_alone(app, db_session, make_org):
    org, cap_el, cap, r, a, i = _world(db_session, make_org, "raci-1")

    result = _answer(app, org.id, cap_el.id)

    assert {k: result[k] for k in WITHDRAWN} == WITHDRAWN
    block = result["raci"]
    assert block["capability_id"] == cap.id
    assert [row["raci"] for row in block["rows"]] == ["A", "I", "R"]
    assert [row["stakeholder_name"] for row in block["rows"]] == ["Head of Ops", "Finance", "Ops lead"]
    assert block["accountable_count"] == 1
    assert block["no_accountable"] is False
    assert block["reason"] is None
    assert block["source"] == "enterprise_raci_assignments"


# (2) a shared catalogue capability never supplies rows; the red run proves the strict predicate
def _catalogue_then_own(db_session, make_org, slug):
    org = make_org(slug)
    cap_el = _element(db_session, org.id, "Cap A")
    catalogue = _capability(db_session, None, cap_el, name="Catalogue")
    # A assignment row always carries an organisation, so this one is this
    # organisation's own row on a shared capability: only the strict
    # capability predicate keeps it out.
    _raci(db_session, org.id, catalogue, "A", "Catalogue owner")
    own = _capability(db_session, org.id, cap_el, name="Own")
    _raci(db_session, org.id, own, "R", "Own team")
    db_session.commit()
    return org, cap_el, own


def test_shared_catalogue_capability_never_supplies_rows(app, db_session, make_org):
    org, cap_el, own = _catalogue_then_own(db_session, make_org, "raci-2a")

    block = _answer(app, org.id, cap_el.id)["raci"]

    assert block["capability_id"] == own.id
    assert [row["stakeholder_name"] for row in block["rows"]] == ["Own team"]


def test_mutation_proof_capability_predicate_keeps_the_catalogue_out(app, db_session, make_org, monkeypatch):
    from app import db
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org, cap_el, own = _catalogue_then_own(db_session, make_org, "raci-2m")

    def names():
        block = _answer(app, org.id, cap_el.id)["raci"]
        return [row["stakeholder_name"] for row in block["rows"] or []]

    assert "Catalogue owner" not in names()  # control

    # Mutation: the strict predicate becomes a no-op.
    monkeypatch.setattr(
        IntelligenceQueryService,
        "_raci_tenant_predicate",
        staticmethod(lambda model, organization_id: db.true()),
    )
    with pytest.raises(AssertionError):
        assert "Catalogue owner" not in names()


# (3) another organisation's capability and rows never appear
def test_another_organisations_capability_and_rows_never_appear(app, db_session, make_org):
    org, cap_el, cap, r, a, i = _world(db_session, make_org, "raci-3a")
    org_b = make_org("raci-3b")
    foreign_cap = _capability(db_session, org_b.id, cap_el, name="B cap")
    _raci(db_session, org_b.id, foreign_cap, "A", "B accountable")
    db_session.commit()

    block = _answer(app, org.id, cap_el.id)["raci"]
    assert {row["stakeholder_name"] for row in block["rows"]} == {"Head of Ops", "Finance", "Ops lead"}

    # A's own capability row goes; only the foreign one still names the element.
    from app import db

    db.session.execute(db.text("DELETE FROM unified_capabilities WHERE id = :id"), {"id": cap.id})
    db_session.commit()
    gone = _answer(app, org.id, cap_el.id)["raci"]
    assert gone == {
        "capability_id": None,
        "rows": None,
        "accountable_count": None,
        "no_accountable": None,
        "reason": "no_capability_in_chain",
        "source": "enterprise_raci_assignments",
    }


def test_element_of_another_organisation_carries_no_raci(app, db_session, make_org):
    org, cap_el, cap, r, a, i = _world(db_session, make_org, "raci-4a")
    org_b = make_org("raci-4b")
    db_session.commit()

    assert _answer(app, org_b.id, cap_el.id) == WITHDRAWN


# (5) (6) (7) not recorded
def test_capability_with_no_assignments_says_so(app, db_session, make_org):
    org = make_org("raci-5")
    cap_el = _element(db_session, org.id, "Cap A")
    cap = _capability(db_session, org.id, cap_el)
    db_session.commit()

    block = _answer(app, org.id, cap_el.id)["raci"]

    assert block == {
        "capability_id": cap.id,
        "rows": None,
        "accountable_count": None,
        "no_accountable": None,
        "reason": "no_raci_recorded",
        "source": "enterprise_raci_assignments",
    }


def test_rows_with_no_accountable_are_a_listed_absence(app, db_session, make_org):
    org = make_org("raci-6")
    cap_el = _element(db_session, org.id, "Cap A")
    cap = _capability(db_session, org.id, cap_el)
    _raci(db_session, org.id, cap, "R", "Ops lead")
    _raci(db_session, org.id, cap, "C", "Legal")
    db_session.commit()

    block = _answer(app, org.id, cap_el.id)["raci"]

    assert block["accountable_count"] == 0
    assert block["no_accountable"] is True
    assert block["reason"] is None


def test_a_null_letter_is_carried_as_null_and_is_not_accountable(app, db_session, make_org):
    org = make_org("raci-7")
    cap_el = _element(db_session, org.id, "Cap A")
    cap = _capability(db_session, org.id, cap_el)
    _raci(db_session, org.id, cap, None, "Unassigned letter")
    db_session.commit()

    block = _answer(app, org.id, cap_el.id)["raci"]

    assert block["rows"][0]["raci"] is None
    assert block["accountable_count"] == 0
    assert block["no_accountable"] is True


# (8) other types answer exactly as before
def test_other_element_types_carry_no_raci_key(app, db_session, make_org):
    org = make_org("raci-8")
    component_el = _element(db_session, org.id, "App", type_="ApplicationComponent", layer="application")
    process_el = _element(db_session, org.id, "Process", type_="BusinessProcess", layer="business")
    db_session.commit()

    for element in (component_el, process_el):
        assert _answer(app, org.id, element.id) == WITHDRAWN


# (9) shape
def test_block_and_entry_shapes(app, db_session, make_org):
    org, cap_el, cap, r, a, i = _world(db_session, make_org, "raci-9")

    block = _answer(app, org.id, cap_el.id)["raci"]

    assert set(block.keys()) == BLOCK_KEYS
    assert all(set(row.keys()) == ENTRY_KEYS for row in block["rows"])


# (10) at most two selects, none for the second when there is no capability
def test_two_selects_for_a_capability_and_none_of_the_second_without_one(app, db_session, make_org, counter):
    org, cap_el, cap, r, a, i = _world(db_session, make_org, "raci-10a")
    counter.statements.clear()
    _answer(app, org.id, cap_el.id)
    assert counter.touching("unified_capabilities") == 1
    assert counter.touching("enterprise_raci_assignments") == 1

    lonely = make_org("raci-10b")
    lonely_el = _element(db_session, lonely.id, "Cap B")
    db_session.commit()
    counter.statements.clear()
    _answer(app, lonely.id, lonely_el.id)
    assert counter.touching("unified_capabilities") == 1
    assert counter.touching("enterprise_raci_assignments") == 0


# (11) nothing invented
def test_reasons_carry_no_values_and_no_default_zero(app, db_session, make_org):
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org = make_org("raci-11")
    empty_el = _element(db_session, org.id, "Cap A")
    empty_cap = _capability(db_session, org.id, empty_el)
    lonely_el = _element(db_session, org.id, "Cap B")
    db_session.commit()

    for element in (empty_el, lonely_el):
        block = _answer(app, org.id, element.id)["raci"]
        assert block["reason"] in REASON_CODES
        assert block["rows"] is None
        assert block["accountable_count"] is None
        assert block["no_accountable"] is None
        text = json.dumps(block)
        assert '"accountable_count": 0' not in text
        assert '"no_accountable": false' not in text
    assert empty_cap.id == _answer(app, org.id, empty_el.id)["raci"]["capability_id"]


# (12) route
def test_route_passes_raci_through_only_when_present(app, db_session, make_org, client, login_as):
    org, cap_el, cap, r, a, i = _world(db_session, make_org, "raci-12a")
    other_el = _element(db_session, org.id, "App", type_="ApplicationComponent", layer="application")
    org_b = make_org("raci-12b")
    user_a = _make_user(db_session, org)
    user_b = _make_user(db_session, org_b)
    db_session.commit()

    login_as(client, user_a)
    with_raci = client.get(f"/api/v1/intelligence/accountability/{cap_el.id}")
    assert with_raci.status_code == 200
    data = with_raci.get_json()["data"]
    assert set(data["raci"].keys()) == BLOCK_KEYS
    assert data["owners"] == [] and data["capacity_not_available"] is True

    without = client.get(f"/api/v1/intelligence/accountability/{other_el.id}")
    assert "raci" not in without.get_json()["data"]

    login_as(client, user_b)
    foreign = client.get(f"/api/v1/intelligence/accountability/{cap_el.id}")
    absent = client.get("/api/v1/intelligence/accountability/999999999")
    assert foreign.status_code == absent.status_code == 404
    assert foreign.get_json()["error"] == absent.get_json()["error"]
