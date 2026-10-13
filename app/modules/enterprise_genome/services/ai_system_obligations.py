"""AI system obligations — compliance requirement rows attached on classification.

When a risk classification is accepted, the system accrues obligations as
``ComplianceRequirement`` rows linked to its element. Each obligation shows
its evidence status.

Reuse
-----
* ``ComplianceRequirement`` (``app.models.compliance_models``) — the one
  requirement model, no second store.
* Evidence fields on ``ComplianceRequirement`` (``evidence_location``,
  ``last_verified_at``, ``implementation_status``) — no second evidence model.

The EU AI Act ``RegulatoryFramework`` rows are NOT read here: the framework
seed data (``RegulatoryFrameworkService``) contains no EU AI Act framework and
no per-risk-class obligation controls, so there is no framework control list to
reuse. The class->obligation template map below is the single source of the
mapping. If per-class framework rows are ever seeded, the map should be read
from them instead of this module.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app import db

logger = logging.getLogger(__name__)

NOT_RECORDED = "not recorded"

# The four EU AI Act risk classes from classification.py.
RISK_CLASSES = ("unacceptable", "high", "limited", "minimal")

# Per-class obligation sets. These are the compliance requirements that must be
# met for a system classified at each tier. They are stored as
# ComplianceRequirement rows when the classification is accepted.
#
# This dictionary IS the source of the class -> obligation mapping; it is not
# read from RegulatoryFramework rows because no framework seed on this branch
# carries an EU AI Act framework or per-risk-class obligation controls. When
# such rows exist, read them instead (the mapping must have one source).
CLASS_OBLIGATIONS = {
    "high": [
        {
            "title": "Human oversight mechanism",
            "description": (
                "High-risk AI systems must have human oversight to prevent "
                "or minimise risks."
            ),
            "requirement_type": "regulatory",
        },
        {
            "title": "Technical documentation",
            "description": (
                "High-risk AI systems require comprehensive technical "
                "documentation before deployment."
            ),
            "requirement_type": "regulatory",
        },
        {
            "title": "Risk management system",
            "description": (
                "A continuous risk management process for the AI system."
            ),
            "requirement_type": "regulatory",
        },
        {
            "title": "Data governance",
            "description": (
                "Training, validation and testing data sets must be relevant, "
                "representative and error-free."
            ),
            "requirement_type": "regulatory",
        },
        {
            "title": "Transparency and explainability",
            "description": (
                "The operation of the AI system must be transparent and "
                "explainable to users."
            ),
            "requirement_type": "regulatory",
        },
    ],
    "limited": [
        {
            "title": "Transparency obligation",
            "description": (
                "Limited-risk AI systems must inform users that they are "
                "interacting with an AI system."
            ),
            "requirement_type": "regulatory",
        },
        {
            "title": "Documentation requirement",
            "description": "Basic documentation of the system's purpose and capabilities.",
            "requirement_type": "regulatory",
        },
    ],
    "minimal": [
        {
            "title": "Voluntary code of conduct",
            "description": (
                "Minimal-risk AI systems should follow voluntary codes of "
                "conduct."
            ),
            "requirement_type": "regulatory",
        },
    ],
    "unacceptable": [
        {
            "title": "Prohibition compliance",
            "description": (
                "Unacceptable risk AI systems are prohibited and must not "
                "be deployed."
            ),
            "requirement_type": "regulatory",
        },
    ],
}


def attach_obligations(
    element,
    risk_class: str,
    organization_id: int,
) -> list[int]:
    """Attach the obligation set for *risk_class* to *element*.

    Creates ``ComplianceRequirement`` rows linked to the element's
    ``archimate_element_id``. Each row gets a default implementation_status of
    ``"not_started"``.

    Skips obligations already attached (by title + archimate_element_id match)
    so re-accepting a classification does not duplicate rows.

    Returns the list of created requirement ids.
    """
    from app.models.compliance_models import ComplianceRequirement

    if risk_class not in CLASS_OBLIGATIONS:
        logger.warning("attach_obligations: unknown risk class '%s'", risk_class)
        return []

    templates = CLASS_OBLIGATIONS[risk_class]
    created: list[int] = []

    for tmpl in templates:
        # Skip if already attached (by element + title, scoped through element)
        existing = ComplianceRequirement.query.filter_by(
            archimate_element_id=element.id,
            title=tmpl["title"],
        ).first()
        if existing is not None:
            continue

        req = ComplianceRequirement(
            archimate_element_id=element.id,
            title=tmpl["title"],
            description=tmpl["description"],
            requirement_type=tmpl.get("requirement_type", "regulatory"),
            status="active",
            implementation_status="not_started",
            created_at=datetime.utcnow(),
        )
        db.session.add(req)
        db.session.flush()
        created.append(req.id)

    return created


def get_obligations_for_system(
    element_id: int,
    organization_id: int,
) -> list[dict]:
    """Read all obligations attached to an AI system element.

    Returns a list of dicts::

        [
            {
                "id": int,
                "title": str,
                "description": str,
                "evidence_status": str,  # the recorded implementation_status, or "not recorded" when unset
                "evidence_status_reason": str | None,  # why it is "not recorded"
                "evidence_location": str | None,
                "last_verified_at": str | None,
            },
            ...
        ]

    Returns an empty list when no obligations are attached. Scoped to
    *organization_id*.
    """
    from app.models.compliance_models import ComplianceRequirement
    from app.models.models import ArchiMateElement

    rows = (
        ComplianceRequirement.query.join(
            ArchiMateElement,
            ComplianceRequirement.archimate_element_id == ArchiMateElement.id,
        )
        .filter(
            ComplianceRequirement.archimate_element_id == element_id,
            ArchiMateElement.organization_id == organization_id,
        )
        .order_by(ComplianceRequirement.id.asc())
        .all()
    )

    obligations: list[dict] = []
    for row in rows:
        # The evidence status is the recorded implementation status. Only an
        # unset status falls back to "not recorded"; evidence location and
        # last-verified date are separate facts, never folded into it.
        impl_status = (row.implementation_status or "").strip()
        evidence_status = impl_status or NOT_RECORDED
        evidence_status_reason = (
            None if impl_status else "no implementation status recorded"
        )

        obligations.append({
            "id": row.id,
            "title": row.title,
            "description": row.description,
            "evidence_status": evidence_status,
            "evidence_status_reason": evidence_status_reason,
            "evidence_location": row.evidence_location or None,
            "last_verified_at": (
                row.last_verified_at.isoformat()
                if row.last_verified_at
                else None
            ),
        })

    return obligations