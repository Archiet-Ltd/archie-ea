"""Technology Standards — the approved-technology register behind TOGAF governance.

Distinct from ARBGovernanceStandard (app/models/architecture_review_board.py), which
holds the *review criteria and checklists* an ARB applies. This model holds the
answer to "is PostgreSQL 15 approved for new work?" — the Technology Standards tab
of the governance dashboard reads it.

New table, so `flask init-db` (create_all) is enough to provision it; no
reconcile-schema step is required for a fresh column on an existing table.
"""
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class TechnologyStandard(TenantMixin, db.Model):
    """An approved, deprecated or prohibited technology in the estate."""

    __tablename__ = "technology_standards"

    id = db.Column(db.Integer, primary_key=True)

    technology_name = db.Column(db.String(255), nullable=False, index=True)
    category = db.Column(db.String(100), index=True)
    # e.g. Programming Language, Database, Message Broker, Runtime, Framework

    approved_version = db.Column(db.String(50))  # "15.x", "3.11+", "LTS only"

    status = db.Column(db.String(30), default="approved", index=True)
    # approved | preferred | acceptable | deprecated | prohibited | under_review

    rationale = db.Column(db.Text)
    notes = db.Column(db.Text)

    # Governance
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    approved_by = db.Column(db.String(100))
    approval_date = db.Column(db.Date)
    review_date = db.Column(db.Date)  # next scheduled review

    # Lifecycle guidance for deprecated/prohibited entries
    replacement_technology = db.Column(db.String(255))
    sunset_date = db.Column(db.Date)

    # The modelled technology this standard governs, when there is one. Its
    # radar ring is not copied here: it is read from that element's
    # TechRadarEntry, the one store for adopt / trial / assess / hold.
    archimate_element_id = db.Column(
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    owner = db.relationship("User", backref="owned_technology_standards")
    element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])

    def to_dict(self):
        """Shape consumed by the governance dashboard's Technology Standards tab."""
        return {
            "id": self.id,
            "technology": self.technology_name,
            "category": self.category or "",
            "status": (self.status or "approved").replace("_", " ").title(),
            "version": self.approved_version or "—",
        }

    def __repr__(self):
        return f"<TechnologyStandard {self.technology_name} [{self.status}]>"
