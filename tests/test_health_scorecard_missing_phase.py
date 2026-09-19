"""Unknown ADM phases must not become fabricated Vision maturity."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def measure(rows, monkeypatch):
    import app.models.solution_models as models

    class Query:
        def with_entities(self, *args):
            return self

        def all(self):
            if isinstance(rows, Exception):
                raise rows
            return [(row,) for row in rows]

    monkeypatch.setattr(models, "Solution", SimpleNamespace(query=Query(), adm_phase="phase"))
    # The tile counts what the user may open (app.services.solution_visibility). This test is about
    # phase classification, so the same fake rows are handed back through that seam.
    import app.services.solution_visibility as visibility

    monkeypatch.setattr(visibility, "accessible_solutions", lambda user: SimpleNamespace(query=Query()))
    tree = ast.parse((ROOT / "app/modules/dashboard/v2/routes/dashboard_views.py").read_text(encoding="utf-8"))
    body = next(n.body for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_assemble_health_scorecard_metrics")
    start = next(i for i, n in enumerate(body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_adm_phase_pct" for t in n.targets))
    ns = {
        "logger": logging.getLogger(__name__),
        "db": SimpleNamespace(session=SimpleNamespace(rollback=lambda: None)),
        "current_user": SimpleNamespace(id=1),
    }
    exec(compile(ast.Module(body=body[start:-1], type_ignores=[]), "health-scorecard-production", "exec"), ns)
    return {key: ns[key] for key in ("avg_maturity", "total_solutions", "adm_distribution")}


def render(metrics):
    env = Environment(loader=ChoiceLoader([
        DictLoader({"layouts/admin_base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(ROOT / "app/templates"),
    ]), autoescape=True)
    env.globals["url_for"] = lambda *a, **kw: "/solutions/"
    return env.get_template("dashboards/health.html").render(
        **metrics, risk_counts={}, arb_pipeline={}, archimate_by_layer={}, total_archimate=0,
    )


@pytest.mark.parametrize("phases", [[None] * 4, ["", "obsolete", None], []])
def test_absent_phase_never_invents_twelve_percent(phases, monkeypatch):
    result = measure(phases, monkeypatch)
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


def test_partial_phase_data_uses_only_known_phases(monkeypatch):
    result = measure(["A", "H", None, ""], monkeypatch)
    assert result["avg_maturity"] == 56
    assert result["adm_distribution"]["A"] == 1
    assert result["adm_distribution"]["Unclassified"] == 2
    assert result["total_solutions"] == 4


def test_normalized_phases_use_same_classification_as_overview(monkeypatch):
    result = measure(["C", "C", " c ", " c ", " c ", None, "", "obsolete"], monkeypatch)
    assert result["avg_maturity"] == 37
    assert result["adm_distribution"]["C"] == 5
    assert result["adm_distribution"]["Unclassified"] == 3
    assert sum(result["adm_distribution"].values()) == 8


def test_failed_phase_query_is_unknown_not_zero(monkeypatch):
    result = measure(RuntimeError("database unavailable"), monkeypatch)
    assert result["total_solutions"] is None
    assert result["avg_maturity"] is None
    assert result["adm_distribution"] is None
    html = render(result)
    assert "Phase data unavailable" in html
    assert "None%" not in html
