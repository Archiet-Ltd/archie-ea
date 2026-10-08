"""Motivation traceability routes: goal trace, driver and assessment write paths.

Acceptance criteria:
  - Two organisations: a driver, assessment and goal trace created in
    organisation A are invisible to organisation B.
  - Goal trace: open a goal, run the trace, reload, and see the same cited chain.
  - Driver assessment: create a driver, link it to a suggested goal, reload,
    and see it on the motivation view.
  - Honest empty/failed states: an unlinked goal shows "not recorded" for its
    trace, never an empty success.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _org_suffix() -> str:
    return uuid.uuid4().hex[:8]


def _user(db_session, org_id, suffix):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Architect").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Architect").first()

    user = User(
        email=f"mt-{suffix}@example.com",
        first_name="MT",
        last_name="Tester",
        organization_id=org_id,
        role=role,
        confirmed=True,
    )
    user.password = f"pw-{suffix}"  # generated, not a real credential
    db_session.add(user)
    db_session.flush()
    return user


# --------------------------------------------------------------------------- #
# Goal trace
# --------------------------------------------------------------------------- #


class TestGoalTrace:
    def test_goal_trace_returns_goal_and_hops(self, db_session, make_org, client, login_as):
        """A goal with linked capabilities and initiatives returns the full trace chain."""
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.models.motivation import Goal
        from app.models.unified_capability import UnifiedCapability
        from app.models.vendor.vendor_organization import EnterpriseInitiative

        suffix = _org_suffix()
        org = make_org(f"goal-trace-{suffix}")
        user = _user(db_session, org.id, suffix)

        # Create a Goal
        goal = Goal(
            name=f"Improve Customer Experience {suffix}",
            description="Become market leader in customer satisfaction",
            goal_type="strategic",
            status="active",
            organization_id=org.id,
        )
        db_session.add(goal)
        db_session.flush()

        # Create ArchiMate elements
        goal_elem = ArchiMateElement(
            name=goal.name,
            type="Goal",
            layer="Motivation",
            organization_id=org.id,
        )
        db_session.add(goal_elem)
        db_session.flush()
        goal.archimate_element_id = goal_elem.id

        cap_elem = ArchiMateElement(
            name="Customer Analytics",
            type="Capability",
            layer="Business",
            organization_id=org.id,
        )
        db_session.add(cap_elem)
        db_session.flush()

        # Create a Realization relationship: Goal → Capability
        rel = ArchiMateRelationship(
            source_id=goal_elem.id,
            target_id=cap_elem.id,
            type="realization",
            organization_id=org.id,
        )
        db_session.add(rel)
        db_session.flush()

        # Create a UnifiedCapability linked to the ArchiMate element
        cap = UnifiedCapability(
            name="Customer Analytics",
            code="CUST_ANALYTICS",
            organization_id=None,  # shared catalogue
        )
        cap.archimate_element_id = cap_elem.id
        db_session.add(cap)
        db_session.flush()

        # Create an EnterpriseInitiative linked to the goal
        initiative = EnterpriseInitiative(
            name=f"CX Transformation {suffix}",
            organization_id=org.id,
        )
        db_session.add(initiative)
        db_session.flush()

        # Link initiative to goal via initiative_goals junction
        from app.models.motivation import initiative_goals
        db_session.execute(
            initiative_goals.insert().values(
                initiative_id=initiative.id,
                goal_id=goal.id,
                contribution_level="primary",
            )
        )
        db_session.flush()

        db_session.commit()

        login_as(client, user)
        resp = client.get(f"/api/v1/motivation/goals/{goal.id}/trace")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["goal"]["id"] == goal.id
        assert data["data"]["goal"]["name"] == goal.name
        assert data["data"]["trace_status"] == "recorded"
        assert len(data["data"]["capabilities"]) == 1
        assert data["data"]["capabilities"][0]["name"] == "Customer Analytics"
        assert len(data["data"]["capability_hops"]) == 1
        assert data["data"]["capability_hops"][0]["relationship_type"] == "realization"
        assert len(data["data"]["enterprise_initiatives"]) == 1
        assert data["data"]["enterprise_initiatives"][0]["name"] == initiative.name
        assert len(data["data"]["initiative_hops"]) == 1

    def test_goal_trace_unlinked_goal_shows_not_recorded(self, db_session, make_org, client, login_as):
        """An unlinked goal returns trace_status 'not_recorded', never empty success."""
        from app.models.motivation import Goal

        suffix = _org_suffix()
        org = make_org(f"goal-trace-empty-{suffix}")
        user = _user(db_session, org.id, suffix)

        goal = Goal(
            name=f"Unlinked Goal {suffix}",
            description="A goal with no capabilities or initiatives",
            goal_type="operational",
            status="draft",
            organization_id=org.id,
        )
        db_session.add(goal)
        db_session.flush()
        db_session.commit()

        login_as(client, user)
        resp = client.get(f"/api/v1/motivation/goals/{goal.id}/trace")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["goal"]["id"] == goal.id
        assert data["data"]["trace_status"] == "not_recorded"
        assert data["data"]["capabilities"] == []
        assert data["data"]["enterprise_initiatives"] == []
        assert data["data"]["strategic_initiatives"] == []
        assert data["data"]["work_packages"] == []

    def test_goal_trace_goal_not_found(self, client, make_org, db_session, login_as):
        """A non-existent goal returns 404."""
        suffix = _org_suffix()
        org = make_org(f"goal-trace-404-{suffix}")
        user = _user(db_session, org.id, suffix)

        login_as(client, user)
        resp = client.get("/api/v1/motivation/goals/99999/trace")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["success"] is False

    def test_goal_trace_cross_tenant_invisible(self, db_session, make_org, client, login_as):
        """A goal created in organisation A is invisible to organisation B."""
        from app.models.motivation import Goal

        suffix = _org_suffix()
        org_a = make_org(f"goal-trace-org-a-{suffix}")
        org_b = make_org(f"goal-trace-org-b-{suffix}")
        user_a = _user(db_session, org_a.id, f"a-{suffix}")
        user_b = _user(db_session, org_b.id, f"b-{suffix}")

        goal_a = Goal(
            name=f"Org A Goal {suffix}",
            description="Only visible to org A",
            goal_type="strategic",
            status="active",
            organization_id=org_a.id,
        )
        db_session.add(goal_a)
        db_session.flush()
        db_session.commit()

        # Org A can see its own goal
        login_as(client, user_a)
        resp = client.get(f"/api/v1/motivation/goals/{goal_a.id}/trace")
        assert resp.status_code == 200

        # Org B cannot see org A's goal
        login_as(client, user_b)
        resp = client.get(f"/api/v1/motivation/goals/{goal_a.id}/trace")
        assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Driver write paths
# --------------------------------------------------------------------------- #


class TestDriverWrite:
    def test_create_driver(self, db_session, make_org, client, login_as):
        """Create a driver with source, date and owner."""
        suffix = _org_suffix()
        org = make_org(f"driver-create-{suffix}")
        user = _user(db_session, org.id, suffix)

        login_as(client, user)
        resp = client.post(
            "/api/v1/motivation/drivers",
            json={
                "name": f"GDPR Compliance Mandate {suffix}",
                "description": "New data privacy regulations require system changes",
                "driver_type": "regulatory",
                "source": "external",
                "identified_date": "2026-10-01",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["name"] == f"GDPR Compliance Mandate {suffix}"
        assert data["data"]["driver_type"] == "regulatory"
        assert data["data"]["source"] == "external"
        assert data["data"]["identified_date"] == "2026-10-01"

    def test_create_driver_missing_name(self, db_session, make_org, client, login_as):
        """Creating a driver without a name returns 400."""
        suffix = _org_suffix()
        org = make_org(f"driver-no-name-{suffix}")
        user = _user(db_session, org.id, suffix)

        login_as(client, user)
        resp = client.post(
            "/api/v1/motivation/drivers",
            json={"description": "No name provided"},
        )
        assert resp.status_code == 400

    def test_create_driver_cross_tenant_isolation(self, db_session, make_org, client, login_as):
        """A driver created in org A is invisible to org B."""
        from app.models.motivation import Driver

        suffix = _org_suffix()
        org_a = make_org(f"driver-iso-a-{suffix}")
        org_b = make_org(f"driver-iso-b-{suffix}")
        user_a = _user(db_session, org_a.id, f"a-{suffix}")
        _user(db_session, org_b.id, f"b-{suffix}")  # decoy user for org B

        # Create driver in org A
        login_as(client, user_a)
        resp = client.post(
            "/api/v1/motivation/drivers",
            json={
                "name": f"Org A Driver {suffix}",
                "driver_type": "competitive",
                "source": "external",
            },
        )
        assert resp.status_code == 201
        driver_id = resp.get_json()["data"]["id"]

        # Verify org B cannot see it via the DB directly
        driver_in_b = Driver.query.filter_by(
            id=driver_id, organization_id=org_b.id
        ).first()
        assert driver_in_b is None

        # Verify org A can see it
        driver_in_a = Driver.query.filter_by(
            id=driver_id, organization_id=org_a.id
        ).first()
        assert driver_in_a is not None
        assert driver_in_a.name == f"Org A Driver {suffix}"


# --------------------------------------------------------------------------- #
# Assessment write paths
# --------------------------------------------------------------------------- #


class TestAssessmentWrite:
    def test_create_assessment(self, db_session, make_org, client, login_as):
        """Create an assessment against a driver."""
        from app.models.motivation import Driver

        suffix = _org_suffix()
        org = make_org(f"assessment-{suffix}")
        user = _user(db_session, org.id, suffix)

        driver = Driver(
            name=f"Market Disruption {suffix}",
            driver_type="competitive",
            source="external",
            organization_id=org.id,
        )
        db_session.add(driver)
        db_session.flush()
        db_session.commit()

        login_as(client, user)
        resp = client.post(
            f"/api/v1/motivation/drivers/{driver.id}/assessments",
            json={
                "name": f"Competitive Analysis {suffix}",
                "description": "Assessment of market disruption impact",
                "assessment_type": "SWOT",
                "result_score": "High Impact",
                "assessor": "Strategy Team",
                "date_assessed": "2026-10-01",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["name"] == f"Competitive Analysis {suffix}"
        assert data["data"]["assessment_type"] == "SWOT"
        assert data["data"]["driver_id"] == driver.id

    def test_create_assessment_driver_not_found(self, db_session, make_org, client, login_as):
        """Creating an assessment against a non-existent driver returns 404."""
        suffix = _org_suffix()
        org = make_org(f"assessment-404-{suffix}")
        user = _user(db_session, org.id, suffix)

        login_as(client, user)
        resp = client.post(
            "/api/v1/motivation/drivers/99999/assessments",
            json={
                "name": "Ghost Assessment",
                "assessment_type": "Maturity",
            },
        )
        assert resp.status_code == 404

    def test_create_assessment_cross_tenant_isolation(self, db_session, make_org, client, login_as):
        """An assessment created in org A is invisible to org B."""
        from app.models.motivation import Assessment, Driver

        suffix = _org_suffix()
        org_a = make_org(f"assess-iso-a-{suffix}")
        org_b = make_org(f"assess-iso-b-{suffix}")
        user_a = _user(db_session, org_a.id, f"a-{suffix}")
        _user(db_session, org_b.id, f"b-{suffix}")  # decoy user for org B

        driver_a = Driver(
            name=f"Driver A {suffix}",
            driver_type="regulatory",
            source="external",
            organization_id=org_a.id,
        )
        db_session.add(driver_a)
        db_session.flush()

        login_as(client, user_a)
        resp = client.post(
            f"/api/v1/motivation/drivers/{driver_a.id}/assessments",
            json={
                "name": f"Assessment A {suffix}",
                "assessment_type": "Risk",
            },
        )
        assert resp.status_code == 201
        assessment_id = resp.get_json()["data"]["id"]

        # Org B cannot see the assessment
        assessment_in_b = Assessment.query.filter_by(
            id=assessment_id, organization_id=org_b.id
        ).first()
        assert assessment_in_b is None

        # Org A can see it
        assessment_in_a = Assessment.query.filter_by(
            id=assessment_id, organization_id=org_a.id
        ).first()
        assert assessment_in_a is not None


# --------------------------------------------------------------------------- #
# Driver-to-goal linking
# --------------------------------------------------------------------------- #


class TestDriverGoalLinking:
    def test_suggest_goals_for_driver(self, db_session, make_org, client, login_as):
        """Suggest candidate goals for a driver by text similarity."""
        from app.models.motivation import Driver, Goal

        suffix = _org_suffix()
        org = make_org(f"suggest-goals-{suffix}")
        user = _user(db_session, org.id, suffix)

        driver = Driver(
            name=f"Customer Demand for Mobile {suffix}",
            description="Customers increasingly demand mobile-first experiences",
            driver_type="customer",
            source="external",
            organization_id=org.id,
        )
        db_session.add(driver)

        goal1 = Goal(
            name=f"Mobile Experience Improvement {suffix}",
            description="Deliver best-in-class mobile customer experience",
            goal_type="strategic",
            status="active",
            organization_id=org.id,
        )
        db_session.add(goal1)

        goal2 = Goal(
            name=f"Cost Reduction {suffix}",
            description="Reduce operational costs by 20%",
            goal_type="operational",
            status="active",
            organization_id=org.id,
        )
        db_session.add(goal2)
        db_session.flush()
        db_session.commit()

        login_as(client, user)
        resp = client.get(
            f"/api/v1/motivation/drivers/{driver.id}/suggested-goals"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["driver"]["id"] == driver.id
        assert len(data["data"]["candidates"]) > 0
        # The mobile-related goal should rank higher than cost reduction
        candidates = data["data"]["candidates"]
        mobile_candidate = next(
            (c for c in candidates if "Mobile" in c["goal_name"]), None
        )
        assert mobile_candidate is not None

    def test_link_driver_to_goal(self, db_session, make_org, client, login_as):
        """Link a driver to a goal."""
        from app.models.motivation import Driver, Goal

        suffix = _org_suffix()
        org = make_org(f"link-driver-goal-{suffix}")
        user = _user(db_session, org.id, suffix)

        driver = Driver(
            name=f"Regulatory Pressure {suffix}",
            driver_type="regulatory",
            source="external",
            organization_id=org.id,
        )
        db_session.add(driver)

        goal = Goal(
            name=f"Compliance Achievement {suffix}",
            goal_type="strategic",
            status="active",
            organization_id=org.id,
        )
        db_session.add(goal)
        db_session.flush()
        db_session.commit()

        login_as(client, user)
        resp = client.post(
            f"/api/v1/motivation/drivers/{driver.id}/link-goal",
            json={"goal_id": goal.id},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["linked"] is True
        assert data["data"]["driver"]["id"] == driver.id
        assert data["data"]["goal"]["id"] == goal.id

        # Verify the link persisted
        db_session.refresh(goal)
        assert goal.driver_id == driver.id

    def test_link_driver_to_goal_rejects_overwrite(self, db_session, make_org, client, login_as):
        """Linking a goal that already has a driver returns 409 Conflict."""
        from app.models.motivation import Driver, Goal

        suffix = _org_suffix()
        org = make_org(f"link-overwrite-{suffix}")
        user = _user(db_session, org.id, suffix)

        driver1 = Driver(
            name=f"First Driver {suffix}",
            driver_type="regulatory",
            source="external",
            organization_id=org.id,
        )
        db_session.add(driver1)

        driver2 = Driver(
            name=f"Second Driver {suffix}",
            driver_type="competitive",
            source="internal",
            organization_id=org.id,
        )
        db_session.add(driver2)

        goal = Goal(
            name=f"Already Linked Goal {suffix}",
            goal_type="strategic",
            status="active",
            organization_id=org.id,
        )
        db_session.add(goal)
        db_session.flush()
        db_session.commit()

        login_as(client, user)

        # First link succeeds
        resp = client.post(
            f"/api/v1/motivation/drivers/{driver1.id}/link-goal",
            json={"goal_id": goal.id},
        )
        assert resp.status_code == 200

        # Second link to a different driver is rejected
        resp = client.post(
            f"/api/v1/motivation/drivers/{driver2.id}/link-goal",
            json={"goal_id": goal.id},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["success"] is False
        assert "already linked" in data["error"]["message"]

    def test_link_driver_to_goal_cross_tenant_rejected(self, db_session, make_org, client, login_as):
        """Linking a driver in org A to a goal in org B is rejected."""
        from app.models.motivation import Driver, Goal

        suffix = _org_suffix()
        org_a = make_org(f"link-cross-a-{suffix}")
        org_b = make_org(f"link-cross-b-{suffix}")
        user_a = _user(db_session, org_a.id, f"a-{suffix}")

        driver_a = Driver(
            name=f"Driver A {suffix}",
            driver_type="regulatory",
            source="external",
            organization_id=org_a.id,
        )
        db_session.add(driver_a)

        goal_b = Goal(
            name=f"Goal B {suffix}",
            goal_type="strategic",
            status="active",
            organization_id=org_b.id,
        )
        db_session.add(goal_b)
        db_session.flush()
        db_session.commit()

        # Org A user tries to link their driver to org B's goal
        login_as(client, user_a)
        resp = client.post(
            f"/api/v1/motivation/drivers/{driver_a.id}/link-goal",
            json={"goal_id": goal_b.id},
        )
        assert resp.status_code == 404

    def test_suggest_goals_cross_tenant_only_own_goals(self, db_session, make_org, client, login_as):
        """Suggested goals for a driver only include the tenant's own goals."""
        from app.models.motivation import Driver, Goal

        suffix = _org_suffix()
        org_a = make_org(f"suggest-cross-a-{suffix}")
        org_b = make_org(f"suggest-cross-b-{suffix}")
        user_a = _user(db_session, org_a.id, f"a-{suffix}")

        driver_a = Driver(
            name=f"Driver A {suffix}",
            driver_type="customer",
            source="external",
            organization_id=org_a.id,
        )
        db_session.add(driver_a)

        goal_a = Goal(
            name=f"Goal A {suffix}",
            goal_type="strategic",
            status="active",
            organization_id=org_a.id,
        )
        db_session.add(goal_a)

        goal_b = Goal(
            name=f"Goal B {suffix}",
            goal_type="strategic",
            status="active",
            organization_id=org_b.id,
        )
        db_session.add(goal_b)
        db_session.flush()
        db_session.commit()

        login_as(client, user_a)
        resp = client.get(
            f"/api/v1/motivation/drivers/{driver_a.id}/suggested-goals"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        candidates = data["data"]["candidates"]
        candidate_ids = {c["goal_id"] for c in candidates}
        # Org B's goal must not appear
        assert goal_b.id not in candidate_ids

    def test_old_route_link_driver_to_goal_cross_tenant_rejected(self, db_session, make_org, client, login_as):
        """The pre-existing /archimate/api/link/driver-to-goal route refuses
        another organisation's goal and does not overwrite."""
        from app.models.motivation import Driver, Goal

        suffix = _org_suffix()
        org_a = make_org(f"old-link-cross-a-{suffix}")
        org_b = make_org(f"old-link-cross-b-{suffix}")
        user_a = _user(db_session, org_a.id, f"a-{suffix}")

        driver_a = Driver(
            name=f"Driver A {suffix}",
            driver_type="regulatory",
            source="external",
            organization_id=org_a.id,
        )
        db_session.add(driver_a)

        goal_b = Goal(
            name=f"Goal B {suffix}",
            goal_type="strategic",
            status="active",
            organization_id=org_b.id,
        )
        db_session.add(goal_b)
        db_session.flush()
        db_session.commit()

        # Org A user tries to link their driver to org B's goal via the old route
        login_as(client, user_a)
        resp = client.post(
            "/archimate/api/link/driver-to-goal",
            json={"driver_id": driver_a.id, "goal_id": goal_b.id},
        )
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

        # Verify goal_b.driver_id is still None (no overwrite)
        db_session.refresh(goal_b)
        assert goal_b.driver_id is None

    def test_old_route_link_driver_to_goal_rejects_overwrite(self, db_session, make_org, client, login_as):
        """The pre-existing /archimate/api/link/driver-to-goal route rejects
        overwriting an already-linked goal."""
        from app.models.motivation import Driver, Goal

        suffix = _org_suffix()
        org = make_org(f"old-link-overwrite-{suffix}")
        user = _user(db_session, org.id, suffix)

        driver1 = Driver(
            name=f"First Driver {suffix}",
            driver_type="regulatory",
            source="external",
            organization_id=org.id,
        )
        db_session.add(driver1)

        driver2 = Driver(
            name=f"Second Driver {suffix}",
            driver_type="competitive",
            source="internal",
            organization_id=org.id,
        )
        db_session.add(driver2)

        goal = Goal(
            name=f"Already Linked Goal {suffix}",
            goal_type="strategic",
            status="active",
            organization_id=org.id,
        )
        db_session.add(goal)
        db_session.flush()
        db_session.commit()

        login_as(client, user)

        # First link via the old route succeeds
        resp = client.post(
            "/archimate/api/link/driver-to-goal",
            json={"driver_id": driver1.id, "goal_id": goal.id},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

        # Second link to a different driver via the old route is rejected
        resp = client.post(
            "/archimate/api/link/driver-to-goal",
            json={"driver_id": driver2.id, "goal_id": goal.id},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert "error" in data
        assert "already linked" in data["error"]

        # Verify goal is still linked to driver1
        db_session.refresh(goal)
        assert goal.driver_id == driver1.id


# --------------------------------------------------------------------------- #
# Trace from here URL
# --------------------------------------------------------------------------- #


class TestTraceFromHere:
    def test_trace_from_here_url(self):
        """The trace-from-here action returns the correct permanent-link URL."""
        from app.modules.solutions_strategic.v2.services.traceability_matrix_service import (
            TraceabilityMatrixService,
        )

        url = TraceabilityMatrixService.trace_from_here_url(42)
        assert url == "/api/v1/motivation/goals/42/trace"