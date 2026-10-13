"""Trust centre: the platform's security assurance material.

The finding register and its tracker live here. Findings are facts about the
platform, the same for every organisation, so nothing in this module reads a
tenant-scoped table other than the audit trail entry written for each change.
"""

from .routes import trust_centre_bp


def register(app):
    """Register the trust centre blueprint on *app* (idempotent)."""
    if trust_centre_bp.name in app.blueprints:
        return
    app.register_blueprint(trust_centre_bp)


__all__ = ["register", "trust_centre_bp"]
