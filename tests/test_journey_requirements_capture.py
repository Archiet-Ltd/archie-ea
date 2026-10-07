"""R1-B43 PR 2 (TB-0188/PB-0190): the guided-design journey's
requirements-capture step writes a Requirement linked to the capability
it realises and the application(s) that implement it.
"""
from __future__ import annotations

import uuid

import pytest


def _user(db_session, org):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"req-capture-{uuid.uuid4().hex[:8]}@example.com", first_name="T", last_name="U",
        organization_id=org.id, role=admin_role, confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _solution(db_session, org, creator):
    from app.models.solution_models import Solution

    solution = Solution(
        name=f"Solution {uuid.uuid4().hex[:6]}", organization_id=org.id, created_by_id=creator.id,
    )
    db_session.add(solution)
    db_session.flush()
    return solution


def _app_component(db_session, org):
    from app.models.application_portfolio import ApplicationComponent

    app_row = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(app_row)
    db_session.flush()
    return app_row


def _capability(db_session, org):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name=f"Capability {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    return cap


class TestCreateSolutionRequirement:
    def test_capture_a_requirement_linked_to_capability_and_application(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("req-capture")
        user = _user(db_session, org)
        solution = _solution(db_session, org, user)
        app_row = _app_component(db_session, org)
        capability = _capability(db_session, org)
        login_as(client, user)

        resp = client.post(
            f"/architecture-journey/{solution.id}/requirements",
            json={
                "title": "Must support SSO",
                "description": "Users sign in via the corporate IdP",
                "capability_id": capability.id,
                "application_component_ids": [app_row.id],
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()["data"]
        assert data["title"] == "Must support SSO"
        assert data["capability_id"] == capability.id
        assert data["capability_name"] == capability.name
        assert data["implementing_application_ids"] == [app_row.id]

    def test_requirement_persists_and_reloads_with_its_links(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("req-persist")
        user = _user(db_session, org)
        solution = _solution(db_session, org, user)
        app_row = _app_component(db_session, org)
        capability = _capability(db_session, org)
        login_as(client, user)

        create_resp = client.post(
            f"/architecture-journey/{solution.id}/requirements",
            json={
                "title": "Persisted requirement",
                "capability_id": capability.id,
                "application_component_ids": [app_row.id],
            },
        )
        assert create_resp.status_code == 200

        list_resp = client.get(f"/architecture-journey/{solution.id}/requirements")
        assert list_resp.status_code == 200
        rows = list_resp.get_json()["data"]["requirements"]
        reloaded = next(r for r in rows if r["title"] == "Persisted requirement")
        assert reloaded["capability_id"] == capability.id
        assert reloaded["implementing_application_ids"] == [app_row.id]

    def test_missing_title_is_refused(self, app, db_session, make_org, client, login_as):
        org = make_org("req-no-title")
        user = _user(db_session, org)
        solution = _solution(db_session, org, user)
        login_as(client, user)

        resp = client.post(f"/architecture-journey/{solution.id}/requirements", json={})
        assert resp.status_code == 400

    def test_a_capability_from_another_organisation_is_refused(
        self, app, db_session, make_org, client, login_as
    ):
        org_a = make_org("req-fence-a")
        org_b = make_org("req-fence-b")
        user_a = _user(db_session, org_a)
        solution_a = _solution(db_session, org_a, user_a)
        capability_b = _capability(db_session, org_b)
        login_as(client, user_a)

        resp = client.post(
            f"/architecture-journey/{solution_a.id}/requirements",
            json={"title": "Cross-org attempt", "capability_id": capability_b.id},
        )
        assert resp.status_code == 400

    def test_two_organisations_requirements_never_cross(
        self, app, db_session, make_org, client, login_as
    ):
        org_a = make_org("req-isolation-a")
        org_b = make_org("req-isolation-b")
        user_a = _user(db_session, org_a)
        user_b = _user(db_session, org_b)
        solution_a = _solution(db_session, org_a, user_a)
        solution_b = _solution(db_session, org_b, user_b)

        login_as(client, user_a)
        client.post(f"/architecture-journey/{solution_a.id}/requirements", json={"title": "A's requirement"})

        login_as(client, user_b)
        client.post(f"/architecture-journey/{solution_b.id}/requirements", json={"title": "B's requirement"})

        resp_b = client.get(f"/architecture-journey/{solution_b.id}/requirements")
        titles_b = {r["title"] for r in resp_b.get_json()["data"]["requirements"]}
        assert "B's requirement" in titles_b
        assert "A's requirement" not in titles_b

    def test_org_b_cannot_read_org_as_solution_requirements(
        self, app, db_session, make_org, client, login_as
    ):
        org_a = make_org("req-read-fence-a")
        org_b = make_org("req-read-fence-b")
        user_a = _user(db_session, org_a)
        user_b = _user(db_session, org_b)
        solution_a = _solution(db_session, org_a, user_a)
        solution_a_id = solution_a.id

        # Solution.query.get_or_404 is identity-map-scoped on a cache HIT
        # (CLAUDE.md's own documented caveat): this single test session
        # already holds solution_a from the line above, so without
        # expunging it the route's own org_id predicate never runs and a
        # real tenant leak would read as passing. One request is one
        # session in production, so this is a test-fixture artifact to
        # defeat, not a route bug to work around in the route itself.
        db_session.expunge_all()

        login_as(client, user_b)
        resp = client.get(f"/architecture-journey/{solution_a_id}/requirements")
        assert resp.status_code == 404
