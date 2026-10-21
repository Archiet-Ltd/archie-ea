"""The executive dashboard's risk roll-ups must read the Risk Register's own
model, not the separate, essentially unused SolutionRisk table.

A 6 Sep 2026 QA acceptance pass found every board-facing risk summary
(Overview tile, CTO tab, Health Scorecard, the CTO-tab solution risk
breakdown) reading zero open risks while the Risk Register itself showed
5 open, 4 of them critical - because those surfaces queried SolutionRisk
(app.models.solution_lifecycle_models), a table with zero rows in
production, instead of Risk (app.models.risk), the model /api/risks and
the Risk Register template actually read.
"""
from app import db


def _make_risk(organization_id, title, likelihood, impact, status="open"):
    from app.models.risk import Risk, RiskStatus

    return Risk(
        organization_id=organization_id, title=title,
        likelihood=likelihood, impact=impact,
        status=RiskStatus[status.upper()],
    )


def test_executive_summary_counts_real_open_risks(db_session, make_org, tenant_ctx):
    from app.modules.dashboard.v2.services.executive_dashboard_service import (
        ExecutiveDashboardService,
    )

    org = make_org("risk-rollup")
    with tenant_ctx(org.id):
        db_session.add_all([
            _make_risk(org.id, "Critical open risk", 5, 5),       # score 25 -> critical
            _make_risk(org.id, "High open risk", 3, 3),           # score 9 -> high
            _make_risk(org.id, "Closed risk (must not count)", 5, 5, status="closed"),
        ])
        db_session.flush()

        summary = ExecutiveDashboardService()._get_risk_summary()

        assert summary["total"] == 2
        assert summary["counts"]["critical"] == 1
        assert summary["counts"]["high"] == 1
        assert summary["counts"]["low"] == 0


def test_executive_summary_error_gives_none(db_session, make_org, monkeypatch, tenant_ctx):
    """Forced query error gives total is None; overview renders — not 0."""
    from app.modules.dashboard.v2.services.executive_dashboard_service import (
        ExecutiveDashboardService,
    )

    org = make_org("risk-rollup-error")
    with tenant_ctx(org.id):
        # Force the Risk.query to raise an exception so the except branch runs
        def failing_query(*args, **kwargs):
            raise RuntimeError("Simulated query failure")

        monkeypatch.setattr("app.models.risk.Risk.query", failing_query)  # type: ignore[attr-defined]

        summary = ExecutiveDashboardService()._get_risk_summary()
        assert summary["total"] is None
        assert summary["counts"] is None


def test_health_scorecard_risk_counts_match_the_risk_register(db_session, make_org, tenant_ctx):
    from app.modules.dashboard.v2.routes.dashboard_views import (
        _assemble_health_scorecard_metrics,
    )

    org = make_org("risk-rollup-scorecard")
    with tenant_ctx(org.id):
        db_session.add_all([
            _make_risk(org.id, "Critical open risk", 5, 5),
            _make_risk(org.id, "Low open risk", 1, 1),
        ])
        db_session.flush()

        metrics = _assemble_health_scorecard_metrics()

        assert metrics["risk_counts"]["critical"] == 1
        assert metrics["risk_counts"]["low"] == 1
        assert metrics["risk_counts"]["high"] == 0
