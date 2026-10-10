"""
Tests for the motivation bridge: promoting solution-scoped AI solution-design
journey motivation elements (SolutionDriver/SolutionGoal/SolutionOutcome/
SolutionPrinciple) into the enterprise motivation layer (Driver/Goal/Outcome/
Principle) via app.services.motivation_bridge_service.promote_solution_motivation.

Covers:
- MotivationBridgeLink model imports, has the right table name, and enforces
  the idempotency-guaranteeing unique constraint.
- promote_solution_motivation() creates an enterprise Driver + Goal (with the
  Driver -> Goal chain preserved through the bridge) plus MotivationBridgeLink
  rows, for a solution that has one SolutionDriver and one SolutionGoal.
- Re-running promote_solution_motivation() on the same solution is a no-op
  (idempotent): nothing new is created/linked, everything is 'skipped'.
- dry_run=True previews the same summary but persists nothing.
"""

import uuid

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    # New table (motivation_bridge_links) — create_all() only creates missing
    # tables, never touches existing ones, so this is safe against the shared
    # test database's pre-existing schema.
    with app.app_context():
        db.create_all()

    return app


def _make_solution_with_driver_and_goal(db, suffix):
    """Create a minimal Organization/User/Solution/SolutionAnalysisSession/
    SolutionProblemDefinition, one SolutionDriver, and one SolutionGoal
    (linked to that driver via driver_id). Returns (solution, driver, goal).
    """
    from app.models.organization import Organization
    from app.models.solution_architect_models import (
        DriverType,
        SolutionAnalysisSession,
        SolutionDriver,
        SolutionGoal,
        SolutionProblemDefinition,
    )
    from app.models.solution_models import Solution
    from app.models.user import User

    org = Organization(name=f"Bridge Test Org {suffix}", slug=f"bridge-test-org-{suffix}")
    db.session.add(org)
    db.session.flush()

    user = User(
        email=f"bridge-test-{suffix}@example.com", first_name="Bridge", last_name="Tester"
    )
    db.session.add(user)
    db.session.flush()

    session = SolutionAnalysisSession(
        name=f"Bridge Test Session {suffix}",
        created_by_id=user.id,
        organization_id=org.id,
    )
    db.session.add(session)
    db.session.flush()

    solution = Solution(
        name=f"Bridge Test Solution {suffix}",
        created_by_id=user.id,
        analysis_session_id=session.id,
        organization_id=org.id,
    )
    db.session.add(solution)
    db.session.flush()

    problem = SolutionProblemDefinition(
        session_id=session.id,
        problem_description="Test problem description for the motivation bridge",
        organization_id=org.id,
    )
    db.session.add(problem)
    db.session.flush()

    driver = SolutionDriver(
        problem_id=problem.id,
        name=f"Bridge Test Driver {suffix}",
        description="A driver created for the motivation bridge test",
        driver_type=DriverType.EXTERNAL,
        organization_id=org.id,
    )
    db.session.add(driver)
    db.session.flush()

    goal = SolutionGoal(
        problem_id=problem.id,
        driver_id=driver.id,
        name=f"Bridge Test Goal {suffix}",
        description="A goal created for the motivation bridge test",
        priority=2,
        organization_id=org.id,
    )
    db.session.add(goal)
    db.session.commit()

    return solution, driver, goal


def _cleanup(db, solution, driver, goal, enterprise_element_ids):
    """Best-effort teardown so the shared test database doesn't accumulate rows."""
    from app.models.motivation import Driver, Goal, MotivationBridgeLink
    from app.models.organization import Organization
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionDriver,
        SolutionGoal,
        SolutionProblemDefinition,
    )
    from app.models.solution_models import Solution
    from app.models.user import User

    try:
        MotivationBridgeLink.query.filter_by(solution_id=solution.id).delete()
        for enterprise_type, enterprise_id in enterprise_element_ids:
            model = Driver if enterprise_type == "Driver" else Goal
            model.query.filter_by(id=enterprise_id).delete()
        SolutionGoal.query.filter_by(id=goal.id).delete()
        SolutionDriver.query.filter_by(id=driver.id).delete()
        SolutionProblemDefinition.query.filter_by(
            session_id=solution.analysis_session_id
        ).delete()
        analysis_session_id = solution.analysis_session_id
        org_id = solution.organization_id
        created_by_id = solution.created_by_id
        Solution.query.filter_by(id=solution.id).delete()
        SolutionAnalysisSession.query.filter_by(id=analysis_session_id).delete()
        User.query.filter_by(id=created_by_id).delete()
        Organization.query.filter_by(id=org_id).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()


class TestMotivationBridgeLinkModel:
    def test_bridge_link_model_importable(self):
        from app.models.motivation import MotivationBridgeLink

        assert MotivationBridgeLink is not None
        assert MotivationBridgeLink.__tablename__ == "motivation_bridge_links"

    def test_bridge_link_columns(self):
        from app.models.motivation import MotivationBridgeLink

        columns = {c.name for c in MotivationBridgeLink.__table__.columns}
        assert columns == {
            "id",
            "solution_id",
            "solution_element_type",
            "solution_element_id",
            "enterprise_element_type",
            "enterprise_element_id",
            "archimate_element_id",
            "created_at",
            "organization_id",  # TenantMixin: this link is now tenant-scoped
        }

    def test_bridge_link_has_unique_constraint(self):
        """The unique constraint on (solution_element_type, solution_element_id)
        is what makes re-running the bridge idempotent."""
        from app.models.motivation import MotivationBridgeLink

        constraint_names = {
            getattr(c, "name", None) for c in MotivationBridgeLink.__table__.constraints
        }
        assert "uq_motivation_bridge_solution_element" in constraint_names


class TestPromoteSolutionMotivation:
    def test_promotes_driver_and_goal_and_is_idempotent(self, app):
        from app import db
        from app.models.motivation import Driver, Goal, MotivationBridgeLink
        from app.services.motivation_bridge_service import promote_solution_motivation

        suffix = uuid.uuid4().hex[:8]

        with app.app_context():
            solution, driver, goal = _make_solution_with_driver_and_goal(db, suffix)
            enterprise_element_ids = []

            try:
                summary = promote_solution_motivation(solution.id)

                assert summary["errors"] == []
                assert summary["created"] == 2  # one Driver + one Goal
                assert summary["linked"] == 0
                assert summary["skipped"] == 0

                driver_link = MotivationBridgeLink.query.filter_by(
                    solution_element_type="SolutionDriver", solution_element_id=driver.id
                ).first()
                goal_link = MotivationBridgeLink.query.filter_by(
                    solution_element_type="SolutionGoal", solution_element_id=goal.id
                ).first()

                assert driver_link is not None
                assert goal_link is not None
                assert driver_link.solution_id == solution.id
                assert goal_link.solution_id == solution.id
                assert driver_link.enterprise_element_type == "Driver"
                assert goal_link.enterprise_element_type == "Goal"
                assert driver_link.archimate_element_id is not None
                assert goal_link.archimate_element_id is not None

                enterprise_element_ids.append(("Driver", driver_link.enterprise_element_id))
                enterprise_element_ids.append(("Goal", goal_link.enterprise_element_id))

                enterprise_driver = db.session.get(Driver, driver_link.enterprise_element_id)
                enterprise_goal = db.session.get(Goal, goal_link.enterprise_element_id)

                assert enterprise_driver is not None
                assert enterprise_driver.name == driver.name
                assert enterprise_driver.archimate_element_id is not None

                assert enterprise_goal is not None
                assert enterprise_goal.name == goal.name
                assert enterprise_goal.archimate_element_id is not None
                # The Driver -> Goal motivation chain is preserved through the bridge.
                assert enterprise_goal.driver_id == enterprise_driver.id

                # Re-running is a no-op: idempotent.
                summary2 = promote_solution_motivation(solution.id)
                assert summary2["errors"] == []
                assert summary2["created"] == 0
                assert summary2["linked"] == 0
                assert summary2["skipped"] == 2

                # No duplicate bridge links or enterprise entities were created.
                assert (
                    MotivationBridgeLink.query.filter_by(
                        solution_element_type="SolutionDriver", solution_element_id=driver.id
                    ).count()
                    == 1
                )
                assert (
                    MotivationBridgeLink.query.filter_by(
                        solution_element_type="SolutionGoal", solution_element_id=goal.id
                    ).count()
                    == 1
                )
                assert Driver.query.filter_by(name=driver.name).count() == 1
                assert Goal.query.filter_by(name=goal.name).count() == 1
            finally:
                _cleanup(db, solution, driver, goal, enterprise_element_ids)

    def test_dry_run_does_not_persist_anything(self, app):
        from app import db
        from app.models.motivation import MotivationBridgeLink
        from app.services.motivation_bridge_service import promote_solution_motivation

        suffix = uuid.uuid4().hex[:8]

        with app.app_context():
            solution, driver, goal = _make_solution_with_driver_and_goal(db, suffix)

            try:
                summary = promote_solution_motivation(solution.id, dry_run=True)

                assert summary["errors"] == []
                assert summary["created"] == 2

                # Nothing was actually written to the database.
                assert (
                    MotivationBridgeLink.query.filter_by(
                        solution_element_type="SolutionDriver", solution_element_id=driver.id
                    ).count()
                    == 0
                )
                assert (
                    MotivationBridgeLink.query.filter_by(
                        solution_element_type="SolutionGoal", solution_element_id=goal.id
                    ).count()
                    == 0
                )
            finally:
                _cleanup(db, solution, driver, goal, [])

    def test_unknown_solution_id_reports_error_not_exception(self, app):
        from app.services.motivation_bridge_service import promote_solution_motivation

        with app.app_context():
            summary = promote_solution_motivation(-1)

        assert summary["created"] == 0
        assert summary["linked"] == 0
        assert summary["skipped"] == 0
        assert len(summary["errors"]) == 1
        assert summary["errors"][0]["element_type"] == "Solution"
