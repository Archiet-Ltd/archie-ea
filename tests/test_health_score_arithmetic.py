"""The health score can be checked from what the dashboard shows: each part, its weight, the sum."""
import re

import pytest
from bs4 import BeautifulSoup

from app.modules.dashboard.v2.services.executive_dashboard_service import health_score_arithmetic


def test_all_four_parts_use_their_own_weights():
    result = health_score_arithmetic(
        {"phase_maturity": 66.7, "risk_posture": 0.0, "capability_coverage": 80.0, "governance": 95.0}
    )
    assert result["weights"] == {
        "phase_maturity": 40.0, "risk_posture": 30.0, "capability_coverage": 20.0, "governance": 10.0,
    }
    assert result["sum"] == "40% × 66.7 + 30% × 0.0 + 20% × 80.0 + 10% × 95.0 = 52.2"


def test_unmeasured_part_carries_no_weight_and_the_rest_add_to_a_hundred():
    result = health_score_arithmetic(
        {"phase_maturity": 50.0, "risk_posture": None, "capability_coverage": 80.0, "governance": None}
    )
    assert result["weights"]["risk_posture"] is None and result["weights"]["governance"] is None
    assert sum(w for w in result["weights"].values() if w) == pytest.approx(100.0, abs=0.1)
    assert result["sum"].endswith("= 60.0")


def test_nothing_measured_has_no_sum():
    result = health_score_arithmetic({k: None for k in ("phase_maturity", "risk_posture", "capability_coverage", "governance")})
    assert result["sum"] is None


def test_dashboard_shows_weights_that_add_up_to_the_score(app, db_session, make_org, tenant_ctx, login_as):
    from app.models.risk import Risk, RiskStatus
    from tests.test_health_score_one_format import _seed

    user = _seed(db_session, tenant_ctx, "health-arith", ["D", "A", "E"])
    org_id = user.organization_id
    other = make_org("health-arith-other")
    with tenant_ctx(org_id):
        db_session.add(Risk(title="Severe", likelihood=5, impact=5, organization_id=org_id, status=RiskStatus.OPEN))
        db_session.flush()
    with tenant_ctx(other.id):
        db_session.add(Risk(title="Foreign", likelihood=1, impact=1, organization_id=other.id, status=RiskStatus.OPEN))
        db_session.flush()
    client = app.test_client()
    login_as(client, user)
    soup = BeautifulSoup(client.get("/dashboard/overview").get_data(as_text=True), "html.parser")

    score = float(soup.select_one('[data-testid="health-score-value"]').get_text(strip=True))
    parts = []
    for li in soup.select('[data-testid="health-score-part"]'):
        value, weight = li.select_one('[data-testid="health-part-value"]'), li.select_one('[data-testid="health-part-weight"]')
        if value is not None:
            parts.append((float(value.get_text().rstrip("%")), float(weight.get_text().rstrip("%"))))
    assert len(parts) >= 2
    assert sum(w for _, w in parts) == pytest.approx(100.0, abs=0.2)
    assert sum(v * w / 100 for v, w in parts) == pytest.approx(score, abs=0.1)
    text = soup.select_one('[data-testid="health-score-sum"]').get_text(" ", strip=True)
    assert text.endswith("= %.1f" % score)
    assert re.search(r"\d+(\.\d)?% × \d+\.\d", text)
