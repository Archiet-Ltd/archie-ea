"""The executive Health Score must not present one lightly-weighted, single
measured component as a confident whole-portfolio number.

Confirmed live on 6 Sep 2026 (Archie-E2E-Workflow-Test-Report.md E2E-M): a
clean tenant with 0 solutions, 0 capabilities and one newly created,
unresolved ARB review reported "Health Score: 90" - phase maturity, risk
posture and capability coverage all had nothing to measure and dropped out
of the weighted composite, leaving only governance (nominal weight 0.1),
whose value then received 100% of the visible score after renormalisation.
A single freshly-created ARB item should not be able to produce a
confident-looking 90/100 for an otherwise entirely unmeasured portfolio.
"""
from app import db


def test_health_score_withheld_when_only_governance_is_measurable(
    app, db_session, make_org, tenant_ctx,
):
    from app.modules.dashboard.v2.services.executive_dashboard_service import (
        ExecutiveDashboardService,
    )
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.user import User

    org = make_org("health-score-confidence")
    with tenant_ctx(org.id):
        submitter = User(email=f"submitter-{org.id}@example.com", enterprise_role="architect")
        db_session.add(submitter)
        db_session.flush()

        # Exactly the reported scenario: no solutions, no capabilities, no
        # risks, one unresolved ARB item - only governance can be scored.
        db_session.add(ARBReviewItem(
            organization_id=org.id, status="draft", review_number="REV-TEST-001",
            title="Test review", review_type="architecture",
            submitter_id=submitter.id,
        ))
        db_session.flush()

        result = ExecutiveDashboardService()._get_health_score()

        # One unresolved item is not a governance measure at all: nothing
        # has been decided, so there is no approval rate to speak of.
        assert result["components"]["governance"]["value"] is None
        assert result["components"]["phase_maturity"]["value"] is None
        assert result["components"]["risk_posture"]["value"] is None
        assert result["components"]["capability_coverage"]["value"] is None
        assert result["composite_score"] is None

        # A decided item makes governance measurable. It is still 10% of the
        # nominal weight - not enough to stand in for the whole composite.
        db_session.add(ARBReviewItem(
            organization_id=org.id, status="approved", review_number="REV-TEST-002",
            title="Decided review", review_type="architecture",
            submitter_id=submitter.id,
        ))
        db_session.flush()

        result = ExecutiveDashboardService()._get_health_score()

        assert result["components"]["governance"]["value"] is not None
        assert result["composite_score"] is None


def test_health_score_is_reported_when_most_of_the_weight_is_measured(
    app, db_session, make_org, tenant_ctx,
):
    from app.modules.dashboard.v2.services.executive_dashboard_service import (
        ExecutiveDashboardService,
    )
    from app.models.solution_models import Solution
    from app.models.risk import Risk, RiskStatus

    org = make_org("health-score-confidence-full")
    with tenant_ctx(org.id):
        db_session.add(Solution(name="Solution A", organization_id=org.id, adm_phase="D"))
        db_session.add(Risk(
            organization_id=org.id, title="Low risk", likelihood=1, impact=1,
            status=RiskStatus.OPEN,
        ))
        db_session.flush()

        result = ExecutiveDashboardService()._get_health_score()

        # phase_maturity (0.4) + risk_posture (0.3) = 0.7 of the weight is
        # measured - enough to report a real composite.
        assert result["components"]["phase_maturity"]["value"] is not None
        assert result["components"]["risk_posture"]["value"] is not None
        assert result["composite_score"] is not None
