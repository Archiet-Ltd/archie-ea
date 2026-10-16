"""A clause of an architecture policy bound to a compliance control the organisation tracks.

The binding is what lets the platform say which applications a clause governs (those
mapped to the bound control) and raise a violation when the control is not met.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class PolicyClauseControl(TenantMixin, db.Model):
    __tablename__ = "policy_clause_controls"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "policy_id", "clause_ref", "control_id",
            name="uq_policy_clause_control",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(
        db.Integer, db.ForeignKey("architecture_policies.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    clause_ref = db.Column(db.String(50), nullable=False)
    clause_text = db.Column(db.Text)
    control_id = db.Column(
        db.Integer, db.ForeignKey("compliance_controls.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    policy = db.relationship(
        "ArchitecturePolicy",
        backref=db.backref("clause_controls", lazy="dynamic", cascade="all, delete-orphan"),
    )
    control = db.relationship("ComplianceControl")

    def to_dict(self):
        return {
            "id": self.id,
            "policy_id": self.policy_id,
            "clause_ref": self.clause_ref,
            "clause_text": self.clause_text,
            "control_id": self.control_id,
            "control_code": self.control.control_code if self.control else None,
            "control_title": self.control.title if self.control else None,
        }
