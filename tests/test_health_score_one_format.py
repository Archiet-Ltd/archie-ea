"""One health score and one maturity reading, wherever they appear.

The dashboard's Overview tab, its CTO tab (the executive summary and the card
beneath it), its CFO tab and the Health Scorecard all report the portfolio's
health score. They must show one value in one format, and a score that could not
be computed must read as an em dash on every one of them, never as a zero.

The two maturity figures are different measures and say so: "Phase Maturity"
(share of solutions in phase C or later) on the dashboard and "Avg Solution
Maturity" (average ADM phase progress) on the scorecard. Each carries its own
one-line definition, and both can be recomputed from the phase distribution the
scorecard shows.
"""
import re

import pytest
from bs4 import BeautifulSoup

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.solution_models import Solution
from tests.test_dashboard_health_score_honesty import _org, _user

EM_DASH = "—"

SCORE_FORMAT = re.compile(r"^\d{1,3}\.\d$")


def _seed(db_session, tenant_ctx, slug, phases):
    """A tenant with one solution per phase and enough applications that the
    dashboard leaves its guided-setup mode and shows the data view."""
    org = _org(db_session, slug)
    with tenant_ctx(org.id):
        user = _user(org, slug)
        for index, phase in enumerate(phases):
            solution = Solution(
                name=f"{slug} solution {index}",
                organization_id=org.id,
                adm_phase=phase,
                maturity_current=0,
            )
            db.session.add(solution)
            db.session.flush()
            solution.adm_phase = phase
        db.session.add_all(
            ApplicationComponent(name=f"{slug} application {index}", organization_id=org.id)
            for index in range(5)
        )
        db.session.flush()
    return user


def _page(client, login_as, user, path):
    login_as(client, user)
    response = client.get(path)
    assert response.status_code == 200, path
    return BeautifulSoup(response.get_data(as_text=True), "html.parser")


def _text(soup, testid):
    element = soup.find(attrs={"data-testid": testid})
    assert element is not None, f"no element with data-testid={testid}"
    return " ".join(element.get_text().split())


def _tile_text(soup, label):
    """Text of the tile whose label is exactly ``label``."""
    for node in soup.find_all(string=lambda value: value and value.strip() == label):
        label_element = node.parent
        if label_element.get("data-slot") == "card-description":
            # A metrics card: the label sits in the header, the definition in
            # the card's content beneath it.
            tile = label_element.find_parent(attrs={"data-slot": "card"})
        else:
            tile = label_element.parent
        return " ".join(tile.get_text().split())
    raise AssertionError(f"no tile labelled {label!r}")


def _score_on_every_screen(client, login_as, user):
    """The health score as each place that shows it renders it."""
    overview = _page(client, login_as, user, "/dashboard/overview")
    login_as(client, user)
    summary = client.get("/dashboard/api/executive-summary")
    assert summary.status_code == 200
    scorecard = _page(client, login_as, user, "/dashboard/health")
    return {
        "overview tab": _text(overview, "health-score-value"),
        "executive summary": summary.get_json()["data"]["Health Score"],
        "cto card": _text(overview, "cto-health-score-value"),
        "cfo card": _text(overview, "cfo-health-score-value"),
        "health scorecard": _text(scorecard, "health-score-value"),
    }


@pytest.mark.parametrize(
    "phases,score,phase_maturity,avg_maturity",
    [
        # Every solution past phase B: a whole-number score, the case the
        # screens used to spell as "100.0", "100" and "100.0/100".
        (["C", "F", "C"], "100.0", "100.0%", "50%"),
        # A fractional score.
        (["A", "C", "F"], "66.7", "66.7%", "41%"),
    ],
)
def test_the_score_is_one_value_in_one_format_on_every_screen(
    db_session, tenant_ctx, client, login_as, phases, score, phase_maturity, avg_maturity
):
    user = _seed(db_session, tenant_ctx, "one-format-" + score.replace(".", "-"), phases)

    shown = _score_on_every_screen(client, login_as, user)

    assert set(shown.values()) == {score}, shown
    assert SCORE_FORMAT.match(score)

    # The two maturity figures are two measures, each stating its definition,
    # and each equal to what the phase distribution says.
    overview = _page(client, login_as, user, "/dashboard/overview")
    tile = _tile_text(overview, "Phase Maturity")
    assert phase_maturity in tile
    assert "Share of solutions with a recorded ADM phase that are in phase C or later" in tile

    scorecard = _page(client, login_as, user, "/dashboard/health")
    tile = _tile_text(scorecard, "Avg Solution Maturity")
    assert avg_maturity in tile
    assert "Average ADM phase progress, phase D counting as 50% and phase H as 100%" in tile
    assert "solutions without a recorded phase are left out" in tile


def test_the_scorecard_names_its_maturity_figure_once(db_session, tenant_ctx, client, login_as):
    user = _seed(db_session, tenant_ctx, "one-name", ["C", "F", "C"])

    scorecard = _page(client, login_as, user, "/dashboard/health")
    text = " ".join(scorecard.get_text().split())

    assert text.count("Avg Solution Maturity") == 2, "the tile and the phase card share one name"
    assert "Average Maturity" not in text


def test_the_two_maturity_figures_are_recomputable_from_the_phase_distribution(
    db_session, tenant_ctx, client, login_as
):
    """A reader can check each definition against the counts on the scorecard."""
    user = _seed(db_session, tenant_ctx, "recompute", ["A", "C", "C", "F"])

    scorecard = _page(client, login_as, user, "/dashboard/health")
    text = " ".join(scorecard.get_text().split())

    # Distribution as rendered: one in A, two in C, one in F.
    assert re.search(r"A: Vision\s+1\s+25\.0%", text)
    assert re.search(r"C: Info Systems\s+2\s+50\.0%", text)
    assert re.search(r"F: Migration\s+1\s+25\.0%", text)
    # Three of four solutions are in phase C or later, which is the dashboard's
    # Phase Maturity and, alone among the components measured, the score.
    assert _text(scorecard, "health-score-value") == "75.0"
    overview = _page(client, login_as, user, "/dashboard/overview")
    assert "75.0%" in _tile_text(overview, "Phase Maturity")
    # Phase progress averages (12 + 37 + 37 + 75) / 4 = 40.25, so 40%.
    assert "40%" in _tile_text(scorecard, "Avg Solution Maturity")


def test_a_score_that_cannot_be_computed_is_an_em_dash_on_every_screen(
    db_session, tenant_ctx, client, login_as
):
    user = _seed(db_session, tenant_ctx, "nothing-to-score", [])

    shown = _score_on_every_screen(client, login_as, user)

    assert shown["executive summary"] is None, "the API sends null, and the fragment renders null as a dash"
    assert {value for name, value in shown.items() if name != "executive summary"} == {EM_DASH}, shown

    overview = _page(client, login_as, user, "/dashboard/overview")
    assert EM_DASH in _tile_text(overview, "Phase Maturity")
    assert not re.search(r"Phase Maturity\s+0", _tile_text(overview, "Phase Maturity"))

    scorecard = _page(client, login_as, user, "/dashboard/health")
    tile = _tile_text(scorecard, "Avg Solution Maturity")
    assert tile.startswith(f"Avg Solution Maturity {EM_DASH}"), tile
    assert not re.search(r"Avg Solution Maturity\s+0", tile)


def test_the_executive_summary_fragment_renders_the_string_it_is_given():
    """The fragment must show the API's Health Score verbatim (no locale
    formatting of a number), and a missing one as a dash."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/templates/dashboards/overview.html").read_text(
        encoding="utf-8"
    )
    expression = re.search(r'x-text="(typeof val ===[^"\n]+)"', source).group(1)
    # Strings and null take the branch that does not reformat them.
    assert "typeof val === 'number' ?" in expression
    assert "(val ?? '—')" in expression


def test_a_failed_score_computation_is_an_em_dash_not_a_zero(
    db_session, tenant_ctx, client, login_as, monkeypatch
):
    from app.modules.dashboard.v2.services.executive_dashboard_service import ExecutiveDashboardService

    user = _seed(db_session, tenant_ctx, "score-fails", ["C", "F", "C"])

    def unavailable(self):
        raise RuntimeError("score store unavailable")

    monkeypatch.setattr(ExecutiveDashboardService, "_get_health_score", unavailable)

    scorecard = _page(client, login_as, user, "/dashboard/health")
    assert _text(scorecard, "health-score-value") == EM_DASH
    overview = _page(client, login_as, user, "/dashboard/overview")
    assert _text(overview, "health-score-value") == EM_DASH
    assert _text(overview, "cto-health-score-value") == EM_DASH


def test_soft_deleted_solutions_are_left_out_of_both_maturity_figures(
    db_session, tenant_ctx, client, login_as
):
    """Both figures count the same solutions, so they cannot drift apart."""
    org = _org(db_session, "soft-deleted")
    with tenant_ctx(org.id):
        user = _user(org, "softdeleted")
        for name, phase in (("Live one", "C"), ("Live two", "C"), ("[DELETED] Gone", "A")):
            solution = Solution(name=name, organization_id=org.id, adm_phase=phase, maturity_current=0)
            db.session.add(solution)
        db.session.add_all(
            ApplicationComponent(name=f"soft-deleted application {index}", organization_id=org.id)
            for index in range(5)
        )
        db.session.flush()

    shown = _score_on_every_screen(client, login_as, user)
    assert set(shown.values()) == {"100.0"}, shown

    scorecard = _page(client, login_as, user, "/dashboard/health")
    assert "37%" in _tile_text(scorecard, "Avg Solution Maturity")
    overview = _page(client, login_as, user, "/dashboard/overview")
    assert "100.0%" in _tile_text(overview, "Phase Maturity")
