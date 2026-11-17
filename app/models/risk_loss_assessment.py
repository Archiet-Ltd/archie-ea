"""The Open FAIR factors a person entered for one risk.

The money result is not kept here: it is written as cost facts (see
``app/services/open_fair.py``), so every screen reads the one figure. This row
keeps what a fact cannot: the ranges entered, who entered them and when.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class RiskLossAssessment(TenantMixin, db.Model):
    __tablename__ = "risk_loss_assessments"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "risk_id", name="uq_risk_loss_assessment_risk"),
    )

    id = db.Column(db.Integer, primary_key=True)
    risk_id = db.Column(
        db.Integer, db.ForeignKey("risks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # {"frequency": [min, likely, max], "vulnerability": [...], "direct_loss": [...],
    #  "knock_on_loss": [...]}
    factors = db.Column(db.JSON, nullable=False)
    trials = db.Column(db.Integer, nullable=False)
    seed = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    entered_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    entered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    risk = db.relationship("Risk", backref=db.backref("loss_assessment", uselist=False,
                                                      cascade="all, delete-orphan"))
