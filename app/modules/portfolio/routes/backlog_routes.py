"""The transformation backlog page."""
from flask import render_template
from flask_login import login_required

from app.modules.portfolio.routes.portfolio_routes import portfolio_bp
from app.modules.portfolio.services.transformation_backlog import transformation_backlog


@portfolio_bp.route("/backlog")
@login_required
def backlog():
    """Gaps nobody is resolving and work packages nobody has scheduled, in priority order."""
    return render_template("portfolio/backlog.html", backlog=transformation_backlog())
