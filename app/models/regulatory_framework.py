"""
Framework adoption and tailoring models.

Extends the shared-reference pattern (HybridTenantMixin) so the platform
seeds a read-only catalogue of frameworks and controls, and each organisation
adopts them into its own tenant scope with optional tailoring.
"""

from datetime import datetime, timezone

from app import db
from app.models.mixins import HybridTenantMixin


def _now_utc_naive():
    """Return naive UTC datetime for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FrameworkAdoption(HybridTenantMixin, db.Model):
    """An organisation's adoption of a regulatory framework from the catalogue.

    Reference rows (organization_id IS NULL, scope='reference') represent the
    platform catalogue entry.  Tenant rows (organization_id = <org>, scope='tenant')
    represent an organisation's adoption with optional tailoring.
    """

    __tablename__ = "framework_adoptions"

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(16), nullable=True, index=True)

    framework_id = db.Column(
        db.Integer, db.ForeignKey("regulatory_frameworks.id"), nullable=False, index=True
    )
    reference_adoption_id = db.Column(
        db.Integer, db.ForeignKey("framework_adoptions.id", ondelete="SET NULL"), nullable=True
    )

    adopted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    adopted_at = db.Column(db.DateTime, default=_now_utc_naive)
    status = db.Column(db.String(20), default="active")  # active, inactive
    tailoring_notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=_now_utc_naive)
    updated_at = db.Column(db.DateTime, default=_now_utc_naive, onupdate=_now_utc_naive)

    # Relationships
    framework = db.relationship("RegulatoryFramework", backref="adoptions")
    adopted_by = db.relationship("User", foreign_keys=[adopted_by_id])
    adopted_controls = db.relationship(
        "ApplicationComplianceControl", back_populates="adoption", lazy="dynamic", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "framework_id", name="uq_org_framework_adoption"
        ),
    )

    def __repr__(self):
        return f"<FrameworkAdoption {self.framework_id} org={self.organization_id}>"