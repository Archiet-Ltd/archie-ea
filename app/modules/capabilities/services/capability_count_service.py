"""Single source of truth for "how many capabilities exist".

`/capability-map/` (via ``api_unified_domains``) and `/capability-map/hierarchy`
used to compute this number independently and disagreed (500 vs 495):

- the domains API did a flat ``BusinessCapability.query.count()`` — every row
  visible to the tenant.
- the hierarchy page's Alpine ``countAll()`` walked only the subtree
  reachable from level-1 roots via ``parent_capability_id`` (built server-side
  in ``map_views.hierarchy()``'s ``cap_to_dict``/``roots`` logic). Any
  capability whose parent chain does not resolve back to a level-1 root —
  an orphan row, or a broken ``parent_capability_id`` — is silently dropped
  from that walk even though the row exists.

Both numbers were counting the *same* underlying set (all BusinessCapability
rows for the tenant); only the second one had a bug that undercounted it.
There is no legitimate "all capabilities" vs "leaf capabilities" distinction
here — route both views through this single function so they can't diverge
again. (The hierarchy *tree render* itself still only draws nodes reachable
from a level-1 root; fixing orphaned capabilities to attach into the visible
tree is a separate, larger change and out of scope here — only the headline
count is unified.)

Now reads from ``UnifiedCapability`` (the canonical store PR 1 built) so the
map, the dashboard total, the heatmap and the capability interface all show
the same number for an organisation.
"""

from app import db
from app.models.unified_capability import UnifiedCapability, BusinessDomain
from sqlalchemy import func


def count_capabilities():
    """Total UnifiedCapability rows visible to the current tenant.

    Tenant-scoped automatically via ``HybridCapabilityTenantMixin``'s ORM event
    when called inside a request context (see ``app/middleware/tenant_isolation.py``
    and ``UnifiedCapability.visibility_predicate``). Includes shared reference
    rows (``scope == "reference"`` and ``organization_id IS NULL``) plus the
    tenant's own rows.
    """
    return UnifiedCapability.query.count()


def count_domains():
    """Distinct business domains that have at least one level-1 capability
    visible to the current tenant.

    A domain is counted only when the tenant has at least one level-1
    (strategic) capability in that domain. This matches the capability map's
    domain grouping in ``_api_hierarchy_context`` and ``api_unified_domains``.

    Tenant-scoped automatically via ``HybridCapabilityTenantMixin``'s ORM event
    on ``UnifiedCapability``. Includes shared reference capabilities
    (``scope == "reference"`` and ``organization_id IS NULL``) plus the
    tenant's own capabilities.
    """
    return (
        db.session.query(func.count(func.distinct(UnifiedCapability.domain_id)))
        .join(BusinessDomain, UnifiedCapability.domain_id == BusinessDomain.id)
        .filter(UnifiedCapability.level == 1)
        .scalar()
        or 0
    )


# Backward-compatible alias for any callers not yet updated.
count_business_capabilities = count_capabilities
