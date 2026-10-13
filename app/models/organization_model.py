"""Enterprise Organization modeling: RACI matrix over capabilities.

A NEW, self-contained model backing the enterprise-wide RACI matrix
(`/organization/raci`). This is deliberately separate from the
Solution-scoped RACI junction tables already living in
`app/models/business_layer.py` (`ProcessActorRaci`, `CapabilityActorRaci`) —
those are tied to individual Solutions, whereas `EnterpriseRaciAssignment`
represents a single enterprise-wide governance view that maps any
stakeholder (a `BusinessActor`, a `BusinessRole`, or a platform `User`) to
any capability in `UnifiedCapability`.

Convention notes (mirrors app/models/business_model.py):
    - TenantMixin supplies organization_id (multi-tenant FK); the app's
      SQLAlchemy event listener in app.middleware.tenant_isolation
      auto-installs tenant filters on any model that has this column.
    - Every new column besides organization_id is nullable so a brand-new
      table (built by db.create_all() on first boot) never conflicts with
      the reconcile-schema drift-repair step.
"""

from app import db
from app.datetime_helpers import utcnow
from app.models.mixins import TenantMixin

# Stakeholder kinds a RACI row can point at.
STAKEHOLDER_TYPES = ("actor", "role", "user")

# The four RACI letters.
RACI_VALUES = ("R", "A", "C", "I")


class EnterpriseRaciAssignment(TenantMixin, db.Model):
    """One cell of the enterprise RACI matrix: a stakeholder × capability
    assignment with an R/A/C/I designation."""

    __tablename__ = "enterprise_raci_assignments"

    __table_args__ = (
        db.UniqueConstraint(
            "stakeholder_type",
            "stakeholder_id",
            "capability_id",
            name="uq_enterprise_raci_stakeholder_capability",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    # Who: 'actor' -> BusinessActor.id, 'role' -> BusinessRole.id, 'user' -> User.id
    stakeholder_type = db.Column(db.String(10), nullable=True, index=True)
    stakeholder_id = db.Column(db.Integer, nullable=True, index=True)
    # Denormalized label so the matrix can render without joining three
    # different tables for every cell.
    stakeholder_name = db.Column(db.String(255), nullable=True)

    # What: the capability this RACI designation applies to.
    capability_id = db.Column(
        db.Integer, db.ForeignKey("unified_capabilities.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # R / A / C / I
    raci = db.Column(db.String(1), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=True)

    capability = db.relationship("UnifiedCapability", foreign_keys=[capability_id])

    def __repr__(self):
        return (
            f"<EnterpriseRaciAssignment {self.stakeholder_type}:{self.stakeholder_id} "
            f"cap={self.capability_id} raci={self.raci!r}>"
        )

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "stakeholder_type": self.stakeholder_type,
            "stakeholder_id": self.stakeholder_id,
            "stakeholder_name": self.stakeholder_name,
            "capability_id": self.capability_id,
            "raci": self.raci,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# How well a person (or team) can do a capability. One optional rating per RACI
# cell, so who-does-what stays in enterprise_raci_assignments and this table only
# adds the one thing that table cannot say.
PROFICIENCY_LEVELS = (1, 2, 3, 4)  # Learning, Working, Proficient, Leads


class CapabilityProficiency(TenantMixin, db.Model):
    """Proficiency of one RACI cell's stakeholder in that capability.

    Personal data about a named individual: read it only where the person's
    own manager or an administrator would be allowed to. NULL ``level`` means
    "not assessed" and is never displayed as zero."""

    __tablename__ = "capability_proficiencies"

    id = db.Column(db.Integer, primary_key=True)
    raci_assignment_id = db.Column(
        db.Integer,
        db.ForeignKey("enterprise_raci_assignments.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
        index=True,
    )
    level = db.Column(db.Integer, nullable=True)
    assessed_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(50), nullable=True)  # e.g. "onboarding"
    created_at = db.Column(db.DateTime, default=utcnow, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=True)

    assignment = db.relationship("EnterpriseRaciAssignment", foreign_keys=[raci_assignment_id])
