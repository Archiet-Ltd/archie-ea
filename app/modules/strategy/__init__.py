"""Strategy framework canvases — SWOT and PESTLE drafted from the model.

A self-contained module: a signed-in architect opens SWOT or PESTLE for their
organisation, drafts proposed points from their own model and website profile
(each cited), accepts, edits or rejects them, and sees the accepted points in
the recognisable SWOT 2x2 and PESTLE six-column layout as Assessment elements.

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
