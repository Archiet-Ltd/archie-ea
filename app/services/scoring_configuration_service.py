"""The one way to write a ScoringConfiguration.

``ScoringConfiguration`` carries no ``organization_id`` of its own --
``scope_type``/``scope_entity_id`` are a business-unit label, not a tenant
fence. One row with ``is_default=True`` sets the fallback weights every
organisation's application-rationalization view uses; creating or editing
one with that flag silently unsets every other row's default, platform-wide.
The write routes were originally gated only by ``@login_required`` --
any signed-in user, not even an organisation admin -- across three parallel
route-tree copies (``app/api/dashboard_routes.py``, the legacy and v2
``dashboard_pages_routes.py``); fixed to ``@platform_admin_required`` in
PR #307 and PR #321 (the third, "DEPRECATED... do not add new code here"
copy that review had missed).

Routing every write through this module instead makes the check structural
rather than decorator-dependent: a fourth copy nobody has written yet, or a
future edit that drops the decorator from one of these three, still cannot
mutate the table without going through code that already refuses a
non-platform-admin caller. One accessor per concept (ADR-0008), not three
near-identical copies of the same mutation logic kept in sync by hand.

The lazy-create-default fallback in
``app/services/rationalization_scoring_service.py`` (materialises the
CIO.gov baseline the first time anyone reads scoring configuration and none
exists) deliberately does NOT go through this module: it is a self-healing
read-time default, not an administrative configuration change, and gating
it would stop an ordinary user from ever seeing a sensible default.
"""

from __future__ import annotations

from flask import abort
from flask_login import current_user

from app import db
from app.middleware.tenant_decorators import is_platform_admin
from app.models.application_rationalization import ScoringConfiguration

_WEIGHT_FIELDS = (
    "technical_health_weight",
    "business_value_weight",
    "cost_efficiency_weight",
    "vendor_risk_weight",
)
_THRESHOLD_FIELDS = (
    "eliminate_threshold",
    "migrate_technical_threshold",
    "migrate_business_threshold",
    "invest_business_threshold",
    "invest_technical_threshold",
    "tolerate_min_threshold",
)


class ScoringConfigurationError(Exception):
    """A validation/lookup failure the route turns into its existing
    {"success": False, "error": ...} envelope at ``status_code`` -- the
    same shape all three route copies already returned, now built once."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _require_platform_admin() -> None:
    """Defense in depth: the route decorator should already have refused a
    non-platform-admin caller before this module runs at all."""
    if not is_platform_admin(current_user):
        abort(403)


def create_scoring_configuration(data: dict) -> ScoringConfiguration:
    _require_platform_admin()
    total_weight = (
        data.get("technical_health_weight", 30)
        + data.get("business_value_weight", 35)
        + data.get("cost_efficiency_weight", 25)
        + data.get("vendor_risk_weight", 10)
    )
    if total_weight != 100:
        raise ScoringConfigurationError(f"Weights must sum to 100, got {total_weight}", 400)

    config = ScoringConfiguration(
        name=data.get("name"),
        description=data.get("description"),
        scope_type=data.get("scope_type", "business_unit"),
        scope_entity_id=data.get("scope_entity_id"),
        scope_entity_type=data.get("scope_entity_type"),
        technical_health_weight=data.get("technical_health_weight", 30),
        business_value_weight=data.get("business_value_weight", 35),
        cost_efficiency_weight=data.get("cost_efficiency_weight", 25),
        vendor_risk_weight=data.get("vendor_risk_weight", 10),
        eliminate_threshold=data.get("eliminate_threshold", 40),
        migrate_technical_threshold=data.get("migrate_technical_threshold", 40),
        migrate_business_threshold=data.get("migrate_business_threshold", 50),
        invest_business_threshold=data.get("invest_business_threshold", 70),
        invest_technical_threshold=data.get("invest_technical_threshold", 50),
        tolerate_min_threshold=data.get("tolerate_min_threshold", 40),
        is_default=data.get("is_default", False),
    )

    if config.is_default:
        ScoringConfiguration.query.filter_by(is_default=True).update({"is_default": False})

    db.session.add(config)
    db.session.commit()
    return config


def update_scoring_configuration(config_id: int, data: dict) -> ScoringConfiguration:
    _require_platform_admin()
    config = ScoringConfiguration.query.get(config_id)
    if not config:
        raise ScoringConfigurationError("Configuration not found", 404)

    for field in ("name", "description", *_WEIGHT_FIELDS):
        if field in data:
            setattr(config, field, data[field])

    if any(field in data for field in _WEIGHT_FIELDS):
        is_valid, error = config.validate_weights()
        if not is_valid:
            raise ScoringConfigurationError(error, 400)

    for field in _THRESHOLD_FIELDS:
        if field in data:
            setattr(config, field, data[field])

    if data.get("is_default") and not config.is_default:
        ScoringConfiguration.query.filter_by(is_default=True).update({"is_default": False})
        config.is_default = True

    config.configuration_version += 1
    db.session.commit()
    return config


def delete_scoring_configuration(config_id: int) -> None:
    """Soft delete: sets is_active=False, never removes the row."""
    _require_platform_admin()
    config = ScoringConfiguration.query.get(config_id)
    if not config:
        raise ScoringConfigurationError("Configuration not found", 404)
    if config.is_default:
        raise ScoringConfigurationError("Cannot delete default configuration", 400)
    config.is_active = False
    db.session.commit()
