"""Purpose-led architecture journey aggregate.

An architecture journey is independent of a Solution.  It may finish with an
architecture document, a governed decision, a transformation roadmap, a
programme, a solution, or no implementation outcome at all.
"""

from datetime import datetime

from app import db
from app.models.mixins import OptimisticLockMixin, TenantMixin


ARCHITECTURE_LAYERS = (
    "motivation",
    "strategy",
    "business",
    "data",
    "application",
    "technology",
    "implementation",
    "governance",
)

JOURNEY_INTENTS = (
    "business_transformation",
    "operating_model",
    "strategy_to_execution",
    "portfolio_change",
    "risk_and_compliance",
    "architecture_assessment",
    "solution_design",
)

OUTCOME_TYPES = (
    "architecture_only",
    "no_change_recommended",
    "decision",
    "roadmap",
    "programme",
    "solution",
    "undecided",
)

JOURNEY_STAGES = ("frame", "discover", "shape", "decide", "deliver")
JOURNEY_STATUSES = ("active", "completed", "archived")


class ArchitectureJourney(TenantMixin, OptimisticLockMixin, db.Model):
    """Durable, resumable architecture work with an optional Solution outcome."""

    __tablename__ = "architecture_journeys"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title = db.Column(db.String(255), nullable=False)
    intent = db.Column(db.String(40), nullable=False)
    selected_layers = db.Column(
        db.JSON, nullable=False, default=list, server_default=db.text("'[]'::json")
    )
    evidence_manifest = db.Column(
        db.JSON, nullable=False, default=list, server_default=db.text("'[]'::json")
    )
    selected_deliverables = db.Column(
        db.JSON, nullable=False, default=list, server_default=db.text("'[]'::json")
    )
    outcome_type = db.Column(db.String(30), nullable=False, default="undecided", server_default="undecided")
    journey_state = db.Column(
        db.JSON, nullable=False, default=dict, server_default=db.text("'{}'::json")
    )
    current_stage = db.Column(db.String(40), nullable=False, default="frame", server_default="frame")
    status = db.Column(db.String(24), nullable=False, default="active", server_default="active")
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    programme_id = db.Column(
        db.Integer,
        db.ForeignKey("strategic_initiatives.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # R1-04/ADR 0012 decision 6: the type is stored once, on the journey, not
    # copied onto the programme. No DB CHECK -- the offered-types set changes
    # as templates are reviewed, and reconcile-schema cannot widen a CHECK.
    # Written once at start; never repointed to a different type afterwards.
    programme_type = db.Column(db.String(40), nullable=True)
    # The next two are written by R1-06 at confirm time (a snapshot of the
    # template used to instantiate, ADR 0012 decision 4); declared here so one
    # reconcile-schema pass covers all three new columns.
    programme_template_version = db.Column(db.String(64), nullable=True)
    arb_required_at_decide = db.Column(db.Boolean, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    owner = db.relationship("User", foreign_keys=[owner_id])
    solution = db.relationship("Solution", foreign_keys=[solution_id])
    programme = db.relationship("StrategicInitiative", foreign_keys=[programme_id])

    __table_args__ = (
        db.CheckConstraint(
            f"intent IN ({', '.join(repr(value) for value in JOURNEY_INTENTS)})",
            name="ck_architecture_journey_intent",
        ),
        db.CheckConstraint(
            f"outcome_type IN ({', '.join(repr(value) for value in OUTCOME_TYPES)})",
            name="ck_architecture_journey_outcome",
        ),
        db.CheckConstraint(
            f"current_stage IN ({', '.join(repr(value) for value in JOURNEY_STAGES)})",
            name="ck_architecture_journey_stage",
        ),
        db.CheckConstraint(
            f"status IN ({', '.join(repr(value) for value in JOURNEY_STATUSES)})",
            name="ck_architecture_journey_status",
        ),
    )

    @property
    def resume_path(self):
        return f"/architecture-journey/work/{self.id}"


__all__ = [
    "ARCHITECTURE_LAYERS",
    "JOURNEY_INTENTS",
    "JOURNEY_STAGES",
    "JOURNEY_STATUSES",
    "OUTCOME_TYPES",
    "ArchitectureJourney",
]
