"""
Hierarchical decomposition models for Vendor Technology Stack Templates

These models provide normalized tables for:
- Capability hierarchy (L0 - L4 decomposition)
- Service catalog with hierarchy and contracts
- Process hierarchy with BPMN decomposition
- Component architecture with layering
- Integration catalog with patterns

This enables proper Enterprise Architecture and Solution Architecture intelligence.
"""
from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class VendorCapabilityHierarchy(TenantMixin, db.Model):
    """
    Hierarchical capability decomposition (L0 - L4).
    Enables gap analysis, heat maps, and build-vs-buy decisions at different abstraction levels.
    """

    __tablename__ = "vendor_capability_hierarchy"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(
        db.Integer, db.ForeignKey("vendor_stack_templates.id"), nullable=False, index=True
    )
    parent_id = db.Column(db.Integer, db.ForeignKey("vendor_capability_hierarchy.id"), index=True)

    # Hierarchy metadata
    level = db.Column(db.Integer, nullable=False)  # 0=Domain, 1=Core, 2=Sub, 3=Micro, 4=Feature
    capability_code = db.Column(db.String(50), index=True)  # e.g., "CRM.LEAD.SCORE"
    capability_name = db.Column(db.String(200), nullable=False)
    capability_description = db.Column(db.Text)

    # Capability attributes
    coverage_percentage = db.Column(db.Float)  # How much of this capability is covered
    maturity_level = db.Column(db.String(50))  # Initial/Managed/Defined/Quantitative/Optimizing
    implementation_complexity = db.Column(db.String(20))  # low/medium/high
    license_tier_required = db.Column(db.String(50))  # Which license tier enables this

    # Business value
    business_criticality = db.Column(db.String(20))  # critical/high/medium/low
    competitive_advantage = db.Column(db.String(20))  # differentiator/parity/basic
    automation_potential = db.Column(db.Float)  # 0 - 100%

    # Capability requirements
    prerequisites = db.Column(db.Text)  # JSON: [{capability_id, description}]
    dependencies = db.Column(db.Text)  # JSON: [{capability_id, dependency_type}]
    related_capabilities = db.Column(db.Text)  # JSON: [{capability_id, relationship_type}]

    # Standards alignment
    togaf_capability_mapping = db.Column(db.String(100))
    apqc_pcf_mapping = db.Column(db.String(100))  # APQC Process Classification Framework
    cobit_mapping = db.Column(db.String(100))

    # System fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    template = db.relationship("VendorStackTemplate", backref="capability_hierarchy")
    parent = db.relationship("VendorCapabilityHierarchy", remote_side=[id], backref="children")

    def __repr__(self):
        return f"<VendorCapabilityHierarchy L{self.level}: {self.capability_name}>"


class VendorServiceCatalog(TenantMixin, db.Model):
    """
    Service catalog with hierarchy, contracts, and versioning.
    Maps business services to application services with SLAs and dependencies.
    """

    __tablename__ = "vendor_service_catalog"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(
        db.Integer, db.ForeignKey("vendor_stack_templates.id"), nullable=False, index=True
    )
    parent_service_id = db.Column(
        db.Integer, db.ForeignKey("vendor_service_catalog.id"), index=True
    )

    # Service identification
    service_code = db.Column(db.String(50), index=True)  # e.g., "SVC-CRM-LEAD - 001"
    service_name = db.Column(db.String(200), nullable=False)
    service_description = db.Column(db.Text)

    # Service classification
    service_type = db.Column(db.String(50))  # Core/Supporting/Enabling
    service_layer = db.Column(db.String(50))  # Business/Application/Technology
    service_level = db.Column(
        db.Integer
    )  # Hierarchy depth (1=top level, 2=child, 3=grandchild, etc.)

    # Service contract
    input_schema = db.Column(db.Text)  # JSON schema for inputs
    output_schema = db.Column(db.Text)  # JSON schema for outputs
    preconditions = db.Column(db.Text)  # JSON: [{condition, description}]
    postconditions = db.Column(db.Text)  # JSON: [{condition, description}]
    invariants = db.Column(db.Text)  # JSON: [{invariant, description}]

    # Service characteristics
    service_style = db.Column(
        db.String(50)
    )  # Synchronous/Asynchronous/Batch/Event-driven/Streaming
    idempotent = db.Column(db.Boolean, default=False)
    stateless = db.Column(db.Boolean, default=True)
    cacheable = db.Column(db.Boolean, default=False)
    cache_ttl_seconds = db.Column(db.Integer)

    # SLA commitments
    sla_availability_percentage = db.Column(db.Float)  # 99.9%, 99.99%, etc.
    sla_response_time_ms = db.Column(db.Integer)  # p50 response time
    sla_response_time_p95_ms = db.Column(db.Integer)  # p95 response time
    sla_response_time_p99_ms = db.Column(db.Integer)  # p99 response time
    sla_throughput_tps = db.Column(db.Integer)  # Transactions per second
    sla_error_rate_percentage = db.Column(db.Float)  # Maximum error rate

    # Versioning
    versioned = db.Column(db.Boolean, default=False)
    current_version = db.Column(db.String(20))
    supported_versions = db.Column(db.Text)  # JSON: [{version, support_end_date, breaking_changes}]
    deprecation_policy = db.Column(db.Text)

    # Access channels
    channels = db.Column(db.Text)  # JSON: [{channel_type, protocol, endpoint, authentication}]
    consumers = db.Column(db.Text)  # JSON: [{consumer_type, consumer_name, usage_pattern}]

    # Dependencies
    upstream_services = db.Column(db.Text)  # JSON: [{service_id, dependency_type, criticality}]
    downstream_services = db.Column(db.Text)  # JSON: [{service_id, notification_type}]

    # Composition
    orchestrates_services = db.Column(db.Text)  # JSON: [{service_id, sequence, conditional}]
    choreography_pattern = db.Column(db.String(100))  # Orchestration/Choreography/Saga/Event-driven

    # Operations
    operations = db.Column(db.Text)  # JSON: [{operation_name, http_method, path, description}]
    events_published = db.Column(db.Text)  # JSON: [{event_type, schema, frequency}]
    events_subscribed = db.Column(db.Text)  # JSON: [{event_type, handler, retry_policy}]

    # Monitoring & observability
    key_metrics = db.Column(db.Text)  # JSON: [{metric_name, unit, threshold, alert_severity}]
    logging_level = db.Column(db.String(20))  # DEBUG/INFO/WARN/ERROR
    tracing_enabled = db.Column(db.Boolean, default=True)

    # Governance
    service_owner = db.Column(db.String(100))
    business_owner = db.Column(db.String(100))
    lifecycle_stage = db.Column(db.String(50))  # Proposed/Development/Active/Deprecated/Retired
    retirement_date = db.Column(db.Date)

    # System fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    template = db.relationship("VendorStackTemplate", backref="service_catalog")
    parent_service = db.relationship(
        "VendorServiceCatalog", remote_side=[id], backref="child_services"
    )

    def __repr__(self):
        return f"<VendorServiceCatalog {self.service_code}: {self.service_name}>"


class VendorProcessHierarchy(TenantMixin, db.Model):
    """
    Business process hierarchy with BPMN decomposition (L0 - L4).
    Enables process mining, optimization, and automation opportunities.
    """

    __tablename__ = "vendor_process_hierarchy"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(
        db.Integer, db.ForeignKey("vendor_stack_templates.id"), nullable=False, index=True
    )
    parent_id = db.Column(db.Integer, db.ForeignKey("vendor_process_hierarchy.id"), index=True)

    # Process identification
    process_code = db.Column(db.String(50), index=True)  # e.g., "P-CRM-LEAD-MGMT"
    process_name = db.Column(db.String(200), nullable=False)
    process_description = db.Column(db.Text)
    process_level = db.Column(db.Integer)  # 0=Category, 1=Group, 2=Process, 3=Activity, 4=Task

    # Process attributes
    process_type = db.Column(db.String(50))  # Core/Support/Management
    automation_level = db.Column(db.String(50))  # Manual/Partially-automated/Fully-automated
    automation_percentage = db.Column(db.Float)  # 0 - 100%

    # Process maturity
    maturity_level = db.Column(db.String(50))  # Initial/Managed/Defined/Quantitative/Optimizing
    cmmi_level = db.Column(db.Integer)  # 1 - 5
    standardization_level = db.Column(db.String(50))  # Ad-hoc/Standardized/Optimized

    # Process steps (only for leaf processes)
    steps = db.Column(
        db.Text
    )  # JSON: [{step_num, name, description, role, system, duration_minutes, decision_points}]
    variants = db.Column(db.Text)  # JSON: [{variant_name, conditions, steps, frequency_percentage}]
    decision_points = db.Column(
        db.Text
    )  # JSON: [{decision_name, criteria, outcomes, routing_rules}]

    # Process metrics
    cycle_time_minutes = db.Column(db.Integer)  # Average end-to-end time
    cycle_time_p95_minutes = db.Column(db.Integer)
    processing_time_minutes = db.Column(db.Integer)  # Actual work time (excluding wait)
    wait_time_minutes = db.Column(db.Integer)  # Queue/handoff time
    throughput_per_day = db.Column(db.Integer)  # Volume handled daily

    # Process performance (KPIs)
    kpis = db.Column(db.Text)  # JSON: [{kpi_name, target, actual, unit, measurement_frequency}]
    error_rate_percentage = db.Column(db.Float)
    rework_rate_percentage = db.Column(db.Float)
    first_time_right_percentage = db.Column(db.Float)

    # Process roles (RACI)
    roles = db.Column(db.Text)  # JSON: [{step, responsible, accountable, consulted, informed}]

    # Process controls
    approval_gates = db.Column(db.Text)  # JSON: [{gate_name, approver_role, criteria, sla_hours}]
    compliance_checkpoints = db.Column(
        db.Text
    )  # JSON: [{checkpoint, regulation, verification_method, evidence}]
    audit_trail = db.Column(db.Boolean, default=True)
    segregation_of_duties = db.Column(db.Text)  # JSON: [{conflict, separation_rule}]

    # Process integration
    system_touchpoints = db.Column(
        db.Text
    )  # JSON: [{system_name, interaction_type, data_exchanged}]
    integration_points = db.Column(
        db.Text
    )  # JSON: [{integration_name, system, direction, protocol, frequency}]

    # Process improvement
    bottlenecks = db.Column(
        db.Text
    )  # JSON: [{bottleneck_name, impact, root_cause, recommendation}]
    automation_opportunities = db.Column(
        db.Text
    )  # JSON: [{opportunity, effort, benefit, roi_months}]
    optimization_recommendations = db.Column(
        db.Text
    )  # JSON: [{recommendation, impact, implementation_effort}]

    # Process compliance
    regulatory_mappings = db.Column(
        db.Text
    )  # JSON: [{regulation, requirement, control_id, evidence}]
    sox_controlled = db.Column(db.Boolean, default=False)
    gdpr_relevant = db.Column(db.Boolean, default=False)
    hipaa_relevant = db.Column(db.Boolean, default=False)

    # System fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    template = db.relationship("VendorStackTemplate", backref="process_hierarchy")
    parent = db.relationship("VendorProcessHierarchy", remote_side=[id], backref="children")

    def __repr__(self):
        return f"<VendorProcessHierarchy L{self.process_level}: {self.process_name}>"


class VendorComponentArchitecture(TenantMixin, db.Model):
    """
    Application component architecture with layering and dependencies.
    Enables architecture assessment, modernization planning, and technical debt analysis.
    """

    __tablename__ = "vendor_component_architecture"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(
        db.Integer, db.ForeignKey("vendor_stack_templates.id"), nullable=False, index=True
    )
    parent_component_id = db.Column(
        db.Integer, db.ForeignKey("vendor_component_architecture.id"), index=True
    )

    # Component identification
    component_code = db.Column(db.String(50), index=True)
    component_name = db.Column(db.String(200), nullable=False)
    component_description = db.Column(db.Text)

    # Component classification
    component_type = db.Column(db.String(50))  # Service/Library/Module/Package/Function
    architectural_layer = db.Column(
        db.String(50)
    )  # Presentation/Business/Data/Integration/Infrastructure
    component_level = db.Column(db.Integer)  # Hierarchy depth

    # Component pattern
    architectural_pattern = db.Column(
        db.String(100)
    )  # Microservice/Monolith/Serverless/Event-driven/Batch
    design_pattern = db.Column(db.Text)  # JSON: [{pattern_name, description, rationale}]
    bounded_context = db.Column(db.String(100))  # DDD bounded context

    # Technology stack
    technology = db.Column(db.String(100))
    framework = db.Column(db.String(100))
    framework_version = db.Column(db.String(20))
    language = db.Column(db.String(50))
    language_version = db.Column(db.String(20))
    runtime = db.Column(db.String(50))

    # Component APIs
    apis_provided = db.Column(
        db.Text
    )  # JSON: [{api_type, protocol, version, endpoint, swagger_url}]
    apis_consumed = db.Column(db.Text)  # JSON: [{component_id, api_name, dependency_type}]

    # Component dependencies
    dependencies = db.Column(
        db.Text
    )  # JSON: [{component_id, dependency_type, coupling_level, cohesion_level}]
    dependency_graph_complexity = db.Column(db.Integer)  # Cyclomatic complexity

    # Component state
    state_management = db.Column(db.String(50))  # Stateless/Stateful/Shared-state/Distributed-cache
    session_handling = db.Column(db.String(50))  # None/Sticky-session/Distributed-session
    persistence_mechanism = db.Column(db.String(100))

    # Component scalability
    scalability_type = db.Column(db.String(50))  # Vertical/Horizontal/Both
    horizontal_scaling_limit = db.Column(db.Integer)  # Max instances
    auto_scaling_enabled = db.Column(db.Boolean, default=False)
    scaling_triggers = db.Column(db.Text)  # JSON: [{metric, threshold, scale_action}]

    # Component resilience
    circuit_breaker = db.Column(db.Boolean, default=False)
    retry_policy = db.Column(db.Text)  # JSON: {max_retries, backoff_strategy, timeout_ms}
    fallback_mechanism = db.Column(db.Text)  # JSON: {fallback_type, degraded_mode, cache_strategy}
    bulkhead_pattern = db.Column(db.Boolean, default=False)

    # Component security
    authentication_required = db.Column(db.Boolean, default=True)
    authorization_model = db.Column(db.String(50))  # RBAC/ABAC/PBAC
    encryption_at_rest = db.Column(db.Boolean, default=False)
    encryption_in_transit = db.Column(db.Boolean, default=True)
    secrets_management = db.Column(db.String(100))

    # Component observability
    logging_framework = db.Column(db.String(100))
    log_level = db.Column(db.String(20))
    structured_logging = db.Column(db.Boolean, default=True)
    distributed_tracing = db.Column(db.Boolean, default=True)
    metrics_collection = db.Column(db.Boolean, default=True)
    health_check_endpoint = db.Column(db.String(200))

    # Component performance
    response_time_ms = db.Column(db.Integer)
    throughput_tps = db.Column(db.Integer)
    memory_usage_mb = db.Column(db.Integer)
    cpu_usage_percentage = db.Column(db.Float)

    # Component lifecycle
    lifecycle_stage = db.Column(db.String(50))  # Development/Active/Maintenance/Deprecated/Retired
    technical_debt_score = db.Column(db.Integer)  # 1 - 100
    code_quality_score = db.Column(db.Integer)  # 1 - 100
    test_coverage_percentage = db.Column(db.Float)

    # Component deployment
    deployment_unit = db.Column(db.String(100))  # JAR/WAR/Container/Serverless-function
    container_image = db.Column(db.String(200))
    deployment_frequency = db.Column(db.String(50))  # Daily/Weekly/Monthly
    rollback_strategy = db.Column(db.String(100))

    # System fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    template = db.relationship("VendorStackTemplate", backref="component_architecture")
    parent_component = db.relationship(
        "VendorComponentArchitecture", remote_side=[id], backref="child_components"
    )

    def __repr__(self):
        return f"<VendorComponentArchitecture {self.component_code}: {self.component_name}>"


class VendorIntegrationCatalog(db.Model):
    """
    Integration catalog with patterns, protocols, and pre-built connectors.
    Enables integration strategy, vendor selection, and TCO analysis.
    """

    __tablename__ = "vendor_integration_catalog"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(
        db.Integer, db.ForeignKey("vendor_stack_templates.id"), nullable=False, index=True
    )

    # Integration identification
    integration_code = db.Column(db.String(50), index=True)
    integration_name = db.Column(db.String(200), nullable=False)
    integration_description = db.Column(db.Text)

    # Integration pattern
    integration_pattern = db.Column(db.String(50))  # P2P/Hub-spoke/Event-driven/API-led/ESB
    integration_style = db.Column(
        db.String(50)
    )  # Synchronous/Asynchronous/Batch/Real-time/Near-real-time
    integration_direction = db.Column(db.String(50))  # Inbound/Outbound/Bi-directional

    # Integration protocol
    primary_protocol = db.Column(db.String(50))  # REST/SOAP/GraphQL/gRPC/Kafka/AMQP/MQTT
    protocol_version = db.Column(db.String(20))
    supported_protocols = db.Column(db.Text)  # JSON: [{protocol, version, use_case}]

    # Pre-built connectors
    target_system = db.Column(db.String(100))  # SAP/Oracle/Workday/Salesforce/etc
    connector_type = db.Column(db.String(50))  # Native/Certified/Community/Custom
    connector_version = db.Column(db.String(20))
    bi_directional = db.Column(db.Boolean, default=False)
    certification_level = db.Column(db.String(50))  # Certified/Verified/Community

    # Data transformation
    transformation_engine = db.Column(db.String(100))
    mapping_complexity = db.Column(db.String(20))  # Low/Medium/High
    supports_json = db.Column(db.Boolean, default=True)
    supports_xml = db.Column(db.Boolean, default=True)
    supports_csv = db.Column(db.Boolean, default=True)
    supports_custom = db.Column(db.Boolean, default=False)
    transformation_rules = db.Column(
        db.Text
    )  # JSON: [{rule_name, input_format, output_format, logic}]

    # Integration capacity
    data_volume_capacity_gb = db.Column(db.BigInteger)
    transaction_capacity_tps = db.Column(db.Integer)
    concurrent_connections = db.Column(db.Integer)
    batch_size_limit = db.Column(db.Integer)

    # Integration SLA
    latency_sla_ms = db.Column(db.Integer)
    throughput_sla_tps = db.Column(db.Integer)
    availability_sla_percentage = db.Column(db.Float)
    data_freshness_sla_minutes = db.Column(db.Integer)

    # Integration security
    authentication_methods = db.Column(db.Text)  # JSON: [{method, protocol, token_type}]
    authorization_model = db.Column(db.String(50))
    encryption_required = db.Column(db.Boolean, default=True)
    certificate_management = db.Column(db.String(100))
    ip_whitelisting = db.Column(db.Boolean, default=False)

    # Integration monitoring
    monitoring_enabled = db.Column(db.Boolean, default=True)
    logging_level = db.Column(db.String(20))
    alerts_configured = db.Column(
        db.Text
    )  # JSON: [{alert_type, threshold, severity, notification}]
    dashboard_url = db.Column(db.String(200))

    # Integration resilience
    retry_policy = db.Column(db.Text)  # JSON: {max_retries, backoff_ms, timeout_ms}
    dead_letter_queue = db.Column(db.Boolean, default=True)
    circuit_breaker_enabled = db.Column(db.Boolean, default=False)
    failover_mechanism = db.Column(db.String(100))

    # Integration governance
    api_catalog_url = db.Column(db.String(200))
    api_documentation_url = db.Column(db.String(200))
    versioning_policy = db.Column(db.String(100))
    deprecation_notice_days = db.Column(db.Integer)
    lifecycle_stage = db.Column(db.String(50))

    # Integration costs
    pricing_model = db.Column(db.String(50))  # Per-call/Per-GB/Flat-rate/Tiered
    cost_per_transaction = db.Column(db.Float)
    cost_per_gb = db.Column(db.Float)
    monthly_base_cost = db.Column(db.Float)
    overage_charges = db.Column(db.Text)  # JSON: [{tier, threshold, rate}]
    connector_license_cost = db.Column(db.Float)

    # System fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    template = db.relationship("VendorStackTemplate", backref="integration_catalog")

    def __repr__(self):
        return f"<VendorIntegrationCatalog {self.integration_code}: {self.integration_name}>"


class VendorCostBreakdown(TenantMixin, db.Model):
    """
    Detailed cost breakdown with pricing models and TCO analysis.
    Enables accurate cost forecasting, budgeting, and vendor comparison.
    """

    __tablename__ = "vendor_cost_breakdown"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(
        db.Integer, db.ForeignKey("vendor_stack_templates.id"), nullable=False, index=True
    )

    # Cost component
    cost_component_name = db.Column(db.String(100), nullable=False)
    cost_category = db.Column(
        db.String(50)
    )  # Licensing/Infrastructure/Professional-services/Training/Support/Maintenance
    cost_description = db.Column(db.Text)

    # Pricing model
    pricing_model = db.Column(
        db.String(50)
    )  # Per-user/Per-transaction/Per-GB/Flat-rate/Tiered/Consumption
    unit = db.Column(db.String(50))  # user/month, transaction, GB, etc.
    base_price = db.Column(db.Float)
    currency = db.Column(db.String(10), default="USD")

    # Commitment
    minimum_commitment = db.Column(db.Float)  # Minimum spend or units
    contract_term_months = db.Column(db.Integer)
    annual_escalation_percentage = db.Column(db.Float)  # Price increase per year

    # Tiered pricing
    pricing_tiers = db.Column(
        db.Text
    )  # JSON: [{tier_name, from_units, to_units, price_per_unit, discount_percentage}]
    volume_discount = db.Column(db.Boolean, default=False)
    committed_use_discount_percentage = db.Column(db.Float)

    # Variable costs
    overage_price = db.Column(db.Float)  # Price beyond commitment
    overage_applies_after = db.Column(db.Integer)  # Units threshold
    peak_pricing = db.Column(db.Boolean, default=False)
    peak_multiplier = db.Column(db.Float)  # e.g., 1.5x during peak hours

    # Hidden costs
    hidden_cost_type = db.Column(
        db.String(100)
    )  # Setup-fee/Data-transfer/API-overage/Storage-overage
    hidden_cost_amount = db.Column(db.Float)
    hidden_cost_frequency = db.Column(db.String(50))  # One-time/Monthly/Annual/Per-incident
    avoidable = db.Column(db.Boolean, default=False)
    avoidance_strategy = db.Column(db.Text)

    # Forecasting
    estimated_monthly_cost = db.Column(db.Float)
    estimated_annual_cost = db.Column(db.Float)
    growth_assumption_percentage = db.Column(db.Float)  # Annual usage growth

    # System fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    template = db.relationship("VendorStackTemplate", backref="cost_breakdown")

    def __repr__(self):
        return f"<VendorCostBreakdown {self.cost_component_name}: {self.pricing_model}>"
