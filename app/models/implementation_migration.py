"""
Implementation & Migration layer domain models.

These models provide full coverage for the ArchiMate 3.2 Implementation &
Migration layer and enable roadmap-generation options:
- Work Packages and Deliverables for execution tracking
- Implementation Events for milestone alignment
- Plateaus for time-phased architectural snapshots
- Gaps for current/target state analysis

Each model maintains a foreign key to `archimate_elements` to preserve
traceability with the core metamodel and uses `architecture_models` for
scoping.
"""

from __future__ import annotations  # dead-code-ok

from datetime import date, datetime  # dead-code-ok
from typing import Optional  # dead-code-ok

# Use db.relationship instead of importing relationship
from sqlalchemy import event, select, update
from sqlalchemy.orm import foreign

from app.datetime_helpers import utcnow

from app.models.mixins import TenantMixin

from .. import db


class TechnologyRoadmapInitiative(db.Model):
    """ENH-013: Multi-year technology roadmap initiative.

    Represents a planned initiative spanning one or more fiscal years,
    tied to the organisation's technology evolution strategy.
    """

    __tablename__ = "technology_roadmap_initiatives"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)
    fiscal_year_start = db.Column(db.Integer, nullable=False)
    fiscal_year_end = db.Column(db.Integer, nullable=False)
    investment_budget = db.Column(db.Numeric(14, 2))
    status = db.Column(
        db.String(30), nullable=False, default="planned",
    )  # planned, in_progress, completed, cancelled
    category = db.Column(db.String(100))  # e.g. Cloud Migration, Legacy Retirement
    owner = db.Column(db.String(200))
    architecture_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id", ondelete="SET NULL"), index=True
    )
    solution_id = db.Column(
        db.Integer, db.ForeignKey("solutions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # this store is retired into unified_work_packages (never dropped).
    # NULL until `merge-work-package-stores` copies the row across.
    retired_into_id = db.Column(
        db.BigInteger,
        db.ForeignKey("unified_work_packages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Set when the row is copied across (merge or bridge). A unified copy that
    # is later deleted nulls retired_into_id (ON DELETE SET NULL) but leaves
    # this set, so the row stays deleted instead of being merged again.
    retired_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "fiscal_year_start": self.fiscal_year_start,
            "fiscal_year_end": self.fiscal_year_end,
            "investment_budget": float(self.investment_budget) if self.investment_budget else None,
            "status": self.status,
            "category": self.category or "",
            "owner": self.owner or "",
            "solution_id": self.solution_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WorkPackage(TenantMixin, db.Model):
    """Represents a discrete unit of work with defined deliverables."""

    __tablename__ = "work_packages"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    summary = db.Column(db.String(512))
    description = db.Column(db.Text)

    architecture_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id", ondelete="SET NULL"), index=True
    )
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="SET NULL"), index=True
    )
    # Link to Application Component (NEW - enables proper querying)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id", ondelete="CASCADE"), index=True
    )

    # Link to Enterprise Initiative (Unification of Work Packages)
    enterprise_initiative_id = db.Column(
        db.Integer, db.ForeignKey("enterprise_initiatives.id", ondelete="SET NULL"), index=True
    )
    strategic_initiative_id = db.Column(
        db.Integer,
        db.ForeignKey("strategic_initiatives.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    programme_workstream_id = db.Column(
        db.Integer,
        db.ForeignKey("programme_workstreams.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # Task 5 installs the FK when decision_brief_versions becomes canonical.
    decision_brief_version_id = db.Column(db.Integer, nullable=True, index=True)
    materialisation_key = db.Column(db.String(64), nullable=True)

    # Link to Goal (WorkPackages realize Goals)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="SET NULL"), index=True)

    # Link to BusinessEvent (WorkPackages triggered by BusinessEvents)
    triggering_business_event_id = db.Column(
        db.Integer, db.ForeignKey("business_events.id", ondelete="SET NULL"), index=True
    )

    # Multi-level architecture context
    context = db.Column(db.String(20), default="architecture", nullable=False, index=True)
    context_id = db.Column(db.Integer, nullable=True)

    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    status = db.Column(db.String(30), default="planned", index=True)
    priority = db.Column(db.String(20), default="medium", index=True)
    togaf_phase = db.Column(db.String(64), index=True)

    start_date = db.Column(db.Date, index=True)
    target_date = db.Column(db.Date)
    completed_date = db.Column(db.Date)

    estimated_effort_hours = db.Column(db.Integer)
    actual_effort_hours = db.Column(db.Integer)

    sequence_order = db.Column(db.Integer, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("work_packages.id", ondelete="SET NULL"))

    # Hierarchy level for roadmap display (1=Initiative, 2=WorkPackage, 3=Task, 4=Subtask, 5=Activity)
    level = db.Column(db.Integer, default=1, index=True)

    # UI customization
    color = db.Column(db.String(7))  # Hex color (inherits from Gap if null)

    # Progress tracking
    percent_complete = db.Column(db.Integer, default=0)  # 0 - 100

    # Cost tracking
    estimated_cost = db.Column(db.Float)
    actual_cost = db.Column(db.Float)

    # Dependencies (JSON array of work_package_ids this depends on)
    dependencies = db.Column(db.JSON)

    # ArchiMate 3.2 Implementation & Migration links (RDM-021)
    plateau_id = db.Column(
        db.Integer, db.ForeignKey("plateaus.id", ondelete="SET NULL"), index=True
    )
    capability_id = db.Column(
        db.Integer, db.ForeignKey("unified_capabilities.id", ondelete="SET NULL"), index=True
    )
    element_type = db.Column(db.String(50))

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    # this store is retired into unified_work_packages (never dropped).
    # NULL until `merge-work-package-stores` copies the row across.
    retired_into_id = db.Column(
        db.BigInteger,
        db.ForeignKey("unified_work_packages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Set when the row is copied across (merge or bridge). A unified copy that
    # is later deleted nulls retired_into_id (ON DELETE SET NULL) but leaves
    # this set, so the row stays deleted instead of being merged again.
    retired_at = db.Column(db.DateTime, nullable=True)

    architecture = db.relationship("ArchitectureModel", backref="migration_work_packages")
    archimate_element = db.relationship(
        "ArchiMateElement", backref="migration_work_package", foreign_keys=[archimate_element_id]
    )
    application_component = db.relationship(
        "ApplicationComponent",
        backref="migration_work_packages",
        foreign_keys=[application_component_id],
    )
    owner = db.relationship("User", backref="owned_migration_work_packages")
    enterprise_initiative = db.relationship(
        "EnterpriseInitiative", backref="migration_work_packages"
    )
    strategic_initiative = db.relationship(
        "StrategicInitiative", foreign_keys=[strategic_initiative_id]
    )
    programme_workstream = db.relationship(
        "ProgrammeWorkstream", foreign_keys=[programme_workstream_id]
    )
    goal = db.relationship("Goal", backref="migration_work_packages")
    triggering_event = db.relationship(
        "BusinessEvent",
        foreign_keys=[triggering_business_event_id],
        backref="triggered_work_packages",
    )
    parent = db.relationship("WorkPackage", remote_side=[id], backref="child_packages")
    plateau = db.relationship(
        "Plateau", foreign_keys=[plateau_id], backref="linked_work_packages"
    )
    capability = db.relationship(
        "UnifiedCapability", foreign_keys=[capability_id], backref="linked_work_packages"
    )

    __table_args__ = (
        db.Index(
            "uq_work_package_materialisation",
            "organization_id",
            "materialisation_key",
            unique=True,
            postgresql_where=materialisation_key.isnot(None),
        ),
        {"extend_existing": True},
    )

    # Relationship defined after Deliverable class
    pass

    @property
    def duration_days(self):
        if self.start_date and self.target_date:
            return (self.target_date - self.start_date).days
        return None

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "strategic_initiative_id": self.strategic_initiative_id,
            "programme_workstream_id": self.programme_workstream_id,
            "decision_brief_version_id": self.decision_brief_version_id,
            "materialisation_key": self.materialisation_key,
            "summary": self.summary or "",
            "description": self.description or "",
            "status": self.status,
            "priority": self.priority,
            "togaf_phase": self.togaf_phase,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "estimated_cost": float(self.estimated_cost) if self.estimated_cost else None,
            "actual_cost": float(self.actual_cost) if self.actual_cost else None,
            "percent_complete": self.percent_complete or 0,
            "dependencies": self.dependencies or [],
            "plateau_id": self.plateau_id,
            "capability_id": self.capability_id,
            "element_type": self.element_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    gaps = db.relationship(
        "app.models.implementation_migration.Gap",
        secondary="gap_work_packages",
        back_populates="work_packages",
    )

    plateaus = db.relationship(
        "app.models.implementation_migration.Plateau",
        secondary="work_package_plateaus",
        back_populates="work_packages",
    )

    implementation_events = db.relationship(
        "app.models.implementation_migration.ImplementationEvent",
        secondary="work_package_events",
        back_populates="work_packages",
    )

    def is_overdue(self) -> bool:
        return bool(
            self.target_date
            and self.status not in {"completed", "cancelled"}
            and self.target_date < date.today()
        )

    def get_effective_color(self) -> str:
        """Get color, falling back to parent or gap color if not set."""
        if self.color:
            return self.color
        # Try parent work package
        if self.parent and self.parent.color:
            return self.parent.color
        # Try associated gap
        if self.gaps:
            gap = list(self.gaps)[0] if self.gaps else None
            if gap and gap.color:
                return gap.color
        # Default
        return "#3B82F6"

    def to_roadmap_dict(self, include_children: bool = False) -> dict:
        """Convert to dictionary for roadmap API responses."""
        data = {
            "id": self.id,
            "archimate_id": f"wp-{self.id}",
            "name": self.name,
            "summary": self.summary,
            "description": self.description,
            "level": self.level or 1,
            "parent_id": self.parent_id,
            "color": self.get_effective_color(),
            "status": self.status,
            "priority": self.priority,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.target_date.isoformat() if self.target_date else None,
            "completed_date": self.completed_date.isoformat() if self.completed_date else None,
            "percent_complete": self.percent_complete or 0,
            "estimated_effort_hours": self.estimated_effort_hours,
            "actual_effort_hours": self.actual_effort_hours,
            "estimated_cost": self.estimated_cost,
            "actual_cost": self.actual_cost,
            "owner_id": self.owner_id,
            "owner_name": (f"{self.owner.first_name or ''} {self.owner.last_name or ''}".strip() or self.owner.email) if self.owner else None,
            "is_overdue": self.is_overdue(),
            "gap_ids": [g.id for g in self.gaps] if self.gaps else [],
            "deliverable_count": self.deliverables.count() if self.deliverables else 0,
            "child_count": len(self.child_packages) if self.child_packages else 0,
            "dependencies": self.dependencies or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_children and self.child_packages:
            data["children"] = [
                child.to_roadmap_dict(include_children=True) for child in self.child_packages
            ]

        return data

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<WorkPackage {self.name} status={self.status}>"


class Deliverable(db.Model):
    """Specific outputs or results produced by work packages."""

    __tablename__ = "deliverables"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # Legacy key: only set when a work_packages row exists for this work
    # package. Nullable so a deliverable can be added to any work package;
    # unified_work_package_id below is the key every reader uses.
    work_package_id = db.Column(
        db.Integer,
        db.ForeignKey("work_packages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    unified_work_package_id = db.Column(
        db.BigInteger,
        db.ForeignKey("unified_work_packages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    architecture_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id", ondelete="SET NULL"), index=True
    )
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="SET NULL"), index=True
    )
    # Link to Application Component (NEW - enables proper querying)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id", ondelete="CASCADE"), index=True
    )

    # Link to Goal (Deliverables contribute to Goal achievement)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="SET NULL"), index=True)

    delivery_status = db.Column(db.String(30), default="planned", index=True)
    deliverable_type = db.Column(db.String(50))
    start_date = db.Column(db.Date)  # When work on deliverable begins
    target_date = db.Column(db.Date)  # Expected completion date
    delivered_date = db.Column(db.Date)  # Actual delivery date
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    artifact_references = db.Column(db.JSON)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    work_package = db.relationship(
        "WorkPackage", foreign_keys=[work_package_id], back_populates="deliverables"
    )
    architecture = db.relationship("ArchitectureModel", backref="migration_deliverables")
    archimate_element = db.relationship(
        "ArchiMateElement", backref="migration_deliverable", foreign_keys=[archimate_element_id]
    )
    assigned_user = db.relationship("User", backref="assigned_migration_deliverables")
    goal = db.relationship("Goal", backref="migration_deliverables")

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "delivery_status": self.delivery_status,
            "deliverable_type": self.deliverable_type,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "delivered_date": self.delivered_date.isoformat() if self.delivered_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def is_completed(self) -> bool:
        return self.delivery_status == "completed"

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Deliverable {self.name} status={self.delivery_status}>"


# Define WorkPackage.deliverables relationship after Deliverable is defined
# Use string reference for Deliverable and let SQLAlchemy auto-detect the foreign key
WorkPackage.deliverables = db.relationship(
    "Deliverable",
    back_populates="work_package",
    cascade="all, delete-orphan",
    lazy="dynamic",
)


class ImplementationEvent(TenantMixin, db.Model):
    """Milestones or significant occurrences during implementation."""

    __tablename__ = "implementation_events"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    architecture_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id", ondelete="SET NULL"), index=True
    )
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="SET NULL"), index=True
    )
    # Link to Application Component (enables proper querying)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id", ondelete="CASCADE"), index=True
    )

    event_type = db.Column(db.String(50), index=True)
    event_date = db.Column(db.Date)
    status = db.Column(db.String(30), default="planned", index=True)
    trigger_condition = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    architecture = db.relationship("ArchitectureModel", backref="implementation_events")
    archimate_element = db.relationship(
        "ArchiMateElement", backref="implementation_event", foreign_keys=[archimate_element_id]
    )
    application_component = db.relationship(
        "ApplicationComponent",
        backref="implementation_events",
        foreign_keys=[application_component_id],
    )

    work_packages = db.relationship(
        "WorkPackage",
        secondary="work_package_events",
        back_populates="implementation_events",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ImplementationEvent {self.name} date={self.event_date}>"


class Plateau(TenantMixin, db.Model):
    """Stable architectural state at a specific point in time."""

    __tablename__ = "plateaus"
    __table_args__ = (
        db.Index(
            "uq_plateau_initiative_scope",
            "initiative_id", "name", "sequence_order",
            unique=True,
            postgresql_where=db.text("initiative_id IS NOT NULL"),
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    architecture_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id", ondelete="SET NULL"), index=True
    )
    # Scopes a plateau to the initiative that provisioned it (e.g. the
    # Interface Register's As-Is/To-Be pair). Nullable: architecture-level
    # plateaus with no single owning initiative predate this column.
    # architecture_id alone is not a safe key -- N initiatives can share one
    # ArchitectureModel, so two initiatives keyed only on architecture_id
    # would silently resolve to the same plateau pair.
    initiative_id = db.Column(
        db.Integer,
        db.ForeignKey("technology_roadmap_initiatives.id", ondelete="SET NULL"),
        index=True,
    )
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="SET NULL"), index=True
    )
    # Link to Application Component (NEW - enables proper querying)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id", ondelete="CASCADE"), index=True
    )

    sequence_order = db.Column(db.Integer, index=True)
    target_date = db.Column(db.Date)
    state_summary = db.Column(db.JSON)

    baseline_plateau_id = db.Column(
        db.Integer, db.ForeignKey("plateaus.id", ondelete="SET NULL"), index=True
    )

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    architecture = db.relationship("ArchitectureModel", backref="plateaus")
    archimate_element = db.relationship(
        "ArchiMateElement", backref="plateau", foreign_keys=[archimate_element_id]
    )
    application_component = db.relationship(
        "ApplicationComponent", backref="plateaus", foreign_keys=[application_component_id]
    )
    baseline_plateau = db.relationship(
        "app.models.implementation_migration.Plateau",
        remote_side=[id],
        backref="transition_plateaus",
    )

    work_packages = db.relationship(
        "app.models.implementation_migration.WorkPackage",
        secondary="work_package_plateaus",
        back_populates="plateaus",
    )

    gaps = db.relationship(
        "app.models.implementation_migration.Gap",
        secondary="plateau_gaps",
        back_populates="plateaus",
    )

    capabilities = db.relationship(
        "BusinessCapability",
        secondary="plateau_capabilities",
        back_populates="plateaus",
    )

    linked_elements = db.relationship(
        "ArchiMateElement",
        secondary="plateau_archimate_elements",
        back_populates="plateau_links",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Plateau {self.name} order={self.sequence_order}>"


# Two different things have been called a "Gap" here, and the product showed
# both numbers on different screens without distinguishing them: the Capability
# Roadmap reported 173 gaps while Gap Analysis reported 0, and neither was
# wrong. Measured 31 Aug 2026 -- of 33 columns on this table, NINE are Work
# Package attributes (owner, estimated_effort_days, estimated_cost,
# target_resolution_date, resolution_status, priority, business_value, colour,
# estimated_start_date), and of 20 rows, ZERO had either plateau link set.
#
# So in practice every row is a capability shortfall with remediation planning
# attached. ArchiMate calls that an Assessment. An ArchiMate Gap is the
# difference between two Plateaus, and the product has never recorded one.
#
# `gap_kind` makes the distinction explicit rather than renaming a concept with
# hundreds of references. It is nullable with a default, so reconcile-schema can
# add it to an existing database (ADD-only, nullable-only -- see CLAUDE.md), and
# every existing row is correctly a capability_shortfall.
#
# It matters beyond the contradiction: the assistant now generates roadmaps from
# this table, and a roadmap made of capability shortfalls with no transition
# states is not a migration plan.
GAP_KIND_CAPABILITY_SHORTFALL = "capability_shortfall"
GAP_KIND_PLATEAU_TRANSITION = "plateau_transition"


def validate_gap_kind(gap) -> None:
    """A plateau transition needs the two plateaus it sits between.

    Enforced here rather than as a database constraint because deploys do not
    run Alembic and reconcile-schema is ADD-COLUMN-nullable-only (ADR-0002), so
    a CHECK constraint would exist on a fresh database and not on any existing
    one -- an invariant that holds in some deployments and not others is worse
    than one enforced in code everywhere.

    Raises ValueError, which the caller surfaces. Refusing is deliberate: a
    "plateau transition" with no plateaus is the ambiguity this field exists to
    remove, and accepting one would recreate it under a new name.
    """
    if getattr(gap, "gap_kind", None) != GAP_KIND_PLATEAU_TRANSITION:
        return
    if not getattr(gap, "originating_plateau_id", None) or not getattr(
        gap, "target_plateau_id", None
    ):
        raise ValueError(
            "A plateau_transition gap is the difference between two plateaus, "
            "so it needs both originating_plateau_id and target_plateau_id. "
            "If you mean a capability that is weaker than it needs to be, that "
            "is a capability_shortfall."
        )


class Gap(TenantMixin, db.Model):
    """A capability shortfall, or a difference between two plateaus.

    Which one is recorded in `gap_kind`. See the note above the class: these are
    different concepts that shared a name, and reporting them as one number is
    how two screens came to disagree.

    TenantMixin declares organization_id NOT NULL, but gaps merged from
    superseded stores (roadmap_gaps, implementation_gaps, compliance_gaps)
    cannot always be attributed to an organisation. Override the column to
    nullable so an unattributable merged row is quarantined
    (organization_id=NULL) -- invisible to every tenant, never a guess, since
    the tenant filter's equality comparison never matches a NULL. The
    gap_register_service.create_gap writer always requires an
    organisation_id, so direct writes are scoped.
    """

    __tablename__ = "gaps"
    __table_args__ = {"extend_existing": True}

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = db.Column(db.Integer, primary_key=True)
    gap_kind = db.Column(
        db.String(32),
        nullable=True,
        default=GAP_KIND_CAPABILITY_SHORTFALL,
        server_default=GAP_KIND_CAPABILITY_SHORTFALL,
        index=True,
    )
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    architecture_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id", ondelete="SET NULL"), index=True
    )
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="SET NULL"), index=True
    )
    # Link to Application Component (NEW - enables proper querying)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id", ondelete="CASCADE"), index=True
    )

    # Multi-level architecture context
    context = db.Column(db.String(20), default="architecture", nullable=False, index=True)
    context_id = db.Column(db.Integer, nullable=True)

    severity = db.Column(db.String(20), default="medium", index=True)
    impact = db.Column(db.String(20), default="medium", index=True)
    priority = db.Column(db.String(20), default="medium", index=True)

    current_state_ref = db.Column(db.String(255))
    target_state_ref = db.Column(db.String(255))

    resolution_status = db.Column(db.String(30), default="identified", index=True)
    resolved_at = db.Column(db.Date)

    originating_plateau_id = db.Column(
        db.Integer, db.ForeignKey("plateaus.id", ondelete="SET NULL"), index=True
    )
    target_plateau_id = db.Column(
        db.Integer, db.ForeignKey("plateaus.id", ondelete="SET NULL"), index=True
    )

    # Gap type classification (from auto-detection or manual)
    gap_type = db.Column(
        db.String(30), index=True
    )  # coverage, quality, retirement, modernization, custom
    gap_sub_types = db.Column(db.JSON)  # Array for multiple gap types: ['coverage', 'quality']

    # UI customization
    color = db.Column(db.String(7), default="#6B7280")  # Hex color for roadmap display

    # Source capability reference (polymorphic link to capability that has the gap)
    source_capability_type = db.Column(db.String(20), index=True)  # business, technical, process
    source_capability_id = db.Column(db.Integer, index=True)

    # Provenance for a row merged in from a superseded gap store (roadmap_gaps,
    # implementation_gaps, compliance_gaps -- see app/commands/consolidate_gaps.py).
    # NULL on a row created directly against this table. Together, unique per
    # source row (enforced by the merge's NOT EXISTS check, not a DB
    # constraint -- reconcile-schema is ADD-COLUMN-nullable-only, ADR 0002).
    source_table = db.Column(db.String(64), nullable=True, index=True)
    source_id = db.Column(db.Integer, nullable=True, index=True)

    # Timeline for roadmap display
    estimated_start_date = db.Column(db.Date)  # When gap resolution should begin
    target_resolution_date = db.Column(db.Date)  # Target date to resolve the gap

    # Additional roadmap fields
    owner = db.Column(db.String(100))  # Gap owner/responsible party
    estimated_effort_days = db.Column(db.Integer)  # Estimated effort to resolve
    estimated_cost = db.Column(db.Float)  # Estimated cost to resolve
    business_value = db.Column(db.String(20))  # high, medium, low - value of resolving

    # === AI / Auto-Detection Tracking ===
    # True when this gap was created by the auto-detection service, not manually
    auto_generated = db.Column(db.Boolean, default=False, nullable=False, index=True)
    # Source of auto-generation: 'maturity_delta' | 'coverage_analysis' | 'ai' | 'manual'
    generation_source = db.Column(db.String(50), nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    architecture = db.relationship("ArchitectureModel", backref="gaps")
    archimate_element = db.relationship(
        "ArchiMateElement", backref="gap", foreign_keys=[archimate_element_id]
    )
    application_component = db.relationship(
        "ApplicationComponent", backref="gaps", foreign_keys=[application_component_id]
    )
    originating_plateau = db.relationship(
        "Plateau", foreign_keys=[originating_plateau_id], backref="originating_gaps"
    )
    target_plateau = db.relationship(
        "Plateau", foreign_keys=[target_plateau_id], backref="targeted_gaps"
    )

    work_packages = db.relationship(
        "app.models.implementation_migration.WorkPackage",
        secondary="gap_work_packages",
        back_populates="gaps",
    )

    plateaus = db.relationship(
        "app.models.implementation_migration.Plateau",
        secondary="plateau_gaps",
        back_populates="gaps",
    )

    capabilities = db.relationship(
        "BusinessCapability",
        secondary="gap_capabilities",
        back_populates="gaps",
    )

    linked_elements = db.relationship(
        "ArchiMateElement",
        secondary="gap_archimate_elements",
        back_populates="gap_links",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Gap {self.name} priority={self.priority}>"

    def get_all_gap_types(self) -> list:
        """Get all gap types (primary + sub types)."""
        types = []
        if self.gap_type:
            types.append(self.gap_type)
        if self.gap_sub_types:
            types.extend([t for t in self.gap_sub_types if t not in types])
        return types

    def set_gap_types(self, gap_types: list):
        """Set gap types from a list (first becomes primary)."""
        if gap_types:
            self.gap_type = gap_types[0]
            self.gap_sub_types = gap_types[1:] if len(gap_types) > 1 else None
        else:
            self.gap_type = None
            self.gap_sub_types = None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "gap_type": self.gap_type,
            "priority": self.priority,
            "severity": self.severity,
            "resolution_status": self.resolution_status,
            "current_state_ref": self.current_state_ref,
            "target_state_ref": self.target_state_ref,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_roadmap_dict(self) -> dict:
        """Convert to dictionary for roadmap API responses."""
        return {
            "id": self.id,
            "archimate_id": f"gap-{self.id}",
            "name": self.name,
            "description": self.description,
            "gap_type": self.gap_type,
            "gap_types": self.get_all_gap_types(),
            "color": self.color or "#6B7280",
            "priority": self.priority,
            "severity": self.severity,
            "impact": self.impact,
            "resolution_status": self.resolution_status,
            "source_capability_type": self.source_capability_type,
            "source_capability_id": self.source_capability_id,
            "start_date": self.estimated_start_date.isoformat()
            if self.estimated_start_date
            else None,
            "end_date": self.target_resolution_date.isoformat()
            if self.target_resolution_date
            else None,
            "owner": self.owner,
            "estimated_effort_days": self.estimated_effort_days,
            "estimated_cost": self.estimated_cost,
            "business_value": self.business_value,
            "work_package_count": len(list(self.work_packages)) if self.work_packages else 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# validate_gap_kind (defined above, line ~550) had zero callers anywhere in the
# tree before Task 03 of the SAP S/4HANA Interface Register bucket -- a
# validator with no producer is the same defect as a store with no producer
# (CLAUDE.md, ADR 0008). Registered here, below the class it validates, as a
# real ORM listener so the invariant holds for every write path, not only the
# one service-layer caller (app/modules/interface_register/services/
# interface_gap_service.py) that also calls validate_gap_kind() explicitly so
# its ValueError reaches the user as an inline 4xx instead of surfacing from a
# flush deep inside a transaction. Zero behaviour change for existing data:
# validate_gap_kind() returns immediately unless gap_kind == 'plateau_transition',
# and nothing in the tree wrote that value before this task.
@event.listens_for(Gap, "before_insert")
@event.listens_for(Gap, "before_update")
def _enforce_gap_kind(mapper, connection, target):
    validate_gap_kind(target)


# ---------------------------------------------------------------------------
# Late binding of back-populated relationships to avoid circular imports.
# ---------------------------------------------------------------------------

from app.models.business_capabilities import BusinessCapability  # noqa: E402
from app.models.models import ArchiMateElement  # noqa: E402

BusinessCapability.plateaus = db.relationship(
    "app.models.implementation_migration.Plateau",
    secondary="plateau_capabilities",
    back_populates="capabilities",
)

BusinessCapability.gaps = db.relationship(
    "app.models.implementation_migration.Gap",
    secondary="gap_capabilities",
    back_populates="capabilities",
)


ArchiMateElement.plateau_links = db.relationship(
    "app.models.implementation_migration.Plateau",
    secondary="plateau_archimate_elements",
    back_populates="linked_elements",
)

ArchiMateElement.gap_links = db.relationship(
    "app.models.implementation_migration.Gap",
    secondary="gap_archimate_elements",
    back_populates="linked_elements",
)


# ============================================================================
# Event Listeners - Auto-update Gap resolution when WorkPackages complete
# ============================================================================


@event.listens_for(WorkPackage, "after_update")
def check_gap_resolution_on_work_package_update(mapper, connection, target):
    """Automatically check gap resolution when WorkPackage status changes."""
    if target.status == "completed":
        # Use connection to query gap_work_packages junction table
        from app.models.relationship_tables import gap_work_packages

        # Get all gaps linked to this work package
        gap_ids = connection.execute(
            select([gap_work_packages.c.gap_id]).where(
                gap_work_packages.c.work_package_id == target.id
            )
        ).fetchall()

        # For each gap, check if all work packages are completed
        for (gap_id,) in gap_ids:
            # Get all work package IDs for this gap
            wp_ids = connection.execute(
                select([gap_work_packages.c.work_package_id]).where(
                    gap_work_packages.c.gap_id == gap_id
                )
            ).fetchall()

            # Check if all work packages are completed
            all_completed = True
            for (wp_id,) in wp_ids:
                wp_status = connection.execute(
                    select([WorkPackage.__table__.c.status]).where(
                        WorkPackage.__table__.c.id == wp_id
                    )
                ).scalar()
                if wp_status != "completed":
                    all_completed = False
                    break

            # Mark gap as resolved if all work packages are done
            if all_completed:
                connection.execute(
                    update(Gap.__table__)
                    .where(Gap.__table__.c.id == gap_id)
                    .values(resolution_status="resolved", resolved_at=date.today())
                )


class MigrationWave(TenantMixin, db.Model):
    """A migration/transition wave grouping work packages by sequenced delivery."""

    __tablename__ = "migration_waves"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    wave_number = db.Column(db.Integer, nullable=False, default=1, index=True)
    description = db.Column(db.Text)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"), nullable=True, index=True)
    status = db.Column(db.String(30), default="planned")
    target_start_date = db.Column(db.Date)
    target_end_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "wave_number": self.wave_number,
            "description": self.description,
            "solution_id": self.solution_id,
            "status": self.status,
            "target_start_date": self.target_start_date.isoformat() if self.target_start_date else None,
            "target_end_date": self.target_end_date.isoformat() if self.target_end_date else None,
        }

    def __repr__(self):
        return f"<MigrationWave {self.wave_number}: {self.name}>"
