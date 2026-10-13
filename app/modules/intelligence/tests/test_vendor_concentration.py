"""Tests for vendor concentration on the risk and portfolio answers.

For each Capability element, the organisation's vendor mappings for the
capability that mirrors it, as recorded, with single-vendor stated as the
count of mapping rows and nothing scored: no spend is summed, no contract end
is picked, no risk word is ranked. A shared catalogue capability or another
organisation's capability never supplies rows. The spend figure is redacted at
both routes for a caller without budget authority.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/intelligence/tests/conftest.py.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

import pytest
from sqlalchemy import event

BLOCK_KEYS = {"mappings", "mapping_count", "single_vendor", "reason", "source"}
ENTRY_KEYS = {
    "mapping_id", "vendor_organization_id", "vendor_name", "relationship_type", "vendor_risk_level",
    "concentration_risk", "lock_in_risk", "dependency_level", "alternative_vendor_available",
    "annual_spend", "contract_end_date", "access_reason",
}


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


def _risk(db_session, org_id, element):
    from app.models.risk import Risk

    row = Risk(organization_id=org_id, archimate_element_id=element.id, title="A risk", likelihood=3, impact=4)
    db_session.add(row)
    db_session.flush()
    return row


def _capability(db_session, org_id, element, name="Cap"):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name=name,
        code=f"VC-{uuid.uuid4().hex[:8]}",
        organization_id=org_id,
        scope="tenant" if org_id is not None else "reference",
        level=1,
        archimate_element_id=element.id,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _vendor(db_session, name=None):
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=name or f"Vendor {uuid.uuid4().hex[:8]}")
    db_session.add(vendor)
    db_session.flush()
    return vendor


def _mapping(db_session, capability, vendor, **kwargs):
    from app.models.capability_to_vendor_mapping import UnifiedCapabilityVendorOrganizationMapping

    row = UnifiedCapabilityVendorOrganizationMapping(
        unified_capability_id=capability.id, vendor_organization_id=vendor.id, **kwargs
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
        email=f"vendor-{uuid.uuid4().hex[:8]}@example.com",
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


def _risk_answer(app, org_id, element_id):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.risk_for_element(element_id)


def _portfolio_answer(app, org_id, element_id):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.portfolio_component_for_element(element_id)


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


def _world(db_session, make_org, slug, mappings=2):
    """Organisation A: an application with a risk, serving a Capability that
    has a capability row and vendor mappings."""
    org = make_org(slug)
    app_a = _element(db_session, org.id, "AppA")
    cap_a = _element(db_session, org.id, "CapA", type_="Capability", layer="strategy")
    _relationship(db_session, org.id, app_a, cap_a)
    _risk(db_session, org.id, app_a)
    capability = _capability(db_session, org.id, cap_a)
    rows = []
    for index in range(mappings):
        rows.append(
            _mapping(
                db_session, capability, _vendor(db_session), relationship_type="primary",
                vendor_risk_level="high", concentration_risk="medium", lock_in_risk="high",
                dependency_level="critical", alternative_vendor_available=False,
                annual_spend=1000 * (index + 1), contract_end_date=datetime(2027, 3, 1),
            )
        )
    db_session.commit()
    return org, app_a, cap_a, capability, rows


# (1) two entries, then one
def test_two_mappings_are_listed_and_one_is_a_single_vendor(app, db_session, make_org):
    org, app_a, cap_a, capability, rows = _world(db_session, make_org, "vc-1a")
    org1, app1, cap1, capability1, rows1 = _world(db_session, make_org, "vc-1b", mappings=1)

    block = _risk_answer(app, org.id, app_a.id)["vendor_concentration"]["by_element"][str(cap_a.id)]

    assert block["mapping_count"] == 2 and block["single_vendor"] is False and block["reason"] is None
    assert [m["mapping_id"] for m in block["mappings"]] == [r.id for r in rows]
    first = block["mappings"][0]
    assert first["lock_in_risk"] == "high" and first["annual_spend"] == 1000.0
    assert first["contract_end_date"] == "2027-03-01" and first["vendor_name"]

    single = _risk_answer(app, org1.id, app1.id)["vendor_concentration"]["by_element"][str(cap1.id)]
    assert single["mapping_count"] == 1 and single["single_vendor"] is True


# (2) a shared catalogue capability never supplies rows; the red run proves the strict predicate
def _catalogue_then_own(db_session, make_org, slug):
    org = make_org(slug)
    app_a = _element(db_session, org.id, "AppA")
    cap_a = _element(db_session, org.id, "CapA", type_="Capability", layer="strategy")
    _relationship(db_session, org.id, app_a, cap_a)
    _risk(db_session, org.id, app_a)
    catalogue = _capability(db_session, None, cap_a, name="Catalogue")
    catalogue_vendor = _vendor(db_session, name=f"Catalogue vendor {uuid.uuid4().hex[:6]}")
    _mapping(db_session, catalogue, catalogue_vendor, vendor_risk_level="low")
    own = _capability(db_session, org.id, cap_a, name="Own")
    own_vendor = _vendor(db_session, name=f"Own vendor {uuid.uuid4().hex[:6]}")
    _mapping(db_session, own, own_vendor, vendor_risk_level="high")
    db_session.commit()
    return org, app_a, cap_a, catalogue_vendor, own_vendor


def _vendor_ids(answer, cap_a):
    block = answer["vendor_concentration"]["by_element"][str(cap_a.id)]
    return {m["vendor_organization_id"] for m in block["mappings"] or []}


def test_shared_catalogue_capability_never_supplies_mappings(app, db_session, make_org):
    org, app_a, cap_a, catalogue_vendor, own_vendor = _catalogue_then_own(db_session, make_org, "vc-2a")

    assert _vendor_ids(_risk_answer(app, org.id, app_a.id), cap_a) == {own_vendor.id}


def test_mutation_proof_capability_predicate_keeps_the_catalogue_out(app, db_session, make_org, monkeypatch):
    from sqlalchemy import or_

    from app.modules.intelligence.services import query_service

    org, app_a, cap_a, catalogue_vendor, own_vendor = _catalogue_then_own(db_session, make_org, "vc-2m")

    def listed():
        return _vendor_ids(_risk_answer(app, org.id, app_a.id), cap_a)

    assert catalogue_vendor.id not in listed()  # control

    monkeypatch.setattr(
        query_service,
        "_vendor_capability_predicate",
        lambda model, organization_id: or_(
            model.organization_id == organization_id, model.organization_id.is_(None)
        ),
    )
    with pytest.raises(AssertionError):
        assert catalogue_vendor.id not in listed()


# (3) another organisation's capability never supplies rows
def test_another_organisations_capability_never_supplies_mappings(app, db_session, make_org):
    org, app_a, cap_a, capability, rows = _world(db_session, make_org, "vc-3a")
    org_b = make_org("vc-3b")
    foreign_capability = _capability(db_session, org_b.id, cap_a, name="B cap")
    foreign_vendor = _vendor(db_session, name=f"B vendor {uuid.uuid4().hex[:6]}")
    _mapping(db_session, foreign_capability, foreign_vendor)
    db_session.commit()

    ids = _vendor_ids(_risk_answer(app, org.id, app_a.id), cap_a)
    assert foreign_vendor.id not in ids and len(ids) == 2

    from app import db

    db.session.execute(db.text("DELETE FROM unified_capabilities WHERE id = :id"), {"id": capability.id})
    db_session.commit()
    gone = _risk_answer(app, org.id, app_a.id)["vendor_concentration"]["by_element"][str(cap_a.id)]
    assert gone["reason"] == "no_capability_in_chain" and gone["mappings"] is None


def test_element_of_another_organisation_answers_not_found(app, db_session, make_org):
    org, app_a, cap_a, capability, rows = _world(db_session, make_org, "vc-4a")
    org_b = make_org("vc-4b")
    db_session.commit()

    result = _risk_answer(app, org_b.id, app_a.id)

    assert result["vendor_concentration"] == {"by_element": None, "reason": "element_not_found"}


# (5) (6) (7) (8) not recorded
def test_capability_with_no_mapping_says_so(app, db_session, make_org):
    org, app_a, cap_a, capability, rows = _world(db_session, make_org, "vc-5", mappings=0)

    block = _risk_answer(app, org.id, app_a.id)["vendor_concentration"]["by_element"][str(cap_a.id)]

    assert block["reason"] == "no_vendor_mapping_recorded"
    assert block["mappings"] is None and block["mapping_count"] is None and block["single_vendor"] is None


def test_unrecorded_entry_values_stay_null(app, db_session, make_org):
    org = make_org("vc-6")
    app_a = _element(db_session, org.id, "AppA")
    cap_a = _element(db_session, org.id, "CapA", type_="Capability", layer="strategy")
    _relationship(db_session, org.id, app_a, cap_a)
    _risk(db_session, org.id, app_a)
    capability = _capability(db_session, org.id, cap_a)
    _mapping(db_session, capability, _vendor(db_session))
    db_session.commit()

    entry = _risk_answer(app, org.id, app_a.id)["vendor_concentration"]["by_element"][str(cap_a.id)][
        "mappings"
    ][0]

    assert entry["annual_spend"] is None and entry["contract_end_date"] is None
    assert entry["lock_in_risk"] is None and entry["access_reason"] is None


def test_no_capability_in_the_radii_and_the_no_risk_branch(app, db_session, make_org):
    org = make_org("vc-7")
    app_a = _element(db_session, org.id, "AppA")
    other = _element(db_session, org.id, "Other")
    _relationship(db_session, org.id, app_a, other)
    _risk(db_session, org.id, app_a)
    lonely = _element(db_session, org.id, "Lonely")
    db_session.commit()

    assert _risk_answer(app, org.id, app_a.id)["vendor_concentration"] == {
        "by_element": None, "reason": "no_capability_in_chain",
    }
    assert _risk_answer(app, org.id, lonely.id)["vendor_concentration"] == {
        "by_element": None, "reason": "no_risk_recorded",
    }


def test_portfolio_answer_for_a_capability_and_for_an_application(app, db_session, make_org):
    org, app_a, cap_a, capability, rows = _world(db_session, make_org, "vc-8")

    capability_answer = _portfolio_answer(app, org.id, cap_a.id)
    assert capability_answer["vendor_concentration"]["mapping_count"] == 2

    application_answer = _portfolio_answer(app, org.id, app_a.id)
    assert application_answer["vendor_concentration"] == {
        "mappings": None, "mapping_count": None, "single_vendor": None,
        "reason": "no_capability_in_chain", "source": "unified_capability_vendor_organization_mappings",
    }


# (9) shape, and the helper runs once per risk answer
def test_block_and_entry_shapes_and_one_helper_call_per_answer(app, db_session, make_org, monkeypatch):
    from app.modules.intelligence.services import query_service

    org, app_a, cap_a, capability, rows = _world(db_session, make_org, "vc-9")
    _risk(db_session, org.id, app_a)
    _risk(db_session, org.id, app_a)
    db_session.commit()

    calls = []
    real = query_service._vendor_mappings_for_capability_elements
    monkeypatch.setattr(
        query_service,
        "_vendor_mappings_for_capability_elements",
        lambda ids, org_id: calls.append(list(ids)) or real(ids, org_id),
    )

    result = _risk_answer(app, org.id, app_a.id)

    assert len(result["risks"]) == 3 and len(calls) == 1
    block = result["vendor_concentration"]["by_element"][str(cap_a.id)]
    assert set(block.keys()) == BLOCK_KEYS
    assert all(set(entry.keys()) == ENTRY_KEYS for entry in block["mappings"])


# (10) three selects regardless of size
def _selects(app, counter, world):
    org, app_a, cap_a, capability, rows = world
    counter.statements.clear()
    _portfolio_answer(app, org.id, cap_a.id)
    return (
        counter.touching("unified_capabilities"),
        counter.touching("unified_capability_vendor_organization_mappings"),
        counter.touching("vendor_organizations"),
    )


def test_three_selects_for_one_mapping_and_for_twenty_and_none_without_a_capability(
    app, db_session, make_org, counter
):
    one_world = _world(db_session, make_org, "vc-10a", mappings=1)
    twenty_world = _world(db_session, make_org, "vc-10b", mappings=20)
    plain_world = _world(db_session, make_org, "vc-10c")

    one = _selects(app, counter, one_world)
    twenty = _selects(app, counter, twenty_world)

    assert one == twenty == (1, 1, 1)

    org, app_a, cap_a, capability, rows = plain_world
    counter.statements.clear()
    _portfolio_answer(app, org.id, app_a.id)
    assert counter.touching("unified_capability_vendor_organization_mappings") == 0


# (11) nothing the risk answer already said changes
def test_risk_rows_are_the_same_with_and_without_mappings(app, db_session, make_org):
    org, app_a, cap_a, capability, rows = _world(db_session, make_org, "vc-11", mappings=0)

    def stable(answer):
        risk = answer["risks"][0]
        summary = {k: v for k, v in risk["affected_summary"].items() if k != "latency_ms"}
        return risk["affected_rows"], summary, risk["risk_score"], risk["risk_level"], answer["reasons"]

    before = stable(_risk_answer(app, org.id, app_a.id))
    _mapping(db_session, capability, _vendor(db_session), annual_spend=5)
    db_session.commit()

    assert stable(_risk_answer(app, org.id, app_a.id)) == before


# (12) nothing invented, summed or ranked
def test_no_summed_spend_no_earliest_end_no_worst_word_and_no_value_beside_a_reason(
    app, db_session, make_org
):
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org, app_a, cap_a, capability, rows = _world(db_session, make_org, "vc-12", mappings=0)
    org2, app2, cap2, capability2, rows2 = _world(db_session, make_org, "vc-12b", mappings=2)

    block = _risk_answer(app, org.id, app_a.id)["vendor_concentration"]["by_element"][str(cap_a.id)]
    assert block["reason"] in REASON_CODES
    assert block["mappings"] is None and block["mapping_count"] is None and block["single_vendor"] is None
    text = json.dumps(block)
    assert '"mapping_count": 0' not in text and '"single_vendor": false' not in text

    answer = _risk_answer(app, org2.id, app2.id)["vendor_concentration"]
    for key in ("total_spend", "earliest_contract_end", "worst_risk", "score", "rank"):
        assert key not in json.dumps(answer)


# (13) routes and redaction
def test_routes_carry_the_block_and_redact_spend_for_a_restricted_role_only(
    app, db_session, make_org, client, login_as
):
    org, app_a, cap_a, capability, rows = _world(db_session, make_org, "vc-13")
    restricted = _make_user(db_session, org, enterprise_role="solution_architect")
    budget_holder = _make_user(db_session, org, enterprise_role="cto")
    db_session.commit()

    def risk_entries(user):
        login_as(client, user)
        data = client.get(f"/api/v1/intelligence/risk/{app_a.id}").get_json()["data"]
        return data["vendor_concentration"]["by_element"][str(cap_a.id)]["mappings"]

    hidden = risk_entries(restricted)
    assert all(e["annual_spend"] is None for e in hidden)
    assert all(e["access_reason"] == "financial_data_restricted" for e in hidden)
    assert all(e["lock_in_risk"] == "high" for e in hidden)
    assert [e["annual_spend"] for e in risk_entries(budget_holder)] == [1000.0, 2000.0]

    login_as(client, restricted)
    portfolio = client.get(f"/api/v1/intelligence/portfolio/{cap_a.id}").get_json()["data"]
    assert all(e["annual_spend"] is None for e in portfolio["vendor_concentration"]["mappings"])
    login_as(client, budget_holder)
    portfolio = client.get(f"/api/v1/intelligence/portfolio/{cap_a.id}").get_json()["data"]
    assert [e["annual_spend"] for e in portfolio["vendor_concentration"]["mappings"]] == [1000.0, 2000.0]
