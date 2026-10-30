"""The health score can be checked from what the dashboard shows: each part, its weight, the sum."""
import re

import pytest

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
