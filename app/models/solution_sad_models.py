"""Solution Architecture Document (SAD) gap models.

14 models completing TOGAF SAD coverage. All are solution-scoped
(solution_id FK) and follow the same CRUD pattern.

Design doc: docs/plans/2026-03-02-solution-sad-models-design.md
"""

from datetime import datetime, date

from app import db


class SolutionIntegrationFlow(db.Model):
    """Data/integration flow between applications within a solution.

    Junction model: auto-creates ArchiMateRelationship(type='flow') on creation.
    """
    __tablename__ = "solution_integration_flows"
    __table_args__ = (
        db.UniqueConstraint("solution_id", "source_app_id", "target_app_id", "flow_name",
                            name="uq_solution_integration_flow"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    source_app_id = db.Column(db.Integer, db.ForeignKey("application_components.id"), nullable=False)
    target_app_id = db.Column(db.Integer, db.ForeignKey("application_components.id"), nullable=False)
    interface_id = db.Column(db.Integer, db.ForeignKey("application_interfaces.id"), nullable=True)
    archimate_flow_id = db.Column(db.Integer, db.ForeignKey("archimate_relationships.id"), nullable=True)

    flow_name = db.Column(db.String(255), nullable=False)
    flow_type = db.Column(db.String(30), default="sync")
    flow_direction = db.Column(db.String(20), default="outbound")
    protocol = db.Column(db.String(50), nullable=True)
    data_format = db.Column(db.String(50), nullable=True)

    # Communication type properties (RUNTIME-01)
    communication_type = db.Column(db.String(30), nullable=True)  # sync_rest, sync_grpc, async_event, async_queue, batch_file
    message_format = db.Column(db.String(20), nullable=True)  # json, avro, protobuf, xml
    auth_method = db.Column(db.String(30), nullable=True)  # none, api_key, oauth2, sasl_ssl, mtls
    topic_or_queue = db.Column(db.String(200), nullable=True)  # topic/queue name for async
    dlq_enabled = db.Column(db.Boolean, default=False)

    frequency = db.Column(db.String(30), nullable=True)
    volume_per_period = db.Column(db.Integer, nullable=True)
    latency_sla_ms = db.Column(db.Integer, nullable=True)
    throughput_tps = db.Column(db.Integer, nullable=True)

    criticality = db.Column(db.String(20), default="medium")
    encryption_required = db.Column(db.Boolean, default=True)
    contains_pii = db.Column(db.Boolean, default=False)
    error_handling = db.Column(db.String(50), nullable=True)

    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Integration Architecture Governance (INTARCH-001) — added via ALTER TABLE
    pattern_id = db.Column(db.Integer, db.ForeignKey("integration_patterns.id"), nullable=True)
    governance_status = db.Column(db.String(30), default="undocumented")

    # Last check of this interface against the pattern catalogue
    # (ConformanceReviewer.check_interfaces). Nullable: an interface that was
    # never checked has neither, and renders as "not checked yet".
    conformance_checked_at = db.Column(db.DateTime, nullable=True)
    conformance_breaches = db.Column(db.JSON, nullable=True)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "source_app_id": self.source_app_id, "target_app_id": self.target_app_id,
            "interface_id": self.interface_id, "archimate_flow_id": self.archimate_flow_id,
            "flow_name": self.flow_name, "flow_type": self.flow_type,
            "flow_direction": self.flow_direction, "protocol": self.protocol,
            "data_format": self.data_format,
            "communication_type": self.communication_type,
            "message_format": self.message_format,
            "auth_method": self.auth_method,
            "topic_or_queue": self.topic_or_queue,
            "dlq_enabled": self.dlq_enabled,
            "frequency": self.frequency,
            "volume_per_period": self.volume_per_period, "latency_sla_ms": self.latency_sla_ms,
            "throughput_tps": self.throughput_tps, "criticality": self.criticality,
            "encryption_required": self.encryption_required, "contains_pii": self.contains_pii,
            "error_handling": self.error_handling, "notes": self.notes,
            "pattern_id": self.pattern_id,
            "governance_status": self.governance_status or "undocumented",
            "conformance_checked_at": (
                self.conformance_checked_at.isoformat() if self.conformance_checked_at else None
            ),
            "conformance_breaches": self.conformance_breaches,
        }


class SolutionComposition(db.Model):
    """How components interlock within the solution."""
    __tablename__ = "solution_compositions"
    __table_args__ = (
        db.UniqueConstraint("solution_id", "component_type", "component_id",
                            name="uq_solution_composition"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    component_type = db.Column(db.String(30), nullable=False)
    component_id = db.Column(db.Integer, nullable=False)
    component_name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="supporting")
    criticality = db.Column(db.String(20), default="medium")
    coupling = db.Column(db.String(30), default="loosely_coupled")
    failure_impact = db.Column(db.String(50), default="degrades_solution")
    replacement_difficulty = db.Column(db.String(20), default="moderate")
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "component_type": self.component_type, "component_id": self.component_id,
            "component_name": self.component_name, "role": self.role,
            "criticality": self.criticality, "coupling": self.coupling,
            "failure_impact": self.failure_impact, "replacement_difficulty": self.replacement_difficulty,
            "notes": self.notes,
        }


class RiskSnapshot(db.Model):
    """Time-series risk tracking across ADM phases."""
    __tablename__ = "solution_risk_snapshots"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    risk_name = db.Column(db.String(255), nullable=False)
    risk_category = db.Column(db.String(50), default="technical")
    adm_phase = db.Column(db.String(5), nullable=True)
    impact = db.Column(db.Integer, default=3)
    probability = db.Column(db.Integer, default=3)
    risk_score = db.Column(db.Integer, default=9)
    trend = db.Column(db.String(20), default="stable")
    mitigation_status = db.Column(db.String(30), default="identified")
    snapshot_date = db.Column(db.Date, default=date.today)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "risk_name": self.risk_name, "risk_category": self.risk_category,
            "adm_phase": self.adm_phase, "impact": self.impact,
            "probability": self.probability, "risk_score": self.risk_score,
            "trend": self.trend, "mitigation_status": self.mitigation_status,
            "snapshot_date": self.snapshot_date.isoformat() if self.snapshot_date else None,
            "notes": self.notes,
        }


class SolutionQualityAttribute(db.Model):
    """Maps architecture principles/constraints to testable quality criteria."""
    __tablename__ = "solution_quality_attributes"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    attribute_name = db.Column(db.String(255), nullable=False)
    attribute_type = db.Column(db.String(50), default="performance")
    principle_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True)
    constraint_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True)
    target_value = db.Column(db.String(100), nullable=True)
    current_value = db.Column(db.String(100), nullable=True)
    verification_method = db.Column(db.String(50), nullable=True)
    test_status = db.Column(db.String(30), default="not_tested")
    evidence_link = db.Column(db.String(500), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "attribute_name": self.attribute_name, "attribute_type": self.attribute_type,
            "principle_id": self.principle_id, "constraint_id": self.constraint_id,
            "target_value": self.target_value, "current_value": self.current_value,
            "verification_method": self.verification_method, "test_status": self.test_status,
            "evidence_link": self.evidence_link, "notes": self.notes,
        }


class SolutionSLA(db.Model):
    """Solution-level performance contract."""
    __tablename__ = "solution_slas"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    sla_name = db.Column(db.String(255), nullable=False)
    availability_target = db.Column(db.Numeric(5, 3), nullable=True)
    response_time_ms = db.Column(db.Integer, nullable=True)
    throughput_tps = db.Column(db.Integer, nullable=True)
    rto_hours = db.Column(db.Integer, nullable=True)
    rpo_hours = db.Column(db.Integer, nullable=True)
    support_hours = db.Column(db.String(50), nullable=True)
    escalation_contact = db.Column(db.String(255), nullable=True)
    penalty_clause = db.Column(db.Text, nullable=True)
    effective_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default="draft")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "sla_name": self.sla_name,
            "availability_target": float(self.availability_target) if self.availability_target else None,
            "response_time_ms": self.response_time_ms, "throughput_tps": self.throughput_tps,
            "rto_hours": self.rto_hours, "rpo_hours": self.rpo_hours,
            "support_hours": self.support_hours, "escalation_contact": self.escalation_contact,
            "penalty_clause": self.penalty_clause,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "status": self.status,
        }


class MigrationDependency(db.Model):
    """Ordering constraints between transition plateaus."""
    __tablename__ = "solution_migration_dependencies"
    __table_args__ = (
        db.UniqueConstraint("solution_id", "from_plateau_id", "to_plateau_id",
                            name="uq_migration_dependency"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    from_plateau_id = db.Column(db.Integer, db.ForeignKey("solution_plateaus.id"), nullable=False)
    to_plateau_id = db.Column(db.Integer, db.ForeignKey("solution_plateaus.id"), nullable=False)
    dependency_type = db.Column(db.String(30), default="strict_precedence")
    condition_description = db.Column(db.Text, nullable=True)
    lag_days = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "from_plateau_id": self.from_plateau_id, "to_plateau_id": self.to_plateau_id,
            "dependency_type": self.dependency_type,
            "condition_description": self.condition_description,
            "lag_days": self.lag_days, "notes": self.notes,
        }


class SolutionInvestmentPhase(db.Model):
    """Funding allocation per plateau/tranche."""
    __tablename__ = "solution_investment_phases"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    plateau_id = db.Column(db.Integer, db.ForeignKey("solution_plateaus.id"), nullable=True)
    phase_name = db.Column(db.String(255), nullable=False)
    phase_number = db.Column(db.Integer, default=1)
    authorized_amount = db.Column(db.Numeric(15, 2), nullable=True)
    currency = db.Column(db.String(3), default="GBP")
    authorized_date = db.Column(db.Date, nullable=True)
    spend_to_date = db.Column(db.Numeric(15, 2), default=0)
    forecast_spend = db.Column(db.Numeric(15, 2), nullable=True)
    variance_pct = db.Column(db.Numeric(5, 2), nullable=True)
    funding_source = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default="pending")
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "plateau_id": self.plateau_id, "phase_name": self.phase_name,
            "phase_number": self.phase_number,
            "authorized_amount": float(self.authorized_amount) if self.authorized_amount else None,
            "currency": self.currency,
            "authorized_date": self.authorized_date.isoformat() if self.authorized_date else None,
            "spend_to_date": float(self.spend_to_date) if self.spend_to_date else 0,
            "forecast_spend": float(self.forecast_spend) if self.forecast_spend else None,
            "variance_pct": float(self.variance_pct) if self.variance_pct else None,
            "funding_source": self.funding_source, "status": self.status, "notes": self.notes,
        }


class SolutionGovernanceException(db.Model):
    """Approved deviations from architecture principles."""
    __tablename__ = "solution_governance_exceptions"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    principle_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True)
    principle_name = db.Column(db.String(255), nullable=True)
    exception_description = db.Column(db.Text, nullable=False)
    justification = db.Column(db.Text, nullable=True)
    risk_accepted = db.Column(db.Text, nullable=True)
    approved = db.Column(db.Boolean, default=False)
    approver_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approval_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    mitigation_plan = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="requested")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "principle_id": self.principle_id, "principle_name": self.principle_name,
            "exception_description": self.exception_description,
            "justification": self.justification, "risk_accepted": self.risk_accepted,
            "approved": self.approved,
            "approval_date": self.approval_date.isoformat() if self.approval_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "mitigation_plan": self.mitigation_plan, "status": self.status,
        }


class SolutionComplianceMapping(db.Model):
    """Solution components mapped to regulatory/compliance controls."""
    __tablename__ = "solution_compliance_mappings"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True)
    element_name = db.Column(db.String(255), nullable=True)
    framework = db.Column(db.String(50), nullable=False)
    control_id = db.Column(db.String(50), nullable=False)
    control_description = db.Column(db.Text, nullable=True)
    verification_status = db.Column(db.String(30), default="not_assessed")
    evidence_link = db.Column(db.String(500), nullable=True)
    last_assessed_date = db.Column(db.Date, nullable=True)
    assessor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    next_review_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "archimate_element_id": self.archimate_element_id, "element_name": self.element_name,
            "framework": self.framework, "control_id": self.control_id,
            "control_description": self.control_description,
            "verification_status": self.verification_status,
            "evidence_link": self.evidence_link,
            "last_assessed_date": self.last_assessed_date.isoformat() if self.last_assessed_date else None,
            "next_review_date": self.next_review_date.isoformat() if self.next_review_date else None,
            "notes": self.notes,
        }


class SolutionChangeRequest(db.Model):
    """Architecture change tracking through ADM lifecycle."""
    __tablename__ = "solution_change_requests"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    change_type = db.Column(db.String(30), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    justification = db.Column(db.Text, nullable=True)
    impact_assessment = db.Column(db.Text, nullable=True)
    affected_phase = db.Column(db.String(5), nullable=True)
    priority = db.Column(db.String(20), default="medium")
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    submitted_date = db.Column(db.Date, default=date.today)
    decision = db.Column(db.String(20), default="pending")
    decision_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    decision_date = db.Column(db.Date, nullable=True)
    decision_rationale = db.Column(db.Text, nullable=True)
    effective_date = db.Column(db.Date, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "change_type": self.change_type, "title": self.title,
            "description": self.description, "justification": self.justification,
            "impact_assessment": self.impact_assessment,
            "affected_phase": self.affected_phase, "priority": self.priority,
            "submitted_date": self.submitted_date.isoformat() if self.submitted_date else None,
            "decision": self.decision,
            "decision_date": self.decision_date.isoformat() if self.decision_date else None,
            "decision_rationale": self.decision_rationale,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
        }


class SolutionFeasibilityReview(db.Model):
    """Formal gate with decision rationale per ADM phase."""
    __tablename__ = "solution_feasibility_reviews"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    review_phase = db.Column(db.String(5), nullable=True)
    review_type = db.Column(db.String(30), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    feasible = db.Column(db.Boolean, nullable=True)
    confidence_level = db.Column(db.String(20), default="medium")
    constraints_violated = db.Column(db.JSON, nullable=True)
    technical_risks = db.Column(db.Text, nullable=True)
    mitigation_plan = db.Column(db.Text, nullable=True)
    contingency_plan = db.Column(db.Text, nullable=True)
    recommendation = db.Column(db.String(30), default="proceed")
    conditions = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "review_phase": self.review_phase, "review_type": self.review_type,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "feasible": self.feasible, "confidence_level": self.confidence_level,
            "constraints_violated": self.constraints_violated,
            "technical_risks": self.technical_risks, "mitigation_plan": self.mitigation_plan,
            "contingency_plan": self.contingency_plan, "recommendation": self.recommendation,
            "conditions": self.conditions, "notes": self.notes,
        }


class SolutionBenefitRealization(db.Model):
    """Tracks actual value delivery against targets."""
    __tablename__ = "solution_benefit_realizations"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    benefit_name = db.Column(db.String(255), nullable=True)
    benefit_type = db.Column(db.String(30), default="operational")
    metric_name = db.Column(db.String(255), nullable=True)
    baseline_value = db.Column(db.Numeric(15, 2), nullable=True)
    baseline_date = db.Column(db.Date, nullable=True)
    target_value = db.Column(db.Numeric(15, 2), nullable=True)
    target_date = db.Column(db.Date, nullable=True)
    current_value = db.Column(db.Numeric(15, 2), nullable=True)
    measurement_date = db.Column(db.Date, nullable=True)
    variance_pct = db.Column(db.Numeric(5, 2), nullable=True)
    status = db.Column(db.String(20), default="not_started")
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    measurement_frequency = db.Column(db.String(20), default="monthly")
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Aggregate reporting period fields
    reporting_period_start = db.Column(db.Date, nullable=True)
    reporting_period_end = db.Column(db.Date, nullable=True)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "benefit_name": self.benefit_name, "benefit_type": self.benefit_type,
            "metric_name": self.metric_name,
            "baseline_value": float(self.baseline_value) if self.baseline_value else None,
            "baseline_date": self.baseline_date.isoformat() if self.baseline_date else None,
            "target_value": float(self.target_value) if self.target_value else None,
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "current_value": float(self.current_value) if self.current_value else None,
            "measurement_date": self.measurement_date.isoformat() if self.measurement_date else None,
            "variance_pct": float(self.variance_pct) if self.variance_pct else None,
            "status": self.status, "measurement_frequency": self.measurement_frequency,
            "notes": self.notes,
        }


class SolutionOrgImpact(db.Model):
    """Organization design change impacts."""
    __tablename__ = "solution_org_impacts"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    impact_area = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=True)
    current_state = db.Column(db.Text, nullable=True)
    target_state = db.Column(db.Text, nullable=True)
    headcount_delta = db.Column(db.Integer, default=0)
    roles_affected = db.Column(db.JSON, nullable=True)
    reskilling_required = db.Column(db.Boolean, default=False)
    reskilling_description = db.Column(db.Text, nullable=True)
    change_readiness = db.Column(db.String(20), default="unknown")
    change_management_plan = db.Column(db.Text, nullable=True)
    timeline_months = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "impact_area": self.impact_area, "description": self.description,
            "current_state": self.current_state, "target_state": self.target_state,
            "headcount_delta": self.headcount_delta, "roles_affected": self.roles_affected,
            "reskilling_required": self.reskilling_required,
            "reskilling_description": self.reskilling_description,
            "change_readiness": self.change_readiness,
            "change_management_plan": self.change_management_plan,
            "timeline_months": self.timeline_months, "notes": self.notes,
        }


class SolutionLessonLearned(db.Model):
    """Organizational learning registry."""
    __tablename__ = "solution_lessons_learned"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    adm_phase = db.Column(db.String(5), nullable=True)
    category = db.Column(db.String(30), default="technical")
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    root_cause = db.Column(db.Text, nullable=True)
    recommendation = db.Column(db.Text, nullable=True)
    impact = db.Column(db.String(20), default="medium")
    applicable_to_future = db.Column(db.Boolean, default=True)
    tags = db.Column(db.JSON, nullable=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    date_captured = db.Column(db.Date, default=date.today)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "adm_phase": self.adm_phase, "category": self.category,
            "title": self.title, "description": self.description,
            "root_cause": self.root_cause, "recommendation": self.recommendation,
            "impact": self.impact, "applicable_to_future": self.applicable_to_future,
            "tags": self.tags,
            "date_captured": self.date_captured.isoformat() if self.date_captured else None,
        }


class SolutionStakeholderSAD(db.Model):
    """Stakeholder register for a solution (SAD layer)."""
    __tablename__ = "solution_stakeholders_sad"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(100), nullable=False)
    organization = db.Column(db.String(255), nullable=True)
    influence_level = db.Column(db.String(20), default="medium")
    interest_level = db.Column(db.String(20), default="medium")
    engagement_strategy = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "name": self.name, "role": self.role,
            "organization": self.organization,
            "influence_level": self.influence_level,
            "interest_level": self.interest_level,
            "engagement_strategy": self.engagement_strategy,
            "notes": self.notes,
            "created_by_id": self.created_by_id,
        }


class SolutionPrincipleSAD(db.Model):
    """Architecture principles governing a solution."""
    __tablename__ = "solution_principles_sad"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    statement = db.Column(db.Text, nullable=True)
    rationale = db.Column(db.Text, nullable=True)
    implications = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "name": self.name, "statement": self.statement,
            "rationale": self.rationale, "implications": self.implications,
        }


class SolutionAssessmentSAD(db.Model):
    """Architecture assessment / gap findings for a solution."""
    __tablename__ = "solution_assessments_sad"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    assessment_type = db.Column(db.String(50), default="gap")
    finding = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), default="medium")
    recommendation = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "assessment_type": self.assessment_type,
            "finding": self.finding, "severity": self.severity,
            "recommendation": self.recommendation,
        }


class SolutionBusinessElement(db.Model):
    """Business-layer ArchiMate element captured in a solution."""
    __tablename__ = "solution_business_elements"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    element_type = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    owner = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "element_type": self.element_type, "name": self.name,
            "description": self.description, "owner": self.owner, "notes": self.notes,
        }


class SolutionAppElement(db.Model):
    """Application-layer ArchiMate element captured in a solution."""
    __tablename__ = "solution_app_elements"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    element_type = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    technology = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Blueprint-to-Code: LLM-proposed + architect-confirmed field specifications.  # migration-exempt
    # Stores confirmed schema fields, types, validation rules, and relationships.
    # Once confirmed, spec generator uses these instead of generic CRUD schemas.
    # Nullable JSON — db.create_all() adds it; won't break existing queries.
    code_spec = db.Column(db.JSON, nullable=True)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "element_type": self.element_type, "name": self.name,
            "description": self.description, "technology": self.technology, "notes": self.notes,
            "code_spec": self.code_spec,
        }


class SolutionTechElement(db.Model):
    """Technology-layer ArchiMate element captured in a solution."""
    __tablename__ = "solution_tech_elements"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    element_type = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    specification = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "solution_id": self.solution_id,
            "element_type": self.element_type, "name": self.name,
            "description": self.description, "specification": self.specification, "notes": self.notes,
        }


class SolutionADRDirect(db.Model):
    """Direct link between a solution and an Architecture Decision Record."""
    __tablename__ = "solution_adr_direct"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    adr_id = db.Column(db.Integer, db.ForeignKey("architecture_decision_records.id", ondelete="CASCADE"), nullable=False, index=True)
    linked_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("solution_id", "adr_id", name="uq_solution_adr_direct"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "solution_id": self.solution_id,
            "adr_id": self.adr_id,
            "linked_by_id": self.linked_by_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SolutionAPQCProcess(db.Model):
    """Direct link between a solution and an APQC process."""
    __tablename__ = "solution_apqc_processes"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    apqc_process_id = db.Column(db.Integer, db.ForeignKey("apqc_process.id", ondelete="CASCADE"), nullable=False, index=True)
    linked_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("solution_id", "apqc_process_id", name="uq_solution_apqc_process"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "solution_id": self.solution_id,
            "apqc_process_id": self.apqc_process_id,
            "linked_by_id": self.linked_by_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
