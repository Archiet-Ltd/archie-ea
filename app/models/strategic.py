"""
Strategic Module Database Models

Contains models for strategic planning and roadmap management:
- StrategicInitiative: Business/IT initiatives with budget tracking
- StrategicMilestone: Initiative milestones and deliverables
- RoadmapItem: Strategic roadmap entries for visualization
- CapabilityHealthOverride: Manual overrides for health scores with audit trail
"""

import json
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app import db
from app.models.mixins import TenantMixin

# StrategicInitiative <-> Goal (2 Sep 2026): before this, a programme's goal
# alignment was recorded as strategic_alignment — a JSON list of goal NAME
# strings on the initiative row, unqueryable as "every programme serving goal
# X" and silently divergent the moment a goal is renamed. A parallel, correct
# junction (initiative_goals) already existed for the SAME purpose but wired
# to EnterpriseInitiative, a second/legacy "programme" concept — leaving this,
# the aggregate root WorkPackage/Benefit/Solution already point at, with no
# real Goal linkage at all. This table is the fix for that root; the string
# column stays for now as a display fallback on old rows only.
strategic_initiative_goals = db.Table(
    "strategic_initiative_goals",
    db.Column(
        "strategic_initiative_id", db.Integer,
        db.ForeignKey("strategic_initiatives.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "goal_id", db.Integer, db.ForeignKey("goals.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("contribution_level", db.String(20)),  # 'primary', 'supporting', 'indirect'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)


class StrategicInitiative(TenantMixin, db.Model):
    """
    Strategic Initiative model representing business/IT initiatives.

    Tracks initiatives with budget, timeline, ownership, and strategic alignment.
    Supports ArchiMate 3.2 Strategy Layer concepts.
    """

    __tablename__ = "strategic_initiatives"
    __table_args__ = {"extend_existing": True}

    # Core fields
    id = Column(Integer, primary_key=True)
    name = Column(String(256), nullable=False, index=True)
    description = Column(Text)
    # Nullable by design: existing initiatives are not silently reclassified.
    record_kind = Column(String(40), nullable=True, index=True)

    # Status and priority
    status = Column(
        String(50), default="draft", index=True
    )  # draft, planning, in_progress, completed, cancelled
    priority = Column(String(20), default="medium", index=True)  # critical, high, medium, low

    # Timeline
    start_date = Column(Date)
    target_completion_date = Column(Date)
    actual_completion_date = Column(Date)

    # Budget tracking
    budget_allocated = Column(Float, default=0.0)
    budget_spent = Column(Float, default=0.0)

    # Ownership
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # Business value and risk
    business_value_score = Column(Integer, default=5)  # 1 - 10 scale
    risk_level = Column(String(20), default="medium")  # low, medium, high, critical

    # migration-exempt — schema evolves via scripts/sync_schema.py per the
    # CLAUDE.md migration freeze (no Alembic on this platform).
    # Transformation programme governance (PROG-001).
    # An initiative with initiative_type set acts as a Transformation Programme
    # grouping member Solutions (Solution.initiative_id) for rollup governance.
    initiative_type = Column(String(30), index=True)  # greenfield | brownfield
    target_platform = Column(String(100))  # e.g. "SAP S/4HANA", "Salesforce", "Microsoft Power Platform", "Custom"
    vendor_key = Column(String(50), index=True)  # aligns with VendorArchiMateTemplate/IntegrationPattern vendor keys (SAP, SALESFORCE, MICROSOFT_POWER, ...)
    clean_core_target = Column(Integer)  # governance target %, e.g. 70 — cockpit shows actual vs target (PROG-004)

    # Legacy display-only fallback — see strategic_initiative_goals above for
    # the real, queryable linkage. Not written to by new code.
    strategic_alignment = Column(Text)  # JSON list: ["goal1", "goal2"]

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    archived_at = Column(DateTime, nullable=True)
    revision = Column(Integer, nullable=False, default=1, server_default="1")

    # Relationships
    owner = relationship("User", backref="owned_strategic_initiatives", foreign_keys=[owner_id])
    milestones = relationship(
        "StrategicMilestone",
        back_populates="initiative",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    roadmap_items = relationship("RoadmapItem", back_populates="initiative", lazy="dynamic")
    goals = relationship("Goal", secondary=strategic_initiative_goals, backref="strategic_initiatives")
    raid_items = relationship("RaidItem", back_populates="strategic_initiative")

    def __repr__(self):
        return f"<StrategicInitiative {self.name}>"

    @property
    def budget_remaining(self) -> float:
        """Calculate remaining budget."""
        return (self.budget_allocated or 0.0) - (self.budget_spent or 0.0)

    @property
    def budget_utilization_percentage(self) -> float:
        """Calculate budget utilization as percentage."""
        if not self.budget_allocated or self.budget_allocated == 0:
            return 0.0
        return round((self.budget_spent or 0.0) / self.budget_allocated * 100, 2)

    @property
    def is_overdue(self) -> bool:
        """Check if initiative is past target completion date."""
        if self.target_completion_date and self.status not in ["completed", "cancelled"]:
            return datetime.now().date() > self.target_completion_date
        return False

    @property
    def completion_percentage(self) -> float:
        """Calculate completion percentage based on milestones."""
        total = self.milestones.count()
        if total == 0:
            return 0.0
        completed = self.milestones.filter_by(status="completed").count()
        return round(completed / total * 100, 2)

    def get_strategic_alignment_list(self) -> list:
        """Parse strategic alignment JSON to list."""
        if not self.strategic_alignment:
            return []
        try:
            return json.loads(self.strategic_alignment)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_strategic_alignment_list(self, goals: list):
        """Set strategic alignment from list."""
        self.strategic_alignment = json.dumps(goals) if goals else None

    def to_dict(self, include_milestones: bool = False) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "record_kind": self.record_kind,
            "status": self.status,
            "priority": self.priority,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "target_completion_date": self.target_completion_date.isoformat()
            if self.target_completion_date
            else None,
            "actual_completion_date": self.actual_completion_date.isoformat()
            if self.actual_completion_date
            else None,
            "budget_allocated": self.budget_allocated,
            "budget_spent": self.budget_spent,
            "budget_remaining": self.budget_remaining,
            "budget_utilization_percentage": self.budget_utilization_percentage,
            "owner_id": self.owner_id,
            "owner_name": self.owner.username if self.owner else None,
            "business_value_score": self.business_value_score,
            "risk_level": self.risk_level,
            "strategic_alignment": self.get_strategic_alignment_list(),
            "is_overdue": self.is_overdue,
            "completion_percentage": self.completion_percentage,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_milestones:
            result["milestones"] = [m.to_dict() for m in self.milestones.all()]
            result["milestone_count"] = self.milestones.count()

        return result


class StrategicMilestone(TenantMixin, db.Model):
    """
    Milestone model for tracking initiative milestones.

    Represents key deliverables and checkpoints within strategic initiatives.
    """

    __tablename__ = "strategic_milestones"
    __table_args__ = {"extend_existing": True}

    # Core fields
    id = Column(Integer, primary_key=True)
    initiative_id = Column(
        Integer, ForeignKey("strategic_initiatives.id"), nullable=False, index=True
    )
    name = Column(String(256), nullable=False, index=True)
    description = Column(Text)

    # Timeline
    due_date = Column(Date)
    completed_date = Column(Date)

    # Status
    status = Column(
        String(50), default="pending", index=True
    )  # pending, in_progress, completed, blocked

    # Deliverables - stored as JSON list
    deliverables = Column(Text)  # JSON list: ["deliverable1", "deliverable2"]

    # Dependencies - stored as JSON list of milestone IDs
    dependencies = Column(Text)  # JSON list: [1, 2, 3]

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    initiative = relationship("StrategicInitiative", back_populates="milestones")

    def __repr__(self):
        return f"<Milestone {self.name}>"

    @property
    def is_overdue(self) -> bool:
        """Check if milestone is past due date."""
        if self.due_date and self.status not in ["completed"]:
            return datetime.now().date() > self.due_date
        return False

    @property
    def days_until_due(self) -> int:
        """Calculate days until due date (negative if overdue)."""
        if not self.due_date:
            return 0
        delta = self.due_date - datetime.now().date()
        return delta.days

    def get_deliverables_list(self) -> list:
        """Parse deliverables JSON to list."""
        if not self.deliverables:
            return []
        try:
            return json.loads(self.deliverables)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_deliverables_list(self, items: list):
        """Set deliverables from list."""
        self.deliverables = json.dumps(items) if items else None

    def get_dependencies_list(self) -> list:
        """Parse dependencies JSON to list of milestone IDs."""
        if not self.dependencies:
            return []
        try:
            return json.loads(self.dependencies)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_dependencies_list(self, milestone_ids: list):
        """Set dependencies from list of milestone IDs."""
        self.dependencies = json.dumps(milestone_ids) if milestone_ids else None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "initiative_id": self.initiative_id,
            "initiative_name": self.initiative.name if self.initiative else None,
            "name": self.name,
            "description": self.description,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "completed_date": self.completed_date.isoformat() if self.completed_date else None,
            "status": self.status,
            "deliverables": self.get_deliverables_list(),
            "dependencies": self.get_dependencies_list(),
            "is_overdue": self.is_overdue,
            "days_until_due": self.days_until_due,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RoadmapItem(TenantMixin, db.Model):
    """
    Roadmap Item model for strategic roadmap visualization.

    Supports swimlane roadmap views with quarterly planning,
    linking to initiatives, capabilities, and applications.
    """

    __tablename__ = "strategic_roadmap_items"
    # Core fields
    id = Column(Integer, primary_key=True)
    initiative_id = Column(
        Integer,
        ForeignKey("strategic_initiatives.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    programme_workstream_id = Column(
        Integer,
        ForeignKey("programme_workstreams.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    work_package_id = Column(
        Integer,
        ForeignKey("work_packages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # Task 5 installs the FK when decision_brief_versions becomes canonical.
    decision_brief_version_id = Column(Integer, nullable=True, index=True)
    materialisation_key = Column(String(64), nullable=True)
    title = Column(String(256), nullable=False, index=True)
    description = Column(Text)

    # Category and lane
    category = Column(
        String(50), default="technology", index=True
    )  # capability, technology, process, organization
    lane = Column(
        String(50), default="application", index=True
    )  # business, application, technology, infrastructure

    # Timeline
    quarter = Column(String(10), index=True)  # Q1 2026, Q2 2026, etc.
    year = Column(Integer, index=True)

    # Status and effort
    status = Column(
        String(50), default="planned", index=True
    )  # planned, in_progress, completed, deferred
    effort_estimate = Column(String(20), default="medium")  # small, medium, large, extra_large

    # Dependencies - stored as JSON list of roadmap item IDs
    dependencies = Column(Text)  # JSON list: [1, 2, 3]

    # Linked entities - stored as JSON lists
    linked_capabilities = Column(Text)  # JSON list of capability IDs
    linked_applications = Column(Text)  # JSON list of application IDs

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    initiative = relationship("StrategicInitiative", back_populates="roadmap_items")
    programme_workstream = relationship(
        "ProgrammeWorkstream", foreign_keys=[programme_workstream_id]
    )
    work_package = relationship("WorkPackage", foreign_keys=[work_package_id])

    __table_args__ = (
        db.Index(
            "uq_roadmap_item_materialisation",
            "organization_id",
            "materialisation_key",
            unique=True,
            postgresql_where=materialisation_key.isnot(None),
        ),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<RoadmapItem {self.title}>"

    @property
    def quarter_sort_key(self) -> int:
        """Generate sortable key from quarter (e.g., Q1 2026 -> 20261)."""
        if not self.quarter or not self.year:
            return 0
        try:
            q_num = int(self.quarter[1]) if self.quarter.startswith("Q") else 0
            return self.year * 10 + q_num
        except (ValueError, IndexError):
            return 0

    def get_dependencies_list(self) -> list:
        """Parse dependencies JSON to list of roadmap item IDs."""
        if not self.dependencies:
            return []
        try:
            return json.loads(self.dependencies)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_dependencies_list(self, item_ids: list):
        """Set dependencies from list of roadmap item IDs."""
        self.dependencies = json.dumps(item_ids) if item_ids else None

    def get_linked_capabilities(self) -> list:
        """Parse linked capabilities JSON to list."""
        if not self.linked_capabilities:
            return []
        try:
            return json.loads(self.linked_capabilities)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_linked_capabilities(self, capability_ids: list):
        """Set linked capabilities from list."""
        self.linked_capabilities = json.dumps(capability_ids) if capability_ids else None

    def get_linked_applications(self) -> list:
        """Parse linked applications JSON to list."""
        if not self.linked_applications:
            return []
        try:
            return json.loads(self.linked_applications)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_linked_applications(self, application_ids: list):
        """Set linked applications from list."""
        self.linked_applications = json.dumps(application_ids) if application_ids else None

    def to_dict(self, include_initiative: bool = False) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "id": self.id,
            "initiative_id": self.initiative_id,
            "programme_workstream_id": self.programme_workstream_id,
            "work_package_id": self.work_package_id,
            "decision_brief_version_id": self.decision_brief_version_id,
            "materialisation_key": self.materialisation_key,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "lane": self.lane,
            "quarter": self.quarter,
            "year": self.year,
            "quarter_sort_key": self.quarter_sort_key,
            "status": self.status,
            "effort_estimate": self.effort_estimate,
            "dependencies": self.get_dependencies_list(),
            "linked_capabilities": self.get_linked_capabilities(),
            "linked_applications": self.get_linked_applications(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_initiative and self.initiative:
            result["initiative"] = {
                "id": self.initiative.id,
                "name": self.initiative.name,
                "status": self.initiative.status,
                "priority": self.initiative.priority,
            }

        return result


class CapabilityHealthOverride(TenantMixin, db.Model):
    """
    Manual override for calculated capability health scores.
    
    Allows business owners to override automated health calculations with human judgment.
    Includes full audit trail and justification requirements for governance.
    """

    __tablename__ = "capability_health_overrides"
    __table_args__ = {"extend_existing": True}

    # Core fields
    id = Column(Integer, primary_key=True)
    capability_id = Column(
        Integer, ForeignKey("business_capability.id"), nullable=False, index=True
    )

    # Score override
    original_score = Column(Float, nullable=False)  # Calculated score at time of override
    override_score = Column(Float, nullable=False)  # Human-set score (0-100)

    # Audit trail
    justification = Column(Text, nullable=False)  # Required explanation for override
    override_reason = Column(
        String(50), nullable=False, index=True
    )  # strategic, political, external_factors, data_quality
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Status
    active = Column(Boolean, default=True, nullable=False, index=True)
    expires_at = Column(Date, nullable=True)  # Optional expiration date

    # Relationships
    capability = relationship("BusinessCapability", backref="health_overrides")
    created_by = relationship("User", backref="capability_health_overrides", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<CapabilityHealthOverride cap={self.capability_id} score={self.override_score} active={self.active}>"

    def is_expired(self) -> bool:
        """Check if override has passed expiration date."""
        if self.expires_at:
            return datetime.now().date() > self.expires_at
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "capability_id": self.capability_id,
            "capability_name": self.capability.name if self.capability else None,
            "original_score": self.original_score,
            "override_score": self.override_score,
            "score_delta": self.override_score - self.original_score,
            "justification": self.justification,
            "override_reason": self.override_reason,
            "created_by_id": self.created_by_id,
            "created_by_name": self.created_by.full_name() if self.created_by else None,
            "is_expired": self.is_expired(),
        }


class StrategicRecommendation(TenantMixin, db.Model):
    """
    LLM-generated strategic recommendation with user feedback tracking.
    
    Stores AI-powered recommendations for strategic planning dashboards with:
    - Full LLM metadata (model, provider, tokens, confidence)
    - User feedback mechanism (ratings, implementation status)
    - Context association (dashboard, capability)
    """

    __tablename__ = "strategic_recommendations"
    __table_args__ = {"extend_existing": True}

    # Core fields
    id = Column(Integer, primary_key=True)
    
    # Context
    dashboard = Column(
        String(50), nullable=False, index=True
    )  # capability_health, investment_matrix, risk_assessment, impact_analysis
    capability_id = Column(
        Integer, ForeignKey("business_capability.id"), nullable=True, index=True
    )
    
    # Recommendation content
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    rationale = Column(Text, nullable=False)
    priority = Column(String(20), nullable=False)  # CRITICAL, HIGH, MEDIUM, LOW
    estimated_effort_weeks = Column(Integer, nullable=True)
    expected_impact = Column(Text, nullable=True)
    dependencies = Column(JSON, nullable=True)  # List of prerequisite strings
    
    # LLM metadata
    confidence_score = Column(Float, nullable=False)  # 0.0-1.0
    model_used = Column(String(100), nullable=False)
    provider_used = Column(String(50), nullable=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    
    # User feedback
    user_rating = Column(Integer, nullable=True, index=True)  # 1-5 stars
    was_implemented = Column(Boolean, default=False)
    feedback_notes = Column(Text, nullable=True)
    rated_at = Column(DateTime, nullable=True)
    rated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Audit
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # Relationships
    capability = relationship("BusinessCapability", backref="strategic_recommendations")
    created_by = relationship(
        "User", backref="created_recommendations", foreign_keys=[created_by_id]
    )
    rated_by = relationship(
        "User", backref="rated_recommendations", foreign_keys=[rated_by_id]
    )

    def __repr__(self):
        return f"<StrategicRecommendation id={self.id} dashboard={self.dashboard} priority={self.priority}>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "dashboard": self.dashboard,
            "capability_id": self.capability_id,
            "capability_name": self.capability.name if self.capability else None,
            "title": self.title,
            "description": self.description,
            "rationale": self.rationale,
            "priority": self.priority,
            "estimated_effort_weeks": self.estimated_effort_weeks,
            "expected_impact": self.expected_impact,
            "dependencies": self.dependencies,
            "confidence_score": self.confidence_score,
            "model_used": self.model_used,
            "provider_used": self.provider_used,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "user_rating": self.user_rating,
            "was_implemented": self.was_implemented,
            "feedback_notes": self.feedback_notes,
            "rated_at": self.rated_at.isoformat() if self.rated_at else None,
            "rated_by_id": self.rated_by_id,
            "rated_by_name": self.rated_by.full_name() if self.rated_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by_id": self.created_by_id,
            "created_by_name": self.created_by.full_name() if self.created_by else None,
            "is_active": self.is_active,
        }


class ProgrammeSnapshot(TenantMixin, db.Model):
    """Point-in-time governance snapshot of a Transformation Programme (PROG-005).

    Written on landscape imports, manual capture, or scheduled runs. The
    diff between consecutive snapshots IS the drift signal: clean-core
    regression, baseline estate changes (systems appearing/disappearing),
    membership changes. Regressions are escalated to the ARB via ARBAuditLog
    at capture time.
    """

    # migration-exempt — new table is created via db.create_all per the
    # CLAUDE.md migration freeze (no Alembic on this platform).
    __tablename__ = "programme_snapshots"

    id = Column(Integer, primary_key=True)
    initiative_id = Column(
        Integer,
        ForeignKey("strategic_initiatives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    taken_at = Column(DateTime, default=datetime.utcnow, index=True)
    source = Column(String(50), default="manual")  # manual | sap_import | salesforce_import | scheduled

    # Governance metrics at capture time
    clean_core_score = Column(Integer)  # None when no scored fit-gap rows
    clean_core_target = Column(Integer)
    fit_counts = Column(JSON)           # {fit_type: count}
    member_count = Column(Integer, default=0)
    arb_approved = Column(Integer, default=0)
    risk_total = Column(Integer, default=0)

    # Baseline estate at capture time (app ids + names for diffing)
    baseline_app_ids = Column(JSON)     # [int, ...]
    baseline_app_count = Column(Integer, default=0)

    # Drift vs the PREVIOUS snapshot, computed at capture time
    drift = Column(JSON)                # {score_delta, apps_added: [...], apps_removed: [...], flagged: bool}

    # PROG-013: AI-on-contact review captured at import time. Compact result of
    # running the conformance + data-stewardship reviewers the moment a landscape
    # lands. None for snapshots that weren't AI-reviewed (e.g. manual captures).
    # {conformance: {score, flagged, findings:[...]}, stewardship: {flagged,
    #  finding_count, findings:[...]}, reviewed_at, flagged_total}
    ai_review = Column(JSON)

    initiative = relationship("StrategicInitiative", backref="snapshots")

    def to_dict(self):
        return {
            "id": self.id,
            "initiative_id": self.initiative_id,
            "taken_at": self.taken_at.isoformat() if self.taken_at else None,
            "source": self.source,
            "clean_core_score": self.clean_core_score,
            "clean_core_target": self.clean_core_target,
            "fit_counts": self.fit_counts or {},
            "member_count": self.member_count,
            "arb_approved": self.arb_approved,
            "risk_total": self.risk_total,
            "baseline_app_count": self.baseline_app_count,
            "drift": self.drift or {},
            "ai_review": self.ai_review or None,
        }


class EnterpriseBriefing(TenantMixin, db.Model):
    """A periodic Enterprise-Architecture briefing (AI-2).

    The EA Briefing Agent computes the week's notable findings from live
    platform data — drift, rationalization shifts, capability coverage,
    stalled governance — and persists them here. Every finding is sourced
    (Rule 11): the deterministic gathering produces counts/names; the
    optional narrative only summarises what was found.
    """

    # migration-exempt — new table via db.create_all per the migration freeze.
    __tablename__ = "enterprise_briefings"

    id = Column(Integer, primary_key=True)
    generated_at = Column(DateTime, default=datetime.utcnow, index=True)
    source = Column(String(30), default="manual")  # manual | scheduled
    generated_by_id = Column(Integer, index=True)

    # Executive narrative (deterministic template or LLM-written over findings)
    headline = Column(String(300))
    summary = Column(Text)

    # Structured findings: [{category, severity, title, detail, evidence,
    #                        action_label, action_url}]
    findings = Column(JSON)
    finding_count = Column(Integer, default=0)
    flagged_count = Column(Integer, default=0)  # high/critical severity

    # Which finding gatherers actually executed this run, and how many
    # findings each produced — lets the page show "N checks ran" rather than
    # asserting "live data" with nothing behind it. Nullable: historical
    # briefings predate this column and reconcile-schema only adds nullable
    # columns (see CLAUDE.md Schema management).
    checks_run = Column(JSON, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "source": self.source,
            "headline": self.headline,
            "summary": self.summary,
            "findings": self.findings or [],
            "finding_count": self.finding_count,
            "flagged_count": self.flagged_count,
            "checks_run": self.checks_run or [],
        }


class SolutionMigrationRoadmap(TenantMixin, db.Model):
    """An AI-generated TOGAF Phase F migration roadmap for a solution (PROG-020).

    Stored solution-scoped (not as enterprise Plateau rows) so the roadmap stays
    tied to the design it was generated from and never becomes a disconnected
    plateau island. ``plateaus`` is the structured roadmap: an ordered list of
    transition plateaus, each with an objective, horizon, and work packages.
    Generated by the LLM and grounded in live solution context; a human can
    regenerate. Table created via db.create_all() per the migration freeze.
    """

    __tablename__ = "solution_migration_roadmaps"

    id = Column(Integer, primary_key=True)
    solution_id = Column(
        Integer, ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    generated_at = Column(DateTime, default=datetime.utcnow, index=True)
    generated_by_id = Column(Integer, index=True)

    headline = Column(String(300))
    summary = Column(Text)
    horizon_months = Column(Integer)          # total roadmap horizon
    plateau_count = Column(Integer, default=0)
    # [{name, sequence, horizon_months, objective, target_state,
    #   work_packages: [{name, effort, depends_on, touches}]}]
    plateaus = Column(JSON)

    def to_dict(self):
        return {
            "id": self.id,
            "solution_id": self.solution_id,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "headline": self.headline,
            "summary": self.summary,
            "horizon_months": self.horizon_months,
            "plateau_count": self.plateau_count,
            "plateaus": self.plateaus or [],
        }


# Convenience exports
__all__ = [
    "StrategicInitiative",
    "StrategicMilestone",
    "RoadmapItem",
    "CapabilityHealthOverride",
    "StrategicRecommendation",
    "ProgrammeSnapshot",
    "EnterpriseBriefing",
    "SolutionMigrationRoadmap",
]
