"""The demonstration company gives the investment matrix capabilities to tell apart.

A matrix where every capability reads the same score cannot help anyone choose,
so the seeded organisation rates its capabilities, links them to objectives
through the model, and has risks on the applications that run them.
"""
import os

from app.commands.seed_demo_company import seed_demo_company


def test_demo_company_scores_vary_across_the_matrix(app, db_session, tenant_ctx):
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    org_id = db_session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    with tenant_ctx(org_id):
        rows = InvestmentPrioritizationService().analyze_investment_priorities()["capability_scores"]

    assert len(rows) >= 24
    strategic = {r["strategic_score"] for r in rows if r["strategic_score"] is not None}
    risk = {r["risk_score"] for r in rows if r["risk_count"]}
    assert len(strategic) >= 3, strategic
    assert len(risk) >= 2, risk
    assert any(r["objective_count"] for r in rows)
    assert all(r["capability_domain"] for r in rows), [r["capability_name"] for r in rows if not r["capability_domain"]]
    assert any(r["investment_need"]["estimated_cost"] for r in rows)
    # a capability nobody rated is scored from the facts the model holds and says so; one with
    # no such fact is not scored, rather than scored 5
    unrated = [r for r in rows if r["strategic_importance"] is None]
    assert unrated
    for r in unrated:
        if r["strategic_basis"] == "derived":
            assert r["strategic_score"] is not None and r["strategic_derivation"]["sentence"].startswith("Derived from")
            assert r["strategic_derivation"]["facts"], r["capability_name"]
        else:
            assert r["strategic_basis"] is None
            assert r["investment_priority_score"] is None and r["strategic_step"]["text"], r["capability_name"]
    assert any(r["strategic_basis"] == "derived" for r in unrated)
