"""
Enhanced Duplicate Application Detection Models

Enterprise-grade duplicate detection system aligned with ArchiMate 3.2 principles.
Provides multi-dimensional analysis for application rationalization and consolidation.

Models:
- DuplicateDetectionRun: Tracks detection analysis runs
- DuplicateGroup: Groups of similar applications
- DuplicateAnalysis: Detailed similarity analysis between applications
- BusinessProcess: Business process hierarchy (L0 - L3)
- ApplicationProcessMapping: Links applications to business processes
- ConsolidationRecommendation: AI-powered consolidation suggestions
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .. import db
from ..models.mixins.core import TenantMixin


class ProcessLevel(PyEnum):
    """Business Process Levels"""

    L0 = "L0"  # Value Chain
    L1 = "L1"  # Process Domain
    L2 = "L2"  # Process Area
    L3 = "L3"  # Specific Process


class DuplicateType(PyEnum):
    """Types of duplication"""

    FUNCTIONAL = "functional"  # Same business processes
    TECHNICAL = "technical"  # Same architecture patterns
    CAPABILITY = "capability"  # Same business capabilities
    PARTIAL = "partial"  # Overlapping functionality
    DATA = "data"  # Same data objects


from ..models.process_data import BusinessProcess


class ApplicationProcessMapping(db.Model):
    """
    Application-Process Mapping for Duplicate Detection

    Links applications to business processes they support for duplicate analysis.
    Separate from the main process mapping to avoid conflicts.
    """

    __tablename__ = "duplicate_app_process_mapping"

    id = Column(db.Integer, primary_key=True)

    # Entities
    application_id = Column(db.Integer, db.ForeignKey("application_components.id"), nullable=False)
    business_process_id = Column(db.Integer, db.ForeignKey("business_processes.id"), nullable=False)

    # Relationship characteristics
    support_type = Column(db.String(30), nullable=False)  # primary, secondary, supporting
    support_percentage = Column(db.Integer, default=0)  # 0 - 100% of process supported
    criticality = Column(db.String(20))  # critical, important, optional

    # Implementation details
    integration_complexity = Column(db.String(20))  # low, medium, high
    integration_type = Column(db.String(30))  # direct, api, batch, event_driven
    data_flow_direction = Column(db.String(20))  # inbound, outbound, bidirectional

    # Performance metrics
    process_coverage = Column(db.Integer)  # 0 - 100% of process steps covered
    automation_contribution = Column(db.Integer)  # 0 - 100% automation contribution
    efficiency_gain = Column(db.Integer)  # percentage efficiency improvement

    # Business impact
    business_value_score = Column(db.Integer)  # 1 - 10
    user_adoption_rate = Column(db.Float)  # percentage
    error_reduction = Column(db.Integer)  # percentage error reduction

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_assessed_date = Column(db.DateTime)

    # Relationships
    application = relationship("ApplicationComponent", backref="process_mappings")
    business_process = relationship("BusinessProcess", backref="application_mappings")

    def __repr__(self):
        return f"<AppProcessMapping {self.application_id}→{self.business_process_id} ({self.support_type})>"


class DuplicateDetectionRun(TenantMixin, db.Model):
    """
    Duplicate Detection Run

    Tracks execution of duplicate detection analysis.
    Provides audit trail and performance metrics.

    TenantMixin declares organization_id NOT NULL, but this is an existing
    table and reconcile-schema can only ADD nullable columns (ADR 0002).
    Override to nullable so the column can land on deployed databases
    without a maintenance window.  Legacy rows with no known owner stay NULL
    and are hidden from every organisation by the tenant filter.
    """

    __tablename__ = "duplicate_detection_runs"

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = Column(db.Integer, primary_key=True)

    # Run metadata
    run_name = Column(db.String(256), nullable=False)
    description = Column(db.Text)
    run_type = Column(db.String(30))  # full, incremental, targeted

    # Configuration
    similarity_threshold = Column(db.Float, default=0.7)  # 0 - 1 threshold for grouping
    weighting_config = Column(db.JSON)  # Weighting for different criteria
    analysis_scope = Column(db.JSON)  # Scope of analysis (domains, categories, etc.)

    # Execution metrics
    status = Column(db.String(20), default="pending")  # pending, running, completed, failed
    started_at = Column(db.DateTime)
    completed_at = Column(db.DateTime)
    duration_seconds = Column(db.Integer)

    # Results summary
    applications_analyzed = Column(db.Integer, default=0)
    duplicate_groups_found = Column(db.Integer, default=0)
    total_duplicates_found = Column(db.Integer, default=0)
    estimated_savings = Column(db.Float)  # Estimated annual savings

    # Performance metrics
    similarity_calculations_performed = Column(db.Integer, default=0)
    average_similarity_score = Column(db.Float)
    processing_rate = Column(db.Float)  # applications per second

    # Error handling
    error_message = Column(db.Text)
    error_count = Column(db.Integer, default=0)

    # Metadata
    triggered_by = Column(db.String(50))  # user, scheduled, api
    ai_model_version = Column(db.String(30))
    confidence_threshold = Column(db.Float, default=0.8)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    duplicate_groups = relationship("DuplicateGroup", backref="detection_run", lazy="dynamic")

    def __repr__(self):
        return f"<DuplicateDetectionRun {self.run_name} ({self.status})>"


class DuplicateGroup(TenantMixin, db.Model):
    """
    Duplicate Application Group

    Groups applications that are duplicates based on multi-criteria analysis.
    Supports different types of duplication (functional, technical, capability).

    TenantMixin declares organization_id NOT NULL, but this is an existing
    table and reconcile-schema can only ADD nullable columns (ADR 0002).
    Override to nullable so the column can land on deployed databases
    without a maintenance window.  Legacy rows with no known owner stay NULL
    and are hidden from every organisation by the tenant filter.
    """

    __tablename__ = "duplicate_groups"

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = Column(db.Integer, primary_key=True)

    # Group metadata
    group_name = Column(db.String(256), nullable=False)
    description = Column(db.Text)
    duplicate_type = Column(db.Enum(DuplicateType), nullable=False)

    # Detection run
    detection_run_id = Column(
        db.Integer, db.ForeignKey("duplicate_detection_runs.id"), nullable=False
    )

    # Similarity metrics
    overall_similarity_score = Column(db.Float, nullable=False)  # 0 - 1
    functional_similarity = Column(db.Float)  # Business process overlap
    technical_similarity = Column(db.Float)  # Architecture similarity
    capability_similarity = Column(db.Float)  # Business capability overlap
    data_similarity = Column(db.Float)  # Data object overlap

    # Group characteristics
    primary_business_process_id = Column(db.Integer, db.ForeignKey("business_processes.id"))
    primary_capability_id = Column(db.Integer, db.ForeignKey("business_capability.id"))
    common_technology_stack = Column(db.JSON)  # Shared technologies

    # Consolidation potential
    consolidation_priority = Column(db.String(20))  # high, medium, low
    consolidation_complexity = Column(db.String(20))  # low, medium, high
    estimated_savings = Column(db.Float)  # Annual savings estimate
    consolidation_timeline_months = Column(db.Integer)

    # Risk assessment
    business_risk = Column(db.String(20))  # low, medium, high
    technical_risk = Column(db.String(20))  # low, medium, high
    migration_risk = Column(db.String(20))  # low, medium, high

    # Analysis details
    similarity_factors = Column(db.JSON)  # Detailed factor breakdown
    exclusion_reasons = Column(db.JSON)  # Reasons not to consolidate
    recommendation_notes = Column(db.Text)

    # Status
    status = Column(
        db.String(20), default="identified"
    )  # identified, analyzing, recommended, excluded
    reviewed_by = Column(db.String(100))
    reviewed_at = Column(db.DateTime)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    applications = relationship(
        "ApplicationComponent", secondary="duplicate_group_members", backref="duplicate_groups"
    )

    primary_business_process = relationship(
        "BusinessProcess", foreign_keys=[primary_business_process_id]
    )
    primary_capability = relationship("BusinessCapability", foreign_keys=[primary_capability_id])

    # Analyses for each application pair in the group
    pairwise_analyses = relationship("DuplicateAnalysis", backref="duplicate_group", lazy="dynamic")

    # Consolidation recommendations
    consolidation_recommendations = relationship(
        "ConsolidationRecommendation", backref="duplicate_group", lazy="dynamic"
    )

    def __repr__(self):
        return f"<DuplicateGroup {self.group_name} ({self.duplicate_type.value})>"


class DuplicateAnalysis(db.Model):
    """
    Detailed Duplicate Analysis

    Stores detailed similarity analysis between two applications.
    Provides the evidence for duplicate grouping decisions.
    """

    __tablename__ = "duplicate_analyses"

    id = Column(db.Integer, primary_key=True)

    # Applications being compared
    application_1_id = Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False
    )
    application_2_id = Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False
    )
    duplicate_group_id = Column(db.Integer, db.ForeignKey("duplicate_groups.id"))

    # Overall similarity
    overall_similarity_score = Column(db.Float, nullable=False)  # 0 - 1
    confidence_level = Column(db.Float)  # Confidence in the analysis

    # Detailed similarity breakdown
    name_similarity = Column(db.Float)  # Name/description similarity
    functional_similarity = Column(db.Float)  # Business process overlap
    capability_similarity = Column(db.Float)  # Business capability overlap
    technical_similarity = Column(db.Float)  # Technology stack similarity
    data_similarity = Column(db.Float)  # Data object similarity
    integration_similarity = Column(db.Float)  # Integration pattern similarity

    # Business process analysis
    shared_processes = Column(db.JSON)  # Array of shared process IDs
    process_overlap_percentage = Column(db.Float)  # Percentage of processes shared
    process_similarity_details = Column(db.JSON)  # Detailed process comparison

    # Capability analysis
    shared_capabilities = Column(db.JSON)  # Array of shared capability IDs
    capability_overlap_percentage = Column(db.Float)
    capability_similarity_details = Column(db.JSON)

    # Technical analysis
    shared_technologies = Column(db.JSON)  # Array of shared technologies
    technology_stack_similarity = Column(db.Float)
    architecture_similarity = Column(db.Float)
    integration_pattern_similarity = Column(db.Float)

    # Data analysis
    shared_data_objects = Column(db.JSON)  # Array of shared data object IDs
    data_model_similarity = Column(db.Float)
    data_flow_similarity = Column(db.Float)

    # User and usage analysis
    user_base_overlap = Column(db.Float)  # Percentage of shared users
    usage_pattern_similarity = Column(db.Float)
    business_unit_overlap = Column(db.Float)

    # Cost analysis
    combined_annual_cost = Column(db.Float)
    potential_savings = Column(db.Float)
    cost_benefit_ratio = Column(db.Float)

    # Analysis metadata
    analysis_method = Column(db.String(50))  # ai_algorithm, rule_based, hybrid
    analysis_version = Column(db.String(20))
    analysis_timestamp = Column(db.DateTime, default=datetime.utcnow)

    # Exclusion factors
    exclusion_factors = Column(db.JSON)  # Reasons these might NOT be duplicates
    business_constraints = Column(db.JSON)  # Business reasons to keep separate
    technical_constraints = Column(db.JSON)  # Technical reasons to keep separate

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    application_1 = relationship("ApplicationComponent", foreign_keys=[application_1_id])
    application_2 = relationship("ApplicationComponent", foreign_keys=[application_2_id])

    def __repr__(self):
        return f"<DuplicateAnalysis {self.application_1_id}↔{self.application_2_id} ({self.overall_similarity_score})>"


class ConsolidationRecommendation(db.Model):
    """
    AI-Powered Consolidation Recommendation

    Provides specific recommendations for consolidating duplicate applications.
    Includes implementation approach, timeline, and risk mitigation.
    """

    __tablename__ = "consolidation_recommendations"

    id = Column(db.Integer, primary_key=True)

    # Recommendation metadata
    duplicate_group_id = Column(db.Integer, db.ForeignKey("duplicate_groups.id"), nullable=False)
    recommendation_type = Column(db.String(30))  # merge, retire, replace, replatform

    # Target application (the one to keep/enhance)
    target_application_id = Column(db.Integer, db.ForeignKey("application_components.id"))
    target_justification = Column(db.Text)

    # Applications to be retired/merged
    source_applications = Column(db.JSON)  # Array of application IDs to retire

    # Implementation plan
    implementation_approach = Column(db.String(50))  # big_bang, phased, parallel
    estimated_timeline_months = Column(db.Integer)
    implementation_phases = Column(db.JSON)  # Detailed phase breakdown

    # Cost analysis
    implementation_cost = Column(db.Float)
    annual_savings = Column(db.Float)
    payback_period_months = Column(db.Integer)
    roi_percentage = Column(db.Float)

    # Risk assessment
    overall_risk_level = Column(db.String(20))  # low, medium, high
    business_risk_factors = Column(db.JSON)
    technical_risk_factors = Column(db.JSON)
    mitigation_strategies = Column(db.JSON)

    # Impact analysis
    affected_users = Column(db.Integer)
    affected_business_processes = Column(db.JSON)  # Array of process IDs
    affected_integrations = Column(db.JSON)  # Array of integration points
    data_migration_complexity = Column(db.String(20))  # low, medium, high

    # Prerequisites and dependencies
    prerequisite_tasks = Column(db.JSON)  # Tasks to complete before consolidation
    required_capabilities = Column(db.JSON)  # Capabilities needed in target app
    dependency_updates = Column(db.JSON)  # Integration updates required

    # Benefits realization
    expected_benefits = Column(db.JSON)  # Array of expected benefits
    success_metrics = Column(db.JSON)  # KPIs to track success
    benefit_timeline_months = Column(db.Integer)

    # Stakeholder management
    business_stakeholders = Column(db.JSON)  # Key business stakeholders
    technical_stakeholders = Column(db.JSON)  # Key technical stakeholders
    change_management_requirements = Column(db.JSON)

    # Recommendation quality
    confidence_score = Column(db.Float)  # 0 - 1 confidence in recommendation
    supporting_evidence = Column(db.JSON)  # Evidence supporting recommendation
    alternative_options = Column(db.JSON)  # Alternative consolidation approaches

    # Status and workflow
    status = Column(
        db.String(20), default="proposed"
    )  # proposed, approved, in_progress, completed, rejected
    approved_by = Column(db.String(100))
    approved_at = Column(db.DateTime)
    implementation_start_date = Column(db.DateTime)
    completion_date = Column(db.DateTime)

    # AI Analysis metadata
    ai_model_version = Column(db.String(30))
    analysis_timestamp = Column(db.DateTime, default=datetime.utcnow)
    human_review_required = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    target_application = relationship("ApplicationComponent", foreign_keys=[target_application_id])

    def __repr__(self):
        return f"<ConsolidationRecommendation {self.recommendation_type} for Group {self.duplicate_group_id}>"


# Association tables
duplicate_group_members = db.Table(
    "duplicate_group_members",
    db.Column(
        "duplicate_group_id", db.Integer, db.ForeignKey("duplicate_groups.id"), primary_key=True
    ),
    db.Column(
        "application_id", db.Integer, db.ForeignKey("application_components.id"), primary_key=True
    ),
    db.Column("similarity_score", db.Float),  # Individual app similarity to group
    db.Column("role_in_group", db.String(20)),  # primary, secondary, outlier
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
    db.Column("updated_at", db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)
