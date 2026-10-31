"""SA-008: Solution design completeness routes.

Provides:
    GET /api/solutions/<solution_id>/completeness  — JSON report
    GET /solutions/<solution_id>/completeness      — HTML page
"""
from flask import redirect, url_for, Blueprint, jsonify
from flask_login import login_required

completeness_bp = Blueprint("completeness", __name__)


@completeness_bp.route("/api/solutions/<int:solution_id>/completeness", methods=["GET"])
@login_required
def api_completeness(solution_id: int):
    """Return JSON completeness report for a solution."""
    from app.models.solution_models import Solution
    from app.services.solution_completeness_service import check_completeness
    from app.utils.route_guards import require_entity

    # A score for a solution that does not exist is fabricated data: 0/100 is
    # indistinguishable from a measured zero. 404 instead.
    require_entity(Solution, solution_id, description="Solution not found")
    report = check_completeness(solution_id)
    return jsonify(report)


@completeness_bp.route("/solutions/<int:solution_id>/completeness", methods=["GET"])
@login_required
def completeness_page(solution_id: int):
    """Render the completeness dashboard page."""
    from app.models.solution_models import Solution
    from app.utils.route_guards import require_entity

    require_entity(Solution, solution_id, description="Solution not found")
    # The ten checks of the design score replaced the completeness report.
    return redirect(url_for("solution_design.view_solution", solution_id=solution_id, tab="overview"))
