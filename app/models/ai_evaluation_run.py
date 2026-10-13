"""AI evaluation run (R1-B55, TB-0331, PB-0234).

One recorded run of a plan's test set against an AI system: the measured values
per metric, the plan version it was made against and the model the system
recorded at that moment. The release check reads the latest run for the system
and compares it with the plan's thresholds.
"""
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class AIEvaluationRun(TenantMixin, db.Model):
    __tablename__ = "ai_evaluation_run"

    id = db.Column(db.Integer, primary_key=True)
    ai_system_id = db.Column(  # nullable-ok: new table, created whole by init-db and its own revision
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id = db.Column(  # nullable-ok: new table, created whole by init-db and its own revision
        db.Integer,
        db.ForeignKey("ai_evaluation_plan.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_version = db.Column(db.Integer, nullable=False)  # nullable-ok: new table, created whole by init-db and its own revision
    # The model id the system recorded when the run was entered; a later model
    # change makes this run not current for the release check.
    model_id = db.Column(db.String(200), nullable=True)
    # {metric: measured number}; only metrics that were measured appear.
    metrics = db.Column(db.JSON, nullable=False, default=dict)
    cases_run = db.Column(db.Integer, nullable=False)  # nullable-ok: new table, created whole by init-db and its own revision
    notes = db.Column(db.Text, nullable=True)
    recorded_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
