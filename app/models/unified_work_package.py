"""
Unified Work Package Model - Combines ArchiMate 3.2, Implementation, and Roadmap capabilities

This model serves as the single source of truth for all work package functionality,
providing both ArchiMate compliance and roadmap visualization capabilities.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import relationship

from .. import db
from .mixins import TenantMixin
from .mixins.core import _default_org_id


class UnifiedWorkPackage(TenantMixin, db.Model):
    """
    Unified Work Package Model - ArchiMate 3.2 Compliant with Roadmap Capabilities

    This model combines the functionality of:
    - ImplementationWorkPackage (ArchiMate 3.2)
    - WorkPackage (Implementation Migration)
    - RoadmapWorkPackage (Roadmap Visualization)

    Purpose: Single source of truth for all work package management
    """

    __tablename__ = "unified_work_packages"
    __table_args__ = (
        # One copy per row of a retired store; the merge and the session bridge
        # insert with ON CONFLICT DO NOTHING on this key.
        Index(
            "uq_unified_wp_source_copy", "source_table", "source_id",
            unique=True, postgresql_where=text("source_table IS NOT NULL"),
        ),
    )

    # === Primary Key ===
    id = Column(BigInteger, primary_key=True)

    # === Tenancy ===
    # This table predates TenantMixin: `flask reconcile-schema` can only ADD a
    # nullable column to a live table (ADR 0002), so organization_id has to stay
    # nullable at the ORM level too, or the model would disagree with the
    # database and trip the schema-drift gate. Isolation still keys on
    # isinstance(TenantMixin), not on nullability (same override as
    # EnterpriseInitiative in app/models/vendor/vendor_organization.py). A row
    # that cannot be attributed an organisation (see
    # backfill-work-package-org) is left NULL: the tenant filter's `=`
    # comparison then matches no organisation, which is the quarantine.
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        default=_default_org_id,
    )

    # === Core Attributes ===
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    documentation = Column(Text)

    # === ArchiMate 3.2 Compliance ===
    element_type = Column(String(50), default="WorkPackage")
    layer = Column(
        String(20), default="implementation"
    )  # business, application, technology, implementation

    # === Architecture Context (from WorkPackage model) ===
    # Multi-level architecture relationships
    archimate_element_id = Column(
        Integer, ForeignKey("archimate_elements.id", ondelete="SET NULL"), index=True
    )
    application_component_id = Column(
        Integer, ForeignKey("application_components.id", ondelete="CASCADE"), index=True
    )
    enterprise_initiative_id = Column(
        Integer, ForeignKey("enterprise_initiatives.id", ondelete="SET NULL"), index=True
    )
    goal_id = Column(Integer, ForeignKey("goals.id", ondelete="SET NULL"), index=True)
    triggering_business_event_id = Column(
        Integer, ForeignKey("business_events.id", ondelete="SET NULL"), index=True
    )

    # Context for multi-level architecture
    context = Column(String(20), default="architecture", nullable=False, index=True)
    context_id = Column(Integer, nullable=True)

    # === Enterprise create screen fields (R1-B04 PR 2, round 3) ===
    # Carried by the enterprise work package form; nullable, nothing is
    # backfilled. The screen's architecture is ``context_id`` above.
    summary = Column(Text, nullable=True)
    estimated_effort_hours = Column(Float, nullable=True)
    actual_effort_hours = Column(Float, nullable=True)
    level = Column(Integer, nullable=True)
    color = Column(String(20), nullable=True)

    # === Capability Context (from RoadmapWorkPackage) ===
    # Nullable: a row merged in from technology_roadmap_initiatives,
    # implementation_work_packages or work_packages may carry no capability
    # link at all. Forcing a value here would be inventing one; the merge
    # command drops the historical NOT NULL rather than fabricate a name.
    business_capability = Column(
        String(100), nullable=True, index=True
    )  # Primary/legacy capability name
    capability_id = Column(
        BigInteger, ForeignKey("unified_capabilities.id", ondelete="SET NULL"), index=True
    )  # Primary capability ID

    # === ArchiMate 3.2 Implementation & Migration Layer ===
    # Links this work package to the Plateau it targets (ArchiMate: WorkPackage realises Plateau)
    plateau_id = Column(
        Integer, ForeignKey("plateaus.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Links this work package to the Gap it resolves (ArchiMate: WorkPackage resolves Gap)
    gap_id = Column(
        Integer, ForeignKey("gaps.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Distinguishes enterprise capability roadmap WPs from application roadmap WPs
    scope = Column(String(20), default="enterprise", nullable=False, index=True)

    # === Multi-Capability Support ===
    capability_ids = Column(JSON)  # Array of capability IDs for multi-capability selection
    capability_names = Column(JSON)  # Array of capability names for display without joins

    # === Timeline and Planning ===
    start_date = Column(DateTime, index=True)
    end_date = Column(DateTime, index=True)
    duration_days = Column(Integer)  # Calculated field

    # === Progress and Status ===
    # 50 wide to match roadmap_work_packages.status, the widest store merged in.
    status = Column(
        String(50), default="planned", index=True
    )  # planned, in_progress, completed, cancelled, on_hold
    progress_percentage = Column(Float, default=0.0)

    # === Assignment and Responsibility ===
    assigned_to = Column(String(255), index=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))

    # === Priority and Risk ===
    priority = Column(String(20), default="medium", index=True)  # low, medium, high, critical
    risk_level = Column(String(20), default="medium", index=True)  # low, medium, high, critical
    risk_mitigation = Column(Text)

    # === Cost and Resources ===
    estimated_cost = Column(Float, default=0.0)
    actual_cost = Column(Float, default=0.0)
    budget_variance = Column(Float, default=0.0)  # Calculated field
    required_resources = Column(JSON)

    # === Dependencies and Relationships ===
    work_dependencies = Column(JSON)  # List of work package IDs this depends on
    prerequisites = Column(JSON)  # List of prerequisites

    # === TOGAF and Enterprise Architecture ===
    togaf_phase = Column(String(64), index=True)

    # === Automation and AI Features (from RoadmapWorkPackage) ===
    auto_generated = Column(Boolean, default=False, index=True)
    source_data = Column(Text)  # JSON string with source information
    source_type = Column(String(50))  # capability, gap, application, manual, ai
    source_id = Column(BigInteger)  # ID of source entity; see source_table below
    # === Consolidation provenance (ADR 0008 rule 2) ===
    # `source_table` names the pre-consolidation store a merged row came from
    # (work_packages, roadmap_work_packages, technology_roadmap_initiatives,
    # implementation_work_packages). A row merged this way carries both
    # source_table and source_id (that store's own primary key), so "where did
    # this row come from" is answered by query. A row that was never merged
    # (created directly here, or auto-generated from a capability/gap per the
    # source_type field above) leaves source_table NULL; source_id keeps its
    # pre-existing "AI generation source entity" meaning in that case, since
    # the two usages never occur on the same row.
    source_table = Column(String(128), index=True)
    confidence_score = Column(Float, default=1.0)
    generation_method = Column(String(100))  # AI, template, rule_based, manual
    complexity_score = Column(Float, default=1.0)

    # === Metadata and Auditing ===
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), index=True)
    updated_by = Column(Integer, ForeignKey("users.id"))

    # Set once work_dependencies hold unified ids (merge remap, or created by the
    # writer). Lets the merge's dependency remap run exactly once per row.
    dependencies_remapped_at = Column(DateTime, nullable=True)

    # Hierarchy (a child work package under a parent), carried from the
    # work_packages store; filled by the merge and the bridge.
    parent_id = Column(
        BigInteger, ForeignKey("unified_work_packages.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # === Sync and Automation Status ===
    last_sync_at = Column(DateTime)
    sync_status = Column(String(20), default="synced")  # synced, pending, error

    # === Relationships ===
    # Note: Relationships are optional to avoid circular imports
    # They can be added later when the related models are properly configured

    # === Methods ===
    def __repr__(self):
        return f"<UnifiedWorkPackage {self.name}>"

    def calculate_duration(self):
        """Calculate duration in days"""
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days
        return 0

    def calculate_budget_variance(self):
        """Calculate budget variance percentage"""
        if self.estimated_cost > 0:
            return ((self.actual_cost - self.estimated_cost) / self.estimated_cost) * 100
        return 0

    def is_overdue(self):
        """Check if work package is overdue"""
        if self.end_date and self.status not in ["completed", "cancelled"]:
            return datetime.utcnow() > self.end_date
        return False

    def get_critical_path_impact(self):
        """Calculate critical path impact based on dependencies"""
        # This would be implemented to calculate critical path impact
        return 0.0

    # === ArchiMate 3.2 Compliance Methods ===
    def to_archimate_json(self):
        """Export to ArchiMate 3.2 JSON format"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "documentation": self.documentation,
            "element_type": self.element_type,
            "layer": self.layer,
            "properties": {
                "start_date": self.start_date.isoformat() if self.start_date else None,
                "end_date": self.end_date.isoformat() if self.end_date else None,
                "status": self.status,
                "progress_percentage": self.progress_percentage,
                "assigned_to": self.assigned_to,
                "priority": self.priority,
                "estimated_cost": self.estimated_cost,
            },
        }

    @classmethod
    def from_archimate_json(cls, data, user_id=None):
        """Create from ArchiMate 3.2 JSON format"""
        wp = cls(
            name=data.get("name"),
            description=data.get("description"),
            documentation=data.get("documentation"),
            element_type=data.get("element_type", "WorkPackage"),
            layer=data.get("layer", "implementation"),
            created_by=user_id,
            updated_by=user_id,
        )

        # Set properties from ArchiMate format
        props = data.get("properties", {})
        wp.start_date = (
            datetime.fromisoformat(props["start_date"]) if props.get("start_date") else None
        )
        wp.end_date = datetime.fromisoformat(props["end_date"]) if props.get("end_date") else None
        wp.status = props.get("status", "planned")
        wp.progress_percentage = props.get("progress_percentage", 0.0)
        wp.assigned_to = props.get("assigned_to")
        wp.priority = props.get("priority", "medium")
        wp.estimated_cost = props.get("estimated_cost", 0.0)

        return wp
