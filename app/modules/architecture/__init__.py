"""
Architecture module -- Centralized architecture blueprint registration.

Consolidates registration of 13 architecture-related blueprints behind a single
feature flag (USE_NEW_ARCHITECTURE).

Migrated from (full-copy):
- app/api/archimate_routes.py                    → api/archimate_routes.py
- app/archimate_crud/                            → routes/archimate_crud/
- app/api/viewpoint_routes.py                    → api/viewpoint_routes.py
- app/routes/architecture_crud_routes.py         → routes/architecture_crud_routes.py
- app/routes/architecture_routes.py              → routes/architecture_routes.py
- app/routes/architecture_assistant_routes.py    → routes/architecture_assistant_routes.py
- app/routes/archimate_export_routes.py          → routes/archimate_export_routes.py
- app/routes/architect_ui_routes.py              → routes/architect_ui_routes.py
- app/routes/architecture_monitoring_routes.py   → routes/architecture_monitoring_routes.py
- app/routes/arb_routes.py                       → routes/arb_routes.py
- app/routes/arb_workflow_routes.py              → routes/arb_workflow_routes.py
- app/routes/adm_kanban_view_routes.py           → routes/adm_kanban_view_routes.py
- app/routes/adm_kanban_routes.py                → routes/adm_kanban_routes.py

Total: ~180 routes across 13 blueprints.
"""

import logging

from flask import Flask

logger = logging.getLogger(__name__)


def register(app: Flask) -> None:
    # --- 1. ArchiMate API (relationship validation) ---
    try:
        from app.modules.architecture.api.archimate_routes import archimate_api

        app.register_blueprint(archimate_api)
        app.logger.info("[BLUEPRINT] ArchiMate API registered at /api/archimate")
    except ImportError as e:
        app.logger.warning(f"ArchiMate API blueprint not available: {e}")

    # --- 2. ArchiMate CRUD Dashboard ---
    try:
        from app.modules.architecture.routes.archimate_crud import archimate_crud

        app.register_blueprint(archimate_crud)
        app.logger.info(
            "[BLUEPRINT] ArchiMate CRUD Dashboard registered at /architecture"
        )
    except ImportError as e:
        app.logger.warning(f"ArchiMate CRUD dashboard blueprint not available: {e}")

    # --- 3. Viewpoint API (PRD - 010.2) ---
    try:
        from app.modules.architecture.api.viewpoint_routes import viewpoint_bp

        app.register_blueprint(viewpoint_bp)
        app.logger.info("[BLUEPRINT] Viewpoint API registered at /api/viewpoints")
    except ImportError as e:
        app.logger.warning(f"Viewpoint API blueprint not available: {e}")

    # --- 4. Architecture CRUD (element + relationship management) ---
    from app.modules.architecture.routes.architecture_crud_routes import (
        architecture_crud_bp,
    )

    app.register_blueprint(architecture_crud_bp)
    app.logger.info("[BLUEPRINT] Architecture CRUD registered at /architecture")

    # --- 5. Architecture Models (legacy) ---
    from app.modules.architecture.routes.architecture_routes import architecture_bp

    app.register_blueprint(architecture_bp)

    # --- 6. Architecture Assistant ---
    try:
        from app.modules.architecture.routes.architecture_assistant_routes import (
            architecture_assistant_bp,
            architecture_assistant_ui_bp,
        )

        app.register_blueprint(architecture_assistant_bp)
        app.logger.info(
            "[BLUEPRINT] Architecture Assistant API registered at /api/architecture-assistant"
        )
        app.register_blueprint(architecture_assistant_ui_bp)
        app.logger.info(
            "[BLUEPRINT] Architecture Assistant UI registered at /architecture-assistant"
        )
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register Architecture Assistant routes: {e}"
        )

    # --- 7. ArchiMate Export API ---
    try:
        from app.modules.architecture.routes.archimate_export_routes import (
            archimate_export_bp,
        )

        app.register_blueprint(archimate_export_bp)
        app.logger.info(
            "[BLUEPRINT] ArchiMate Export API registered at /api/archimate-export"
        )
    except ImportError as e:
        app.logger.warning(f"[BLUEPRINT] ArchiMate Export API not available: {e}")

    # --- 8. Architect UI (entry points for composer, roadmap, etc.) ---
    try:
        from app.modules.architecture.routes.architect_ui_routes import architect_ui_bp

        app.register_blueprint(architect_ui_bp)
        app.logger.info("[BLUEPRINT] Architect UI routes registered")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Architect UI routes: {e}")

    # --- 9. Architecture Monitoring API ---
    try:
        from app.modules.architecture.routes.architecture_monitoring_routes import (
            architecture_monitoring_bp,
            monitoring_api_enabled,
        )

        if monitoring_api_enabled(app):
            app.register_blueprint(architecture_monitoring_bp)
            app.logger.info(
                "[BLUEPRINT] Architecture Monitoring API registered at /api/architecture-monitoring"
            )
        else:
            app.logger.info(
                "[BLUEPRINT] Architecture Monitoring API not mounted (ARCHITECTURE_MONITORING_API_ENABLED off)"
            )
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register Architecture Monitoring routes: {e}"
        )

    # --- 10. ARB (Architecture Review Board) ---
    try:
        from app.modules.architecture.routes.arb_routes import arb_bp

        app.register_blueprint(arb_bp)
        app.logger.info("[BLUEPRINT] ARB routes registered at /arb")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register ARB routes: {e}")

    # --- 11. ARB Workflow API ---
    try:
        from app.modules.architecture.routes.arb_workflow_routes import arb_workflow_bp

        app.register_blueprint(arb_workflow_bp)
        app.logger.info("[BLUEPRINT] ARB Workflow API registered at /api/arb-workflow")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register ARB Workflow routes: {e}")

    # --- 12. ADM Kanban Views ---
    try:
        from app.modules.architecture.routes.adm_kanban_view_routes import (
            adm_kanban_view_bp,
        )

        app.register_blueprint(adm_kanban_view_bp)
        app.logger.info("[BLUEPRINT] ADM Kanban views registered at /adm-kanban")
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register ADM Kanban view routes: {e}"
        )

    # --- 13. ADM Kanban API ---
    try:
        from app.modules.architecture.routes.adm_kanban_routes import adm_kanban_bp

        app.register_blueprint(adm_kanban_bp)
        app.logger.info("[BLUEPRINT] ADM Kanban API registered at /api/adm-kanban")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register ADM Kanban API routes: {e}")

    # --- 14. Integration Workflow Routes ---
    try:
        from app.modules.architecture.routes.integration_routes import integration_bp

        app.register_blueprint(integration_bp)
        app.logger.info(
            "[BLUEPRINT] Integration Workflow routes registered at /integration"
        )
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register Integration Workflow routes: {e}"
        )

    # --- 15. Data Architecture Routes ---
    try:
        from app.modules.architecture.routes.data_architecture_routes import (
            data_architecture_bp,
        )

        app.register_blueprint(data_architecture_bp)
        app.logger.info(
            "[BLUEPRINT] Data Architecture routes registered at /architecture"
        )
    except Exception as e:
        app.logger.warning(
            f"[BLUEPRINT] Failed to register Data Architecture routes: {e}"
        )

    # --- 16. SA-005: ArchiMate OEF XML export ---
    try:
        from app.modules.architecture.routes.archimate_routes import archimate_bp

        app.register_blueprint(archimate_bp)
        app.logger.info("[BLUEPRINT] ArchiMate XML export registered at /archimate")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register ArchiMate XML export: {e}")

    # --- 17. ENT-001: Architecture Decision Records (ADRs) ---
    try:
        from app.modules.architecture.routes.adr_routes import adr_bp

        app.register_blueprint(adr_bp)
        app.logger.info("[BLUEPRINT] ADR routes registered at /architecture/adrs")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register ADR routes: {e}")

    # --- 18. Risk Register (ENT-006) ---
    try:
        from app.modules.architecture.routes.risk_routes import risk_bp

        app.register_blueprint(risk_bp)
        app.logger.info("[BLUEPRINT] Risk register routes registered at /architecture/risks")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Risk routes: {e}")

    app.logger.info("[MODULE] architecture registered (~220 routes, 19 blueprints)")
