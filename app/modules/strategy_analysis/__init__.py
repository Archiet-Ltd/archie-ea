"""Strategy analyses drafted from the company's public footprint: the competitor matrix, and the SWOT and PESTLE drafts.

The engine is app/services/strategy_draft_service.py; this module is the screens over it.
"""

from .routes import strategy_analysis_bp


def register(app):
    """Register the Strategy analyses blueprint on *app* (idempotent)."""
    if strategy_analysis_bp.name in app.blueprints:
        return
    app.register_blueprint(strategy_analysis_bp)


__all__ = ["register", "strategy_analysis_bp"]
