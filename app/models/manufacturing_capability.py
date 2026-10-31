"""
Manufacturing Capability Framework

Industry-specific capability enrichment for manufacturing organizations.
Includes manufacturing-specific capabilities, value streams, and KPIs.
Integrates with PCF Process Classification Framework for operational excellence.

Manufacturing Focus Areas:
- Production Management
- Supply Chain Management
- Quality Management
- Asset Management
- Product Lifecycle Management
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .. import db
from app.models.mixins import TenantMixin


class ManufacturingDomainHierarchy(db.Model):
    """
    Manufacturing Domain Hierarchy

    Organizes manufacturing domains into hierarchical structure.
    Primary domains: Production, Supply Chain, Quality, Maintenance, Engineering
    Can have subdomains for specialization.
    """

    __tablename__ = "manufacturing_domain_hierarchy"

    id = Column(db.Integer, primary_key=True)
    code = Column(
        db.String(50), unique=True, nullable=False, index=True
    )  # PROD, SC, QUAL, MAINT, ENG
    name = Column(db.String(256), nullable=False)  # Production, Supply Chain, etc.
    description = Column(db.Text)

    # Hierarchy
    parent_id = Column(db.Integer, db.ForeignKey("manufacturing_domain_hierarchy.id"))
    level = Column(db.Integer, default=1)  # 1=Primary, 2=Sub-domain, 3=Specialization

    # Domain metadata
    is_active = Column(db.Boolean, default=True)
    sort_order = Column(db.Integer, default=0)

    # L4 Patterns for this domain
    domain_patterns = Column(db.JSON)  # Domain-specific patterns

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Self-referential relationship
    parent = relationship("ManufacturingDomainHierarchy", remote_side=[id], backref="subdomains")

    def __repr__(self):
        return f"<ManufacturingDomainHierarchy {self.code} - {self.name}>"


class ManufacturingCapability(TenantMixin, db.Model):
    """
    Manufacturing-Specific Capability Model

    Extends unified capability framework with manufacturing industry specifics.
    Provides manufacturing KPIs, benchmarks, and best practices.
    """

    __tablename__ = "manufacturing_capabilities"
    __tenant_parent__ = (('unified_capability_id', 'unified_capabilities'),)

    id = Column(db.Integer, primary_key=True)

    # Link to unified capability
    unified_capability_id = Column(
        db.Integer, db.ForeignKey("unified_capabilities.id"), nullable=False
    )

    # Specialization type marker
    specialization_type = Column(
        db.String(50), default="MANUFACTURING", index=True
    )  # Explicit type marker

    # Manufacturing classification
    manufacturing_domain = Column(
        db.String(50)
    )  # production, supply_chain, quality, maintenance, engineering
    manufacturing_process_type = Column(
        db.String(50)
    )  # make_to_order, make_to_stock, engineer_to_order
    industry_subsector = Column(db.String(50))  # automotive, chemicals, consumer_goods, industrial

    # Manufacturing-specific metrics
    oee_target = Column(db.Float)  # Overall Equipment Effectiveness target (0 - 100%)
    oee_current = Column(db.Float)  # Current OEE performance
    first_pass_yield_target = Column(db.Float)  # FPY target (0 - 100%)
    first_pass_yield_current = Column(db.Float)  # Current FPY
    on_time_delivery_target = Column(db.Float)  # OTD target (0 - 100%)
    on_time_delivery_current = Column(db.Float)  # Current OTD
    inventory_turns_target = Column(db.Float)  # Inventory turns target
    inventory_turns_current = Column(db.Float)  # Current inventory turns

    # Quality metrics
    cost_of_quality_target = Column(db.Float)  # CoQ as % of revenue
    cost_of_quality_current = Column(db.Float)  # Current CoQ
    defect_rate_target = Column(db.Float)  # Defect rate target (ppm)
    defect_rate_current = Column(db.Float)  # Current defect rate
    scrap_rate_target = Column(db.Float)  # Scrap rate target (%)
    scrap_rate_current = Column(db.Float)  # Current scrap rate

    # Production metrics
    cycle_time_target = Column(db.Float)  # Target cycle time (hours)
    cycle_time_current = Column(db.Float)  # Current cycle time
    capacity_utilization_target = Column(db.Float)  # Target capacity utilization (%)
    capacity_utilization_current = Column(db.Float)  # Current capacity utilization
    changeover_time_target = Column(db.Float)  # Target changeover time (minutes)
    changeover_time_current = Column(db.Float)  # Current changeover time

    # Supply chain metrics
    supplier_on_time_delivery_target = Column(db.Float)  # S-OTD target (%)
    supplier_on_time_delivery_current = Column(db.Float)  # Current S-OTD
    supplier_quality_rating_target = Column(db.Float)  # Supplier quality rating target
    supplier_quality_rating_current = Column(db.Float)  # Current supplier quality rating
    supply_chain_visibility_score = Column(db.Float)  # Supply chain visibility (0 - 100)

    # Maintenance metrics
    mtbf_target = Column(db.Float)  # Mean Time Between Failures target (hours)
    mtbf_current = Column(db.Float)  # Current MTBF
    mttr_target = Column(db.Float)  # Mean Time To Repair target (hours)
    mttr_current = Column(db.Float)  # Current MTTR
    planned_maintenance_percentage_target = Column(db.Float)  # PM % target
    planned_maintenance_percentage_current = Column(db.Float)  # Current PM %

    # Safety and compliance
    safety_incident_rate_target = Column(db.Float)  # Safety incident rate target
    safety_incident_rate_current = Column(db.Float)  # Current safety incident rate
    environmental_compliance_score = Column(db.Float)  # Environmental compliance (0 - 100)
    regulatory_compliance_score = Column(db.Float)  # Regulatory compliance (0 - 100)

    # Digital transformation
    automation_level = Column(db.Integer, default=1)  # 1 - 5 automation scale
    iot_maturity = Column(db.Integer, default=1)  # 1 - 5 IoT maturity scale
    analytics_maturity = Column(db.Integer, default=1)  # 1 - 5 analytics maturity scale
    digital_twin_capability = Column(db.Boolean, default=False)

    # Manufacturing excellence programs
    lean_maturity = Column(db.Integer, default=1)  # 1 - 5 lean maturity
    six_sigma_level = Column(
        db.String(20)
    )  # yellow_belt, green_belt, black_belt, master_black_belt
    continuous_improvement_programs = Column(db.Text)  # JSON array of active programs

    # Benchmarking data
    industry_benchmarks = Column(db.Text)  # JSON with industry benchmark data
    best_practices = Column(db.Text)  # JSON array of best practices
    improvement_opportunities = Column(db.Text)  # JSON array of identified opportunities

    # Assessment metadata
    last_assessed = Column(db.DateTime, default=datetime.utcnow)
    assessor = Column(db.String(100))
    assessment_methodology = Column(db.String(50))  # internal, external, hybrid
    assessment_notes = Column(db.Text)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    unified_capability = relationship("UnifiedCapability", backref="manufacturing_details")

    def __repr__(self):
        return f"<ManufacturingCapability {self.unified_capability.name}>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "unified_capability_id": self.unified_capability_id,
            "capability_name": self.unified_capability.name if self.unified_capability else None,
            "manufacturing_domain": self.manufacturing_domain,
            "manufacturing_process_type": self.manufacturing_process_type,
            "industry_subsector": self.industry_subsector,
            "oee_target": self.oee_target,
            "oee_current": self.oee_current,
            "first_pass_yield_target": self.first_pass_yield_target,
            "first_pass_yield_current": self.first_pass_yield_current,
            "on_time_delivery_target": self.on_time_delivery_target,
            "on_time_delivery_current": self.on_time_delivery_current,
            "inventory_turns_target": self.inventory_turns_target,
            "inventory_turns_current": self.inventory_turns_current,
            "cost_of_quality_target": self.cost_of_quality_target,
            "cost_of_quality_current": self.cost_of_quality_current,
            "cycle_time_target": self.cycle_time_target,
            "cycle_time_current": self.cycle_time_current,
            "capacity_utilization_target": self.capacity_utilization_target,
            "capacity_utilization_current": self.capacity_utilization_current,
            "automation_level": self.automation_level,
            "iot_maturity": self.iot_maturity,
            "analytics_maturity": self.analytics_maturity,
            "digital_twin_capability": self.digital_twin_capability,
            "lean_maturity": self.lean_maturity,
            "six_sigma_level": self.six_sigma_level,
            "safety_incident_rate_target": self.safety_incident_rate_target,
            "safety_incident_rate_current": self.safety_incident_rate_current,
            "environmental_compliance_score": self.environmental_compliance_score,
            "regulatory_compliance_score": self.regulatory_compliance_score,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ManufacturingValueStream(db.Model):
    """
    Manufacturing-Specific Value Streams

    Standard manufacturing value streams with industry-specific stages.
    Supports make-to-order, make-to-stock, and engineer-to-order processes.
    """

    __tablename__ = "manufacturing_value_streams"

    id = Column(db.Integer, primary_key=True)

    # Value stream identity
    name = Column(db.String(256), nullable=False, index=True)
    description = Column(db.Text)
    code = Column(db.String(50), unique=True, index=True)  # e.g., MTO, MTS, ETO

    # Manufacturing classification
    value_stream_type = Column(
        db.String(50)
    )  # make_to_order, make_to_stock, engineer_to_order, configure_to_order
    manufacturing_model = Column(db.String(50))  # discrete, process, mixed_mode
    industry_focus = Column(db.String(50))  # automotive, chemicals, consumer_goods, industrial

    # Strategic context
    strategic_importance = Column(db.String(20))  # critical, high, medium, low
    business_owner = Column(db.String(100))
    value_stream_manager = Column(db.String(100))

    # Performance targets
    total_cycle_time_target = Column(db.Integer)  # Target total cycle time (days)
    total_cycle_time_current = Column(db.Integer)  # Current total cycle time (days)
    quality_target = Column(db.Float)  # Target quality percentage
    quality_current = Column(db.Float)  # Current quality percentage
    cost_per_unit_target = Column(db.Float)  # Target cost per unit
    cost_per_unit_current = Column(db.Float)  # Current cost per unit

    # Customer metrics
    customer_lead_time_target = Column(db.Integer)  # Target customer lead time (days)
    customer_lead_time_current = Column(db.Integer)  # Current customer lead time (days)
    customer_satisfaction_target = Column(db.Float)  # Target satisfaction score
    customer_satisfaction_current = Column(db.Float)  # Current satisfaction score

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    stages = relationship("ManufacturingValueStreamStage", backref="value_stream", lazy="dynamic")

    def __repr__(self):
        return f"<ManufacturingValueStream {self.name}>"


class ManufacturingValueStreamStage(db.Model):
    """
    Manufacturing Value Stream Stages

    Individual stages within manufacturing value streams.
    Maps to manufacturing processes and capabilities.
    """

    __tablename__ = "manufacturing_value_stream_stages"

    id = Column(db.Integer, primary_key=True)

    # Stage identity
    name = Column(db.String(256), nullable=False, index=True)
    description = Column(db.Text)

    # Value stream context
    value_stream_id = Column(
        db.Integer, db.ForeignKey("manufacturing_value_streams.id"), nullable=False
    )
    stage_order = Column(db.Integer, nullable=False)  # Sequence in value stream

    # Stage characteristics
    stage_type = Column(db.String(50))  # strategic, operational, supporting, quality
    customer_facing = Column(db.Boolean, default=False)
    value_adding = Column(db.Boolean, default=True)

    # Performance metrics
    target_duration = Column(db.Integer)  # Target duration (hours)
    current_duration = Column(db.Integer)  # Current duration (hours)
    quality_gate = Column(db.Boolean, default=False)
    bottleneck_stage = Column(db.Boolean, default=False)

    # Process mapping
    supporting_processes = Column(db.Text)  # JSON array of PCF process codes
    required_capabilities = Column(db.Text)  # JSON array of required capability codes

    # Resource requirements
    labor_intensity = Column(db.String(20))  # low, medium, high
    automation_level = Column(db.Integer, default=1)  # 1 - 5 automation scale
    equipment_requirements = Column(db.Text)  # JSON array of required equipment

    # Quality and compliance
    quality_checks_required = Column(db.Text)  # JSON array of quality checks
    compliance_requirements = Column(db.Text)  # JSON array of compliance requirements

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ManufacturingValueStreamStage {self.name}>"


class ManufacturingProcess(db.Model):
    """
    Manufacturing Process Model (PCF Integration)

    Standard manufacturing processes from PCF framework.
    Maps to capabilities and applications for operational excellence.
    """

    __tablename__ = "manufacturing_processes"

    id = Column(db.Integer, primary_key=True)

    # Process identity
    process_code = Column(db.String(20), nullable=False, index=True)  # e.g., "3.1.1", "4.2.3"
    process_name = Column(db.String(256), nullable=False, index=True)
    process_description = Column(db.Text)

    # PCF Hierarchy
    category_level_1 = Column(db.String(100))  # e.g., "3.0 Manage Products and Services"
    category_level_2 = Column(db.String(100))  # e.g., "3.1 Develop Products and Services"
    category_level_3 = Column(db.String(100))  # e.g., "3.1.1 Product Development Strategy"

    # Manufacturing classification
    manufacturing_domain = Column(
        db.String(50)
    )  # production, supply_chain, quality, maintenance, engineering
    process_type = Column(db.String(50))  # core, supporting, management
    process_complexity = Column(db.String(20))  # low, medium, high

    # Process metrics and benchmarking
    benchmark_available = Column(db.Boolean, default=False)
    industry_benchmarks = Column(db.Text)  # JSON with manufacturing benchmark data
    world_class_performance = Column(db.Text)  # JSON with world class metrics
    kpi_definitions = Column(db.Text)  # JSON with manufacturing KPI definitions

    # Process governance
    process_owner = Column(db.String(100))
    process_maturity = Column(db.Integer, default=1)  # 1 - 5 maturity scale
    improvement_priority = Column(db.String(20))  # critical, high, medium, low

    # Technology enablement
    automation_potential = Column(db.Integer, default=1)  # 1 - 5 automation potential
    digital_transformation_ready = Column(db.Boolean, default=False)
    iot_applicable = Column(db.Boolean, default=False)

    # Quality and compliance
    quality_critical_process = Column(db.Boolean, default=False)
    safety_critical_process = Column(db.Boolean, default=False)
    environmental_impact = Column(db.String(20))  # low, medium, high

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    parent_process_id = Column(db.Integer, db.ForeignKey("manufacturing_processes.id"))
    parent_process = relationship(
        "ManufacturingProcess", remote_side="ManufacturingProcess.id", backref="child_processes"
    )

    def __repr__(self):
        return f"<ManufacturingProcess {self.process_code} - {self.process_name}>"


class ManufacturingBenchmark(db.Model):
    """
    Manufacturing Benchmark Data

    Industry benchmarks for manufacturing capabilities and processes.
    Supports performance analysis and improvement targeting.
    """

    __tablename__ = "manufacturing_benchmarks"

    id = Column(db.Integer, primary_key=True)

    # Benchmark identity
    benchmark_name = Column(db.String(256), nullable=False, index=True)
    benchmark_description = Column(db.Text)
    benchmark_source = Column(db.String(100))  # APQC, Industry Association, Consulting Firm

    # Benchmark classification
    industry_sector = Column(db.String(50))  # automotive, chemicals, consumer_goods, industrial
    company_size = Column(db.String(30))  # small, medium, large, enterprise
    geographic_region = Column(db.String(50))  # north_america, europe, asia_pacific, global

    # Benchmark metrics
    metric_category = Column(db.String(50))  # productivity, quality, cost, delivery, safety
    metric_name = Column(db.String(100))  # OEE, FPY, OTD, etc.
    metric_unit = Column(db.String(50))  # percentage, days, hours, etc.

    # Performance levels
    bottom_quartile = Column(db.Float)  # 25th percentile
    median = Column(db.Float)  # 50th percentile
    top_quartile = Column(db.Float)  # 75th percentile
    best_in_class = Column(db.Float)  # 90th percentile
    world_class = Column(db.Float)  # Top 10%

    # Benchmark methodology
    data_collection_method = Column(db.String(50))  # survey, interview, operational_data
    sample_size = Column(db.Integer)  # Number of companies in benchmark
    data_year = Column(db.Integer)  # Year of data collection

    # Contextual factors
    industry_specific_factors = Column(db.Text)  # JSON array of industry-specific factors
    adjustment_factors = Column(db.Text)  # JSON array of adjustment factors

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ManufacturingBenchmark {self.benchmark_name}>"


class ManufacturingImprovementInitiative(db.Model):
    """
    Manufacturing Improvement Initiative

    Tracks improvement initiatives for manufacturing capabilities.
    Links to lean, six sigma, and digital transformation programs.
    """

    __tablename__ = "manufacturing_improvement_initiatives"

    id = Column(db.Integer, primary_key=True)

    # Initiative identity
    initiative_name = Column(db.String(256), nullable=False, index=True)
    initiative_description = Column(db.Text)
    initiative_code = Column(db.String(50), unique=True, index=True)

    # Initiative classification
    improvement_methodology = Column(
        db.String(50)
    )  # lean, six_sigma, kaizen, digital_transformation
    initiative_type = Column(db.String(50))  # productivity, quality, cost, delivery, safety
    priority_level = Column(db.String(20))  # critical, high, medium, low

    # Link to capability
    manufacturing_capability_id = Column(db.Integer, db.ForeignKey("manufacturing_capabilities.id"))

    # Initiative scope
    scope_description = Column(db.Text)
    affected_processes = Column(db.Text)  # JSON array of affected process codes
    affected_applications = Column(db.Text)  # JSON array of affected application IDs

    # Goals and targets
    baseline_performance = Column(db.Float)  # Current performance level
    target_performance = Column(db.Float)  # Target performance level
    improvement_percentage = Column(db.Float)  # Expected improvement percentage

    # Timeline and resources
    start_date = Column(db.Date)
    target_completion_date = Column(db.Date)
    actual_completion_date = Column(db.Date)
    estimated_cost = Column(db.Float)
    actual_cost = Column(db.Float)
    team_size = Column(db.Integer)

    # Initiative status
    status = Column(db.String(20), default="planned")  # planned, in_progress, completed, cancelled
    progress_percentage = Column(db.Integer, default=0)  # 0 - 100% complete
    roi_achieved = Column(db.Float)  # Actual ROI achieved

    # Team and governance
    initiative_leader = Column(db.String(100))
    sponsor = Column(db.String(100))
    team_members = Column(db.Text)  # JSON array of team members

    # Results and outcomes
    actual_improvement = Column(db.Float)  # Actual improvement achieved
    lessons_learned = Column(db.Text)
    best_practices_identified = Column(db.Text)  # JSON array of best practices

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    manufacturing_capability = relationship(
        "ManufacturingCapability", backref="improvement_initiatives"
    )

    def __repr__(self):
        return f"<ManufacturingImprovementInitiative {self.initiative_name}>"
