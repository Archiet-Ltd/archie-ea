"""Strategy canvas module — VRIO, McKinsey 7S and Porter's five forces.

A self-contained feature blueprint. Each canvas is a projection over the
tenant's own ArchiMate elements (no per-record store), rendered in its
recognisable layout inside the shared canvas view. Exposes register(app) to
attach strategy_canvas_bp, the same pattern the business-model and
business-case modules use.
"""

from .routes import strategy_canvas_bp


def register(app):
    """Register the strategy canvas blueprint on *app* (idempotent)."""
    if strategy_canvas_bp.name in app.blueprints:
        return
    app.register_blueprint(strategy_canvas_bp)


__all__ = ["register", "strategy_canvas_bp"]
