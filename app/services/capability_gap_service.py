"""
DEPRECATED: Import from app.modules.capabilities.services instead.
-> app.modules.capabilities.services.analysis_service

Backward-compat re-export. Canonical: app/modules/capabilities/services/capability_gap_service.py
"""

from app.modules.capabilities.services.capability_gap_service import (  # noqa: F401
    CapabilityGapAnalysisService,
)


def _signed_in_organization():
    from flask import g

    return getattr(g, "current_org_id", None)


def _applications_in_scope(organization_id, cap, scope: set) -> int:
    """How many of the scoped applications are recorded as supporting the capability.

    Read through the one support reader, so an application counts once however its support is recorded.
    A shared catalogue capability is counted through the organisation's own record of it, where its
    support is kept."""
    from app.services import capability_support as support

    if organization_id is None:
        return 0
    record_ids = [cap.id] if cap.organization_id == organization_id else []
    if cap.organization_id is None:
        record_ids = []
        for element in support._elements_named(organization_id, cap.name, "capability"):
            record = support.existing_capability_record(organization_id, element)
            if record is not None:
                record_ids.append(record.id)
    applications = set()
    for capability_id in record_ids:
        applications |= {r["application_id"] for r in support.applications_of_capability(organization_id, capability_id)}
    return len(applications & scope)


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
    if not scope_app_ids:
        return []

    total = len(scope_app_ids)

    capabilities = UnifiedCapability.query.all()

    organization_id = _signed_in_organization()
    scope = set(scope_app_ids)

    results = []
    for cap in capabilities:
        app_count = _applications_in_scope(organization_id, cap, scope)

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
