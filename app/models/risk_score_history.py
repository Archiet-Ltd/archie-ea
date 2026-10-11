"""RiskScoreHistory — one row per inherent/residual score change on a Risk.

The score is stored, not only displayed. Before this, nothing in the
risk register persisted a score's history at all — a likelihood/impact
column could be overwritten with no trace of what it used to be. This mirrors
the existing history-row-per-change shape already used elsewhere in this
codebase (app/models/kanban_card_history.py: one row per column transition,
FK to the parent, no in-place mutation of past rows) rather than inventing a
new pattern; TenantMixin is added because unlike KanbanCardHistory this table
is queried directly by organisation (score history is per organisation is a
first-class acceptance check for this feature, not just reachable via a join
through its parent).

New table, ADD-only via init-db's create_all -- no existing-database impact,
the same precedent as RiskEntityLink's own docstring
(app/models/risk_entity_link.py).
"""
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin

#: A history row records either the inherent (pre-mitigation) or the
#: residual (post-mitigation) assessment. Closed set for the same reason
#: RiskEntityLink's ENTITY_TYPES is closed: an unconstrained string lets a
#: typo create a kind nothing ever reads back correctly.
SCORE_KINDS = ("inherent", "residual")


class RiskScoreHistory(TenantMixin, db.Model):
    """One recorded inherent/residual likelihood+impact pair for a Risk."""

    __tablename__ = "risk_score_history"
    __table_args__ = (
        db.CheckConstraint(
            "score_kind IN ('inherent', 'residual')",
            name="ck_risk_score_history_kind",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    risk_id = db.Column(
        db.Integer, db.ForeignKey("risks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score_kind = db.Column(db.String(20), nullable=False, index=True)  # SCORE_KINDS
    likelihood = db.Column(db.Integer, nullable=False)
    impact = db.Column(db.Integer, nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    recorded_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    risk = db.relationship(
        "Risk",
        backref=db.backref(
            "score_history",
            cascade="all, delete-orphan",
            order_by="RiskScoreHistory.recorded_at",
        ),
    )
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])

    def to_dict(self):
        return {
            "id": self.id,
            "risk_id": self.risk_id,
            "score_kind": self.score_kind,
            "likelihood": self.likelihood,
            "impact": self.impact,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }
