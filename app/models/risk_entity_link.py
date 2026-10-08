"""RiskEntityLink — H1: the Risk Register had no field linking a risk to the
Application/Solution/Programme it actually threatens, so a risk existed only
in isolation and never surfaced on the entity it was about.

A new junction table rather than three nullable FK columns on Risk: a risk
can threaten more than one entity (e.g. a shared vendor risk touching several
applications), and the parent-entity types are heterogeneous (Application,
Solution, StrategicInitiative/"Programme"), which a single polymorphic table
handles without three near-duplicate columns.

New table, ADD-only via init-db's create_all -- no existing-database impact.

"constraint" (SolutionConstraint) was added for the one-risk-register
consolidation: a risk solution_ai_orchestrator.py derives from a named
constraint keeps that relationship as a risk link here, the same mechanism
every other entity type already uses, rather than a column the canonical
Risk model has no room for.
"""
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin

# "Programme" in the product vocabulary is StrategicInitiative in the schema
# (see app/models/transformation_programme.py: ProgrammeWorkstream.programme_id
# -> strategic_initiatives.id).
ENTITY_TYPES = ("application", "solution", "programme", "constraint")


class RiskEntityLink(TenantMixin, db.Model):
    __tablename__ = "risk_entity_links"
    __table_args__ = (
        db.UniqueConstraint("risk_id", "entity_type", "entity_id", name="uq_risk_entity_link"),
    )

    id = db.Column(db.Integer, primary_key=True)
    risk_id = db.Column(
        db.Integer, db.ForeignKey("risks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type = db.Column(db.String(20), nullable=False, index=True)  # ENTITY_TYPES
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    risk = db.relationship("Risk", backref=db.backref("entity_links", cascade="all, delete-orphan"))

    def to_dict(self):
        return {
            "id": self.id,
            "risk_id": self.risk_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
