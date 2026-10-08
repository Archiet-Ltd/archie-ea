"""Governance Gate configuration model.  # migration-exempt

Defines hard gates that BLOCK solution progression (e.g. ARB submission)
until completeness thresholds are met.  Part of GOV-03.
Table created via db.create_all() per migration-freeze policy.
"""

from app import db
from app.models.mixins import TenantMixin
from datetime import datetime


class GovernanceGate(TenantMixin, db.Model):
    __tablename__ = "governance_gates"
    # A gate name is unique within one organisation, not across the platform:
    # each organisation overrides the same system-default gate (for example
    # "arb_submission") by name. Existing databases carried a platform-wide
    # UNIQUE(gate_name); `flask reconcile-schema` replaces it.
    __table_args__ = (
        db.UniqueConstraint("organization_id", "gate_name", name="uq_governance_gates_org_gate_name"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    gate_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    required_sections = db.Column(db.JSON)  # ["vision_motivation", "business_process_view", ...]
    min_completeness = db.Column(db.Integer, default=80)
    required_decisions_count = db.Column(db.Integer, default=0)
    require_risk_mitigations = db.Column(db.Boolean, default=False)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "gate_name": self.gate_name,
            "description": self.description,
            "required_sections": self.required_sections or [],
            "min_completeness": self.min_completeness,
            "required_decisions_count": self.required_decisions_count,
            "require_risk_mitigations": self.require_risk_mitigations,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
