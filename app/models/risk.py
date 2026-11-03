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
    def _score_basis(self):
        """The one selection of the pair a risk is scored on: ("Residual" | "Inherent", likelihood, impact).
        Residual when both residual values are recorded, then the inherent pair, then the base
        likelihood and impact (which is an inherent score)."""
        if self.residual_likelihood is not None and self.residual_impact is not None:
            return "Residual", self.residual_likelihood, self.residual_impact
        if self.inherent_likelihood is not None and self.inherent_impact is not None:
            return "Inherent", self.inherent_likelihood, self.inherent_impact
        return "Inherent", self.likelihood, self.impact

    @property
    def score_kind(self):
        """"Residual" or "Inherent": the name of the pair the score and the band come from."""
        return self._score_basis[0]

    @property
    def score_word(self):
        """"recorded score" when only the base pair exists, else "residual score" or "inherent score"."""
        if self.residual_likelihood is None and self.inherent_likelihood is None:
            return "recorded score"
        return "%s score" % self.score_kind.lower()

    @property
    def score_pair_text(self):
        """The pair the score is computed from, in words, named when it is not the base pair."""
        _kind, likelihood, impact = self._score_basis
        words = "likelihood %d of 5, impact %d of 5" % (likelihood, impact)
        if self.score_word == "recorded score":
            return words
        return "%s %s" % (self.score_kind.lower(), words)

    @property
    def _effective_score(self):
        """The score used for the risk_level badge: residual when available,
        falling back to inherent, then to the base likelihood×impact pair."""
        _kind, likelihood, impact = self._score_basis
        return likelihood * impact

    @property
    def score_text(self):
        """The effective score with the name of the pair it comes from ("Residual 6", "Inherent 10"),
        so the level beside it can be checked against the number it bands."""
        return "%s %d" % (self.score_kind, self._effective_score)

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
            "risk_score": self._effective_score,
            "score_text": self.score_text,
            "risk_level": self.risk_level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
