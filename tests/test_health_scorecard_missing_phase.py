"""Unknown ADM phases must not become fabricated Vision maturity."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def measure(rows, monkeypatch, db_session):
    """The scorecard's phase figures for ``rows``: the route's own assembly, with
    only the read of the solutions' phases replaced by ``rows`` (or by a failure)."""
    from app.modules.dashboard.v2.routes.dashboard_views import _assemble_health_scorecard_metrics
    from app.modules.dashboard.v2.services import solution_phase_measures as phase_measures

    def recorded_phase_summary():
        if isinstance(rows, Exception):
            raise rows
        return phase_measures.summarise_phases(rows)

    monkeypatch.setattr(phase_measures, "recorded_phase_summary", recorded_phase_summary)
    metrics = _assemble_health_scorecard_metrics()
    return {key: metrics[key] for key in ("avg_maturity", "total_solutions", "adm_distribution")}


def render(metrics):
    env = Environment(loader=ChoiceLoader([
        DictLoader({"layouts/admin_base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(ROOT / "app/templates"),
    ]), autoescape=True)
    env.globals["url_for"] = lambda *a, **kw: "/solutions/"
    env.globals["flask"] = SimpleNamespace(current_app=SimpleNamespace(view_functions={}))
    return env.get_template("dashboards/health.html").render(
        **metrics, risk_counts={}, arb_pipeline={}, archimate_by_layer={}, total_archimate=0,
    )


@pytest.mark.parametrize("phases", [[None] * 4, ["", "obsolete", None], []])
def test_absent_phase_never_invents_twelve_percent(phases, monkeypatch, db_session):
    result = measure(phases, monkeypatch, db_session)
    assert result["avg_maturity"] is None
    assert result["total_solutions"] == len(phases)
    assert result["adm_distribution"]["A"] == 0
    assert sum(result["adm_distribution"].values()) == len(phases)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(render(result))
            text = page.inner_text("body")
            assert "12%" not in text
            assert "None%" not in text
            assert "—" in text
            if phases:
                assert "Unclassified" in text
        finally:
            browser.close()


def test_partial_phase_data_uses_only_known_phases(monkeypatch, db_session):
    result = measure(["A", "H", None, ""], monkeypatch, db_session)
    assert result["avg_maturity"] == 56
    assert result["adm_distribution"]["A"] == 1
    assert result["adm_distribution"]["Unclassified"] == 2
    assert result["total_solutions"] == 4


def test_normalized_phases_use_same_classification_as_overview(monkeypatch, db_session):
    result = measure(["C", "C", " c ", " c ", " c ", None, "", "obsolete"], monkeypatch, db_session)
    assert result["avg_maturity"] == 37
    assert result["adm_distribution"]["C"] == 5
    assert result["adm_distribution"]["Unclassified"] == 3
    assert sum(result["adm_distribution"].values()) == 8


def test_failed_phase_query_is_unknown_not_zero(monkeypatch, db_session):
    result = measure(RuntimeError("database unavailable"), monkeypatch, db_session)
    assert result["total_solutions"] is None
    assert result["avg_maturity"] is None
    assert result["adm_distribution"] is None
    html = render(result)
    assert "Phase data unavailable" in html
    assert "None%" not in html
