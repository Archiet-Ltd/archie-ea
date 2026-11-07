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
