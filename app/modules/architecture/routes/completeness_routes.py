"""Solution design score routes.

Provides:
    GET /api/solutions/<solution_id>/completeness  - the design score as JSON
    GET /solutions/<solution_id>/completeness      - redirects to the score on
                                                     the solution page

The completeness report these addresses used to serve read a link table the
product never writes, so it showed 0 out of 100 for a design with 33 linked
elements. Both addresses now answer from the one design score service.
"""
from flask import Blueprint, jsonify, redirect
from flask_login import login_required

completeness_bp = Blueprint("completeness", __name__)


@completeness_bp.route("/api/solutions/<int:solution_id>/completeness", methods=["GET"])
@login_required
def api_completeness(solution_id: int):
    """Return the design score of a solution as JSON."""
    from app.models.solution_models import Solution
    from app.services.solution_design_score_service import compute
    from app.utils.route_guards import require_entity

    # A score for a solution that does not exist (or belongs to another
    # organisation) is fabricated data. 404 instead.
    solution = require_entity(Solution, solution_id, description="Solution not found")
    return jsonify(compute(solution).to_dict())


@completeness_bp.route("/solutions/<int:solution_id>/completeness", methods=["GET"])
@login_required
def completeness_page(solution_id: int):
    """The design score lives on the solution page."""
    from app.models.solution_models import Solution
    from app.utils.route_guards import require_entity

    require_entity(Solution, solution_id, description="Solution not found")
    return redirect("/solutions/%d#design-score" % solution_id)
