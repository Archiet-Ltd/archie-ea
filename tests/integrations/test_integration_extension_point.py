"""The integration extension point, end to end below the browser.

A connector read becomes pending facts; a person's approval applies them through
the one set of writers; the elements, the team that owns them and the source of
each are then readable by the screens and answers that ask.
"""
import json
import uuid
from datetime import datetime

import pytest

from app import db
from app.integrations.fact_store import (
    apply_fact,
    record_facts,
    reject_fact,
    sources_for_elements,
)
from app.integrations.mappings.github import GitHubMapping
from app.models.integration_fact import IntegrationFact, IntegrationFactStatus
from app.models.models import ArchiMateElement, ArchiMateRelationship
from app.modules.ai_chat.services.ai_chat_approval_service import (
    AIChatApprovalService,
    create_approval_record,
)

GITHUB_URL = "https://github.com/acme/billing-service"


def _repo(**overrides):
    repo = {
        "id": 4242,
        "name": "billing-service",
        "full_name": "acme/billing-service",
        "description": "Invoices and payments",
        "html_url": GITHUB_URL,
        "language": "Python",
        "stargazers_count": 3,
        "forks_count": 1,
        "archived": False,
        "owner": {"login": "acme-platform", "type": "Organization"},
    }
    repo.update(overrides)
    return repo


def _approver(org_id, label="Approver"):
    """A person in the organisation who may approve changes."""
    from app.models.user import Permission, Role, User

    user = User(
        email=f"{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label,
        last_name="Reviewer",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="platform_administrator",
    )
    db.session.add(user)
    db.session.flush()
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER, index="main", default=False)
        db.session.add(role)
        db.session.flush()
    user.role = role
    db.session.flush()
    return user


def _approve(fact, approver):
    """Approve one fact the way the review page does: the integration proposes
    and a signed-in person decides."""
    approval = create_approval_record(
        organization_id=fact.organization_id,
        operation_type="create",
        entity_type="integration_fact",
        entity_id=fact.id,
        summary=f"Apply {fact.fact_type} fact",
        operation_payload={"fact_id": fact.id},
        user_id=None,
        source_table="integration_facts",
        source_id=fact.id,
    )
    db.session.commit()
    return AIChatApprovalService(user_id=approver.id).approve_and_execute(approval.id)


def _approve_all(org_id, item=None):
    approver = _approver(org_id)
    facts = IntegrationFact.query.filter_by(organization_id=org_id, status=IntegrationFactStatus.PENDING).all()
    order = {"element": 0, "relationship": 1, "owner": 2}
    for fact in sorted(facts, key=lambda f: (order[f.fact_type], f.id)):
        result = _approve(fact, approver)
        assert result.get("success") is True, result
    return approver


def _element(org_id, name):
    return ArchiMateElement.query.filter_by(organization_id=org_id, name=name).one()


@pytest.fixture
def two_orgs(app, db_session, make_org):
    return make_org("a"), make_org("b")


# ── the approval executor takes the integration_fact entity type ─────────────


def test_approving_an_integration_fact_applies_it_and_nothing_before(app, db_session, two_orgs):
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    assert ArchiMateElement.query.filter_by(organization_id=org.id, name="billing-service").count() == 0

    element_fact = IntegrationFact.query.filter_by(organization_id=org.id, fact_type="element").one()
    result = _approve(element_fact, _approver(org.id))

    assert result["success"] is True
    db.session.refresh(element_fact)
    assert element_fact.status == IntegrationFactStatus.APPLIED
    element = _element(org.id, "billing-service")
    assert element.id == element_fact.applied_element_id
    assert element.type == "ApplicationComponent"


def test_the_person_who_approves_is_never_refused_as_the_requester(app, db_session, two_orgs):
    """The integration proposes; a proposal that named the approver as its
    requester would be refused as self-approval and nothing could be applied."""
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    fact = IntegrationFact.query.filter_by(organization_id=org.id, fact_type="element").one()
    approver = _approver(org.id)

    approval = create_approval_record(
        organization_id=org.id,
        operation_type="create",
        entity_type="integration_fact",
        entity_id=fact.id,
        summary="Apply element fact",
        operation_payload={"fact_id": fact.id},
        user_id=approver.id,
    )
    db.session.commit()
    refused = AIChatApprovalService(user_id=approver.id).approve_and_execute(approval.id)

    assert refused["success"] is False and refused.get("code") == "APPROVAL_DENIED"
    db.session.refresh(fact)
    assert fact.status == IntegrationFactStatus.PENDING


def test_rejecting_the_approval_rejects_the_fact(app, db_session, two_orgs):
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    fact = IntegrationFact.query.filter_by(organization_id=org.id, fact_type="element").one()
    approval = create_approval_record(
        organization_id=org.id,
        operation_type="create",
        entity_type="integration_fact",
        entity_id=fact.id,
        summary="Apply element fact",
        operation_payload={"fact_id": fact.id},
        user_id=None,
    )
    db.session.commit()

    result = AIChatApprovalService(user_id=_approver(org.id).id).reject_approval(approval.id, reason="Not ours")

    assert result["success"] is True
    db.session.refresh(fact)
    assert fact.status == IntegrationFactStatus.REJECTED
    assert fact.rejection_reason == "Not ours"
    assert ArchiMateElement.query.filter_by(organization_id=org.id, name="billing-service").count() == 0


def test_an_approval_cannot_apply_another_organisations_fact(app, db_session, two_orgs):
    org_a, org_b = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org_a.id)
    foreign = IntegrationFact.query.filter_by(organization_id=org_a.id, fact_type="element").one()
    approval = create_approval_record(
        organization_id=org_b.id,
        operation_type="create",
        entity_type="integration_fact",
        entity_id=foreign.id,
        summary="Apply element fact",
        operation_payload={"fact_id": foreign.id},
        user_id=None,
    )
    db.session.commit()

    result = AIChatApprovalService(user_id=_approver(org_b.id).id).approve_and_execute(approval.id)

    assert result["success"] is False
    db.session.refresh(foreign)
    assert foreign.status == IntegrationFactStatus.PENDING
    assert ArchiMateElement.query.filter_by(name="billing-service").count() == 0


def test_a_fact_is_applied_once(app, db_session, two_orgs):
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    fact = IntegrationFact.query.filter_by(organization_id=org.id, fact_type="element").one()

    assert apply_fact(fact) is True
    assert apply_fact(fact) is False
    assert ArchiMateElement.query.filter_by(organization_id=org.id, name="billing-service").count() == 1


# ── recording is idempotent ──────────────────────────────────────────────────


def test_a_second_read_with_no_changes_proposes_nothing(app, db_session, two_orgs):
    org, _ = two_orgs
    first = record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    again = record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)

    assert len(first) == 2 and again == []
    assert IntegrationFact.query.filter_by(organization_id=org.id).count() == 2

    _approve_all(org.id)
    assert record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id) == []
    assert IntegrationFact.query.filter_by(organization_id=org.id).count() == 2


def test_a_changed_pending_fact_is_refreshed_not_duplicated(app, db_session, two_orgs):
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)

    proposed = record_facts(mapping=GitHubMapping(), item=_repo(description="Now with refunds"), organization_id=org.id)

    assert proposed == []
    fact = IntegrationFact.query.filter_by(organization_id=org.id, fact_type="element").one()
    assert json.loads(fact.fact_data)["description"] == "Now with refunds"


def test_a_rejected_fact_is_not_proposed_again(app, db_session, two_orgs):
    org, _ = two_orgs
    facts = record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    for fact in facts:
        reject_fact(fact, reason="Not ours")

    assert record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id) == []
    assert IntegrationFact.query.filter_by(organization_id=org.id, status=IntegrationFactStatus.PENDING).count() == 0


# ── relationship provenance: connector against manual ────────────────────────


def test_a_relationship_records_the_integration_it_came_from(app, db_session, two_orgs):
    from app.modules.architecture.services.archimate_relationship_service import ArchiMateRelationshipService

    org, _ = two_orgs
    actor = ArchiMateElement(name="Platform team", type="BusinessActor", layer="business", organization_id=org.id)
    service = ArchiMateElement(name="Payments", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add_all([actor, service])
    db_session.flush()
    read_at = datetime(2026, 10, 1, 9, 30)

    from_connector = ArchiMateRelationshipService.create_relationship(
        actor, service, "assignment", None, organization_id=org.id,
        provenance={
            "source_kind": "integration",
            "source_ref": "github:4242:owners.0",
            "source_label": "github repository",
            "source_recorded_at": read_at,
        },
    )
    drawn = ArchiMateRelationshipService.create_relationship(
        service, actor, "association", None, organization_id=org.id,
    )

    assert (from_connector.source_kind, from_connector.source_ref, from_connector.source_label) == (
        "integration", "github:4242:owners.0", "github repository",
    )
    assert from_connector.source_recorded_at.replace(tzinfo=None) == read_at
    assert drawn.source_kind == "manual"
    assert drawn.source_ref is None or drawn.source_ref != "github:4242:owners.0"


# ── team owners ──────────────────────────────────────────────────────────────


def test_applying_a_team_owner_assigns_the_team_to_the_application(app, db_session, two_orgs):
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    _approve_all(org.id)

    team = _element(org.id, "acme-platform")
    application = _element(org.id, "billing-service")
    assert team.type == "BusinessActor"
    assert team.custom_properties["source_kind"] == "integration"
    assignment = ArchiMateRelationship.query.filter_by(
        organization_id=org.id, type="assignment", source_id=team.id, target_id=application.id
    ).one()
    assert assignment.source_kind == "integration"
    assert assignment.source_ref == "github:4242:owners.0"


def test_a_team_that_owns_two_repositories_is_one_actor(app, db_session, two_orgs):
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    record_facts(
        mapping=GitHubMapping(),
        item=_repo(id=4343, name="ledger", full_name="acme/ledger", html_url="https://github.com/acme/ledger"),
        organization_id=org.id,
    )
    _approve_all(org.id)

    assert ArchiMateElement.query.filter_by(organization_id=org.id, name="acme-platform").count() == 1
    team = _element(org.id, "acme-platform")
    assert ArchiMateRelationship.query.filter_by(
        organization_id=org.id, type="assignment", source_id=team.id
    ).count() == 2


def test_owners_of_returns_team_owners_alongside_person_owners(app, db_session, two_orgs):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User
    from app.services.element_ownership_service import owners_of

    org, other = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    _approve_all(org.id)
    application = _element(org.id, "billing-service")

    assert owners_of(application.id, org.id) == [{"name": "acme-platform", "role": "Team"}]

    row = ApplicationComponent(name="Billing", organization_id=org.id, archimate_element_id=application.id)
    person = User(first_name="Ada", last_name="Owner", email=f"ada-{uuid.uuid4().hex[:6]}@example.com",
                  organization_id=org.id)
    db_session.add_all([row, person])
    db_session.flush()
    db_session.add(ApplicationOwner(
        application_id=row.id, user_id=person.id, organization_id=org.id, ownership_type="primary",
    ))
    db_session.commit()

    assert owners_of(application.id, org.id) == [
        {"name": "Ada Owner", "role": "Primary"},
        {"name": "acme-platform", "role": "Team"},
    ]
    assert owners_of(application.id, other.id) == []


def test_only_a_business_actor_assigned_to_an_element_is_its_team_owner(app, db_session, two_orgs):
    from app.services.element_ownership_service import owners_of

    org, _ = two_orgs
    target = ArchiMateElement(name="Payments", type="ApplicationComponent", layer="application", organization_id=org.id)
    component = ArchiMateElement(name="Batch job", type="ApplicationComponent", layer="application", organization_id=org.id)
    actor = ArchiMateElement(name="Finance", type="BusinessActor", layer="business", organization_id=org.id)
    db_session.add_all([target, component, actor])
    db_session.flush()
    db_session.add_all([
        ArchiMateRelationship(source_id=component.id, target_id=target.id, type="assignment", organization_id=org.id),
        ArchiMateRelationship(source_id=actor.id, target_id=target.id, type="serving", organization_id=org.id),
    ])
    db_session.commit()

    assert owners_of(target.id, org.id) == []

    db_session.add(ArchiMateRelationship(source_id=actor.id, target_id=target.id, type="assignment", organization_id=org.id))
    db_session.commit()

    assert owners_of(target.id, org.id) == [{"name": "Finance", "role": "Team"}]


# ── the source of a connector-derived element ────────────────────────────────


def test_a_connector_derived_element_names_its_source(app, db_session, two_orgs):
    org, other = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    _approve_all(org.id)
    application = _element(org.id, "billing-service")
    team = _element(org.id, "acme-platform")
    drawn = ArchiMateElement(name="Hand drawn", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(drawn)
    db_session.commit()

    sources = sources_for_elements([application.id, team.id, drawn.id], org.id)

    assert set(sources) == {application.id, team.id}
    assert sources[application.id]["system"] == "github"
    assert sources[application.id]["item"] == "billing-service"
    assert sources[application.id]["url"] == GITHUB_URL
    assert sources[application.id]["read_at"]
    assert sources[team.id]["item"] == "billing-service"
    assert sources_for_elements([application.id], other.id) == {}


def test_a_source_address_that_is_not_a_web_address_is_not_linked(app, db_session, two_orgs):
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(html_url="javascript:alert(1)"), organization_id=org.id)
    _approve_all(org.id)
    application = _element(org.id, "billing-service")

    assert sources_for_elements([application.id], org.id)[application.id]["url"] is None


# ── citations carry the source ───────────────────────────────────────────────


def _ask(app, org_id, element_id, kind="impact"):
    from flask import g

    from app.modules.intelligence.services.answer_envelope import ask_and_store

    params = {"element_id": element_id}
    if kind == "impact":
        params["direction"] = "both"
    with app.test_request_context("/"):
        g.current_org_id = org_id
        return ask_and_store(kind, params)


def test_impact_citations_from_a_connector_carry_the_source(app, db_session, two_orgs):
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    _approve_all(org.id)
    application = _element(org.id, "billing-service")
    team = _element(org.id, "acme-platform")

    record = _ask(app, org.id, application.id)

    cited = {(c["type"], c["id"]): c for c in record.citations}
    assignment = ArchiMateRelationship.query.filter_by(
        organization_id=org.id, type="assignment", source_id=team.id, target_id=application.id
    ).one()
    source = cited[("relationship", assignment.id)]["source"]
    assert set(source) == {"system", "item", "url", "read_at"}
    assert source["system"] == "github" and source["item"] == "billing-service" and source["url"] == GITHUB_URL
    assert cited[("element", team.id)]["source"] == source


def test_a_risk_on_a_connector_derived_element_cites_the_source(app, db_session, two_orgs):
    from app.models.risk import Risk

    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    _approve_all(org.id)
    application = _element(org.id, "billing-service")
    risk = Risk(organization_id=org.id, archimate_element_id=application.id, title="Unpatched dependency",
                likelihood=4, impact=5)
    db_session.add(risk)
    db_session.commit()

    record = _ask(app, org.id, application.id, kind="risk_propagation")

    cited = {(c["type"], c["id"]): c for c in record.citations}
    assert cited[("risk", risk.id)]["source"]["item"] == "billing-service"
    assert cited[("risk", risk.id)]["source"]["url"] == GITHUB_URL


def test_citations_without_a_connector_origin_are_unchanged(app, db_session, two_orgs):
    org, _ = two_orgs
    first = ArchiMateElement(name="Hand drawn A", type="ApplicationComponent", layer="application", organization_id=org.id)
    second = ArchiMateElement(name="Hand drawn B", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add_all([first, second])
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=first.id, target_id=second.id, type="serving", organization_id=org.id))
    db_session.commit()

    record = _ask(app, org.id, first.id)

    assert record.citations
    assert all(set(c) <= {"type", "id", "origin", "recorded_at", "confidence"} for c in record.citations)
    assert {c["type"] for c in record.citations} == {"relationship"}


# ── the review routes ────────────────────────────────────────────────────────


def test_the_review_routes_only_touch_the_signed_in_organisations_facts(app, db_session, two_orgs, client, login_as):
    org_a, org_b = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org_a.id)
    foreign_ids = [f.id for f in IntegrationFact.query.filter_by(organization_id=org_a.id)]
    person_b = _approver(org_b.id, "Bperson")
    db_session.commit()

    login_as(client, person_b)
    page = client.get("/integrations/review")
    assert page.status_code == 200
    assert b"billing-service" not in page.data

    login_as(client, person_b)
    approved = client.post("/integrations/approve", json={"fact_ids": foreign_ids})
    login_as(client, person_b)
    rejected = client.post("/integrations/reject", json={"fact_ids": foreign_ids, "reason": "x"})

    assert approved.get_json()["approved"] == [] and rejected.get_json()["rejected"] == []
    statuses = db.session.execute(
        db.text("SELECT DISTINCT status::text FROM integration_facts WHERE organization_id = :org"),
        {"org": org_a.id},
    ).scalars().all()
    assert statuses == ["PENDING"]
    assert ArchiMateElement.query.filter_by(name="billing-service").count() == 0


def test_approve_all_applies_the_elements_before_what_depends_on_them(app, db_session, two_orgs, client, login_as):
    org, _ = two_orgs
    record_facts(mapping=GitHubMapping(), item=_repo(), organization_id=org.id)
    person = _approver(org.id, "Reviewer")
    # Newest first, as the review page lists them: the owner fact before the element.
    ids = [f.id for f in IntegrationFact.query.filter_by(organization_id=org.id).order_by(IntegrationFact.id.desc())]
    db_session.commit()

    login_as(client, person)
    response = client.post("/integrations/approve", json={"fact_ids": ids})

    body = response.get_json()
    assert body["success"] is True, body
    assert sorted(body["approved"]) == sorted(ids)
    assert ArchiMateRelationship.query.filter_by(organization_id=org.id, type="assignment").count() == 1
