"""AI evaluation plan (R1-B55, TB-0331, PB-0234).

One plan per AI system per organisation: the named test set and the thresholds
a release check compares the latest run against. No evaluation or threshold
store existed before this table (searched: no ``Evaluation*`` model, no
``groundedness`` / ``task_success`` field in ``app``). The AI system itself is
the existing ``ArchiMateElement`` the AI systems register models, so
``ai_system_id`` is that element's id and nothing is copied from it.

``version`` rises whenever the test set or a threshold changes; a run records
the version it was made against, so a run made against an older plan never
satisfies the current one.
"""
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class AIEvaluationPlan(TenantMixin, db.Model):
    __tablename__ = "ai_evaluation_plan"

    id = db.Column(db.Integer, primary_key=True)
    ai_system_id = db.Column(  # nullable-ok: new table, created whole by init-db and its own revision
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    test_set_name = db.Column(db.String(200), nullable=False)  # nullable-ok: new table, created whole by init-db and its own revision
    test_set_size = db.Column(db.Integer, nullable=False)  # nullable-ok: new table, created whole by init-db and its own revision
    # {metric: number}; the direction (minimum or maximum) of each metric is
    # fixed by ai_evaluation_service.METRICS, not stored per plan.
    thresholds = db.Column(db.JSON, nullable=False, default=dict)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "ai_system_id", name="uq_ai_evaluation_plan_org_system"
        ),
    )
