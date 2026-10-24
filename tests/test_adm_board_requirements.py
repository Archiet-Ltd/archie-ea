"""The requirements the ADM board shows come from the requirements backlog's own store.

A requirement names the solution it serves and its TOGAF phase on the one
``solution_requirements`` row; the board reads and writes those, nothing else.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _org(db_session, make_org, label):
    from app.models.solution_architect_models import RequirementType, SolutionRequirement
    from app.models.solution_models import Solution
    from app.models.user import Role, User

    org = make_org(label)
    Role.insert_roles()
    user = User(
        email="req.%s@example.com" % uuid.uuid4().hex[:8], first_name="A", last_name="B",
        organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        role=Role.query.filter_by(name="Viewer").first(),
    )
    user.password = "Passw0rd!x"
    db_session.add(user)
    solution = Solution(name="%s solution" % label, organization_id=org.id, adm_phase="D")
    db_session.add(solution)
    db_session.flush()

    def requirement(name, priority=None, phase=None, linked=True):
        row = SolutionRequirement(
            name=name, description="Test requirement.", organization_id=org.id, status="Draft", moscow_priority=priority,
            togaf_phase=phase, requirement_type=RequirementType.FUNCTIONAL,
            solution_id=solution.id if linked else None)
        db_session.add(row)
        db_session.flush()
        return row

    return org, user, solution, requirement


def test_a_solutions_requirements_come_highest_priority_first(db_session, make_org, tenant_ctx):
    from app.services.adm_requirement_service import requirements_for_solutions

    org, _user, solution, requirement = _org(db_session, make_org, "alpha")
    requirement("could do", "COULD")
    requirement("must do", "MUST")
    requirement("unranked")
    requirement("should do", "SHOULD")
    with tenant_ctx(org.id):
        names = [r["name"] for r in requirements_for_solutions([solution.id])[solution.id]]
    assert names == ["must do", "should do", "could do", "unranked"]


def test_each_organisation_sees_only_its_own_requirements(db_session, make_org, client, login_as):
    org_a, user_a, solution_a, req_a = _org(db_session, make_org, "alpha")
    org_b, user_b, solution_b, req_b = _org(db_session, make_org, "bravo")
    own = req_a("Alpha requirement", "MUST", "D")
    other = req_b("Bravo requirement", "MUST", "D")
    db_session.flush()

    login_as(client, user_a)
    listed = client.get("/api/adm-kanban/v2/requirements")
    assert listed.status_code == 200
    rows = listed.get_json()["requirements"]
    assert [r["name"] for r in rows] == ["Alpha requirement"]
    assert rows[0]["phase_label"] == "Technology Architecture"
    assert rows[0]["solution_name"] == "alpha solution"

    # Another organisation's requirement and solution read as not found, and nothing changes.
    login_as(client, user_a)
    refused = client.patch("/api/adm-kanban/v2/requirements/%d/solution" % other.id,
                           json={"solution_id": solution_a.id})
    assert refused.status_code == 404
    assert other.solution_id == solution_b.id
    login_as(client, user_a)
    refused = client.patch("/api/adm-kanban/v2/requirements/%d/solution" % own.id,
                           json={"solution_id": solution_b.id})
    assert refused.status_code == 404
    assert own.solution_id == solution_a.id


def test_linking_writes_the_solution_and_a_missing_phase_and_unlinking_clears_it(
        db_session, make_org, client, login_as):
    org, user, solution, requirement = _org(db_session, make_org, "alpha")
    loose = requirement("Loose", "SHOULD", None, linked=False)
    other_solution_requirement = requirement("Phased", "SHOULD", "B", linked=False)
    db_session.flush()

    login_as(client, user)
    done = client.patch("/api/adm-kanban/v2/requirements/%d/solution" % loose.id,
                        json={"solution_id": solution.id})
    assert done.status_code == 200
    body = done.get_json()["requirement"]
    assert (body["solution_id"], body["phase"]) == (solution.id, "D")
    assert loose.solution_id == solution.id and loose.togaf_phase == "D"

    # A requirement that already has a phase keeps it.
    login_as(client, user)
    client.patch("/api/adm-kanban/v2/requirements/%d/solution" % other_solution_requirement.id,
                 json={"solution_id": solution.id})
    assert other_solution_requirement.togaf_phase == "B"

    login_as(client, user)
    cleared = client.patch("/api/adm-kanban/v2/requirements/%d/solution" % loose.id, json={"solution_id": None})
    assert cleared.status_code == 200 and loose.solution_id is None


def test_a_person_the_matrix_refuses_cannot_link(db_session, make_org, client, login_as):
    from app.models.user import Role, User

    org, _user, solution, requirement = _org(db_session, make_org, "alpha")
    loose = requirement("Loose", "SHOULD", None, linked=False)
    reader = User(
        email="reader.%s@example.com" % uuid.uuid4().hex[:8], first_name="R", last_name="D",
        organization_id=org.id, enterprise_role="viewer", confirmed=True,
        role=Role.query.filter_by(name="Viewer").first())
    reader.password = "Passw0rd!x"
    db_session.add(reader)
    db_session.flush()

    login_as(client, reader)
    refused = client.patch("/api/adm-kanban/v2/requirements/%d/solution" % loose.id,
                           json={"solution_id": solution.id})
    assert refused.status_code == 403
    assert loose.solution_id is None
