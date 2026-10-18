"""Business-first Transformation Room domain and HTTP registration."""

from __future__ import annotations


def register(app) -> None:
    """Register the room HTML routes and its single versioned API surface."""
    from app.core.compat import mark_blueprint_guardrailed
    from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
        solution_design_bp,
    )
    from app.modules.transformation_room.api import transformation_api_bp
    from app.modules.transformation_room.objectives_api import objectives_api_bp
    from app.modules.transformation_room.routes import (
        register_transformation_room_routes,
    )

    if not getattr(solution_design_bp, "_transformation_room_routes_registered", False):
        register_transformation_room_routes(solution_design_bp)
        solution_design_bp._transformation_room_routes_registered = True

    if transformation_api_bp.name not in app.blueprints:
        mark_blueprint_guardrailed(transformation_api_bp)
        app.register_blueprint(transformation_api_bp)

    if objectives_api_bp.name not in app.blueprints:
        mark_blueprint_guardrailed(objectives_api_bp)
        app.register_blueprint(objectives_api_bp)


__all__ = ["register"]
