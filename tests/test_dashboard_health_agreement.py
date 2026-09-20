"""Health Score must mean the same measurement in both dashboard consumers."""

import ast
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


def _overview_health_context():
    """Execute the route's actual health block without its unrelated DB queries."""
    source = Path(__file__).resolve().parents[1] / "app/modules/dashboard/v2/routes/dashboard_views.py"
    overview = next(node for node in ast.parse(source.read_text(encoding="utf-8")).body
                    if isinstance(node, ast.FunctionDef) and node.name == "overview")
    def assignment(node, name):
        return isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets)
    start = next(i for i, node in enumerate(overview.body) if assignment(node, "health_score"))
    end = next(i for i, node in enumerate(overview.body) if assignment(node, "data_coverage"))
    namespace = {"persona_metrics": {}, "solutions": [], "capability_health": [],
                 "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=overview.body[start:end], type_ignores=[]), str(source), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("score,components", [
    (42.9, {"phase_maturity": 0.0, "risk_posture": None, "capability_coverage": 100.0, "governance": 100.0}),
    (0.0, {"phase_maturity": 0.0, "risk_posture": 0.0, "capability_coverage": 0.0, "governance": None}),
    (None, {"phase_maturity": None, "risk_posture": None, "capability_coverage": None, "governance": None}),
])
def test_overview_preserves_canonical_score_precision_and_missing_components(monkeypatch, score, components):
    from app.modules.dashboard.v2.services.executive_dashboard_service import ExecutiveDashboardService
    monkeypatch.setattr(ExecutiveDashboardService, "_get_health_score", lambda self: {
        "composite_score": score, "components": components,
        "unavailable_components": [key for key, value in components.items() if value is None],
    })
    context = _overview_health_context()
    assert context["health_score"] == score
    assert context["health_components"] == {
        "phase_maturity": components["phase_maturity"], "risk_health": components["risk_posture"],
        "capability_coverage": components["capability_coverage"], "governance": components["governance"],
    }


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


def test_executive_summary_formats_missing_value_without_hiding_measured_zero():
    from playwright.sync_api import sync_playwright
    source = (Path(__file__).resolve().parents[1] / "app/templates/dashboards/overview.html").read_text(encoding="utf-8")
    expression = re.search(r'x-text="(typeof val ===[^"\n]+)"', source).group(1)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            values = page.evaluate("expression => [null, 0, 42.9].map(val => new Function('val', 'return ' + expression)(val))", expression)
            assert values == ["—", "0", "42.9"]
        finally:
            browser.close()


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
