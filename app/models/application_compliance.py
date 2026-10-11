"""Application Compliance Mapping Model Extension.

Extends the existing compliance framework to link applications with compliance controls.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class ApplicationComplianceControl(TenantMixin, db.Model):
    """
    Maps applications to compliance controls they must satisfy.

    Tracks implementation status, evidence, and verification.
    Better than forcing compliance into ElementTemplate - respects separation of concerns.
    """

    __tablename__ = "application_compliance_controls"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id", ondelete="CASCADE"), nullable=True
    )
    control_id = db.Column(
        db.Integer, db.ForeignKey("compliance_controls.id", ondelete="CASCADE"), nullable=False
    )
    adoption_id = db.Column(
        db.Integer, db.ForeignKey("framework_adoptions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Implementation tracking
    implementation_status = db.Column(db.String(20), nullable=False, default="planned")
    # planned, in_progress, implemented, verified, not_applicable, waived

    # Evidence and documentation
    evidence_url = db.Column(db.String(500))  # Link to documentation/evidence
    notes = db.Column(db.Text)

    # Verification
    verified_date = db.Column(db.DateTime)
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    # Audit trail
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    application = db.relationship(
        "ApplicationComponent",
        backref=db.backref("compliance_controls", lazy="dynamic", cascade="all, delete-orphan"),
    )
    control = db.relationship(
        "ComplianceControl", backref=db.backref("application_mappings", lazy="dynamic")
    )
    verified_by = db.relationship("User", foreign_keys=[verified_by_id])

    adoption = db.relationship("FrameworkAdoption", back_populates="adopted_controls")

    __table_args__ = (db.UniqueConstraint("application_id", "control_id", name="uq_app_control"),)

    def to_dict(self):
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "application_id": self.application_id,
            "control_id": self.control_id,
            "control_code": self.control.control_code if self.control else None,
            "control_title": self.control.title if self.control else None,
            "framework": self.control.framework.code
            if self.control and self.control.framework
            else None,
            "implementation_status": self.implementation_status,
            "evidence_url": self.evidence_url,
            "notes": self.notes,
            "verified_date": self.verified_date.isoformat() if self.verified_date else None,
            "verified_by": self.verified_by.email if self.verified_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<AppCompliance {self.application_id}:{self.control.control_code if self.control else self.control_id} [{self.implementation_status}]>"
