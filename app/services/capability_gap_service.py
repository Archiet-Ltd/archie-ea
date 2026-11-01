"""
DEPRECATED: Import from app.modules.capabilities.services instead.
-> app.modules.capabilities.services.analysis_service

Backward-compat re-export. Canonical: app/modules/capabilities/services/capability_gap_service.py
"""

from app.modules.capabilities.services.capability_gap_service import (  # noqa: F401
    CapabilityGapAnalysisService,
)


def _compute_capability_heatmap(self, scope_app_ids: list) -> list:
    """
    Compute a coverage heatmap for unified capabilities against a scoped set of applications.

    For each unified capability, calculates how many of the scoped applications have an
    active mapping, then classifies coverage as:
      - 'gap'     : coverage_score < 0.3
      - 'partial' : 0.3 <= coverage_score <= 0.7
      - 'covered' : coverage_score > 0.7

    Args:
        scope_app_ids: List of application_component_id values defining the scope.

    Returns:
        List of dicts with keys: capability_id, capability_name, coverage_score, status, app_count
    """
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    if not scope_app_ids:
        return []

    total = len(scope_app_ids)

    capabilities = UnifiedCapability.active().all()

    results = []
    for cap in capabilities:
        app_count = (
            UnifiedApplicationCapabilityMapping.query
            .filter(
                UnifiedApplicationCapabilityMapping.unified_capability_id == cap.id,
                UnifiedApplicationCapabilityMapping.application_component_id.in_(scope_app_ids),
                UnifiedApplicationCapabilityMapping.is_active.is_(True),
            )
            .count()
        )

        coverage_score = app_count / total

        if coverage_score < 0.3:
            status = "gap"
        elif coverage_score <= 0.7:
            status = "partial"
        else:
            status = "covered"

        results.append(
            {
                "capability_id": cap.id,
                "capability_name": cap.name,
                "coverage_score": coverage_score,
                "status": status,
                "app_count": app_count,
            }
        )

    return results


CapabilityGapAnalysisService.compute_capability_heatmap = _compute_capability_heatmap
