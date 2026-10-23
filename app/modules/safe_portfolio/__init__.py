"""SAFe Portfolio Kanban board module."""

from flask import Blueprint

safe_portfolio_bp = Blueprint(
    "safe_portfolio",
    __name__,
    url_prefix="/safe/portfolio",
)

safe_portfolio_api_bp = Blueprint(
    "safe_portfolio_api",
    __name__,
    url_prefix="/api/safe/portfolio",
)

from . import routes  # noqa: F401, E402
