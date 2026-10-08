"""
Capability core service — imports from inlined canonical sources.

Consolidates:
- capability_mapping_service (CapabilityMappingService)
- capability_taxonomy_service (CapabilityTaxonomyService)
- capability_governance_service (CapabilityGovernanceService)
- capability_tagging_service (CapabilityTagService)
- dual_capability_mapping_service (DualCapabilityMappingService)
- apqc_capability_mapping_service (APQCCapabilityMappingService)

Also provides the single creator for UnifiedCapability records
(ensure_capability_record) so the map's add/edit controls write through
one accessor, never to BusinessCapability directly.
"""

from app.modules.capabilities.services.capability_mapping_service import (  # noqa: F401
    CapabilityMappingService,
)

from app.modules.capabilities.services.capability_taxonomy_service import (  # noqa: F401
    CapabilityTaxonomyService,
    ValidationResult,
    ValidationViolation,
)

from app.modules.capabilities.services.capability_governance_service import (  # noqa: F401
    CapabilityGovernanceService,
)

from app.modules.capabilities.services.capability_tagging_service import (  # noqa: F401
    CapabilityTagService,
)

from app.modules.capabilities.services.dual_capability_mapping_service import (  # noqa: F401
    DualCapabilityMappingService,
)

from app.modules.capabilities.services.apqc_capability_mapping_service import (  # noqa: F401
    APQCCapabilityMappingRules,
    APQCCapabilityMappingService,
    AuditEntry,
    MappingConfidenceCalculator,
    MappingValidationResult,
)

# ---------------------------------------------------------------------------
# Single creator for UnifiedCapability (PR 1's canonical accessor)
# ---------------------------------------------------------------------------
def ensure_capability_record(
    *,
    name: str,
    level: int = 2,
    description: str = "",
    code: str | None = None,
    domain_id: int | None = None,
    category: str | None = None,
    capability_type: str | None = None,
    parent_capability_id: int | None = None,
    specialization_type: str = "BUSINESS",
    organization_id: int | None = None,
    scope: str | None = None,
    **extra_fields,
):
    """
    Create or retrieve a UnifiedCapability record — the one creator for the
    canonical capability store.

    This is the only function that should create UnifiedCapability rows. All
    callers (map add/edit dialogs, API endpoints, seeders, migrations) must
    go through this instead of instantiating the model directly.

    Args:
        name: Capability name (required)
        level: Hierarchy level 1-3 (default 2)
        description: Optional description
        code: Optional short code (e.g., "CUST-CRM-ACQ")
        domain_id: FK to BusinessDomain
        category: core, supporting, differentiating
        capability_type: strategic, operational, supporting
        parent_capability_id: FK to parent UnifiedCapability
        specialization_type: BUSINESS, MANUFACTURING, APPLICATION, TECHNICAL
        organization_id: Tenant ID (None for shared reference capabilities)
        scope: "reference" for shared catalogue, "tenant" for tenant-owned
        **extra_fields: Any other UnifiedCapability columns

    Returns:
        (UnifiedCapability, created_bool) tuple
    """
    from app import db
    from app.models.unified_capability import UnifiedCapability
    from flask import g, has_request_context

    # Determine organization_id from request context if not provided
    if organization_id is None and has_request_context():
        organization_id = getattr(g, "current_org_id", None)

    # Determine scope: reference capabilities have no org, tenant capabilities do
    if scope is None:
        scope = "reference" if organization_id is None else "tenant"

    # Check for existing capability with same name/code/org
    query = UnifiedCapability.query.filter(UnifiedCapability.name == name)
    if code:
        query = query.filter(UnifiedCapability.code == code)
    if organization_id is not None:
        query = query.filter(UnifiedCapability.organization_id == organization_id)
    else:
        query = query.filter(UnifiedCapability.organization_id.is_(None))

    existing = query.first()
    if existing:
        return existing, False

    # Create new capability
    cap = UnifiedCapability(
        name=name,
        level=level,
        description=description,
        code=code,
        domain_id=domain_id,
        category=category,
        capability_type=capability_type,
        parent_capability_id=parent_capability_id,
        specialization_type=specialization_type,
        organization_id=organization_id,
        scope=scope,
        **extra_fields,
    )
    db.session.add(cap)
    db.session.flush()  # Get ID without committing
    return cap, True
