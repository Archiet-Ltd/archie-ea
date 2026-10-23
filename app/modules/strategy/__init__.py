"""The one canvas view for every strategy framework.

A signed-in architect opens any of the eight frameworks (SWOT, PESTLE, VRIO,
McKinsey 7S, Porter's five forces, BCG, Ansoff, Blue Ocean) for their
organisation through this one view. Each is rendered from its CANVAS_TEMPLATES
entry and its layout hint; the drafting engine is a panel shown for a
framework with a prompt file; placement and counts go through the one
membership rule. There is no second canvas view, renderer or membership rule.

Exposes register(app) to attach strategy_bp — mirrors the pattern used by the
other canvas blueprints registered from app/_bootstrap/blueprints.py.
"""

from .routes import strategy_bp


def register(app):
    """Register the strategy canvas blueprint on *app* (idempotent)."""
    if strategy_bp.name in app.blueprints:
        return
    app.register_blueprint(strategy_bp)


__all__ = ["register", "strategy_bp"]
