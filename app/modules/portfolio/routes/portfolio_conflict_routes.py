"""Initiative conflicts for a quarter: who is competing for the same capability or owner."""
from flask import flash, render_template, request
from flask_login import login_required

from app.modules.portfolio.routes.portfolio_routes import portfolio_bp
from app.modules.portfolio.services.initiative_conflicts import (
    find_conflicts,
    next_quarter,
    parse_quarter,
)


@portfolio_bp.route("/conflicts")
@login_required
def conflicts():
    """Conflicts for the chosen quarter (default: the coming one)."""
    raw = (request.args.get("quarter") or "").strip()
    parsed = parse_quarter(raw) if raw else None
    if raw and parsed is None:
        flash("Quarter must look like 2026-Q4.", "error")
    year, quarter = parsed or next_quarter()
    result = find_conflicts(year, quarter)
    return render_template("portfolio/conflicts.html", result=result, quarter_value="%d-Q%d" % (year, quarter))
