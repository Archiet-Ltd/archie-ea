"""TPM-013: Risk heat map route — /solutions/<id>/risks/heatmap."""
import logging

from flask import Blueprint, render_template
from flask_login import login_required

from app.models.solution_models import Solution
from app.models.solution_lifecycle_models import SolutionRisk

logger = logging.getLogger(__name__)

solutions_bp = Blueprint("solutions_risk", __name__, url_prefix="/solutions")

# Map text-level values to integers 1-5 for the 5×5 heat-map grid.
_LEVEL_MAP = {
    "very_low": 1,
    "very low": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "very_high": 5,
    "very high": 5,
    "critical": 5,
}


_RISK_LEVEL_NAMES = {1: "Very low", 2: "Low", 3: "Medium", 4: "High", 5: "Very high"}


def _level_to_int(val, default=3):
    """Convert a string or integer probability/impact value to an integer 1-5."""
    if isinstance(val, int):
        return max(1, min(5, val))
    if isinstance(val, str) and val.isdigit():
        return max(1, min(5, int(val)))
    return _LEVEL_MAP.get(str(val).lower().strip(), default)


@solutions_bp.route("/<int:solution_id>/export/narrative")
@login_required
def export_narrative(solution_id):
    """SA-004: Export solution narrative document."""
    from app.services.solution_narrative_service import generate_sad as _gen
    _gen(solution_id)
    solution = Solution.query.get_or_404(solution_id)
    risks = SolutionRisk.query.filter_by(solution_id=solution_id).all()
    return render_template(
        "exports/solution_design_document.html",
        solution=solution,
        options=solution.options if hasattr(solution, 'options') else [],
        risks=risks,
        capabilities=[],
        requirements=[],
    )


@solutions_bp.route("/<int:solution_id>/risks/heatmap")
@login_required
def risk_heatmap(solution_id):
    """The solution's 5x5 risk heat map: probability across, impact upward.

    Plots exactly the risks the solution's own risk list returns (the register
    risks linked to it plus any not-yet-merged legacy rows), each in the cell
    of its own probability and impact.
    """
    from app.services import risk_service
    from app.utils.route_guards import require_entity

    solution = require_entity(Solution, solution_id, description="Solution not found")
    canonical, legacy = risk_service.solution_risk_sources(solution_id)
    rows = [
        {
            "id": r.id,
            "legacy": False,
            "title": r.title,
            "description": r.description or "",
            "probability": _level_to_int(r.likelihood),
            "impact": _level_to_int(r.impact),
            "mitigation": r.mitigation_plan or "",
            "status": r.status.value,
            "owner": r.owner or "",
        }
        for r in canonical
    ] + [
        {
            "id": r.id,
            "legacy": True,
            "title": (r.risk_name or r.risk_description or "Risk %s" % r.id)[:120],
            "description": r.risk_description or "",
            "probability": _level_to_int(r.probability),
            "impact": _level_to_int(r.impact),
            "mitigation": r.mitigation or "",
            "status": r.status or "open",
            "owner": r.owner or "",
        }
        for r in legacy
    ]
    cells = {}
    for row in rows:
        row["score"] = row["probability"] * row["impact"]
        row["band"] = risk_service.risk_band(row["score"])
        cells.setdefault((row["probability"], row["impact"]), []).append(row)
    grid = [
        [
            {
                "probability": p,
                "impact": i,
                "score": p * i,
                "band": risk_service.risk_band(p * i),
                "risks": cells.get((p, i), []),
            }
            for p in range(1, 6)
        ]
        for i in range(5, 0, -1)
    ]
    bands = [
        {
            "key": key,
            "label": label,
            "floor": floor,
            "count": sum(1 for r in rows if r["band"] == key),
        }
        for key, label, floor in risk_service.RISK_BANDS
    ]
    return render_template(
        "solutions/risk_heatmap.html",
        solution=solution,
        grid=grid,
        bands=bands,
        total=len(rows),
        risks=rows,
        levels=[(n, _RISK_LEVEL_NAMES[n]) for n in range(1, 6)],
    )
