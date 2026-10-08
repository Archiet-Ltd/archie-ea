"""
Architecture Decision Record (ADR) Model

Tracks architecture decisions for Solution Architecture governance and traceability.
Based on Michael Nygard's ADR format.
"""

from datetime import datetime

from sqlalchemy.ext.associationproxy import association_proxy

from .. import db
from .mixins import TenantMixin


class ArchitectureDecisionRecord(TenantMixin, db.Model):
    """
    Architecture Decision Record (ADR) for tracking significant architecture decisions.

    ADRs document the context, decision, rationale, and consequences of architecture
    choices. They provide traceability and prevent knowledge loss.

    Reference: https://adr.github.io/

    Usage:
        adr = ArchitectureDecisionRecord(
            adr_number=1,
            title="Use REST APIs for external integrations",
            status='accepted',
            context="Need standardized integration approach",
            decision="Use RESTful APIs with OpenAPI specs",
            rationale="Industry standard, tooling support, team expertise",
            consequences="Need API gateway, versioning strategy"
        )
    """

    __tablename__ = "architecture_decision_records"

    id = db.Column(db.Integer, primary_key=True)
    adr_number = db.Column(db.Integer, nullable=False)  # Sequential ADR number within project
    title = db.Column(db.String(200), nullable=False)  # Short descriptive title

    # Linkage to Architecture
    architecture_model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True
    )
    technology_stack_id = db.Column(
        db.Integer, db.ForeignKey("technology_stacks.id"), nullable=True
    )
    project_id = db.Column(db.Integer, nullable=True)  # Future: Link to projects
    # AI-3: direct link to the solution this decision belongs to (nullable;
    # added via sync_schema per the migration freeze — migration-exempt).
    solution_id = db.Column(db.Integer, nullable=True, index=True)

    # ADR Status
    status = db.Column(
        db.String(20), nullable=False, default="proposed"
    )  # proposed, accepted, rejected, deprecated, superseded

    # ADR Core Content (Michael Nygard format)
    context = db.Column(db.Text, nullable=False)  # What is the issue we're solving?
    decision = db.Column(db.Text, nullable=False)  # What did we decide?
    rationale = db.Column(db.Text, nullable=False)  # Why did we decide this?
    consequences = db.Column(db.Text, nullable=False)  # What are the implications?

    # Additional Context
    alternatives_considered = db.Column(db.Text)  # JSON: Other options evaluated
    assumptions = db.Column(db.Text)  # Key assumptions made
    constraints = db.Column(db.Text)  # Constraints that influenced decision
    risks = db.Column(db.Text)  # Risks associated with this decision

    # Decision Metadata
    decision_date = db.Column(db.Date)  # When was this decided?
    decided_by = db.Column(db.String(100))  # Who made the decision?
    stakeholders = db.Column(db.Text)  # JSON: List of involved stakeholders

    # Impact Assessment
    affected_systems = db.Column(db.Text)  # JSON: List of affected system IDs
    estimated_effort = db.Column(db.String(50))  # Small, Medium, Large, XL
    business_value = db.Column(db.String(50))  # Low, Medium, High, Critical

    # Superseding Relationships
    supersedes_adr_id = db.Column(
        db.Integer, db.ForeignKey("architecture_decision_records.id"), nullable=True
    )
    superseded_by_adr_id = db.Column(
        db.Integer, db.ForeignKey("architecture_decision_records.id"), nullable=True
    )
    supersede_reason = db.Column(db.Text)  # Why was this superseded?

    # Related ADRs
    related_adr_ids = db.Column(db.Text)  # JSON: Array of related ADR IDs

    # Decision register consolidation: the canonical architecture_decisions
    # row this ADR is also visible through (dual-write, not a move -- this
    # record stays the system of record for its rich review-board fields,
    # which architecture_decisions has no columns for: capability/process
    # links, governance_decision_id, implementation_plan, risk_register,
    # cost_analysis,
    # arb_review, decision_matrix and the rest). Nullable: set on backfilled
    # rows and every new one going forward; NULL only on a row created before
    # this consolidation shipped and not yet backfilled.
    retired_into_id = db.Column(
        db.Integer, db.ForeignKey("architecture_decisions.id"), nullable=True, index=True
    )

    # Governance snapshot stored on ADR after migration
    governance_blob = db.Column(db.JSON, nullable=True)

    # Link to canonical capability governance decision (Option B)
    governance_decision_id = db.Column(
        db.Integer, db.ForeignKey("capability_governance_decision.id"), nullable=True, index=True
    )

    # Review & Approval
    review_status = db.Column(db.String(20))  # pending, approved, changes-requested
    reviewed_by = db.Column(db.String(100))
    review_date = db.Column(db.Date)
    review_comments = db.Column(db.Text)

    # Tags & Classification
    tags = db.Column(db.Text)  # JSON: Array of tags for categorization
    category = db.Column(
        db.String(50)
    )  # technology, security, integration, data, ui, infrastructure
    archimate_layer = db.Column(
        db.String(30)
    )  # motivation, strategy, business, application, technology

    # Review board enriched template fields
    implementation_plan = db.Column(db.JSON, nullable=True)
    success_metrics = db.Column(db.JSON, nullable=True)
    rollback_strategy = db.Column(db.Text, nullable=True)
    risk_register = db.Column(db.JSON, nullable=True)
    cost_analysis = db.Column(db.JSON, nullable=True)
    monitoring_plan = db.Column(db.JSON, nullable=True)
    arb_review = db.Column(db.JSON, nullable=True)
    decision_matrix = db.Column(db.JSON, nullable=True)
    compliance_assurance = db.Column(db.JSON, nullable=True)
    reference_links = db.Column(db.JSON, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(100))
    updated_by = db.Column(db.String(100))

    # Relationships
    architecture_model = db.relationship("ArchitectureModel", backref="architecture_decisions")
    technology_stack = db.relationship("TechnologyStack", backref="architecture_decisions")

    supersedes = db.relationship(
        "ArchitectureDecisionRecord",
        foreign_keys=[supersedes_adr_id],
        remote_side="ArchitectureDecisionRecord.id",
        backref="superseded_by_adr",
    )
    superseded_by = db.relationship(
        "ArchitectureDecisionRecord",
        foreign_keys=[superseded_by_adr_id],
        remote_side="ArchitectureDecisionRecord.id",
        backref="supersedes_adr",
    )

    process_links = db.relationship(
        "ADRProcessLink", back_populates="adr", cascade="all, delete-orphan"
    )
    capability_links = db.relationship(
        "ADRCapabilityLink", back_populates="adr", cascade="all, delete-orphan"
    )
    linked_processes = association_proxy("process_links", "process")
    linked_capabilities = association_proxy("capability_links", "capability")

    # (Governance canonical table will be migrated away in Option A)
    governance_decision = db.relationship("CapabilityGovernanceDecision", backref="adrs")

    # Unique constraint: ADR number should be unique per architecture model
    __table_args__ = (
        db.UniqueConstraint("architecture_model_id", "adr_number", name="uix_adr_number_per_model"),
    )

    def __repr__(self):
        return f"<ADR-{self.adr_number}: {self.title} ({self.status})>"

    def pair_with_canonical_register(self):
        """Create (or return the existing) paired ArchitectureDecision row.

        Dual-write, not a move: this record stays the system of record for
        its own rich review-board fields; the paired row is what makes it
        visible in the one canonical register. Idempotent -- a record already
        paired (retired_into_id set) returns its existing pair instead of
        creating a second one. Skips (returns None) when this record has no
        organisation to preserve; never guesses a tenant.
        """
        import json

        from app import db
        from app.models.architecture_decision import ArchitectureDecision

        if self.retired_into_id is not None:
            # .query.get() checks the identity map first and can return an
            # already-loaded row from a different organisation's session
            # activity without re-issuing a SELECT, bypassing the tenant
            # filter (do_orm_execute only fires on an actual query) -- the
            # same pitfall documented in app/middleware/tenant_isolation.py.
            # filter_by().first() always queries, so the filter always applies.
            return ArchitectureDecision.query.filter_by(id=self.retired_into_id).first()
        if self.organization_id is None:
            return None

        def _safe_json(value):
            if not value:
                return None
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return {"legacy_text": value}

        context = self.context
        if self.decided_by:
            context = f"{context}\n\n(Decided by: {self.decided_by})" if context else f"(Decided by: {self.decided_by})"

        canonical = ArchitectureDecision(
            organization_id=self.organization_id,
            title=self.title,
            # Verbatim, not remapped: ArchitectureDecisionRecord.status's
            # full vocabulary (SolutionOptionsAdvisor.set_status's own
            # allowlist) is proposed/accepted/rejected/deprecated/superseded.
            # "rejected" previously became "deprecated" here, losing the
            # real distinction a reader needs (arb_decision_routes.py checks
            # for 'superseded' and filters proposed/under_review/accepted --
            # nothing there depends on "rejected" never appearing).
            status=self.status or "proposed",
            context=context,
            decision=self.decision,
            consequences=self.consequences,
            # ArchitectureDecision.alternatives is Text ("text or JSON", per
            # its own column comment) -- SolutionOptionsAdvisor and this
            # record's own detail template both treat it as a string
            # (json.loads'd by the one caller that needs structure,
            # rendered verbatim by the other), so it is carried over as-is,
            # never through _safe_json, which returns a parsed dict/list
            # that cannot be stored in a Text column at all.
            alternatives=self.alternatives_considered,
            rationale=self.rationale,
            constraints=_safe_json(self.constraints),
            decision_type=self.category,
            source_table="architecture_decision_records",
            source_id=self.id,
        )
        db.session.add(canonical)
        db.session.flush()
        self.retired_into_id = canonical.id
        return canonical

    def to_dict(self, include_content=True):
        """
        Convert to dictionary for API responses.

        Args:
            include_content: If False, excludes large text fields for list views
        """
        base_dict = {
            "id": self.id,
            "adr_number": self.adr_number,
            "title": self.title,
            "status": self.status,
            "category": self.category,
            "archimate_layer": self.archimate_layer,
            "decision_date": self.decision_date.isoformat() if self.decision_date else None,
            "decided_by": self.decided_by,
            "estimated_effort": self.estimated_effort,
            "business_value": self.business_value,
            "supersedes_adr_id": self.supersedes_adr_id,
            "superseded_by_adr_id": self.superseded_by_adr_id,
            "review_status": self.review_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "capabilities": [
                {
                    "id": link.capability_id,
                    "name": link.capability.name if link.capability else None,
                    "relationship_type": link.relationship_type,
                    "impact_level": link.impact_level,
                    "notes": link.notes,
                }
                for link in self.capability_links
            ],
            "processes": [
                {
                    "id": link.process_id,
                    "name": link.process.name if link.process else None,
                    "relationship_type": link.relationship_type,
                    "impact_level": link.impact_level,
                    "notes": link.notes,
                }
                for link in self.process_links
            ],
        }

        if include_content:
            base_dict.update(
                {
                    "context": self.context,
                    "decision": self.decision,
                    "rationale": self.rationale,
                    "consequences": self.consequences,
                    "alternatives_considered": self.alternatives_considered,
                    "assumptions": self.assumptions,
                    "constraints": self.constraints,
                    "risks": self.risks,
                    "stakeholders": self.stakeholders,
                    "affected_systems": self.affected_systems,
                    "supersede_reason": self.supersede_reason,
                    "related_adr_ids": self.related_adr_ids,
                    "review_comments": self.review_comments,
                    "tags": self.tags,
                    "implementation_plan": self.implementation_plan or {},
                    "success_metrics": self.success_metrics or {},
                    "rollback_strategy": self.rollback_strategy,
                    "risk_register": self.risk_register or [],
                    "cost_analysis": self.cost_analysis or {},
                    "monitoring_plan": self.monitoring_plan or {},
                    "arb_review": self.arb_review or {},
                    "decision_matrix": self.decision_matrix or {},
                    "compliance_assurance": self.compliance_assurance or {},
                    "reference_links": self.reference_links or [],
                }
            )

        # Include merged governance decision if present
        if self.governance_decision:
            try:
                base_dict["governance_decision"] = self.governance_decision.to_dict()
            except Exception:
                base_dict["governance_decision"] = {"id": self.governance_decision_id}

        return base_dict

    def supersede_by(self, new_adr, reason):
        """
        Mark this ADR as superseded by a new ADR.

        Args:
            new_adr: The new ADR that supersedes this one
            reason: Reason for superseding
        """
        self.status = "superseded"
        self.superseded_by_adr_id = new_adr.id
        self.supersede_reason = reason
        new_adr.supersedes_adr_id = self.id
        db.session.commit()

    @staticmethod
    def get_next_adr_number(architecture_model_id=None):
        """Get the next available ADR number for an architecture model."""
        if architecture_model_id:
            last_adr = (
                ArchitectureDecisionRecord.query.filter_by(
                    architecture_model_id=architecture_model_id
                )
                .order_by(ArchitectureDecisionRecord.adr_number.desc())
                .first()
            )
        else:
            last_adr = ArchitectureDecisionRecord.query.order_by(
                ArchitectureDecisionRecord.adr_number.desc()
            ).first()

        return (last_adr.adr_number + 1) if last_adr else 1


class ADRCapabilityLink(db.Model):
    __tablename__ = "adr_capability_links"

    adr_id = db.Column(
        db.Integer,
        db.ForeignKey("architecture_decision_records.id", ondelete="CASCADE"),
        primary_key=True,
    )
    capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id", ondelete="CASCADE"), primary_key=True
    )
    relationship_type = db.Column(db.String(50))
    impact_level = db.Column(db.String(30))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    adr = db.relationship("ArchitectureDecisionRecord", back_populates="capability_links")
    capability = db.relationship("BusinessCapability", back_populates="decision_links")


class ADRProcessLink(db.Model):
    __tablename__ = "adr_process_links"

    adr_id = db.Column(
        db.Integer,
        db.ForeignKey("architecture_decision_records.id", ondelete="CASCADE"),
        primary_key=True,
    )
    process_id = db.Column(
        db.Integer, db.ForeignKey("business_processes.id", ondelete="CASCADE"), primary_key=True
    )
    relationship_type = db.Column(db.String(50))
    impact_level = db.Column(db.String(30))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    adr = db.relationship("ArchitectureDecisionRecord", back_populates="process_links")
    process = db.relationship("BusinessProcess", back_populates="decision_links")
