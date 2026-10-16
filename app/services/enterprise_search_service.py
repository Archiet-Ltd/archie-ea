"""
Enterprise Search Service

Provides search functionality across capabilities and compliance entities.
"""

from app.models.unified_capability import UnifiedCapability
from app.middleware.tenant_context import current_org_id
from app.models.compliance_models import CompliancePolicy, ComplianceViolation


class EnterpriseSearchService:
    """Service for searching enterprise entities."""

    @staticmethod
    def search(query, entity_type="all", page=1, per_page=20):
        """Search across capabilities and compliance entities.

        Args:
            query: Search query string
            entity_type: Type of entity to search ('all', 'capability', 'compliance')
            page: Page number for pagination
            per_page: Results per page

        Returns:
            Dictionary with search results
        """
        results = {
            "query": query,
            "entity_type": entity_type,
            "page": page,
            "per_page": per_page,
            "capabilities": [],
            "policies": [],
            "violations": [],
            "total": 0,
        }

        # Search capabilities
        if entity_type in ("all", "capability"):
            cap_query = UnifiedCapability.query.filter(
                (UnifiedCapability.name.ilike(f"%{query}%"))
                | (UnifiedCapability.description.ilike(f"%{query}%"))
            )
            cap_total = cap_query.count()
            capabilities = cap_query.limit(per_page).offset((page - 1) * per_page).all()

            results["capabilities"] = [
                {
                    **cap.to_dict(),
                    "type": "capability",
                }
                for cap in capabilities
            ]
            results["total"] += cap_total

        # Policies and violations belong to an organisation; with no active
        # organisation nothing is searched.
        org_id = current_org_id()
        compliance = entity_type in ("all", "compliance") and org_id is not None

        # Search policies
        if compliance:
            policy_query = CompliancePolicy.query.filter(
                CompliancePolicy.organization_id == org_id,
                (CompliancePolicy.name.ilike(f"%{query}%"))
                | (CompliancePolicy.description.ilike(f"%{query}%")),
            )
            policy_total = policy_query.count()
            policies = policy_query.limit(per_page).offset((page - 1) * per_page).all()

            results["policies"] = [
                {
                    **p.to_dict(),
                    "type": "policy",
                }
                for p in policies
            ]
            results["total"] += policy_total

        # Search violations
        if compliance:
            violation_query = ComplianceViolation.query.filter(
                ComplianceViolation.organization_id == org_id,
                ComplianceViolation.description.ilike(f"%{query}%"),
            )
            violation_total = violation_query.count()
            violations = violation_query.limit(per_page).offset((page - 1) * per_page).all()

            results["violations"] = [
                {
                    **v.to_dict(),
                    "type": "violation",
                }
                for v in violations
            ]
            results["total"] += violation_total

        return results
