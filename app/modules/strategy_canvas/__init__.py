"""Strategy canvas module — BCG, Ansoff and Blue Ocean.

Opens each framework in its recognisable layout for a signed-in architect,
placing the organisation's own Products, Business Services, Assessments and
Goals into each box by the profile that names it. The boxes, their element
types and their profiles are the shared canvas contract in
``app/config/archimate_viewpoints.py``; the placement and the add-from-canvas
writer are ``app/services/strategy_canvas_service.py``.

Exposes register(app) to attach strategy_canvas_bp — mirrors the standalone
feature blueprints registered from
app/_bootstrap/blueprints.py::_register_optional_standalone (see e.g.
app/modules/business_case).
"""

from .routes import strategy_canvas_bp


def register(app):
    """Register the Strategy Canvas blueprint on *app* (idempotent)."""
    if strategy_canvas_bp.name in app.blueprints:
        return
    app.register_blueprint(strategy_canvas_bp)


__all__ = ["register", "strategy_canvas_bp"]
