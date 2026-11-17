"""Health Score must mean the same measurement in both dashboard consumers."""

import ast
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("score,components", [
    (42.9, {"phase_maturity": 0.0, "risk_posture": None, "capability_coverage": 100.0, "governance": 100.0}),
    (0.0, {"phase_maturity": 0.0, "risk_posture": 0.0, "capability_coverage": 0.0, "governance": None}),
    (None, {"phase_maturity": None, "risk_posture": None, "capability_coverage": None, "governance": None}),
])
def test_overview_preserves_canonical_score_precision_and_missing_components(
    monkeypatch, score, components, db_session, client, login_as, tenant_ctx
):
    from app import db
    from app.modules.dashboard.v2.services.executive_dashboard_service import ExecutiveDashboardService
    from tests.test_dashboard_health_score_honesty import _org, _user

    monkeypatch.setattr(ExecutiveDashboardService, "_get_health_score", lambda self: {
        "composite_score": score, "components": components,
        "unavailable_components": [key for key, value in components.items() if value is None],
    })
    org = _org(db_session, "agreement-precision")
    with tenant_ctx(org.id):
        user = _user(org, "precision")
        db.session.flush()
    login_as(client, user)
    html = client.get("/dashboard/health").get_data(as_text=True)
    shown = re.search(r'data-testid="health-score-value">([^<]+)</strong>', html).group(1)
    assert shown == ("—" if score is None else f"{score:.1f}")
    missing = re.search(r'data-testid="health-score-not-measured">Not measured: ([^<]+)\.</span>', html)
    names = {"phase_maturity": "phase maturity", "risk_posture": "risk posture",
             "capability_coverage": "capability coverage", "governance": "governance"}
    for key, value in components.items():
        if value is None:
            assert missing is not None and names[key] in missing.group(1)
        elif missing is not None:
            assert names[key] not in missing.group(1)


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


@pytest.mark.parametrize("score,want", [(None, None), (0.0, "0.0"), (42.9, "42.9")])
def test_executive_summary_formats_missing_value_without_hiding_measured_zero(
    monkeypatch, score, want, db_session, client, login_as, tenant_ctx
):
    from app import db
    from app.modules.dashboard.v2.services.executive_dashboard_service import ExecutiveDashboardService
    from tests.test_dashboard_health_score_honesty import _org, _user

    monkeypatch.setattr(ExecutiveDashboardService, "get_executive_summary",
                        lambda self: {"architecture_health": {"composite_score": score}})
    org = _org(db_session, "agreement-summary")
    with tenant_ctx(org.id):
        user = _user(org, "summary")
        db.session.flush()
    login_as(client, user)
    data = client.get("/dashboard/api/executive-summary").get_json()["data"]
    # A missing score is null (the page draws it as a dash); a measured zero stays "0.0".
    assert data["Health Score"] == want


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
