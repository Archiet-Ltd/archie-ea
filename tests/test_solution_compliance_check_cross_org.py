"""One organisation's compliance check never shows another organisation's controls or gaps.

Two organisations each hold a solution with controls mapped to it. The first architect runs the analysis on
their own solution and finds only their own controls; asking for the other organisation's solution answers
404, and the check on their own solution never carries a register gap that belongs to the other.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _estate(db_session, make_org, label, framework, control, status):
    from app.models.implementation_migration import Gap
    from app.models.solution_models import Solution
    from app.models.solution_sad_models import SolutionComplianceMapping
    from app.models.user import Role, User

    org = make_org(label)
    Role.insert_roles()
    user = User(email="%s.%s@example.com" % (label, uuid.uuid4().hex[:8]), first_name=label, last_name="Architect",
                organization_id=org.id, enterprise_role="solution_architect", confirmed=True)
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Passw0rd!x"
    db_session.add(user)
    db_session.flush()
    solution = Solution(name="%s platform" % label, organization_id=org.id, created_by_id=user.id)
    db_session.add(solution)
    db_session.flush()
    mapping = SolutionComplianceMapping(solution_id=solution.id, framework=framework, control_id=control,
                                        control_description="%s control" % label, verification_status=status)
    db_session.add(mapping)
    db_session.flush()
    gap = Gap(organization_id=org.id, name="%s gap" % label, source_table="solution_compliance_mappings",
              source_id=mapping.id, priority="high", severity="high", resolution_status="identified")
    db_session.add(gap)
    db_session.flush()
    return {"user": user, "solution": solution, "mapping": mapping, "gap": gap}


def test_the_check_lists_only_the_signed_in_organisations_controls(client, db_session, make_org, login_as):
    a = _estate(db_session, make_org, "alpha", "ISO 27001", "A.9.1", "non_compliant")
    b = _estate(db_session, make_org, "bravo", "HIPAA", "164.312", "non_compliant")
    db_session.commit()

    login_as(client, a["user"])
    response = client.get("/solutions/%d/api/compliance-gap" % a["solution"].id)
    assert response.status_code == 200
    gap = response.get_json()["gap"]
    assert [i["source"] for i in gap["gaps"]] == ["ISO 27001 A.9.1"]
    assert gap["gaps"][0]["gap_id"] == a["gap"].id
    body = response.get_data(as_text=True)
    assert "HIPAA" not in body and "bravo" not in body
    assert str(b["gap"].id) not in [str(i["gap_id"]) for i in gap["gaps"]]


def test_asking_for_the_other_organisations_solution_answers_not_found(client, db_session, make_org, login_as):
    a = _estate(db_session, make_org, "alpha", "ISO 27001", "A.9.1", "verified")
    b = _estate(db_session, make_org, "bravo", "HIPAA", "164.312", "non_compliant")
    db_session.commit()

    login_as(client, a["user"])
    response = client.get("/solutions/%d/api/compliance-gap" % b["solution"].id)
    assert response.status_code == 404
    assert "HIPAA" not in response.get_data(as_text=True)

    login_as(client, b["user"])
    own = client.get("/solutions/%d/api/compliance-gap" % b["solution"].id)
    assert own.status_code == 200
    assert [i["source"] for i in own.get_json()["gap"]["gaps"]] == ["HIPAA 164.312"]
