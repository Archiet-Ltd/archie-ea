"""The three remaining live writers to the superseded solution_risks store,
named in the PR 316 fix-round follow-up: each must create a risk through the
canonical risk register (app/services/risk_service.py) plus the solution
link, never a SolutionRisk row.

- app/modules/ai_chat/tools/executor.py (ToolExecutor._tool_create_risk)
- app/modules/solutions_strategic/v2/services/solution_ai_orchestrator.py
  (SolutionAIOrchestrator._create_entities_from_draft)
- app/services/solution_orchestration_service.py
  (SolutionOrchestrationService.accept_recommendation)

Uses the shared fixtures in tests/conftest.py.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _solution(db_session, org, name="Remaining Writer Solution"):
    from app.models.solution_models import Solution

    solution = Solution(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    return solution


def _user(db_session, org, label):
    from app.models.user import User

    user = User(email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
                first_name="Remaining", last_name="Writer",
                organization_id=org.id, confirmed=True)
    user.password = "not-used-in-tests-123"
    db_session.add(user)
    db_session.flush()
    return user


def test_ai_chat_tool_create_risk_writes_the_canonical_risk_and_leaves_the_old_table_unchanged(
        app, db_session, make_org, tenant_ctx):
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.models.solution_lifecycle_models import SolutionRisk
    from app.modules.ai_chat.tools.executor import ToolExecutor

    org = make_org("remaining-writer-ai-chat")
    solution = _solution(db_session, org)
    user = _user(db_session, org, "ai-chat-tool-user")
    before_solution_risks = SolutionRisk.query.filter_by(solution_id=solution.id).count()

    with tenant_ctx(org.id):
        executor = ToolExecutor(user_id=user.id)
        result = executor._tool_create_risk({
            "solution_id": solution.id,
            "risk_description": "Vendor lock-in on the chosen platform",
            "impact": "high",
            "probability": "medium",
            "mitigation": "Negotiate an exit clause",
        })

    assert result["success"] is True, result
    risk_id = result["result"]["id"]

    risk = Risk.query.filter_by(id=risk_id).first()
    assert risk is not None, "canonical risks row was never created"
    assert risk.solution_id == solution.id
    assert risk.organization_id == org.id
    assert risk.description == "Vendor lock-in on the chosen platform"
    assert risk.mitigation_plan == "Negotiate an exit clause"
    assert (risk.likelihood, risk.impact) == (3, 4)  # medium/high under _level_to_int

    link = RiskEntityLink.query.filter_by(risk_id=risk.id).one()
    assert (link.entity_type, link.entity_id) == ("solution", solution.id)

    assert SolutionRisk.query.filter_by(solution_id=solution.id).count() == before_solution_risks == 0


def test_solution_ai_orchestrator_create_entities_from_draft_writes_the_canonical_risk_and_leaves_the_old_table_unchanged(
        app, db_session, make_org, tenant_ctx):
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.models.solution_lifecycle_models import SolutionRisk
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
        SolutionAIOrchestrator,
    )

    org = make_org("remaining-writer-orchestrator")
    solution = _solution(db_session, org)
    user = _user(db_session, org, "ai-orchestrator-user")
    before_solution_risks = SolutionRisk.query.filter_by(solution_id=solution.id).count()

    parsed = {
        "risks": [
            {
                "risk_description": "Single point of failure in the integration layer",
                "impact": "critical",
                "probability": "high",
                "mitigation": "Add a secondary provider",
                "owner": "Platform team",
            },
        ],
    }

    with tenant_ctx(org.id):
        orchestrator = SolutionAIOrchestrator()
        created, failed = orchestrator._create_entities_from_draft(solution, parsed, user.id)

    assert created.get("risks") == 1, (created, failed)
    assert not failed.get("risks"), failed

    risks = Risk.query.filter_by(solution_id=solution.id).all()
    assert len(risks) == 1, risks
    risk = risks[0]
    assert risk.organization_id == org.id
    assert risk.description == "Single point of failure in the integration layer"
    assert risk.owner == "Platform team"
    assert (risk.likelihood, risk.impact) == (4, 5)  # high/critical under _level_to_int

    link = RiskEntityLink.query.filter_by(risk_id=risk.id).one()
    assert (link.entity_type, link.entity_id) == ("solution", solution.id)

    assert SolutionRisk.query.filter_by(solution_id=solution.id).count() == before_solution_risks == 0


def test_solution_ai_orchestrator_keeps_the_risks_link_to_the_constraint_it_answers(
        app, db_session, make_org, tenant_ctx):
    """PR 316 re-review open point: a risk derived from a named constraint
    must keep that relationship as a RiskEntityLink (entity_type="constraint"),
    since the canonical Risk model has no constraint_id column of its own."""
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
        SolutionAIOrchestrator,
    )

    org = make_org("remaining-writer-orchestrator-constraint")
    solution = _solution(db_session, org)
    user = _user(db_session, org, "ai-orchestrator-constraint-user")

    parsed = {
        "constraints": [
            {
                "name": "Budget cap",
                "description": "No more than £500K total spend",
                "constraint_type": "budget",
                "value": "£500K",
            },
        ],
        "risks": [
            {
                "risk_description": "The budget cap may be breached mid-delivery",
                "impact": "high",
                "probability": "medium",
                "constraint_name": "Budget cap",
            },
        ],
    }

    with tenant_ctx(org.id):
        orchestrator = SolutionAIOrchestrator()
        created, failed = orchestrator._create_entities_from_draft(solution, parsed, user.id)

    assert created.get("constraints") == 1, (created, failed)
    assert created.get("risks") == 1, (created, failed)
    assert not failed.get("risks"), failed

    from app.models.solution_architect_models import SolutionConstraint

    constraint = SolutionConstraint.query.filter_by(name="Budget cap").one()
    risk = Risk.query.filter_by(solution_id=solution.id).one()

    links = {
        link.entity_type: link.entity_id
        for link in RiskEntityLink.query.filter_by(risk_id=risk.id).all()
    }
    assert links.get("solution") == solution.id
    assert links.get("constraint") == constraint.id


def test_solution_orchestration_service_accept_recommendation_writes_the_canonical_risk_and_leaves_the_old_table_unchanged(
        app, db_session, make_org, tenant_ctx):
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.models.solution_architect_models import (
        RecommendationOptionType,
        SolutionAnalysisSession,
        SolutionRecommendation,
    )
    from app.models.solution_lifecycle_models import SolutionRisk
    from app.services.solution_orchestration_service import SolutionOrchestrationService

    org = make_org("remaining-writer-accept-rec")
    user = _user(db_session, org, "accept-rec-user")

    with tenant_ctx(org.id):
        session_obj = SolutionAnalysisSession(
            name="Accept Recommendation Session", created_by_id=user.id,
        )
        db_session.add(session_obj)
        db_session.flush()

        rec = SolutionRecommendation(
            session_id=session_obj.id,
            option_type=RecommendationOptionType.BUILD,
            rank=1,
            justification="Best fit for the budget",
            risks=["Integration risk with the legacy ledger", "Skills gap on the new stack"],
        )
        db_session.add(rec)
        db_session.flush()
        session_id, rec_id = session_obj.id, rec.id

        service = SolutionOrchestrationService()
        result = service.accept_recommendation(
            session_id=session_id, recommendation_id=rec_id, user_id=user.id,
        )

    assert result["success"] is True, result
    assert result["risks_created"] == 2, result
    solution_id = result["solution_id"]

    risks = Risk.query.filter_by(solution_id=solution_id).order_by(Risk.id).all()
    assert [r.description for r in risks] == [
        "Integration risk with the legacy ledger", "Skills gap on the new stack",
    ]
    for risk in risks:
        assert risk.organization_id == org.id
        link = RiskEntityLink.query.filter_by(risk_id=risk.id).one()
        assert (link.entity_type, link.entity_id) == ("solution", solution_id)

    # Scoped to this test's own solution: SolutionRisk.query.count() alone
    # would be a bare, tenant-unscoped count that also counts any row
    # belonging to a solution this test never touched.
    assert SolutionRisk.query.filter_by(solution_id=solution_id).count() == 0
