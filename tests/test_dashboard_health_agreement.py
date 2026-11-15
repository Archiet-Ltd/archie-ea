"""Health Score must mean the same measurement in both dashboard consumers."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("score,components,shown,not_measured", [
    (42.9, {"phase_maturity": 0.0, "risk_posture": None, "capability_coverage": 100.0, "governance": 100.0},
     "42.9", "risk posture"),
    (0.0, {"phase_maturity": 0.0, "risk_posture": 0.0, "capability_coverage": 0.0, "governance": None},
     "0.0", "governance"),
    (None, {"phase_maturity": None, "risk_posture": None, "capability_coverage": None, "governance": None},
     "\u2014", "phase maturity, risk posture, capability coverage and governance"),
])
def test_health_page_preserves_canonical_score_precision_and_missing_components(
        monkeypatch, db_session, client, login_as, tenant_ctx, score, components, shown, not_measured):
    from app import db
    from app.modules.dashboard.v2.services.executive_dashboard_service import ExecutiveDashboardService
    from tests.test_dashboard_health_score_honesty import _org, _user
    monkeypatch.setattr(ExecutiveDashboardService, "_get_health_score", lambda self: {
        "composite_score": score, "components": components,
        "unavailable_components": [key for key, value in components.items() if value is None],
    })
    org = _org(db_session, "health-page-" + shown)
    with tenant_ctx(org.id):
        user = _user(org, "healthpage")
        db.session.flush()
    login_as(client, user)
    response = client.get("/dashboard/health")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert re.search(r'data-testid="health-score-value">([^<]+)</strong>', page).group(1) == shown
    assert re.search(r'data-testid="health-score-not-measured">Not measured: ([^<]+)\.</span>', page).group(1) == not_measured


def test_health_card_browser_assertion_uses_rendered_value_element():
    from jinja2 import Environment, FileSystemLoader
    from playwright.sync_api import expect, sync_playwright

    root = Path(__file__).resolve().parents[1]
    env = Environment(loader=FileSystemLoader(root / "app/templates"), autoescape=True)
    rendered = env.from_string(
        "{% from 'components/metrics_card.html' import metrics_card %}"
        "{{ metrics_card(title='Health Score', value='100.0', "
        "description='Architecture health', href='/dashboard/health') }}"
    ).render()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(rendered)
            score = page.locator('a[href="/dashboard/health"] [data-slot="card-title"]')
            expect(score).to_be_visible()
            expect(score).to_have_text("100.0")
            assert score.inner_text().strip() == "100.0"
        finally:
            browser.close()


def test_empty_portfolio_has_no_measured_health_components(monkeypatch):
    from sqlalchemy import func
    from app.modules.dashboard.v2.services import executive_dashboard_service as module
    class EmptyQuery:
        def filter(self, *args):
            return self
        def scalar(self):
            return 0
    monkeypatch.setattr(module, "db", SimpleNamespace(func=func, session=SimpleNamespace(query=lambda *args: EmptyQuery())))
    from app.modules.dashboard.v2.services import solution_phase_measures as phase_measures
    monkeypatch.setattr(phase_measures, "recorded_phase_summary", lambda: phase_measures.summarise_phases([]))
    monkeypatch.setattr(module.ExecutiveDashboardService, "_get_capability_coverage",
                        lambda self: {"percentage": None})
    result = module.ExecutiveDashboardService()._get_health_score()
    assert result["composite_score"] is None
    assert result["components"] == dict.fromkeys(["phase_maturity", "risk_posture", "capability_coverage", "governance"])
    assert result["unavailable_components"] == ["capability_coverage", "governance", "phase_maturity", "risk_posture"]


def test_executive_summary_formats_missing_value_without_hiding_measured_zero(client, login_as, make_org):
    """On the signed-in overview, a figure that cannot be worked out is explained and
    a figure measured as exactly 0 is shown as 0."""
    from tests.test_overview_after_sample_removed import _own_record
    from tests.test_sample_company_working_data import _architect

    org = make_org()
    user = _architect(org, "summary-zero@example.com")
    _own_record(org, user)  # one model element, no application and no contract
    login_as(client, user)
    response = client.get("/dashboard/overview")
    assert response.status_code == 200
    page = response.get_data(as_text=True)

    # Missing: no contract states a cost, so the card has no number, says why, and shows no dash or zero.
    cost = re.search(r'data-testid="card-run-cost"(.*?)data-testid="card-relationships"', page, re.S).group(1)
    assert 'data-testid="card-run-cost-value"' not in cost
    assert re.search(r'data-testid="card-run-cost-sentence">\s*Appears here once a contract states its annual cost\.', cost)
    assert "\u2014" not in cost and "$0" not in cost

    # Measured: the organisation holds zero applications, and the welcome figure says 0.
    applications = re.search(r'data-testid="welcome-applications">(.*?)</a>', page, re.S).group(1)
    assert re.sub(r"<[^>]+>", " ", applications).split() == ["0", "Applications"]


@pytest.mark.parametrize("phases,want", [
    (["A", "C", None, "Z"], 50.0),
    ([None, "", "Z"], None),
    ([" c ", "C", None], 100.0),
    ([" A ", "B", None], 0.0),
])
def test_phase_component_counts_only_valid_normalized_observations(phases, want):
    """The arithmetic of the phase component on raw phase values: only recorded,
    valid phases supply the denominator, and a value is normalised before it is
    classified.

    This runs the shared classification and share functions on a list of values.
    It does not touch the database read; the PostgreSQL cases in
    test_dashboard_health_integration.py run the real query, including cases where
    normalising a value changes the answer.
    """
    from app.modules.dashboard.v2.services.solution_phase_measures import (
        share_in_advanced_phases,
        summarise_phases,
    )

    assert share_in_advanced_phases(summarise_phases(phases)) == want
