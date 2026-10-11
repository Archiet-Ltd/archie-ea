"""Shared query helpers for compliance and governance data.

These replace the removed CapabilityDerivationService and provide a single
canonical access path for compliance requirement lookups.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def query_compliance_requirements(capability_id: int) -> List[Dict[str, Any]]:
    """Query compliance requirements for a business capability.

    Uses the canonical ComplianceRequirement store instead of the removed
    CapabilityDerivationService.  Returns a list of dicts with keys
    ``id``, ``name``, ``framework``, ``description``.

    Raises the underlying exception on failure so callers can distinguish
    "no data" from "query failed".
    """
    from app.models.relationship_tables import capability_compliance_requirements
    from app.models.compliance_models import ComplianceRequirement

    reqs = (
        ComplianceRequirement.query.join(capability_compliance_requirements)
        .filter(
            capability_compliance_requirements.c.business_capability_id
            == capability_id
        )
        .all()
    )
    return [
        {
            "id": r.id,
            "name": r.name,
            "framework": getattr(r, "framework_name", ""),
            "description": r.description or "",
        }
        for r in reqs
    ]