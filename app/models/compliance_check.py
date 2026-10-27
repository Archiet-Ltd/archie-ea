"""Compliance Check model — runtime spec drift detection.  # migration-exempt

Records compliance checks between published API specs and deployed services,
tracking endpoint coverage, schema mismatches, and SLA violations.

Table created via db.create_all() — no Alembic migration needed (migration freeze).
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class RuntimeComplianceCheck(TenantMixin, db.Model):
    """Records a compliance check between published spec and runtime service."""

    __tablename__ = "runtime_compliance_checks"
    __tenant_parent__ = (('solution_id', 'solutions'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id"), nullable=False, index=True
    )
    published_spec_id = db.Column(
        db.Integer, db.ForeignKey("published_api_specs.id"), nullable=False
    )

    # Target service
    service_url = db.Column(db.String(500), nullable=False)

    # Results
    status = db.Column(db.String(20), default="pending")  # pending, passed, drifted, unreachable, error
    compliance_score = db.Column(db.Float)  # 0.0 - 1.0

    # Drift details
    missing_endpoints = db.Column(db.JSON)  # endpoints in spec but not in service
    extra_endpoints = db.Column(db.JSON)  # endpoints in service but not in spec
    schema_mismatches = db.Column(db.JSON)  # field type/required differences
    sla_violations = db.Column(db.JSON)  # response time, availability issues

    # Metadata
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)
    checked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    duration_ms = db.Column(db.Integer)

    # Relationships
    solution = db.relationship("Solution", foreign_keys=[solution_id], lazy="select")
    published_spec = db.relationship(
        "PublishedAPISpec", foreign_keys=[published_spec_id], lazy="select"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "solution_id": self.solution_id,
            "published_spec_id": self.published_spec_id,
            "service_url": self.service_url,
            "status": self.status,
            "compliance_score": self.compliance_score,
            "missing_endpoints": self.missing_endpoints,
            "extra_endpoints": self.extra_endpoints,
            "schema_mismatches": self.schema_mismatches,
            "sla_violations": self.sla_violations,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "checked_by_id": self.checked_by_id,
            "duration_ms": self.duration_ms,
        }
