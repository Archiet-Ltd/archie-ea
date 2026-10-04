"""Risk model for TPM-013 risk heat map feature."""
import enum
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class RiskStatus(enum.Enum):
    OPEN = "open"
    MITIGATED = "mitigated"
    ACCEPTED = "accepted"
    CLOSED = "closed"


class Risk(TenantMixin, db.Model):
    __tablename__ = "risks"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, nullable=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    likelihood = db.Column(db.Integer, nullable=False)  # 1-5
    impact = db.Column(db.Integer, nullable=False)       # 1-5
    # Inherent (before mitigation) and residual (after mitigation) scores,
    # each 1-5, nullable: a risk created before this consolidation, or one
    # nobody has scored either way yet, has neither. Kept separate from the
    # legacy likelihood/impact pair above rather than repurposing it, so
    # every existing reader of likelihood/impact (the heat map, the register
    # table, risk_detail_modal.html) keeps working unchanged. Each write goes
    # through risk_service.set_risk_score, which also appends a
    # RiskScoreHistory row (app/models/risk_score_history.py) — the score is
    # stored, not only displayed.
    inherent_likelihood = db.Column(db.Integer, nullable=True)
    inherent_impact = db.Column(db.Integer, nullable=True)
    residual_likelihood = db.Column(db.Integer, nullable=True)
    residual_impact = db.Column(db.Integer, nullable=True)
    status = db.Column(db.Enum(RiskStatus), default=RiskStatus.OPEN, nullable=False)
    owner = db.Column(db.String(128), nullable=True)
    mitigation_plan = db.Column(db.Text, nullable=True)
    # The ArchiMate mirror of this row. Added 31 Aug 2026: CLAUDE.md requires
    # every motivation entity to have a matching ArchiMateElement, but this
    # model had nowhere to record one, so its creation paths could never
    # comply. Nullable and SET NULL so reconcile-schema can add it to
    # existing databases; populated automatically by
    # app/services/archimate_backbone.py.
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="SET NULL"),
        index=True, nullable=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def risk_score(self):
        return self.likelihood * self.impact

    @property
    def _effective_score(self):
        """The score used for the risk_level badge: residual when available,
        falling back to inherent, then to the base likelihood×impact pair."""
        if self.residual_likelihood is not None and self.residual_impact is not None:
            return self.residual_likelihood * self.residual_impact
        if self.inherent_likelihood is not None and self.inherent_impact is not None:
            return self.inherent_likelihood * self.inherent_impact
        return self.likelihood * self.impact

    @property
    def risk_level(self):
        s = self._effective_score
        if s >= 15:
            return "critical"
        if s >= 9:
            return "high"
        if s >= 5:
            return "medium"
        return "low"

    def to_dict(self):
        return {
            "id": self.id,
            "solution_id": self.solution_id,
            "title": self.title,
            "description": self.description,
            "likelihood": self.likelihood,
            "impact": self.impact,
            "inherent_likelihood": self.inherent_likelihood,
            "inherent_impact": self.inherent_impact,
            "residual_likelihood": self.residual_likelihood,
            "residual_impact": self.residual_impact,
            "status": self.status.value,
            "owner": self.owner,
            "mitigation_plan": self.mitigation_plan,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
