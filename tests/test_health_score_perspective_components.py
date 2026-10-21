"""The health score is built from named perspective components.

Each component is ``{"key", "label", "value", "basis", "source", "as_of"}`` and
its value is a number or ``None`` for not measured. Nothing that is not measured
is shown as 0 or 100; the composite uses only what was measured and names what
it left out; the health page and the Overview block read the same service.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import db
from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_portfolio import ApplicationComponent
from app.models.architecture_review_board import ARBReviewItem
from app.models.business_capability import BusinessCapability
from app.models.risk import Risk, RiskStatus
from app.models.solution_models import Solution
from app.models.user import User
from tests.test_dashboard_health_score_honesty import _org, _user
from tests.test_health_score_one_format import _health_block_tile, _page, _seed, _text

COMPONENT_KEYS = ("phase_maturity", "risk_posture", "capability_coverage", "governance")
CONTRACT_FIELDS = {"key", "label", "value", "basis", "source", "as_of"}
EM_DASH = "—"


def _service():
    from app.modules.dashboard.v2.services.executive_dashboard_service import (
        ExecutiveDashboardService,
    )

    return ExecutiveDashboardService()


def _values(result):
    return {key: result["components"][key]["value"] for key in COMPONENT_KEYS}


def _add_solutions(org, phases):
    for index, phase in enumerate(phases):
        solution = Solution(name=f"Solution {index} {org.id}", organization_id=org.id, adm_phase=phase)
        db.session.add(solution)
        db.session.flush()
        solution.adm_phase = phase
    db.session.flush()


def _add_risk(org, likelihood, impact):
    db.session.add(Risk(
        organization_id=org.id, title=f"Risk {likelihood}x{impact}",
        likelihood=likelihood, impact=impact, status=RiskStatus.OPEN,
    ))
    db.session.flush()


def _add_capabilities(org, total, mapped):
    """``total`` top-level capabilities, the first ``mapped`` of them with an application."""
    application = ApplicationComponent(name=f"App {org.id}", organization_id=org.id)
    db.session.add(application)
    db.session.flush()
    for index in range(total):
        capability = BusinessCapability(name=f"Capability {index} {org.id}", organization_id=org.id, level=1)
        db.session.add(capability)
        db.session.flush()
        if index < mapped:
            db.session.add(ApplicationCapabilityMapping(
                application_component_id=application.id,
                business_capability_id=capability.id,
                organization_id=org.id,
            ))
    db.session.flush()


def _add_review(org, submitter, status, number, age_days=0):
    item = ARBReviewItem(
        organization_id=org.id, status=status, review_number=number,
        title=f"Review {number}", review_type="architecture", submitter_id=submitter.id,
    )
    db.session.add(item)
    db.session.flush()
    if age_days:
        item.created_at = datetime.utcnow() - timedelta(days=age_days)
        db.session.flush()
    return item


def _submitter(org):
    submitter = User(email=f"submitter-{org.id}@example.com", enterprise_role="architect")
    db.session.add(submitter)
    db.session.flush()
    return submitter


# 1 -------------------------------------------------------------------------
def test_an_empty_organisation_measures_nothing_and_shows_dashes(
    db_session, tenant_ctx, client, login_as
):
    org = _org(db_session, "perspective-empty")
    user = _user(org, "perspective-empty")
    with tenant_ctx(org.id):
        result = _service()._get_health_score()

        for key in COMPONENT_KEYS:
            component = result["components"][key]
            assert set(component) == CONTRACT_FIELDS
            assert component["key"] == key
            assert component["value"] is None, key
            assert component["basis"], f"{key}: a plain sentence on why it is not measured"
        assert result["composite_score"] is None
        assert result["left_out"] == [
            "phase maturity", "risk posture", "capability coverage", "governance",
        ]
        assert result["unavailable_components"] == sorted(COMPONENT_KEYS)

    soup = _page(client, login_as, user, "/dashboard/health")
    assert _text(soup, "health-score-value") == EM_DASH
    assert _text(soup, "health-score-not-measured") == (
        "Not measured: phase maturity, risk posture, capability coverage and governance."
    )
    for key in COMPONENT_KEYS:
        assert _text(soup, f"health-component-{key}").split()[-1] != "0%"
        value = soup.find(attrs={"data-testid": f"health-component-{key}"}).find(
            attrs={"data-testid": "health-component-value"}
        )
        assert value.get_text().strip() == EM_DASH, key


# 2 -------------------------------------------------------------------------
def test_phases_and_capabilities_only_give_those_two_renormalised(db_session, make_org, tenant_ctx):
    org = make_org("perspective-two-measures")
    with tenant_ctx(org.id):
        _add_solutions(org, ["C", "C", "A"])
        _add_capabilities(org, total=2, mapped=1)

        result = _service()._get_health_score()

        assert _values(result) == {
            "phase_maturity": 66.7, "risk_posture": None,
            "capability_coverage": 50.0, "governance": None,
        }
        assert result["composite_score"] == round((66.7 * 0.4 + 50.0 * 0.2) / 0.6, 1)
        assert result["left_out"] == ["risk posture", "governance"]
        assert result["unavailable_components"] == ["governance", "risk_posture"]


# 3 -------------------------------------------------------------------------
def test_one_unresolved_review_item_no_longer_scores_ninety(db_session, make_org, tenant_ctx):
    org = make_org("perspective-governance")
    with tenant_ctx(org.id):
        submitter = _submitter(org)
        _add_review(org, submitter, "draft", "REV-G-1")

        result = _service()._get_health_score()

        assert result["components"]["governance"]["value"] is None
        assert result["composite_score"] is None
        assert result["components"]["governance"]["basis"] == "No review item has been decided yet."


def test_governance_has_no_invented_default_and_no_floor(db_session, make_org, tenant_ctx):
    org = make_org("perspective-governance-measured")
    with tenant_ctx(org.id):
        submitter = _submitter(org)
        _add_review(org, submitter, "approved", "REV-G-A")
        _add_review(org, submitter, "rejected", "REV-G-R")
        # Decided half and half, nothing open: timeliness 1.0, approval 0.5.
        assert _service()._governance_component()["value"] == 83.3

    other = make_org("perspective-governance-floor")
    with tenant_ctx(other.id):
        submitter = _submitter(other)
        _add_review(other, submitter, "rejected", "REV-F-R")
        _add_review(other, submitter, "submitted", "REV-F-O", age_days=40)
        # One rejected, one open for 40 days: a measured 0, not the old floor of 40.
        assert _service()._governance_component()["value"] == 0.0


# 4 -------------------------------------------------------------------------
def test_risk_posture_is_what_the_risk_summary_returns_with_no_second_query(
    db_session, make_org, tenant_ctx, monkeypatch
):
    service_class = type(_service())
    calls = []
    real = service_class._get_risk_summary

    def spy(self):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(service_class, "_get_risk_summary", spy)

    org = make_org("perspective-risk")
    with tenant_ctx(org.id):
        _add_risk(org, 5, 5)  # critical
        _add_risk(org, 3, 3)  # high
        _add_risk(org, 1, 1)  # low

        statements = []

        def record(conn, cursor, statement, *args):
            if "FROM risks" in statement:
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            result = _service()._get_health_score()
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

        summary = real(_service())
        severe = summary["counts"]["critical"] + summary["counts"]["high"]
        assert result["components"]["risk_posture"]["value"] == round(
            (1 - severe / summary["total"]) * 100, 1
        ) == 33.3
        assert len(calls) == 1, "the component asks the risk summary once"
        assert len(statements) == 1, f"one risk query, not two: {statements}"


def test_a_failed_risk_read_is_not_measured_not_perfect(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("perspective-risk-failed")
    with tenant_ctx(org.id):
        def failing(*args, **kwargs):
            raise RuntimeError("simulated query failure")

        monkeypatch.setattr("app.models.risk.Risk.query", failing)
        assert _service()._get_risk_summary() == {"counts": None, "total": None}
        component = _service()._get_health_score()["components"]["risk_posture"]
        assert component["value"] is None


def test_no_open_risk_recorded_is_not_a_perfect_posture(db_session, make_org, tenant_ctx):
    org = make_org("perspective-risk-none")
    with tenant_ctx(org.id):
        assert _service()._get_health_score()["components"]["risk_posture"]["value"] is None


# 5 -------------------------------------------------------------------------
def test_one_organisations_components_never_count_anothers_rows(db_session, make_org, tenant_ctx):
    org_a = make_org("perspective-org-a")
    org_b = make_org("perspective-org-b")
    with tenant_ctx(org_a.id):
        _add_solutions(org_a, ["C", "A"])
        _add_risk(org_a, 5, 5)
        _add_risk(org_a, 1, 1)
        _add_capabilities(org_a, total=1, mapped=1)
        submitter = _submitter(org_a)
        _add_review(org_a, submitter, "approved", "REV-A-1")
    with tenant_ctx(org_b.id):
        _add_solutions(org_b, ["A", "A", "A", "B"])
        for _ in range(4):
            _add_risk(org_b, 5, 5)
        _add_capabilities(org_b, total=4, mapped=0)
        submitter = _submitter(org_b)
        _add_review(org_b, submitter, "rejected", "REV-B-1")

    with tenant_ctx(org_a.id):
        assert _values(_service()._get_health_score()) == {
            "phase_maturity": 50.0, "risk_posture": 50.0,
            "capability_coverage": 100.0, "governance": 100.0,
        }
    with tenant_ctx(org_b.id):
        assert _values(_service()._get_health_score()) == {
            "phase_maturity": 0.0, "risk_posture": 0.0,
            "capability_coverage": 0.0, "governance": 66.7,
        }


# 6 -------------------------------------------------------------------------
@pytest.mark.parametrize("slug,phases,risks", [
    ("agree-measured", ["C", "F", "A"], [(5, 5), (1, 1)]),
    ("agree-phases-only", ["C", "F", "C"], []),
    ("agree-nothing", [], []),
])
def test_the_overview_block_and_the_health_page_show_the_same_score_and_parts(
    db_session, tenant_ctx, client, login_as, slug, phases, risks
):
    user = _seed(db_session, tenant_ctx, slug, phases)
    with tenant_ctx(user.organization_id):
        for likelihood, impact in risks:
            db.session.add(Risk(
                organization_id=user.organization_id, title=f"{slug} {likelihood}x{impact}",
                likelihood=likelihood, impact=impact, status=RiskStatus.OPEN,
            ))
        db.session.flush()
        expected = _service()._get_health_score()

    overview = _page(client, login_as, user, "/dashboard/overview")
    scorecard = _page(client, login_as, user, "/dashboard/health")

    from app.modules.dashboard.v2.services.executive_dashboard_service import format_health_score

    shown = format_health_score(expected["composite_score"])
    assert _text(overview, "health-score-value") == shown
    assert _text(scorecard, "health-score-value") == shown

    labels = {
        "phase_maturity": "Phase Maturity", "risk_posture": "Risk Posture",
        "capability_coverage": "Capability Coverage", "governance": "Governance",
    }
    for key, label in labels.items():
        value = expected["components"][key]["value"]
        text = EM_DASH if value is None else f"{value}%"
        assert _health_block_tile(overview, label).startswith(f"{label} {text}"), key
        cell = scorecard.find(attrs={"data-testid": f"health-component-{key}"})
        assert cell.find(attrs={"data-testid": "health-component-value"}).get_text().strip() == text, key
