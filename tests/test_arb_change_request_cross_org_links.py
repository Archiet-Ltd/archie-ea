"""A change request cannot be linked to another organisation's capability or solution.

Two real organisations: the person in organisation A names organisation B's capability or solution number
and is told, against that field, that it is not in their organisation. Nothing is created.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _architect(db_session, org, tag):
    from app.models.user import Role, User

    user = User(email=f"cr.link.{tag}.{org.id}@example.com", first_name="Link", last_name="Check",
                organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
                role=Role.query.filter_by(name="Architect").one())
    user.password = "Passw0rd!Passw0rd!"
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def two_orgs(db_session, make_org):
    from app.models.solution_models import Solution
    from app.models.unified_capability import UnifiedCapability

    tag = uuid.uuid4().hex[:8]
    org_a, org_b = make_org("cra"), make_org("crb")
    user_a, user_b = _architect(db_session, org_a, tag), _architect(db_session, org_b, tag)
    cap_a = UnifiedCapability(name=f"A capability {tag}", organization_id=org_a.id, scope="tenant", level=1)
    cap_b = UnifiedCapability(name=f"B capability {tag}", organization_id=org_b.id, scope="tenant", level=1)
    sol_a = Solution(name=f"A solution {tag}", description="A", organization_id=org_a.id,
                     created_by_id=user_a.id, governance_status="draft")
    sol_b = Solution(name=f"B solution {tag}", description="B", organization_id=org_b.id,
                     created_by_id=user_b.id, governance_status="draft")
    db_session.add_all([cap_a, cap_b, sol_a, sol_b])
    db_session.flush()
    return {"tag": tag, "user_a": user_a, "cap_a": cap_a, "cap_b": cap_b, "sol_a": sol_a, "sol_b": sol_b}


def _count(tag):
    from app.models.architecture_decision import ArchitectureChangeRequest

    return ArchitectureChangeRequest.query.filter(
        ArchitectureChangeRequest.title.like(f"%{tag}")).count()


def _payload(world, **links):
    return {"title": f"Link check {world['tag']}", "description": "Move to the shared store.",
            "trigger_type": "regulation", **links}


@pytest.mark.parametrize("field,key,label", [
    ("capability_id", "cap_b", "capability"),
    ("solution_id", "sol_b", "solution"),
])
def test_another_organisations_record_is_refused_against_its_field_and_nothing_is_created(
        client, login_as, two_orgs, field, key, label):
    foreign = two_orgs[key].id
    login_as(client, two_orgs["user_a"])
    response = client.post("/arb/api/change-requests", json=_payload(two_orgs, **{field: foreign}))
    assert response.status_code == 400
    body = response.get_json()
    assert body["errors"][field] == [f"No {label} with number {foreign} exists in your organisation."]
    assert _count(two_orgs["tag"]) == 0


def test_the_same_request_naming_the_callers_own_records_is_created(client, login_as, two_orgs):
    login_as(client, two_orgs["user_a"])
    response = client.post("/arb/api/change-requests", json=_payload(
        two_orgs, capability_id=two_orgs["cap_a"].id, solution_id=two_orgs["sol_a"].id))
    assert response.status_code == 201
    assert _count(two_orgs["tag"]) == 1


def _accepted_decision_on(db_session, org, capability_name):
    """A business capability, the unified row projected from it, and an accepted decision governing it."""
    from app.models.architecture_decision import ArchitectureDecision, DecisionCapabilityLink
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    source = BusinessCapability(name=capability_name, organization_id=org.id, level=1)
    db_session.add(source)
    db_session.flush()
    decision = ArchitectureDecision(title=f"Decision for {capability_name}", organization_id=org.id,
                                    enterprise_level=False, status="accepted")
    db_session.add(decision)
    db_session.flush()
    db_session.add(DecisionCapabilityLink(decision_id=decision.id, capability_id=source.id))
    db_session.flush()
    return UnifiedCapability.query.filter_by(source_table="business_capability", source_id=str(source.id)).one()


@pytest.fixture
def assessable(db_session, two_orgs):
    from app.models.architecture_decision import ArchitectureChangeRequest

    org_a = two_orgs["user_a"].organization_id
    user_b = _architect(db_session, _org_of(db_session, two_orgs["cap_b"].organization_id), "other")
    cap_a = _accepted_decision_on(db_session, _org_of(db_session, org_a), f"A governed {two_orgs['tag']}")
    cap_b = _accepted_decision_on(db_session, _org_of(db_session, two_orgs["cap_b"].organization_id),
                                  f"B governed {two_orgs['tag']}")
    cr_a = ArchitectureChangeRequest(acr_reference=f"ACR-A-{two_orgs['tag']}", title=f"A request {two_orgs['tag']}",
                                     trigger_type="regulation", organization_id=org_a, capability_id=cap_a.id,
                                     raised_by_id=two_orgs["user_a"].id)
    cr_b = ArchitectureChangeRequest(acr_reference=f"ACR-B-{two_orgs['tag']}", title=f"B request {two_orgs['tag']}",
                                     trigger_type="regulation", organization_id=two_orgs["cap_b"].organization_id,
                                     capability_id=cap_b.id, raised_by_id=user_b.id)
    db_session.add_all([cr_a, cr_b])
    db_session.flush()
    return {**two_orgs, "cap_a_gov": cap_a, "cap_b_gov": cap_b, "cr_a": cr_a, "cr_b": cr_b}


def _org_of(db_session, org_id):
    from app.models.organization import Organization

    return db_session.get(Organization, org_id)


def _impacts(cr_id):
    from app.models.architecture_decision import ChangeImpactAssessment

    return ChangeImpactAssessment.query.filter_by(change_request_id=cr_id).count()


def test_assessing_another_organisations_request_finds_nothing_and_records_nothing(client, login_as, assessable):
    login_as(client, assessable["user_a"])
    response = client.post(f"/arb/api/change-requests/{assessable['cr_b'].id}/assess-impact", json={})
    assert response.status_code == 404
    assert _impacts(assessable["cr_b"].id) == 0
    assert assessable["cr_b"].status == "open"


def test_assessing_with_another_organisations_capability_is_refused_and_records_nothing(client, login_as, assessable):
    login_as(client, assessable["user_a"])
    foreign = assessable["cap_b_gov"].id
    response = client.post(f"/arb/api/change-requests/{assessable['cr_a'].id}/assess-impact",
                           json={"capability_ids": [foreign]})
    assert response.status_code == 400
    assert response.get_json()["error"] == f"No capability with number {foreign} exists in your organisation."
    assert _impacts(assessable["cr_a"].id) == 0


def test_the_raiser_assesses_their_own_request_with_their_own_capability(client, login_as, assessable):
    login_as(client, assessable["user_a"])
    response = client.post(f"/arb/api/change-requests/{assessable['cr_a'].id}/assess-impact", json={})
    assert response.status_code == 200
    assert _impacts(assessable["cr_a"].id) == 1


def test_deciding_and_noticing_another_organisations_request_find_nothing(client, login_as, db_session, assessable):
    approver = assessable["user_a"]
    approver.enterprise_role = "enterprise_architect"
    db_session.flush()
    login_as(client, approver)
    cr_id = assessable["cr_b"].id
    assert client.post(f"/arb/api/change-requests/{cr_id}/set-disposition", json={"disposition": "full_adm_cycle"}).status_code == 404
    assert client.post(f"/arb/api/change-requests/{cr_id}/issue-notice", json={"notice_text": "x"}).status_code == 404
    assert assessable["cr_b"].disposition is None and assessable["cr_b"].status == "open"


@pytest.mark.parametrize("path,body", [
    ("set-disposition", {"disposition": "full_adm_cycle"}),
    ("issue-notice", {"notice_text": "Closed by the raiser"}),
])
def test_the_raiser_cannot_decide_or_close_their_own_request(client, login_as, assessable, path, body):
    login_as(client, assessable["user_a"])
    response = client.post(f"/arb/api/change-requests/{assessable['cr_a'].id}/{path}", json=body)
    assert response.status_code == 403
    assert assessable["cr_a"].disposition is None and assessable["cr_a"].status == "open"


@pytest.mark.parametrize("path,body,status", [
    ("set-disposition", {"disposition": "full_adm_cycle"}, 200),
    ("issue-notice", {"notice_text": "Approved as asked"}, 201),
])
def test_a_board_approver_decides_and_closes_the_request(client, login_as, db_session, assessable, path, body, status):
    approver = assessable["user_a"]
    approver.enterprise_role = "enterprise_architect"
    db_session.flush()
    login_as(client, approver)
    response = client.post(f"/arb/api/change-requests/{assessable['cr_a'].id}/{path}", json=body)
    assert response.status_code == status
