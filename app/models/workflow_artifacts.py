"""
Workflow Output Artifact Models

Persisted EA artifacts produced by workflow completion handlers.
These are the domain-valuable outputs of the workflow engine — not
process telemetry (which is in workflow_models.py), but actual
architecture deliverables.

ADR: docs/adr/ADR-001-workflow-artifact-storage.md
"""
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class WorkflowArtifactMixin:
    """Shared columns for all workflow output artifacts.

    Every artifact is linked to a workflow instance via FK, supports
    versioning, and tracks approval status.
    """

    version = db.Column(db.Integer, default=1, nullable=False)
    status = db.Column(db.String(32), default="draft", nullable=False)
    content = db.Column(db.JSON, nullable=False, default=dict)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @staticmethod
    def _valid_statuses():
        return ("draft", "approved", "superseded", "rejected")

    def approve(self, user_id):
        self.status = "approved"
        self.approved_by_id = user_id
        self.approved_at = datetime.utcnow()

    def supersede(self):
        self.status = "superseded"

    def to_artifact_dict(self):
        return {
            "id": self.id,
            "workflow_instance_id": self.workflow_instance_id,
            "version": self.version,
            "status": self.status,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "approved_at": getattr(self, "approved_at", None),
        }


class ArchitectureVisionDocument(TenantMixin, WorkflowArtifactMixin, db.Model):
    """TOGAF ADM Phase A output: the Architecture Vision Document.

    Produced by the ADM_PHASE_A_VISION workflow upon completion.
    """

    __tablename__ = "architecture_vision_documents"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    title = db.Column(db.String(500), nullable=False)
    scope_summary = db.Column(db.Text)
    stakeholder_concerns = db.Column(db.JSON)
    architecture_principles = db.Column(db.JSON)
    business_goals = db.Column(db.JSON)
    constraints = db.Column(db.JSON)
    target_architecture_summary = db.Column(db.Text)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("vision_documents", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "architecture_vision_document",
                "title": self.title,
                "scope_summary": self.scope_summary,
                "stakeholder_concerns": self.stakeholder_concerns,
                "architecture_principles": self.architecture_principles,
                "business_goals": self.business_goals,
                "constraints": self.constraints,
                "target_architecture_summary": self.target_architecture_summary,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat()
                if self.approved_at
                else None,
            }
        )
        return base

    def __repr__(self):
        return f"<ArchitectureVisionDocument {self.id}: {self.title}>"


class ArchitectureReviewFinding(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Individual finding from an AI-Assisted Architecture Review.

    Produced by the ARCH_REVIEW workflow. Each finding is a discrete,
    actionable record that can be accepted, rejected, or deferred.
    """

    __tablename__ = "architecture_review_findings"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    finding_type = db.Column(
        db.String(64), nullable=False, default="recommendation"
    )
    severity = db.Column(db.String(32), nullable=False, default="medium")
    element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    element_name = db.Column(db.String(255))
    description = db.Column(db.Text, nullable=False)
    recommendation = db.Column(db.Text)
    resolution_status = db.Column(db.String(32), default="open")
    resolution_notes = db.Column(db.Text)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    resolved_at = db.Column(db.DateTime)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("review_findings", lazy="dynamic"),
    )
    element = db.relationship("ArchiMateElement", foreign_keys=[element_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_id])

    VALID_FINDING_TYPES = (
        "recommendation",
        "violation",
        "observation",
        "risk",
        "improvement",
    )
    VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")
    VALID_RESOLUTIONS = ("open", "accepted", "rejected", "deferred", "resolved")

    def accept(self, user_id, notes=None):
        self.resolution_status = "accepted"
        self.resolved_by_id = user_id
        self.resolved_at = datetime.utcnow()
        if notes:
            self.resolution_notes = notes

    def reject(self, user_id, notes=None):
        self.resolution_status = "rejected"
        self.resolved_by_id = user_id
        self.resolved_at = datetime.utcnow()
        if notes:
            self.resolution_notes = notes

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "architecture_review_finding",
                "finding_type": self.finding_type,
                "severity": self.severity,
                "element_id": self.element_id,
                "element_name": self.element_name,
                "description": self.description,
                "recommendation": self.recommendation,
                "resolution_status": self.resolution_status,
                "resolution_notes": self.resolution_notes,
                "resolved_by_id": self.resolved_by_id,
                "resolved_at": self.resolved_at.isoformat()
                if self.resolved_at
                else None,
            }
        )
        return base

    def __repr__(self):
        return f"<ArchitectureReviewFinding {self.id}: {self.finding_type}/{self.severity}>"


class VendorSelectionReport(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Formal output of the Vendor Selection workflow.

    Produced by the VENDOR_SELECTION workflow upon board approval.
    Contains the scored shortlist, TCO analysis, and recommendation.
    """

    __tablename__ = "vendor_selection_reports"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    title = db.Column(db.String(500), nullable=False)
    capability_gap_summary = db.Column(db.Text)
    shortlisted_vendors = db.Column(db.JSON)
    vendor_scores = db.Column(db.JSON)
    tco_analysis = db.Column(db.JSON)
    recommendation = db.Column(db.Text)
    decision_rationale = db.Column(db.Text)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("vendor_reports", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "vendor_selection_report",
                "title": self.title,
                "capability_gap_summary": self.capability_gap_summary,
                "shortlisted_vendors": self.shortlisted_vendors,
                "vendor_scores": self.vendor_scores,
                "tco_analysis": self.tco_analysis,
                "recommendation": self.recommendation,
                "decision_rationale": self.decision_rationale,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat()
                if self.approved_at
                else None,
            }
        )
        return base

    def __repr__(self):
        return f"<VendorSelectionReport {self.id}: {self.title}>"


class ComplianceScanReport(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Output of a compliance scan workflow run.

    Produced by the COMPLIANCE_SCAN workflow upon completion.
    """

    __tablename__ = "compliance_scan_reports"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    scan_scope = db.Column(db.String(64), default="full")
    total_violations = db.Column(db.Integer, default=0)
    violations_by_severity = db.Column(db.JSON, default=dict)
    policies_evaluated = db.Column(db.Integer, default=0)
    applications_scanned = db.Column(db.Integer, default=0)
    auto_remediated_count = db.Column(db.Integer, default=0)
    remediation_summary = db.Column(db.JSON)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("compliance_reports", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "compliance_scan_report",
                "scan_scope": self.scan_scope,
                "total_violations": self.total_violations,
                "violations_by_severity": self.violations_by_severity,
                "policies_evaluated": self.policies_evaluated,
                "applications_scanned": self.applications_scanned,
                "auto_remediated_count": self.auto_remediated_count,
                "remediation_summary": self.remediation_summary,
            }
        )
        return base

    def __repr__(self):
        return f"<ComplianceScanReport {self.id}: {self.total_violations} violations>"


class WorkflowCompletionSummary(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Universal completion summary for any workflow type.

    Created automatically when any workflow reaches 'completed' status.
    Provides a human-readable summary of what was accomplished.
    """

    __tablename__ = "workflow_completion_summaries"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    workflow_code = db.Column(db.String(100), nullable=False)
    workflow_name = db.Column(db.String(255))
    total_steps = db.Column(db.Integer, default=0)
    completed_steps = db.Column(db.Integer, default=0)
    duration_seconds = db.Column(db.Integer)
    artifacts_created = db.Column(db.JSON, default=list)
    steps_summary = db.Column(db.JSON, default=list)
    key_outputs = db.Column(db.JSON, default=dict)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("completion_summary", uselist=False),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "workflow_completion_summary",
                "workflow_code": self.workflow_code,
                "workflow_name": self.workflow_name,
                "total_steps": self.total_steps,
                "completed_steps": self.completed_steps,
                "duration_seconds": self.duration_seconds,
                "artifacts_created": self.artifacts_created,
                "steps_summary": self.steps_summary,
                "key_outputs": self.key_outputs,
            }
        )
        return base

    def __repr__(self):
        return f"<WorkflowCompletionSummary {self.id}: {self.workflow_code}>"


class MigrationPlanDocument(TenantMixin, WorkflowArtifactMixin, db.Model):
    """TOGAF ADM Phase E/F output: Migration Plan.

    Produced by ADM_PHASE_E_OPPORTUNITIES or ADM_PHASE_F_MIGRATION workflows.
    Contains prioritized projects, transition architectures, and roadmap items.
    """

    __tablename__ = "migration_plan_documents"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    title = db.Column(db.String(500), nullable=False)
    adm_phase = db.Column(db.String(2), nullable=False)
    consolidated_gaps = db.Column(db.JSON, default=list)
    prioritized_projects = db.Column(db.JSON, default=list)
    roadmap_item_ids = db.Column(db.JSON, default=list)
    transition_architectures = db.Column(db.JSON, default=list)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("migration_plans", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "migration_plan_document",
                "title": self.title,
                "adm_phase": self.adm_phase,
                "consolidated_gaps": self.consolidated_gaps,
                "prioritized_projects": self.prioritized_projects,
                "roadmap_item_ids": self.roadmap_item_ids,
                "transition_architectures": self.transition_architectures,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat()
                if self.approved_at
                else None,
            }
        )
        return base

    def __repr__(self):
        return f"<MigrationPlanDocument {self.id}: Phase {self.adm_phase} — {self.title}>"


class ComplianceGovernanceReport(TenantMixin, WorkflowArtifactMixin, db.Model):
    """TOGAF ADM Phase G output: Implementation Governance Report.

    Produced by ADM_PHASE_G_GOVERNANCE workflow. Contains compliance scan
    results, classified violations, and remediation task references.
    """

    __tablename__ = "compliance_governance_reports"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    title = db.Column(db.String(500), nullable=False)
    policies_evaluated = db.Column(db.Integer, default=0)
    total_violations = db.Column(db.Integer, default=0)
    violations_by_severity = db.Column(db.JSON, default=dict)
    remediation_tasks_created = db.Column(db.Integer, default=0)
    remediation_task_ids = db.Column(db.JSON, default=list)
    scan_scope = db.Column(db.String(64), default="full")

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("governance_reports", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "compliance_governance_report",
                "title": self.title,
                "policies_evaluated": self.policies_evaluated,
                "total_violations": self.total_violations,
                "violations_by_severity": self.violations_by_severity,
                "remediation_tasks_created": self.remediation_tasks_created,
                "remediation_task_ids": self.remediation_task_ids,
                "scan_scope": self.scan_scope,
            }
        )
        return base

    def __repr__(self):
        return f"<ComplianceGovernanceReport {self.id}: {self.total_violations} violations>"


class ChangeManagementRecord(TenantMixin, WorkflowArtifactMixin, db.Model):
    """TOGAF ADM Phase H output: Change Management Record.

    Produced by ADM_PHASE_H_CHANGE workflow. Records change triggers,
    impact assessment, and routing decision for ADM cycle re-entry.
    """

    __tablename__ = "change_management_records"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    title = db.Column(db.String(500), nullable=False)
    change_triggers = db.Column(db.JSON, default=list)
    impact_assessment = db.Column(db.JSON, default=dict)
    impact_severity = db.Column(db.String(32), default="medium")
    routing_decision = db.Column(db.JSON, default=dict)
    routed_to_phase = db.Column(db.String(2))

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("change_records", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "change_management_record",
                "title": self.title,
                "change_triggers": self.change_triggers,
                "impact_assessment": self.impact_assessment,
                "impact_severity": self.impact_severity,
                "routing_decision": self.routing_decision,
                "routed_to_phase": self.routed_to_phase,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat()
                if self.approved_at
                else None,
            }
        )
        return base

    def __repr__(self):
        return f"<ChangeManagementRecord {self.id}: routed to Phase {self.routed_to_phase}>"


class RequirementsTraceabilityMatrix(TenantMixin, WorkflowArtifactMixin, db.Model):
    """TOGAF ADM Requirements Management output.

    Produced by ADM_REQUIREMENTS_MGMT workflow. Maps captured requirements
    to APQC processes and tracks validation status.
    """

    __tablename__ = "requirements_traceability_matrices"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    title = db.Column(db.String(500), nullable=False)
    requirements_count = db.Column(db.Integer, default=0)
    requirements = db.Column(db.JSON, default=list)
    apqc_mappings = db.Column(db.JSON, default=list)
    validation_status = db.Column(db.String(32), default="pending")
    coverage_percent = db.Column(db.Float, default=0.0)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("traceability_matrices", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "requirements_traceability_matrix",
                "title": self.title,
                "requirements_count": self.requirements_count,
                "requirements": self.requirements,
                "apqc_mappings": self.apqc_mappings,
                "validation_status": self.validation_status,
                "coverage_percent": self.coverage_percent,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat()
                if self.approved_at
                else None,
            }
        )
        return base

    def __repr__(self):
        return f"<RequirementsTraceabilityMatrix {self.id}: {self.requirements_count} requirements>"


class GapRemediationReport(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Output of a gap remediation workflow run (GLB-WF-012).

    Produced by the GAP_REMEDIATION workflow upon completion.
    Contains detected gaps, created roadmap items, and remediation summary.
    """

    __tablename__ = "gap_remediation_reports"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    title = db.Column(db.String(500), nullable=False)
    detected_gaps_count = db.Column(db.Integer, default=0)
    roadmap_items_created = db.Column(db.Integer, default=0)
    roadmap_item_ids = db.Column(db.JSON, default=list)
    gaps_by_severity = db.Column(db.JSON, default=dict)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("gap_remediation_reports", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "gap_remediation_report",
                "title": self.title,
                "detected_gaps_count": self.detected_gaps_count,
                "roadmap_items_created": self.roadmap_items_created,
                "roadmap_item_ids": self.roadmap_item_ids,
                "gaps_by_severity": self.gaps_by_severity,
            }
        )
        return base

    def __repr__(self):
        return f"<GapRemediationReport {self.id}: {self.detected_gaps_count} gaps>"


# ── Wave 7-11: High-Value EA Transformation Workflows ─────────────────────────


class ApplicationDispositionRecord(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Output of APP_DISPOSITION workflow.

    Captures Retire/Retain/Replace/Re-engineer/Consolidate decisions
    for a scoped application portfolio, with migration wave sequencing
    and 3-year business case. Covers ADM Phases B, E, F.
    """

    __tablename__ = "application_disposition_records"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    scope_app_ids = db.Column(db.JSON, default=list)
    dispositions = db.Column(db.JSON, default=list)
    migration_waves = db.Column(db.JSON, default=list)
    business_case = db.Column(db.JSON, default=dict)
    total_apps_in_scope = db.Column(db.Integer, default=0)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("disposition_records", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "application_disposition_record",
                "scope_app_ids": self.scope_app_ids,
                "dispositions": self.dispositions,
                "migration_waves": self.migration_waves,
                "business_case": self.business_case,
                "total_apps_in_scope": self.total_apps_in_scope,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            }
        )
        return base

    def __repr__(self):
        return f"<ApplicationDispositionRecord {self.id}: {self.total_apps_in_scope} apps>"


class PlatformMigrationScope(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Output of PLATFORM_MIGRATION_SCOPING workflow.

    Captures integration inventory, custom object register, process
    dispositions, wave plan, and risk register for a brownfield platform
    migration (SAP ECC→S4, Oracle EBS→Fusion, etc.).
    Covers ADM Phases C, D, E, F, G.
    """

    __tablename__ = "platform_migration_scopes"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    source_platform_app_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True
    )
    source_platform_name = db.Column(db.String(255))
    integration_inventory = db.Column(db.JSON, default=list)
    custom_objects = db.Column(db.JSON, default=list)
    process_dispositions = db.Column(db.JSON, default=list)
    migration_waves = db.Column(db.JSON, default=list)
    risk_register = db.Column(db.JSON, default=list)
    total_integrations = db.Column(db.Integer, default=0)
    total_effort_days = db.Column(db.Float, default=0.0)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("migration_scopes", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "platform_migration_scope",
                "source_platform_app_id": self.source_platform_app_id,
                "source_platform_name": self.source_platform_name,
                "integration_inventory": self.integration_inventory,
                "custom_objects": self.custom_objects,
                "process_dispositions": self.process_dispositions,
                "migration_waves": self.migration_waves,
                "risk_register": self.risk_register,
                "total_integrations": self.total_integrations,
                "total_effort_days": self.total_effort_days,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            }
        )
        return base

    def __repr__(self):
        return f"<PlatformMigrationScope {self.id}: {self.source_platform_name}>"


class ARBSubmissionPack(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Output of ARB_PACK_GENERATION workflow.

    Structured ARB submission document replacing manual 2-3 day assembly.
    Includes current state pull, proposed changes, impact assessment,
    completeness scoring, and ARB decision record.
    Covers ADM Phases A-H (all phases — ARB is the cross-phase gate).
    """

    __tablename__ = "arb_submission_packs"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    solution_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True
    )
    solution_name = db.Column(db.String(255))
    proposed_changes = db.Column(db.JSON, default=dict)
    impact_assessment = db.Column(db.JSON, default=dict)
    completeness_score = db.Column(db.Float, default=0.0)
    completeness_gaps = db.Column(db.JSON, default=list)
    submission_status = db.Column(db.String(32), default="draft")
    arb_decision = db.Column(db.JSON, default=dict)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime)
    decided_at = db.Column(db.DateTime)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("arb_packs", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "arb_submission_pack",
                "solution_id": self.solution_id,
                "solution_name": self.solution_name,
                "proposed_changes": self.proposed_changes,
                "impact_assessment": self.impact_assessment,
                "completeness_score": self.completeness_score,
                "completeness_gaps": self.completeness_gaps,
                "submission_status": self.submission_status,
                "arb_decision": self.arb_decision,
                "generated_at": self.generated_at.isoformat() if self.generated_at else None,
                "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
                "decided_at": self.decided_at.isoformat() if self.decided_at else None,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            }
        )
        return base

    def __repr__(self):
        return f"<ARBSubmissionPack {self.id}: {self.solution_name} ({self.submission_status})>"


class CapabilityInvestmentPlan(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Output of CAPABILITY_INVESTMENT_PLANNING workflow.

    Maps capability gaps to build/buy/partner investment options with
    3-year roadmap. Feeds ADM Phase A vision and Phase B architecture.
    Covers ADM Phases A, B, E.
    """

    __tablename__ = "capability_investment_plans"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    capability_baseline = db.Column(db.JSON, default=list)
    strategic_weights = db.Column(db.JSON, default=dict)
    gaps = db.Column(db.JSON, default=list)
    investment_roadmap = db.Column(db.JSON, default=list)
    total_investment_3yr = db.Column(db.Float, default=0.0)
    capabilities_addressed = db.Column(db.Integer, default=0)
    total_capabilities_assessed = db.Column(db.Integer, default=0)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("investment_plans", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "capability_investment_plan",
                "capability_baseline": self.capability_baseline,
                "strategic_weights": self.strategic_weights,
                "gaps": self.gaps,
                "investment_roadmap": self.investment_roadmap,
                "total_investment_3yr": self.total_investment_3yr,
                "capabilities_addressed": self.capabilities_addressed,
                "total_capabilities_assessed": self.total_capabilities_assessed,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            }
        )
        return base

    def __repr__(self):
        return f"<CapabilityInvestmentPlan {self.id}: {self.capabilities_addressed} gaps addressed>"


class IntegrationImpactRegister(TenantMixin, WorkflowArtifactMixin, db.Model):
    """Output of INTEGRATION_IMPACT_ASSESSMENT workflow.

    Captures direct + transitive integration impacts when a platform changes,
    with architect-confirmed classifications, remediation plan, cutover
    sequence, and pre-go-live test matrix.
    Covers ADM Phases C, F, G.
    """

    __tablename__ = "integration_impact_registers"
    __tenant_parent__ = (('workflow_instance_id', 'ea_workflow_instances'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    workflow_instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id"),
        nullable=False,
        index=True,
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)

    target_app_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=True
    )
    target_app_name = db.Column(db.String(255))
    direct_impacts = db.Column(db.JSON, default=list)
    transitive_impacts = db.Column(db.JSON, default=list)
    remediation_plan = db.Column(db.JSON, default=list)
    cutover_sequence = db.Column(db.JSON, default=list)
    test_matrix = db.Column(db.JSON, default=list)
    total_effort_days = db.Column(db.Float, default=0.0)
    go_live_blocker_count = db.Column(db.Integer, default=0)

    instance = db.relationship(
        "EAWorkflowInstance",
        backref=db.backref("impact_registers", lazy="dynamic"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def to_dict(self):
        base = self.to_artifact_dict()
        base.update(
            {
                "artifact_type": "integration_impact_register",
                "target_app_id": self.target_app_id,
                "target_app_name": self.target_app_name,
                "direct_impacts": self.direct_impacts,
                "transitive_impacts": self.transitive_impacts,
                "remediation_plan": self.remediation_plan,
                "cutover_sequence": self.cutover_sequence,
                "test_matrix": self.test_matrix,
                "total_effort_days": self.total_effort_days,
                "go_live_blocker_count": self.go_live_blocker_count,
                "approved_by_id": self.approved_by_id,
                "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            }
        )
        return base

    def __repr__(self):
        return f"<IntegrationImpactRegister {self.id}: {self.target_app_name} ({self.go_live_blocker_count} blockers)>"
