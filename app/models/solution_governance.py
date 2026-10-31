# migration-exempt: index rename only, no schema changes
"""
Solution governance models for Phase 5A.
Handles: versioning, approvals, execution tracking, issues, ARB integration.
"""

from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB
from app import db
from app.models.mixins import TenantMixin


class SolutionVersion(TenantMixin, db.Model):
    """Multi-round solution versioning with approval tracking."""
    __tablename__ = 'solution_versions'
    
    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey('solutions.id', ondelete='CASCADE'), nullable=False)
    version_number = db.Column(db.Integer, nullable=False)  # v1, v2, v3
    
    # Who created this version
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # What changed from previous version
    change_summary = db.Column(db.Text, nullable=True)  # "Added security architecture, adjusted timeline to Q3"
    change_delta = db.Column(JSONB, nullable=True)  # Structured diff: {vendors: [...], risks: [...], timeline: {...}}
    
    # Approval status for this version
    approval_status = db.Column(db.String(50), default='pending')  # pending, approved, rejected, conditional
    approval_notes = db.Column(db.Text, nullable=True)
    
    # Who approved (if approved)
    approved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    
    # Approval conditions (if conditional approval)
    approval_conditions = db.Column(JSONB, nullable=True)  # [{condition: "...", owner_id: X}]
    
    # Version snapshot (full solution state at this version)
    solution_snapshot = db.Column(JSONB, nullable=False)  # Complete copy of solution at this version
    
    # Rejection reason (if rejected)
    rejection_reason = db.Column(db.Text, nullable=True)
    
    # Relationships (lazy loaded to avoid circular imports - no User backrefs)
    # Users will be loaded on demand if needed
    
    # Indexes
    __table_args__ = (
        db.Index('idx_sol_gov_version_solution_id', 'solution_id'),
        db.Index('idx_sol_gov_version_created_at', 'created_at'),
        db.Index('idx_sol_gov_version_approval_status', 'approval_status'),
    )
    
    def __repr__(self):
        return f'<SolutionVersion v{self.version_number} of solution {self.solution_id}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'solution_id': self.solution_id,
            'version_number': self.version_number,
            'created_by_id': self.created_by_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'change_summary': self.change_summary,
            'change_delta': self.change_delta,
            'approval_status': self.approval_status,
            'approval_notes': self.approval_notes,
            'approved_by_id': self.approved_by_id,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'approval_conditions': self.approval_conditions,
            'rejection_reason': self.rejection_reason,
        }


class SolutionExecutionTracking(TenantMixin, db.Model):
    """Track actual implementation progress vs planned timeline."""
    __tablename__ = 'solution_execution_tracking'
    
    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey('solutions.id', ondelete='CASCADE'), nullable=False)
    workflow_task_id = db.Column(db.Integer, db.ForeignKey('solution_workflow_tasks.id'), nullable=True)
    
    # Progress tracking
    percent_complete = db.Column(db.Float, default=0.0)  # 0-100
    actual_start_date = db.Column(db.Date, nullable=True)
    actual_end_date = db.Column(db.Date, nullable=True)
    
    # Planned vs actual
    planned_duration_days = db.Column(db.Integer, nullable=True)
    actual_duration_days = db.Column(db.Integer, nullable=True)
    planned_end_date = db.Column(db.Date, nullable=True)
    
    # Variance tracking
    variance_days = db.Column(db.Integer, default=0)  # actual_end - planned_end (positive = late)
    variance_percentage = db.Column(db.Float, default=0.0)
    
    # Wizard Step 7 fields
    work_package_name = db.Column(db.String(255), nullable=True)
    milestone_name = db.Column(db.String(255), nullable=True)
    blockers = db.Column(db.Text, nullable=True)

    # Status
    status = db.Column(db.String(50), default='on_track')  # on_track, at_risk, blocked, delayed, planned
    status_reason = db.Column(db.Text, nullable=True)
    
    # Risk realization
    realized_risks = db.Column(JSONB, default=list)  # [{risk_id, realized_on, impact}]
    
    # Updates tracking
    last_updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    last_updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships (none - to avoid circular import issues with Solution and SolutionWorkflowTask)
    # FK references are defined but relationships lazy-loaded on demand
    
    # Indexes
    __table_args__ = (
        db.Index('idx_exec_tracking_solution_id', 'solution_id'),
        db.Index('idx_exec_tracking_status', 'status'),
        db.Index('idx_exec_tracking_updated', 'last_updated_at'),
    )
    
    def __repr__(self):
        return f'<ExecutionTracking solution={self.solution_id} status={self.status}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'solution_id': self.solution_id,
            'workflow_task_id': self.workflow_task_id,
            'percent_complete': self.percent_complete,
            'actual_start_date': self.actual_start_date.isoformat() if self.actual_start_date else None,
            'actual_end_date': self.actual_end_date.isoformat() if self.actual_end_date else None,
            'planned_end_date': self.planned_end_date.isoformat() if self.planned_end_date else None,
            'variance_days': self.variance_days,
            'variance_percentage': self.variance_percentage,
            'status': self.status,
            'status_reason': self.status_reason,
            'realized_risks': self.realized_risks,
            'last_updated_at': self.last_updated_at.isoformat() if self.last_updated_at else None,
        }


class SolutionIssue(TenantMixin, db.Model):
    """Track implementation issues and blockers."""
    __tablename__ = 'solution_issues'
    
    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey('solutions.id', ondelete='CASCADE'), nullable=False)
    workflow_task_id = db.Column(db.Integer, db.ForeignKey('solution_workflow_tasks.id'), nullable=True)
    
    # Issue details
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=True)  # vendor, resource, technical, budget, timeline, etc.
    
    # Severity and priority
    severity = db.Column(db.String(10), default='P3')  # P1 (critical), P2 (high), P3 (medium), P4 (low)
    priority = db.Column(db.Integer, default=999)  # Numeric priority for sorting
    
    # Status
    status = db.Column(db.String(50), default='open')  # open, investigating, resolved, closed, on_hold
    
    # Impact assessment
    impact_area = db.Column(db.String(100), nullable=True)  # timeline, budget, scope, quality, etc.
    estimated_impact = db.Column(db.Text, nullable=True)  # "Delays go-live by 3 weeks"
    
    # Owner and escalation
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    escalated_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Escalation tracking
    escalation_count = db.Column(db.Integer, default=0)
    escalation_reason = db.Column(db.Text, nullable=True)
    escalated_at = db.Column(db.DateTime, nullable=True)
    
    # Resolution
    resolution_plan = db.Column(db.Text, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Timeline
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    target_resolution_date = db.Column(db.Date, nullable=True)
    
    # Auto-escalation thresholds (if not resolved by this date, escalate)
    auto_escalate_if_not_resolved_by = db.Column(db.Date, nullable=True)
    
    # Relationships (none - to avoid circular import issues)
    # FK references defined but relationships lazy-loaded on demand
    
    # Indexes
    __table_args__ = (
        db.Index('idx_issue_solution_id', 'solution_id'),
        db.Index('idx_issue_severity', 'severity'),
        db.Index('idx_issue_status', 'status'),
        db.Index('idx_issue_created_at', 'created_at'),
    )
    
    def __repr__(self):
        return f'<SolutionIssue {self.severity} "{self.title}" on solution {self.solution_id}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'solution_id': self.solution_id,
            'title': self.title,
            'description': self.description,
            'category': self.category,
            'severity': self.severity,
            'priority': self.priority,
            'status': self.status,
            'impact_area': self.impact_area,
            'estimated_impact': self.estimated_impact,
            'assigned_to_id': self.assigned_to_id,
            'escalation_count': self.escalation_count,
            'escalated_at': self.escalated_at.isoformat() if self.escalated_at else None,
            'created_at': self.created_at.isoformat(),
            'target_resolution_date': self.target_resolution_date.isoformat() if self.target_resolution_date else None,
        }


class SolutionARBReview(TenantMixin, db.Model):
    """Architecture Review Board approval tracking."""
    __tablename__ = 'solution_arb_reviews'
    
    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey('solutions.id', ondelete='CASCADE'), nullable=False)
    version_id = db.Column(db.Integer, db.ForeignKey('solution_versions.id'), nullable=True)
    
    # Submission details
    submitted_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    submission_version = db.Column(db.String(50), nullable=True)  # e.g., "v2"
    
    # ARB decision
    arb_decision = db.Column(db.String(50), default='pending')  # pending, approved, rejected, conditional
    arb_decision_reason = db.Column(db.Text, nullable=True)
    
    # Decision details
    decided_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # Lead ARB member
    decided_at = db.Column(db.DateTime, nullable=True)
    
    # ARB attendance (who voted)
    arb_attendees = db.Column(JSONB, default=list)  # [{user_id, name, vote: approved/rejected/abstain}]
    
    # Approval conditions (if conditional)
    conditions = db.Column(JSONB, default=list)  # [{condition, owner_id, target_date}]
    
    # Compliance tracking
    compliance_areas_reviewed = db.Column(JSONB, default=list)  # [security, finance, ops, etc.]
    compliance_notes = db.Column(JSONB, default=dict)  # {area: notes}
    
    # Next steps
    next_steps = db.Column(db.Text, nullable=True)
    next_review_date = db.Column(db.Date, nullable=True)
    
    # Timeline
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships (none - to avoid circular imports)
    # FK references defined but relationships lazy-loaded on demand
    
    # Indexes
    __table_args__ = (
        db.Index('idx_arb_solution_id', 'solution_id'),
        db.Index('idx_arb_decision', 'arb_decision'),
        db.Index('idx_arb_submitted_at', 'submitted_at'),
    )
    
    def __repr__(self):
        return f'<ARBReview solution={self.solution_id} decision={self.arb_decision}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'solution_id': self.solution_id,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'arb_decision': self.arb_decision,
            'decided_at': self.decided_at.isoformat() if self.decided_at else None,
            'arb_attendees': self.arb_attendees,
            'conditions': self.conditions,
            'compliance_areas_reviewed': self.compliance_areas_reviewed,
            'next_steps': self.next_steps,
            'next_review_date': self.next_review_date.isoformat() if self.next_review_date else None,
        }


class SolutionOutcomeTracking(TenantMixin, db.Model):
    """Track actual outcomes vs predicted for learning loop."""
    __tablename__ = 'solution_outcome_tracking'
    __tenant_parent__ = (('solution_id', 'solutions'),)
    
    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey('solutions.id', ondelete='CASCADE'), nullable=False)
    
    # Project completion details
    project_completed_at = db.Column(db.DateTime, nullable=True)
    go_live_date = db.Column(db.Date, nullable=True)
    
    # Predicted vs Actual timeline
    predicted_duration_weeks = db.Column(db.Float, nullable=True)
    actual_duration_weeks = db.Column(db.Float, nullable=True)
    timeline_accuracy_percentage = db.Column(db.Float, nullable=True)  # actual / predicted * 100
    
    # Predicted vs Actual cost
    predicted_cost_usd = db.Column(db.Float, nullable=True)
    actual_cost_usd = db.Column(db.Float, nullable=True)
    cost_accuracy_percentage = db.Column(db.Float, nullable=True)
    
    # Vendor performance ratings
    vendor_performance = db.Column(JSONB, default=dict)  # {vendor_id: {rating: 4.5, comment: "..."}}
    
    # Risk realization analysis
    predicted_risks = db.Column(JSONB, default=list)  # From recommendation
    realized_risks = db.Column(JSONB, default=list)  # Actual risks that occurred
    unforecast_risks = db.Column(JSONB, default=list)  # Risks we didn't predict
    risk_accuracy_percentage = db.Column(db.Float, nullable=True)  # predicted_and_realized / total_actual
    
    # Lessons learned
    lessons_learned = db.Column(db.Text, nullable=True)
    what_went_well = db.Column(db.Text, nullable=True)
    what_to_improve = db.Column(db.Text, nullable=True)
    
    # Business outcomes
    business_value_realized = db.Column(db.Text, nullable=True)  # Qualitative assessment
    estimated_business_value_usd = db.Column(db.Float, nullable=True)
    roi_percentage = db.Column(db.Float, nullable=True)
    
    # Process insights
    process_insights = db.Column(JSONB, default=dict)  # {metric: value}
    
    # Recorded by
    recorded_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Used for retraining
    used_for_retraining = db.Column(db.Boolean, default=False)
    retraining_version = db.Column(db.String(50), nullable=True)  # v2.1, v3.0, etc.
    
    # Relationships (none - to avoid circular imports)
    # FK references defined but relationships lazy-loaded on demand
    
    # Indexes
    __table_args__ = (
        db.Index('idx_outcome_solution_id', 'solution_id'),
        db.Index('idx_outcome_recorded_at', 'recorded_at'),
        db.Index('idx_outcome_used_for_retraining', 'used_for_retraining'),
    )
    
    def __repr__(self):
        return f'<OutcomeTracking solution={self.solution_id} accuracy={self.timeline_accuracy_percentage}%>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'solution_id': self.solution_id,
            'project_completed_at': self.project_completed_at.isoformat() if self.project_completed_at else None,
            'predicted_duration_weeks': self.predicted_duration_weeks,
            'actual_duration_weeks': self.actual_duration_weeks,
            'timeline_accuracy_percentage': self.timeline_accuracy_percentage,
            'predicted_cost_usd': self.predicted_cost_usd,
            'actual_cost_usd': self.actual_cost_usd,
            'cost_accuracy_percentage': self.cost_accuracy_percentage,
            'vendor_performance': self.vendor_performance,
            'predicted_risks': self.predicted_risks,
            'realized_risks': self.realized_risks,
            'unforecast_risks': self.unforecast_risks,
            'risk_accuracy_percentage': self.risk_accuracy_percentage,
            'lessons_learned': self.lessons_learned,
            'what_went_well': self.what_went_well,
            'what_to_improve': self.what_to_improve,
            'business_value_realized': self.business_value_realized,
            'estimated_business_value_usd': self.estimated_business_value_usd,
            'roi_percentage': self.roi_percentage,
            'process_insights': self.process_insights,
            'recorded_at': self.recorded_at.isoformat() if self.recorded_at else None,
        }


class SolutionAIBacktesting(TenantMixin, db.Model):
    """
    AI Backtesting Framework: Validates AI recommendations against actual project outcomes.
    
    Records predictions from the AI orchestrator (vendors, costs, timelines, risks)
    against actual implementation results to calculate accuracy metrics.
    
    Purpose:
    - Measure AI model accuracy (MAPE: Mean Absolute Percentage Error)
    - Track confidence interval calibration
    - Identify systematic biases in recommendations
    - Enable continuous learning and model improvement
    """
    
    __tablename__ = 'solution_ai_backtesting'
    __tenant_parent__ = (('solution_id', 'solutions'),)
    
    # Primary Key
    id = db.Column(db.Integer, primary_key=True)
    
    # Foreign Keys
    solution_id = db.Column(
        db.Integer,
        db.ForeignKey('solutions.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    
    # Recommendation type being validated
    recommendation_type = db.Column(
        db.String(50),
        nullable=False,
        index=True
    )  # vendor, cost, timeline, risk
    
    # AI Predicted Value
    predicted_value = db.Column(JSONB, nullable=False)  # {vendor: "SAP", confidence: 0.92}
    predicted_confidence = db.Column(db.Float, nullable=False)  # 0.0-1.0
    
    # Actual Implementation Outcome
    actual_value = db.Column(JSONB, nullable=False)  # {vendor: "SAP"} or {cost: 2500000}
    
    # Accuracy Metrics
    accuracy_pct = db.Column(db.Float, nullable=True)  # How close prediction was to actual (0-100)
    error_magnitude = db.Column(db.Float, nullable=True)  # Absolute error (for quantitative metrics)
    error_percentage = db.Column(db.Float, nullable=True)  # Relative error for MAPE calculation
    
    # Confidence Calibration
    confidence_interval_lower = db.Column(db.Float, nullable=True)  # ±% lower bound
    confidence_interval_upper = db.Column(db.Float, nullable=True)  # ±% upper bound
    calibration_status = db.Column(
        db.String(50),
        default='uncalibrated'
    )  # calibrated, over_confident, under_confident
    
    # Metadata
    reasoning_state_id = db.Column(
        db.Integer,
        db.ForeignKey('solution_ai_reasoning_states.id', ondelete='SET NULL'),
        nullable=True
    )  # Link to original AI reasoning
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes for efficient querying
    __table_args__ = (
        db.Index('idx_backtesting_solution_id', 'solution_id'),
        db.Index('idx_backtesting_rec_type', 'recommendation_type'),
        db.Index('idx_backtesting_created_at', 'created_at'),
        db.Index('idx_backtesting_calibration', 'calibration_status'),
    )
    
    def __repr__(self):
        return f'<SolutionAIBacktesting solution={self.solution_id} type={self.recommendation_type} accuracy={self.accuracy_pct}%>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'solution_id': self.solution_id,
            'recommendation_type': self.recommendation_type,
            'predicted_value': self.predicted_value,
            'predicted_confidence': self.predicted_confidence,
            'actual_value': self.actual_value,
            'accuracy_pct': self.accuracy_pct,
            'error_magnitude': self.error_magnitude,
            'error_percentage': self.error_percentage,
            'confidence_interval_lower': self.confidence_interval_lower,
            'confidence_interval_upper': self.confidence_interval_upper,
            'calibration_status': self.calibration_status,
            'reasoning_state_id': self.reasoning_state_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class SolutionNotification(TenantMixin, db.Model):
    """In-app notifications for solution lifecycle events (phase advance, ARB submission, etc.)."""
    __tablename__ = 'solution_notifications'
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey('solutions.id', ondelete='CASCADE'), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    type = db.Column(db.String(50), nullable=False, index=True)  # phase_advance, arb_submission, etc.
    message = db.Column(db.Text, nullable=False)
    read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index('idx_solution_notif_user_read', 'user_id', 'read'),
    )

    def __repr__(self):
        return f'<SolutionNotification {self.id} {self.type} user={self.user_id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'solution_id': self.solution_id,
            'user_id': self.user_id,
            'type': self.type,
            'message': self.message,
            'read': self.read,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class SolutionTemplate(TenantMixin, db.Model):
    """Reusable solution architecture template (ENT-016)."""
    __tablename__ = 'solution_templates'
    __tenant_parent__ = (('source_solution_id', 'solutions'), ('created_by_id', 'users'),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    domain = db.Column(db.String(100), nullable=True)
    template_json = db.Column(db.Text, nullable=False)  # JSON blob of entity lists
    # source_solution_id tracks which solution this template was saved from (ENT-016 AC)
    source_solution_id = db.Column(db.Integer, db.ForeignKey('solutions.id', ondelete='SET NULL'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    usage_count = db.Column(db.Integer, default=0, nullable=False)

    @property
    def template_data(self):
        """Alias for template_json parsed as dict (ENT-016 acceptance criteria)."""
        import json
        if not self.template_json:
            return {}
        try:
            return json.loads(self.template_json)
        except (ValueError, TypeError):
            return {}

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'domain': self.domain or '',
            'source_solution_id': self.source_solution_id,
            'created_by_id': self.created_by_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'usage_count': self.usage_count,
        }
