"""The demonstration company's Monitoring and Alerting capability realises its availability goal."""
import os

from app.commands.seed_demo_company import seed_demo_company


def test_monitoring_and_alerting_is_linked_to_its_availability_goal(app, db_session, tenant_ctx):
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.models import ArchiMateRelationship
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    org_id = db_session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    with tenant_ctx(org_id):
        rows = InvestmentPrioritizationService().analyze_investment_priorities()["capability_scores"]
        row = next(r for r in rows if r["capability_name"] == "Monitoring and Alerting")
        assert row["objective_count"] == 1

        goal = db_session.query(ArchiMateElement).filter_by(
            organization_id=org_id, name="Maintain 99.9% system availability").one()
        realisations = db_session.query(ArchiMateRelationship).filter_by(
            organization_id=org_id, target_id=goal.id, type="Realization").all()
        assert len(realisations) == 1
