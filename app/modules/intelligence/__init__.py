"""Intelligence module (Four Intelligences extension) — DE-1..DE-18, sdd-v2.md AA-1.

``register(app)`` is the one place this module attaches its pieces: the ORM
invalidation hook and the API blueprint, then the UI blueprint that serves the
Ask and Twin map pages.

The derivation runner itself mounts nothing: it computes and returns a
``DerivationResult``. Everything a person can open lives in the two blueprints
registered below.
"""

from __future__ import annotations


def register(app) -> None:
    """Register the intelligence module's blueprints and event hooks.

    The ORM invalidation hook (DE-3), the API blueprint (the recompute,
    provenance-expansion, impact and yield routes) and the UI blueprint (the
    Ask and Twin map pages) each register in their own ``try`` so that a
    failure in one degrades that one feature, not the whole app.
    """
    try:
        from app.modules.intelligence.services.invalidation import (
            register_invalidation_listener,
        )

        register_invalidation_listener()
    except Exception:
        app.logger.exception(
            "[MODULE] intelligence: invalidation listener registration failed"
        )

    try:
        from app.modules.intelligence.routes.api import intelligence_api

        app.register_blueprint(intelligence_api)
    except Exception:
        app.logger.exception(
            "[MODULE] intelligence: API blueprint registration failed"
        )

    try:
        from app.modules.intelligence.routes.ui import intelligence_ui

        app.register_blueprint(intelligence_ui)
    except Exception:
        app.logger.exception(
            "[MODULE] intelligence: UI blueprint registration failed"
        )


__all__ = ["register"]
