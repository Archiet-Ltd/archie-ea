"""Access defects in the options and vendor-analysis surfaces.

1. The stakeholder list, invite and consensus endpoints of a vendor analysis
   checked only that the analysis existed, not that the caller owns or
   administers it.
2. The phase E initiative shortlist read every organisation's solution options.
3. The options advisor's status endpoint changed any decision, including ones
   no solution's options advisor produced.
"""

from __future__ import annotations

import uuid

PASSWORD = "test-password-123"


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"OS {label} {suffix}", slug=f"os-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, *, role_name, org_role="viewer"):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name=role_name).first()
    suffix = uuid.uuid4().hex[:6]
    user = User(
        first_name="OS", last_name=f"User-{suffix}", email=f"os-{suffix}@example.test",
        password=PASSWORD, confirmed=True, organization_id=org.id, role=role,
        is_org_admin=False, is_platform_admin=False,
    )
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role, granted_by_id=user.id)
    db_session.flush()
    return user


def _capability(db_session, org, name="Cap"):
    from app.models.business_capability import BusinessCapability

    cap = BusinessCapability(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    return cap


def _analysis(db_session, org, creator, capability=None):
    from app.models.vendor_analysis import OptionsAnalysis

    capability = capability or _capability(db_session, org)
    analysis = OptionsAnalysis(
        name="Security analysis", capability_id=capability.id,
        created_by_id=creator.id, organization_id=org.id,
    )
    db_session.add(analysis)
    db_session.flush()
    return analysis


# -- 1. stakeholder endpoints --------------------------------------------------------


def test_stakeholder_endpoints_refuse_another_organisations_analysis(
    app, db_session, login_as, client
):
    org_a, org_b = _org(db_session, "sa"), _org(db_session, "sb")
    intruder = _user(db_session, org_a, role_name="Administrator", org_role="org_admin")
    owner = _user(db_session, org_b, role_name="User")
    analysis = _analysis(db_session, org_b, owner)
    db_session.commit()
    analysis_id, owner_id = analysis.id, owner.id

    login_as(client, intruder)
    db_session.expunge_all()
    base = f"/dashboard/api/vendor-analysis/{analysis_id}/stakeholders"

    assert client.get(base).status_code == 404
    assert client.get(f"{base}/consensus").status_code == 404
    resp = client.post(base, json={"stakeholder_id": owner_id, "stakeholder_role": "IT"})
    assert resp.status_code == 404


def test_stakeholder_endpoints_refuse_a_colleague_who_neither_owns_nor_administers(
    app, db_session, login_as, client
):
    """The remove endpoint and the analysis record allow only the owner or an admin;
    list, invite and consensus now apply the same check."""
    org = _org(db_session, "same")
    owner = _user(db_session, org, role_name="User")
    colleague = _user(db_session, org, role_name="User")
    analysis = _analysis(db_session, org, owner)
    db_session.commit()
    analysis_id, colleague_id = analysis.id, colleague.id

    login_as(client, colleague)
    base = f"/dashboard/api/vendor-analysis/{analysis_id}/stakeholders"

    assert client.get(base).status_code == 403
    assert client.get(f"{base}/consensus").status_code == 403
    resp = client.post(base, json={"stakeholder_id": colleague_id, "stakeholder_role": "IT"})
    assert resp.status_code == 403


def test_the_owner_still_lists_invites_and_reads_consensus(app, db_session, login_as, client):
    org = _org(db_session, "own")
    owner = _user(db_session, org, role_name="User")
    invitee = _user(db_session, org, role_name="User")
    analysis = _analysis(db_session, org, owner)
    db_session.commit()
    analysis_id, invitee_id = analysis.id, invitee.id

    login_as(client, owner)
    base = f"/dashboard/api/vendor-analysis/{analysis_id}/stakeholders"

    resp = client.post(base, json={"stakeholder_id": invitee_id, "stakeholder_role": "IT"})
    assert resp.status_code == 201
    assert len(client.get(base).get_json()["stakeholders"]) == 1
    assert client.get(f"{base}/consensus").get_json()["total_invited"] == 1


# -- 2. initiative shortlist ---------------------------------------------------------


def _gap_option(db_session, org, name, score):
    from app.models.capability_gap_analysis import (
        CapabilityGapAnalysis,
        CapabilityGapDetail,
        GapSolutionOption,
    )
    from app.models.unified_capability import UnifiedCapability

    ucap = UnifiedCapability(name=f"UCap {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(ucap)
    db_session.flush()
    gap = CapabilityGapAnalysis(analysis_name=f"Gap {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(gap)
    db_session.flush()
    detail = CapabilityGapDetail(analysis_id=gap.id, capability_id=ucap.id)
    db_session.add(detail)
    db_session.flush()
    option = GapSolutionOption(gap_detail_id=detail.id, solution_name=name, prioritisation_score=score)
    db_session.add(option)
    db_session.flush()
    return option


def test_initiative_shortlist_shows_only_the_callers_organisation(
    app, db_session, login_as, client
):
    org_a, org_b = _org(db_session, "ia"), _org(db_session, "ib")
    user_a = _user(db_session, org_a, role_name="User")
    mine = _gap_option(db_session, org_a, "Org A option", 10.0)
    theirs = _gap_option(db_session, org_b, "Org B option", 99.0)
    db_session.commit()
    mine_id, theirs_id = mine.id, theirs.id

    # The phase E blueprint is not registered on the app, so no URL reaches this
    # view today; call it inside a signed-in request for org A instead.
    from flask import g
    from flask_login import login_user

    from app.modules.ea_workflows.routes.phase_e_routes import initiative_shortlist

    with app.test_request_context("/api/ea/phase-e/initiative-shortlist"):
        g.current_org_id = org_a.id
        login_user(user_a)
        resp, status = initiative_shortlist()

    assert status == 200
    ids = {row["id"] for row in resp.get_json()["initiatives"]}
    assert mine_id in ids
    assert theirs_id not in ids


# -- 3. options advisor status -------------------------------------------------------


def _decision(db_session, org, *, solution=None, title="Decision"):
    from app.models.architecture_decision import ArchitectureDecision

    adr = ArchitectureDecision(
        title=title, status="proposed", organization_id=org.id,
        solution_id=solution.id if solution else None,
    )
    db_session.add(adr)
    db_session.flush()
    return adr


def _solution(db_session, org):
    from app.models.solution_models import Solution

    sol = Solution(name=f"Solution {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(sol)
    db_session.flush()
    return sol


def test_options_advisor_status_refuses_a_decision_no_solution_owns(
    app, db_session, login_as, client
):
    from sqlalchemy import text

    org = _org(db_session, "adr")
    user = _user(db_session, org, role_name="User", org_role="architect")
    unrelated = _decision(db_session, org, title="Board decision")
    db_session.commit()
    adr_id = unrelated.id

    login_as(client, user)
    resp = client.post(f"/solutions/options-advisor/{adr_id}/status", json={"status": "accepted"})

    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
    status = db_session.execute(
        text("SELECT status FROM architecture_decisions WHERE id = :id"), {"id": adr_id}
    ).scalar()
    assert status == "proposed"


def test_options_advisor_status_still_accepts_a_solutions_decision(
    app, db_session, login_as, client
):
    org = _org(db_session, "adr-ok")
    user = _user(db_session, org, role_name="User", org_role="architect")
    adr = _decision(db_session, org, solution=_solution(db_session, org))
    db_session.commit()
    adr_id = adr.id

    login_as(client, user)
    resp = client.post(f"/solutions/options-advisor/{adr_id}/status", json={"status": "accepted"})

    assert resp.status_code == 200
    assert resp.get_json()["adr"]["status"] == "accepted"
