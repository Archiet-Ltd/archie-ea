"""Both real dashboard endpoints must derive health from the same tenant data."""

import re
import pytest

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.solution_models import Solution
from tests.test_dashboard_health_score_honesty import _org, _user


def test_dashboard_and_executive_api_agree_for_known_phase_portfolio(db_session, client, login_as, tenant_ctx):
    org = _org(db_session, "health-agreement")
    with tenant_ctx(org.id):
        user = _user(org, "agreement")
        db.session.add_all([
            Solution(name="Early architecture", organization_id=org.id, adm_phase="A", maturity_current=0),
            Solution(name="Advanced architecture", organization_id=org.id, adm_phase="C", maturity_current=0),
        ])
        db.session.add_all([
            ApplicationComponent(name=f"Health application {index}", organization_id=org.id)
            for index in range(5)
        ])
        db.session.flush()
    login_as(client, user)
    overview = client.get("/dashboard/overview")
    assert overview.status_code == 200
    match = re.search(r'data-testid="health-score-value">([^<]+)</p>', overview.get_data(as_text=True))
    assert match is not None, "Must exercise the populated dashboard, not skip its health card"
    # One of two solutions is past Phase B. Other components have no observations;
    # reweighting over phase alone gives 50, not maturity_current's measured zero.
    assert float(match.group(1)) == 50.0
    summary = client.get("/dashboard/api/executive-summary")
    assert summary.status_code == 200
    # The executive summary carries the score as the string the cards show.
    assert summary.get_json()["data"]["Health Score"] == match.group(1) == "50.0"


@pytest.mark.parametrize("phases,want", [
    (["A", "C", None, "Z"], 50.0),
    ([None, "", "Z"], None),
    ([" c ", "C", None], 100.0),
    ([" A ", "B", None], 0.0),
])
def test_health_phase_denominator_uses_only_recorded_valid_phases(db_session, tenant_ctx, phases, want):
    from app.modules.dashboard.v2.services.executive_dashboard_service import ExecutiveDashboardService
    org = _org(db_session, "phase-denominator-" + str(want))
    with tenant_ctx(org.id):
        for index, phase in enumerate(phases):
            solution = Solution(name=f"Phase observation {index}", organization_id=org.id, adm_phase=phase)
            db.session.add(solution)
            db.session.flush()
            # Preserve SQL NULL even if the model gains a Python insert default.
            solution.adm_phase = phase
        db.session.flush()
        result = ExecutiveDashboardService()._get_health_score()
        assert result["components"]["phase_maturity"] == want
        assert result["composite_score"] == want
        assert ("phase_maturity" in result["unavailable_components"]) == (want is None)


def _add_solutions(org, phases, name="Phase observation"):
    """Add one solution per phase value, keeping each value exactly as given."""
    for index, phase in enumerate(phases):
        solution = Solution(name=f"{name} {index}", organization_id=org.id, adm_phase=phase)
        db.session.add(solution)
        db.session.flush()
        # Preserve the value (or SQL NULL) even if the model gains a Python default.
        solution.adm_phase = phase
    db.session.flush()


@pytest.mark.parametrize("slug,phases,phase_maturity,avg_maturity", [
    # A lower-case or padded value is the phase it spells. Read literally, the
    # first two would be unclassified and only the A would count: 0.0 and 12.
    ("mixed-case", ["c", " c ", "A"], 66.7, 29),
    ("padded", [" d", "a", "A"], 33.3, 25),
])
def test_imported_spellings_of_a_phase_count_as_that_phase(
    db_session, tenant_ctx, slug, phases, phase_maturity, avg_maturity
):
    from sqlalchemy import text

    from app.modules.dashboard.v2.routes.dashboard_views import _assemble_health_scorecard_metrics
    from app.modules.dashboard.v2.services.executive_dashboard_service import ExecutiveDashboardService

    org = _org(db_session, "normalisation-" + slug)
    with tenant_ctx(org.id):
        _add_solutions(org, phases)
        stored = db.session.execute(
            text("SELECT adm_phase FROM solutions WHERE organization_id = :org"), {"org": org.id}
        ).scalars().all()
        assert sorted(stored) == sorted(phases), "the values must reach the query as written"

        assert ExecutiveDashboardService()._get_health_score()["components"]["phase_maturity"] == phase_maturity
        metrics = _assemble_health_scorecard_metrics()
        assert metrics["avg_maturity"] == avg_maturity
        assert metrics["total_solutions"] == len(phases)
        assert metrics["adm_distribution"].get("Unclassified", 0) == 0


def test_the_phase_read_is_one_grouped_count(db_session, tenant_ctx):
    """However many solutions there are, the read returns one row per distinct phase value."""
    from sqlalchemy import event

    from app.modules.dashboard.v2.services.solution_phase_measures import recorded_phase_summary

    org = _org(db_session, "grouped-read")
    phases = ["A"] * 6 + ["C"] * 4 + [None] * 3 + ["f"] * 2
    with tenant_ctx(org.id):
        _add_solutions(org, phases)
        reads = []

        def record(conn, cursor, statement, parameters, context, executemany):
            reads.append((statement, cursor.rowcount))

        connection = db.session.connection()
        event.listen(connection, "after_cursor_execute", record)
        try:
            summary = recorded_phase_summary()
        finally:
            event.remove(connection, "after_cursor_execute", record)

    solution_reads = [(statement, rows) for statement, rows in reads if "FROM solutions" in statement]
    assert len(solution_reads) == 1
    statement, rows = solution_reads[0]
    assert "GROUP BY" in statement.upper()
    assert rows == 4, "one row for each of A, C, f and no recorded phase, not one per solution"
    assert summary["total"] == len(phases)
    assert summary["counts"]["A"] == 6
    assert summary["counts"]["C"] == 4
    assert summary["counts"]["F"] == 2
    assert summary["unclassified"] == 3


def test_the_phase_read_counts_only_the_callers_organisation(db_session, make_org, tenant_ctx):
    from app.modules.dashboard.v2.services.solution_phase_measures import recorded_phase_summary

    mine = make_org("phase-read-mine")
    theirs = make_org("phase-read-theirs")
    with tenant_ctx(theirs.id):
        _add_solutions(theirs, ["A"] * 6, name="Other organisation")
    with tenant_ctx(mine.id):
        _add_solutions(mine, ["C", "C"], name="Own")
        summary = recorded_phase_summary()

    assert summary["total"] == 2
    assert summary["counts"]["C"] == 2
    assert summary["counts"]["A"] == 0
