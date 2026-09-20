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
