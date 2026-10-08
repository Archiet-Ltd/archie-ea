"""Three routes acted on a row without checking that the caller may.

Found by triaging reads of models that carry no tenant fence:

* stakeholder score submission tested ``not current_user.is_admin`` (the bound method, always
  truthy), so the "only the invited stakeholder or an admin" check never denied anyone, and
  removing a stakeholder from an analysis checked nothing at all;
* deleting a solution stakeholder looked the row up by ``solution_id`` from the URL without
  ever loading the solution, so a foreign solution id reached another organisation's row;
* setting a RACI cell had no solution check, unlike the read beside it.
"""

from __future__ import annotations

import uuid

PASSWORD = "test-password-123"


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Gap {label} {suffix}", slug=f"gap-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, *, role_name="User"):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name=role_name).first()
    suffix = uuid.uuid4().hex[:6]
    user = User(
        first_name="Gap", last_name=f"User-{suffix}", email=f"gap-{suffix}@example.test",
        password=PASSWORD, confirmed=True, organization_id=org.id, role=role,
        is_org_admin=False, is_platform_admin=False,
    )
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "viewer", granted_by_id=user.id)
    db_session.flush()
    return user


def _analysis_with_input(db_session, org, creator, stakeholder):
    from app.models.business_capability import BusinessCapability
    from app.models.vendor_analysis import OptionsAnalysis, StakeholderInput

    capability = BusinessCapability(name=f"Cap {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(capability)
    db_session.flush()
    # These models gain a tenant column in a separate change; set it when it exists so this
    # test is correct before and after that change lands.
    def extra(model):
        return {"organization_id": org.id} if hasattr(model, "organization_id") else {}

    analysis = OptionsAnalysis(
        name="Gap analysis", capability_id=capability.id, created_by_id=creator.id, **extra(OptionsAnalysis)
    )
    db_session.add(analysis)
    db_session.flush()
    row = StakeholderInput(
        analysis_id=analysis.id, stakeholder_id=stakeholder.id, stakeholder_role="technical",
        **extra(StakeholderInput),
    )
    db_session.add(row)
    db_session.flush()
    return analysis, row


# -- stakeholder scores: the is_admin() bug ------------------------------------------


def test_a_user_who_is_not_the_stakeholder_and_not_an_admin_cannot_submit_scores(
    app, db_session, login_as, client
):
    org = _org(db_session, "scores")
    creator, invited, intruder = (_user(db_session, org) for _ in range(3))
    analysis, row = _analysis_with_input(db_session, org, creator, invited)
    db_session.commit()

    login_as(client, intruder)
    resp = client.patch(
        f"/dashboard/api/vendor-analysis/{analysis.id}/stakeholders/{row.id}/scores",
        json={"vendor_scores": {"1": {"cost": 1}}},
    )

    assert resp.status_code == 403


def test_the_invited_stakeholder_can_still_submit_their_own_scores(app, db_session, login_as, client):
    org = _org(db_session, "own-scores")
    creator, invited = _user(db_session, org), _user(db_session, org)
    analysis, row = _analysis_with_input(db_session, org, creator, invited)
    db_session.commit()

    login_as(client, invited)
    resp = client.patch(
        f"/dashboard/api/vendor-analysis/{analysis.id}/stakeholders/{row.id}/scores",
        json={"vendor_scores": {"1": {"cost": 5}}},
    )

    assert resp.status_code == 200


# -- removing a stakeholder from an analysis -----------------------------------------


def test_only_the_analysis_owner_or_an_admin_can_remove_a_stakeholder(app, db_session, login_as, client):
    from app.models.vendor_analysis import StakeholderInput

    org = _org(db_session, "remove")
    creator, invited, intruder = (_user(db_session, org) for _ in range(3))
    analysis, row = _analysis_with_input(db_session, org, creator, invited)
    db_session.commit()
    row_id = row.id

    login_as(client, intruder)
    denied = client.delete(f"/dashboard/api/vendor-analysis/{analysis.id}/stakeholders/{row_id}")

    assert denied.status_code == 403
    assert db_session.get(StakeholderInput, row_id) is not None

    login_as(client, creator)
    allowed = client.delete(f"/dashboard/api/vendor-analysis/{analysis.id}/stakeholders/{row_id}")

    assert allowed.status_code == 200


# -- solution stakeholder delete: the solution was never loaded ----------------------


def test_a_solution_stakeholder_of_another_organisation_cannot_be_deleted(app, db_session, login_as, client):
    from app.models.solution_models import Solution
    from app.models.solution_sad_models import SolutionStakeholderSAD

    org_a, org_b = _org(db_session, "sad-a"), _org(db_session, "sad-b")
    intruder = _user(db_session, org_a)
    solution = Solution(name="Other tenant solution", organization_id=org_b.id)
    db_session.add(solution)
    db_session.flush()
    row = SolutionStakeholderSAD(solution_id=solution.id, name="Sam Stake", role="Sponsor")
    db_session.add(row)
    db_session.commit()
    solution_id, row_id = solution.id, row.id

    login_as(client, intruder)
    resp = client.delete(f"/solutions/{solution_id}/stakeholders/{row_id}")

    assert resp.status_code == 404
    assert db_session.get(SolutionStakeholderSAD, row_id) is not None


# -- RACI write: the read beside it checks the solution, the write did not -----------


def test_a_raci_cell_of_another_organisations_solution_cannot_be_set(
    app, db_session, login_as, client, monkeypatch
):
    from app.models.solution_models import Solution
    from app.services import raci_service

    org_a, org_b = _org(db_session, "raci-a"), _org(db_session, "raci-b")
    intruder = _user(db_session, org_a)
    solution = Solution(name="Other tenant solution", organization_id=org_b.id)
    db_session.add(solution)
    db_session.commit()
    calls = []
    monkeypatch.setattr(
        raci_service, "set_raci_assignment", lambda *args, **kwargs: calls.append(args) or {"success": True}
    )

    login_as(client, intruder)
    resp = client.put(
        f"/solutions/{solution.id}/api/raci",
        json={"stakeholder_id": 1, "raci_type": "R", "value": True},
    )

    assert resp.status_code == 404
    assert calls == []
