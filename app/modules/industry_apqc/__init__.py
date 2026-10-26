"""
Industry APQC Module - Process Framework Management

Provides centralized management of industry-specific APQC process frameworks
and their integration with capabilities and applications.

Registered via app._bootstrap.blueprints
"""

import logging

from flask import Flask

logger = logging.getLogger(__name__)


def register(app: Flask) -> None:
    """Register Industry APQC blueprints with the Flask app."""

    # --- 1. Industry APQC UI Routes ---
    try:
        from app.modules.industry_apqc.routes.industry_apqc_routes import (
            industry_apqc_bp,
        )

        app.register_blueprint(industry_apqc_bp)
        app.logger.info("[BLUEPRINT] Industry APQC UI registered at /industry-apqc")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register Industry APQC UI: {e}")

    # --- 2. APQC API Routes ---
    try:
        from app.modules.industry_apqc.routes.apqc_api_routes import apqc_bp

        app.register_blueprint(apqc_bp)
        app.logger.info("[BLUEPRINT] APQC API registered at /api/apqc")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register APQC API: {e}")

    # --- 3. The frameworks gallery ---
    try:
        from app.modules.industry_apqc.routes.frameworks_gallery_routes import frameworks_bp

        app.register_blueprint(frameworks_bp)
        app.logger.info("[BLUEPRINT] Frameworks gallery registered at /frameworks")
    except Exception as e:
        app.logger.warning(f"[BLUEPRINT] Failed to register the frameworks gallery: {e}")

    app.logger.info("[MODULE] industry_apqc registered (~25 routes, 3 blueprints)")
