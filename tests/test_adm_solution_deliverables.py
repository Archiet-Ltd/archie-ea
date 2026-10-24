"""A solution's own deliverables set against its phase's TOGAF checklist.

The deliverables (``deliverables`` joined by ``solution_deliverables``) are the
record; ``adm_deliverables`` is only the checklist they are matched to, and a
tick is the deliverable's own status.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _org_with_solution(db_session, make_org, phase="B"):
    from app.models.implementation_migration import Deliverable
    from app.models.solution_models import Solution, solution_deliverables
    from app.models.user import Role, User
    from app.services.adm_deliverable_service import seed_deliverables

    seed_deliverables()
    org = make_org("adm")
    Role.insert_roles()
    user = User(
        email="adm.%s@example.com" % uuid.uuid4().hex[:8], first_name="A", last_name="B",
        organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        role=Role.query.filter_by(name="Viewer").first(),
    )
    user.password = "Passw0rd!x"
    db_session.add(user)
    solution = Solution(name="Checklist Solution", organization_id=org.id, adm_phase=phase)
    db_session.add(solution)
    db_session.flush()

    def add(name, template_name=None, status="planned"):
        from app.models.adm_deliverable import ADMDeliverable

        template = (
            ADMDeliverable.query.filter_by(phase=phase, name=template_name, is_template=True).one()
            if template_name else None
        )
        d = Deliverable(
            name=name, organization_id=org.id, delivery_status=status,
            adm_template_id=template.id if template else None,
        )
        db_session.add(d)
        db_session.flush()
        db_session.execute(solution_deliverables.insert().values(solution_id=solution.id, deliverable_id=d.id))
        return d

    return org, user, solution, add


def test_checklist_matches_own_deliverables_and_lists_the_rest(db_session, make_org, tenant_ctx):
    from app.services.adm_deliverable_service import solution_checklist

    org, _user, solution, add = _org_with_solution(db_session, make_org)
    add("Process model", "Business Architecture document", "in_progress")
    add("Impact note")
    with tenant_ctx(org.id):
        result = solution_checklist(solution)
    by_name = {row["name"]: row["deliverable"] for row in result["checklist"]}
    assert set(by_name) == {
        "Business Architecture document",
        "Architecture Definition Document (business view)",
        "Gap Analysis",
    }
    assert by_name["Business Architecture document"]["name"] == "Process model"
    assert by_name["Business Architecture document"]["complete"] is False
    assert by_name["Gap Analysis"] is None
    assert [d["name"] for d in result["also_produced"]] == ["Impact note"]
    assert (result["total"], result["complete"]) == (2, 0)


def test_the_tick_is_the_deliverables_own_status(db_session, make_org, tenant_ctx):
    from app.services.adm_deliverable_service import set_complete, solution_checklist

    org, _user, solution, add = _org_with_solution(db_session, make_org)
    d = add("Process model", "Business Architecture document", "in_progress")
    with tenant_ctx(org.id):
        set_complete(d.id, True)
        assert d.delivery_status == "completed"
        assert solution_checklist(solution)["complete"] == 1
        set_complete(d.id, False)
        assert d.delivery_status == "in_progress"
        assert solution_checklist(solution)["complete"] == 0


def test_one_organisation_cannot_read_or_tick_anothers_deliverables(
        db_session, make_org, client, login_as):
    org_a, user_a, solution_a, add_a = _org_with_solution(db_session, make_org)
    d_a = add_a("A's model", "Business Architecture document", "in_progress")
    org_b, user_b, solution_b, add_b = _org_with_solution(db_session, make_org)
    d_b = add_b("B's model", "Business Architecture document", "in_progress")
    db_session.flush()

    login_as(client, user_a)
    own = client.get("/api/adm-kanban/v2/solutions/%d/deliverables" % solution_a.id)
    assert own.status_code == 200
    assert own.get_json()["checklist"][0]["deliverable"]["name"] == "A's model"
    login_as(client, user_a)
    assert client.get("/api/adm-kanban/v2/solutions/%d/deliverables" % solution_b.id).status_code == 404
    login_as(client, user_a)
    refused = client.patch("/api/adm-kanban/v2/deliverables/%d/complete" % d_b.id, json={"complete": True})
    assert refused.status_code == 404
    assert d_b.delivery_status == "in_progress"
    login_as(client, user_a)
    done = client.patch("/api/adm-kanban/v2/deliverables/%d/complete" % d_a.id, json={"complete": True})
    assert done.status_code == 200 and done.get_json()["deliverable"]["complete"] is True


def test_board_cards_carry_the_solutions_own_counts_and_deliverable_cards(db_session, make_org, tenant_ctx):
    from app.services.kanban_projection_service import KanbanProjectionService

    org, _user, solution, add = _org_with_solution(db_session, make_org)
    add("Process model", "Business Architecture document", "completed")
    add("Impact note")
    with tenant_ctx(org.id):
        cards = KanbanProjectionService().get_cards()["cards"]
    sol = next(c for c in cards if c["id"] == "solution:%d" % solution.id)
    assert (sol["meta"]["deliverables_total"], sol["meta"]["deliverables_complete"]) == (2, 1)
    names = {c["title"]: c for c in cards if c["card_type"] == "deliverable"}
    assert set(names) == {"Process model", "Impact note"}
    assert names["Process model"]["column"] == "done"
    assert names["Impact note"]["subtitle"] == "For Checklist Solution"


def test_a_deliverable_can_be_created_against_a_checklist_item(db_session, make_org, tenant_ctx):
    from app.models.adm_deliverable import ADMDeliverable
    from app.services import work_package_service
    from app.services.work_package_service import WorkPackageError

    org, _user, _solution, _add = _org_with_solution(db_session, make_org)
    template = ADMDeliverable.query.filter_by(phase="B", name="Gap Analysis").one()
    with tenant_ctx(org.id):
        from app.models.unified_work_package import UnifiedWorkPackage

        wp = UnifiedWorkPackage(name="WP", organization_id=org.id)
        db_session.add(wp)
        db_session.flush()
        made = work_package_service.create_deliverable(
            wp.id, organization_id=org.id, name="Our gap analysis", adm_template_id=template.id)
        assert made.adm_template_id == template.id
        with pytest.raises(WorkPackageError):
            work_package_service.create_deliverable(
                wp.id, organization_id=org.id, name="Bad", adm_template_id=99999999)
