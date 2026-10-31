"""
ArchiMate 3.2 Strategy Layer Models

Completes the Strategy layer with missing element types:
- Resource: Strategic resources (assets, capabilities)
- CourseOfAction: Strategic actions/programs
- ValueStream: End-to-end value creation flows

Integrates with existing BusinessCapability model for complete strategy modeling.
"""

from sqlalchemy import CheckConstraint, event  # dead-code-ok
from sqlalchemy.orm import validates  # dead-code-ok

from app.models.mixins import TenantMixin
from app.models.mixins.core import _default_org_id

from .. import db


class StrategyResource(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Resource - Strategic resource (human, information, tangible)

    Represents assets or capabilities that are strategically important.
    Maps to ArchiMate Strategy Layer 'Resource' element.

    Examples:
    - Brand reputation
    - Intellectual property
    - Skilled workforce
    - Customer relationships
    - Technology platforms
    """

    __tablename__ = "strategy_resources"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    code = db.Column(db.String(50), unique=True, index=True)
    description = db.Column(db.Text)

    # Resource classification
    resource_type = db.Column(db.String(50))  # human, information, technology, financial, physical
    resource_category = db.Column(db.String(100))  # asset, capability, competency

    # Strategic importance
    strategic_value = db.Column(db.String(20))  # critical, high, medium, low
    competitive_advantage = db.Column(db.Boolean, default=False)  # Differentiating resource?
    scarcity_level = db.Column(db.String(20))  # rare, common, abundant

    # Resource characteristics
    is_tangible = db.Column(db.Boolean, default=True)
    is_renewable = db.Column(db.Boolean, default=False)
    lifecycle_stage = db.Column(db.String(50))  # emerging, mature, declining

    # Ownership & governance
    owning_organization_id = db.Column(db.Integer, db.ForeignKey("business_actors.id"))
    custodian_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # ArchiMate integration
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    archimate_layer = db.Column(db.String(20), default="Strategy")

    # Override TenantMixin's normally non-nullable column for an ADD-only
    # rollout: the 20261018_typed_tables_org_id migration adds this to an
    # existing table and leaves a row NULL when no organisation is derivable
    # from its linked parents. The mixin is still essential: tenant
    # middleware identifies tenant-owned models by it and applies the
    # automatic query fence.
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        default=_default_org_id,
    )

    # Metrics
    utilization_percentage = db.Column(db.Float)
    investment_required = db.Column(db.Numeric(15, 2))
    annual_cost = db.Column(db.Numeric(15, 2))
    roi_percentage = db.Column(db.Float)

    # Audit fields
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    owning_organization = db.relationship("BusinessActor", foreign_keys=[owning_organization_id])
    custodian = db.relationship("User", foreign_keys=[custodian_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<StrategyResource {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "resource_type": self.resource_type,
            "resource_category": self.resource_category,
            "strategic_value": self.strategic_value,
            "competitive_advantage": self.competitive_advantage,
            "archimate_element_id": self.archimate_element_id,
        }


class CourseOfAction(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Course of Action - Strategic action or approach

    Represents an approach or plan for achieving goals.
    Maps to ArchiMate Strategy Layer 'Course of Action' element.

    Examples:
    - Cloud migration strategy
    - Digital transformation program
    - Market expansion initiative
    - Cost reduction campaign
    - Merger integration approach
    """

    __tablename__ = "courses_of_action"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    code = db.Column(db.String(50), unique=True, index=True)
    description = db.Column(db.Text)

    # Action classification
    action_type = db.Column(db.String(50))  # program, initiative, campaign, approach
    strategic_theme = db.Column(db.String(100))  # growth, efficiency, innovation, transformation
    scope = db.Column(db.String(50))  # enterprise, division, department

    # Timeline
    start_date = db.Column(db.Date)
    target_date = db.Column(db.Date)
    duration_months = db.Column(db.Integer)
    current_phase = db.Column(db.String(50))  # planning, execution, monitoring, complete

    # Strategy alignment
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id"))  # Link to Goal
    outcome_id = db.Column(db.Integer, db.ForeignKey("outcomes.id"))  # Expected outcome

    # Resources required
    required_resource_ids = db.Column(db.Text)  # JSON array of strategy_resource IDs
    estimated_budget = db.Column(db.Numeric(15, 2))
    allocated_budget = db.Column(db.Numeric(15, 2))

    # Risk assessment
    risk_level = db.Column(db.String(20))  # low, medium, high, critical
    success_probability = db.Column(db.Float)  # 0 - 100%
    mitigation_strategy = db.Column(db.Text)

    # Governance
    sponsor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    owner_id = db.Column(db.Integer, db.ForeignKey("business_actors.id"))
    approval_status = db.Column(
        db.String(20), default="draft"
    )  # draft, approved, active, suspended, complete

    # ArchiMate integration
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    archimate_layer = db.Column(db.String(20), default="Strategy")

    # Performance tracking
    progress_percentage = db.Column(db.Float, default=0.0)
    benefits_realized = db.Column(db.Numeric(15, 2))
    roi_percentage = db.Column(db.Float)

    # Audit fields
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    goal = db.relationship("Goal", foreign_keys=[goal_id])
    outcome = db.relationship("Outcome", foreign_keys=[outcome_id])
    sponsor = db.relationship("User", foreign_keys=[sponsor_id])
    owner = db.relationship("BusinessActor", foreign_keys=[owner_id])
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<CourseOfAction {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "action_type": self.action_type,
            "strategic_theme": self.strategic_theme,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "risk_level": self.risk_level,
            "approval_status": self.approval_status,
            "progress_percentage": self.progress_percentage,
            "archimate_element_id": self.archimate_element_id,
        }


# Import ValueStreamStage from unified_capability to avoid duplication
# Import ValueStream from unified_capability to avoid duplication
from app.models.unified_capability import ValueStream, ValueStreamStage  # dead-code-ok


# Auto-create ArchiMate elements when strategy models are created.
#
# These run as mapper ``after_insert`` events, i.e. INSIDE the unit-of-work flush.
# They previously called ``db.session.add()`` + ``db.session.flush()`` — a reentrant
# flush that raises "Session is already flushing" and poisons the whole transaction
# (UIQA-005: broke document-upload element creation and every multi-object flush that
# inserted a CourseOfAction/StrategyResource/ValueStream without a preset FK). The
# correct pattern is to write through the event's own ``connection`` (Core, not the
# session) and reflect the new FK with ``set_committed_value`` so no further flush is
# provoked. A Core insert does NOT re-fire ORM mapper events, so there is no cascade.
def _link_strategy_archimate(connection, target, element_type, desc_prefix):
    from sqlalchemy.orm.attributes import set_committed_value

    from app.models.models import ArchiMateElement

    archimate_table = ArchiMateElement.__table__
    # ArchiMateElement.name is String(100); several strategy models (notably
    # BusinessCapability) allow String(256), so a long name would raise on
    # insert and poison the flush. Truncate with an ellipsis and keep the full
    # name recoverable — the element IS the field, so it must never fail to be
    # created because the field was long.
    name = target.name or ""
    stored_name = name if len(name) <= 100 else name[:99] + "…"
    values = {
        "name": stored_name,
        "type": element_type,
        "layer": "Strategy",
        "description": target.description or f"{desc_prefix}: {name}",
    }
    org_id = getattr(target, "organization_id", None)
    if org_id is not None:
        values["organization_id"] = org_id

    result = connection.execute(archimate_table.insert().values(**values))
    new_id = result.inserted_primary_key[0]
    # The mirror is written with a Core insert, which fires no mapper event, so its first version is
    # recorded here: the element then has its create entry in the change history like any other.
    from app.middleware.audit_middleware import record_create

    # reuse-ok: a transient instance, never added to a session, that only describes the row the Core insert above wrote
    record_create(connection, "archimate_elements", ArchiMateElement(id=new_id, **values))

    connection.execute(
        target.__table__.update()
        .where(target.__table__.c.id == target.id)
        .values(archimate_element_id=new_id)
    )
    # keep the in-memory object consistent without marking it dirty (no re-flush)
    set_committed_value(target, "archimate_element_id", new_id)


@event.listens_for(StrategyResource, "after_insert")
def create_archimate_resource(mapper, connection, target):
    """Automatically create ArchiMateElement for StrategyResource"""
    if not target.archimate_element_id:
        _link_strategy_archimate(connection, target, "Resource", "Strategic resource")


@event.listens_for(CourseOfAction, "after_insert")
def create_archimate_course_of_action(mapper, connection, target):
    """Automatically create ArchiMateElement for CourseOfAction"""
    if not target.archimate_element_id:
        _link_strategy_archimate(connection, target, "CourseOfAction", "Strategic action")


@event.listens_for(ValueStream, "after_insert")
def create_archimate_value_stream(mapper, connection, target):
    """Automatically create ArchiMateElement for ValueStream"""
    if not target.archimate_element_id:
        _link_strategy_archimate(connection, target, "ValueStream", "Value stream")


# NOTE: BusinessCapability already has its OWN before_insert listener
# (create_capability_archimate_element in app/models/business_capabilities.py) that
# mirrors every capability into a Strategy-layer Capability element. It is the single
# source of that sync — do NOT add a competing one here.
