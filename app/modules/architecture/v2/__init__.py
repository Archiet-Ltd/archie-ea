"""
Architecture v2 — Guardrail-enabled thin wrapper over v1 module.

Strangler Fig migration from app/modules/architecture/ (v1).
Applies mark_blueprint_guardrailed() to all blueprints registered by the
v1 module, enabling guardrail detection and request-level timing.

Feature flag: USE_ARCHITECTURE_V2
Fallback: v1 routes (unchanged)
Rollback: Set USE_ARCHITECTURE_V2=false → v1 routes take over instantly.
"""

from flask import Flask


def register(app: Flask) -> None:
    """Register the architecture v2 module (guardrail-wrapped v1 blueprints)."""
    from app import csrf
    from app.core.compat import mark_blueprint_guardrailed

    # Import all V1 blueprints
    from app.modules.architecture.api.archimate_routes import archimate_api
    from app.modules.architecture.routes.archimate_crud import archimate_crud
    from app.modules.architecture.api.viewpoint_routes import viewpoint_bp
    from app.modules.architecture.routes.architecture_crud_routes import (
        architecture_crud_bp,
    )
    from app.modules.architecture.routes.architecture_routes import architecture_bp
    from app.modules.architecture.routes.architecture_assistant_routes import (
        architecture_assistant_bp,
    )
    from app.modules.architecture.routes.archimate_export_routes import (
        archimate_export_bp,
    )
    from app.modules.architecture.routes.architect_ui_routes import architect_ui_bp
    from app.modules.architecture.routes.architecture_monitoring_routes import (
        architecture_monitoring_bp,
        monitoring_api_enabled,
    )
    from app.modules.architecture.routes.arb_routes import arb_bp
    from app.modules.architecture.routes.arb_workflow_routes import arb_workflow_bp
    from app.modules.architecture.routes.adm_kanban_view_routes import (
        adm_kanban_view_bp,
    )
    from app.modules.architecture.routes.adm_kanban_routes import adm_kanban_bp
    from app.modules.architecture.routes.integration_routes import integration_bp
    from app.modules.architecture.routes.archimate_routes import archimate_bp
    from app.modules.architecture.routes.adr_routes import adr_bp

    # Mark all blueprints as guardrailed BEFORE registration
    blueprints = [
        archimate_api,
        archimate_crud,
        viewpoint_bp,
        architecture_crud_bp,
        architecture_bp,
        architecture_assistant_bp,
        archimate_export_bp,
        architect_ui_bp,
        arb_bp,
        arb_workflow_bp,
        adm_kanban_view_bp,
        adm_kanban_bp,
        integration_bp,
        archimate_bp,
        adr_bp,
    ]

    for bp in blueprints:
        mark_blueprint_guardrailed(bp)

    # Register all blueprints
    app.register_blueprint(archimate_api)
    app.register_blueprint(archimate_crud)
    app.register_blueprint(viewpoint_bp)
    app.register_blueprint(architecture_crud_bp)
    app.register_blueprint(architecture_bp)
    app.register_blueprint(architecture_assistant_bp)
    app.register_blueprint(archimate_export_bp)
    app.register_blueprint(architect_ui_bp)
    app.register_blueprint(arb_bp)
    app.register_blueprint(arb_workflow_bp)
    app.register_blueprint(adm_kanban_view_bp)
    app.register_blueprint(adm_kanban_bp)
    app.register_blueprint(integration_bp)
    app.register_blueprint(archimate_bp)
    app.register_blueprint(adr_bp)

    if monitoring_api_enabled(app):
        mark_blueprint_guardrailed(architecture_monitoring_bp)
        app.register_blueprint(architecture_monitoring_bp)
    else:
        app.logger.info(
            "[MODULE-V2] architecture monitoring API not mounted (ARCHITECTURE_MONITORING_API_ENABLED off)"
        )

    app.logger.info(
        "[MODULE-V2] architecture v2 registered (guardrail-enabled, ~215 routes, 15 or 16 blueprints)"
    )
