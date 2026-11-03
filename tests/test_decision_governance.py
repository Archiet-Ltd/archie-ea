"""Decision governance: who is on a decision, and where the model has drifted from it.

Real organisations, elements, relationships and decisions written through the ORM. Covers each
drift check, the routing of a finding to the decision's owner, adding and removing people on a
decision, the governance page, and that one organisation never sees another's decisions or people.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture(autouse=True)
def _no_two_factor_banner(monkeypatch):
    """The first-week two-factor banner is not under test here; its date format ('%-d') is glibc-only
    and raises on Windows, so it is switched off to let these tests run on any machine."""
    from app.services import mfa_service

    monkeypatch.setattr(mfa_service, "window_ends_at", lambda user: None)


def _forget_cached_user():
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


def _signed_in(app, db_session, org):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(email=f"gov-{uuid.uuid4().hex[:8]}@example.com", first_name="View", last_name="Point",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid
    _forget_cached_user()
    return client


def _element(db_session, org, name, type_, layer="Motivation", description=None):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org.id, description=description)
    db_session.add(el)
    db_session.flush()
    return el


def _relate(db_session, org, source, target, type_="Influence"):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org.id)
    db_session.add(rel)
    db_session.flush()
    return rel



def _decision(db_session, org, title, status="accepted", element_ids=(), **kw):
    from app.models.architecture_decision import ArchitectureDecision

    d = ArchitectureDecision(title=title, status=status, organization_id=org.id,
                             archimate_element_ids=[e.id if hasattr(e, "id") else e for e in element_ids], **kw)
    db_session.add(d)
    db_session.flush()
    return d


def _party(db_session, org, decision, element, role):
    from app.models.architecture_decision import DecisionParty

    p = DecisionParty(decision_id=decision.id, element_id=element.id, role=role, organization_id=org.id)
    db_session.add(p)
    db_session.flush()
    return p


def _keys(org, decision=None):
    from app.services.decision_governance import findings

    out = findings(org.id)
    if decision is not None:
        out = [f for f in out if f.decision_id == decision.id]
    return out


def test_unowned_accepted_decision_is_high_and_owner_clears_it(db_session, make_org):
    org = make_org("gov-owner")
    cap = _element(db_session, org, "Payments", "Capability", "Strategy")
    cfo = _element(db_session, org, "CFO", "Business Role", "Business")
    d = _decision(db_session, org, "Single payments platform", element_ids=[cap])
    found = _keys(org, d)
    assert [(f.key, f.severity) for f in found] == [("no_owner", "high")]
    _party(db_session, org, d, cfo, "owner")
    assert _keys(org, d) == []


def test_proposed_unowned_is_medium_and_rejected_is_ignored(db_session, make_org):
    org = make_org("gov-status")
    cap = _element(db_session, org, "Claims", "Capability", "Strategy")
    proposed = _decision(db_session, org, "Proposed", status="proposed", element_ids=[cap])
    rejected = _decision(db_session, org, "Rejected", status="rejected", element_ids=[cap])
    assert [f.severity for f in _keys(org, proposed)] == ["medium"]
    assert _keys(org, rejected) == []


def test_element_owner_not_consulted_is_found_and_routed(db_session, make_org):
    org = make_org("gov-consult")
    app_el = _element(db_session, org, "Ledger", "ApplicationComponent", "Application")
    owner = _element(db_session, org, "Finance IT", "BusinessActor", "Business")
    arch = _element(db_session, org, "Lead Architect", "BusinessRole", "Business")
    _relate(db_session, org, owner, app_el, "Assignment")
    d = _decision(db_session, org, "Ledger on Postgres", element_ids=[app_el])
    _party(db_session, org, d, arch, "owner")
    found = _keys(org, d)
    assert [f.key for f in found] == ["owner_not_consulted"]
    assert found[0].route_to == ["Lead Architect", "Finance IT"]
    _party(db_session, org, d, owner, "consulted")
    assert _keys(org, d) == []


def test_lower_case_relationship_and_spaced_type_still_count(db_session, make_org):
    org = make_org("gov-case")
    app_el = _element(db_session, org, "CRM", "ApplicationComponent", "Application")
    owner = _element(db_session, org, "Sales Ops", "Business Actor", "Business")
    _relate(db_session, org, owner, app_el, "assignment")
    d = _decision(db_session, org, "CRM SaaS", element_ids=[app_el])
    _party(db_session, org, d, owner, "owner")
    assert _keys(org, d) == []


def test_removed_element_and_empty_scope_are_found(db_session, make_org):
    org = make_org("gov-missing")
    role = _element(db_session, org, "Architect", "BusinessRole", "Business")
    stale = _decision(db_session, org, "Points at nothing", element_ids=[987654321])
    empty = _decision(db_session, org, "Names nothing")
    _party(db_session, org, stale, role, "owner")
    _party(db_session, org, empty, role, "owner")
    assert [f.key for f in _keys(org, stale)] == ["missing_element"]
    assert [f.key for f in _keys(org, empty)] == ["nothing_affected"]


def test_expired_deviation_is_high(db_session, make_org):
    org = make_org("gov-expired")
    role = _element(db_session, org, "Architect", "BusinessRole", "Business")
    node = _element(db_session, org, "Oracle 11", "SystemSoftware", "Technology")
    d = _decision(db_session, org, "Keep Oracle for now", element_ids=[node], decision_type="deviation",
                  valid_until=datetime.utcnow() - timedelta(days=1))
    _party(db_session, org, d, role, "owner")
    assert [(f.key, f.severity) for f in _keys(org, d)] == [("expired_exception", "high")]


def test_stakeholder_concern_without_decision_until_one_names_it(db_session, make_org):
    org = make_org("gov-concern")
    cfo = _element(db_session, org, "CFO", "Stakeholder")
    driver = _element(db_session, org, "Cost pressure", "Driver")
    _relate(db_session, org, cfo, driver, "Association")
    found = [f for f in _keys(org) if f.key == "unanswered_concern"]
    assert len(found) == 1 and found[0].route_to == ["CFO"] and found[0].element_name == "Cost pressure"
    role = _element(db_session, org, "Architect", "BusinessRole", "Business")
    d = _decision(db_session, org, "Consolidate data centres", element_ids=[driver])
    _party(db_session, org, d, role, "owner")
    assert [f for f in _keys(org) if f.key == "unanswered_concern"] == []


def test_findings_never_cross_organisations(db_session, make_org):
    mine, theirs = make_org("gov-mine"), make_org("gov-theirs")
    _decision(db_session, theirs, "Their unowned decision")
    assert _keys(mine) == []


def test_people_are_added_and_removed_on_the_decision_page(app, db_session, make_org):
    from app.models.architecture_decision import DecisionParty

    org = make_org("gov-page")
    role = _element(db_session, org, f"Chief Architect {uuid.uuid4().hex[:4]}", "BusinessRole", "Business")
    d = _decision(db_session, org, "Event bus for integration")
    client = _signed_in(app, db_session, org)
    html = client.get(f"/architecture/decisions/{d.id}").get_data(as_text=True)
    assert 'data-testid="decision-parties-empty"' in html and role.name in html
    resp = client.post(f"/architecture/decisions/{d.id}/parties", data={"element_id": role.id, "role": "owner"})
    assert resp.status_code == 302
    party = DecisionParty.query.filter_by(decision_id=d.id).one()
    assert party.role == "owner" and party.organization_id == org.id
    html = client.get(f"/architecture/decisions/{d.id}").get_data(as_text=True)
    assert 'data-role="owner"' in html
    client.post(f"/architecture/decisions/{d.id}/parties/{party.id}/delete")
    assert DecisionParty.query.filter_by(decision_id=d.id).count() == 0


def test_another_organisations_element_cannot_be_added(app, db_session, make_org):
    from app.models.architecture_decision import DecisionParty

    mine, theirs = make_org("gov-add-mine"), make_org("gov-add-theirs")
    foreign = _element(db_session, theirs, "Their role", "BusinessRole", "Business")
    plain = _element(db_session, mine, "A goal", "Goal")
    d = _decision(db_session, mine, "Mine")
    client = _signed_in(app, db_session, mine)
    client.post(f"/architecture/decisions/{d.id}/parties", data={"element_id": foreign.id, "role": "owner"})
    client.post(f"/architecture/decisions/{d.id}/parties", data={"element_id": plain.id, "role": "owner"})
    client.post(f"/architecture/decisions/{d.id}/parties", data={"element_id": plain.id, "role": "boss"})
    assert DecisionParty.query.filter_by(decision_id=d.id).count() == 0


def test_governance_page_lists_findings_routed_to_owner(app, db_session, make_org):
    org = make_org("gov-view")
    tag = uuid.uuid4().hex[:6]
    cap = _element(db_session, org, f"Onboarding {tag}", "Capability", "Strategy")
    _decision(db_session, org, f"Unowned {tag}", element_ids=[cap])
    client = _signed_in(app, db_session, org)
    resp = client.get("/architecture/decisions/governance")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'data-check="no_owner"' in html and f"Unowned {tag}" in html
    assert "No owner yet" in html


def test_governance_page_says_so_when_checks_fail(app, db_session, make_org, monkeypatch):
    from app.services import decision_governance

    def boom(org_id, now=None):
        raise RuntimeError("model read failed")

    monkeypatch.setattr(decision_governance, "findings", boom)
    client = _signed_in(app, db_session, make_org("gov-fail"))
    resp = client.get("/architecture/decisions/governance")
    assert resp.status_code == 500
    assert 'data-testid="governance-error"' in resp.get_data(as_text=True)
