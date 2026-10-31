"""
APQC Process Classification Framework Model

Separates APQC processes from business capabilities for proper enterprise architecture.
Provides process classification, benchmarking, and operational improvement capabilities.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class APQCProcess(db.Model):
    """
    APQC Process Classification Framework

    Represents standardized processes from APQC PCF for benchmarking and improvement.
    Separate from business capabilities to maintain architectural clarity.
    """

    __tablename__ = "apqc_process"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # APQC Process identification
    process_code = db.Column(db.String(20), nullable=False, index=True)  # e.g., "1.0", "2.1.1"
    process_name = db.Column(db.String(256), nullable=False, index=True)
    process_description = db.Column(db.Text)

    # APQC Hierarchy
    category_level_1 = db.Column(db.String(100))  # e.g., "1.0 Develop Vision and Strategy"
    category_level_2 = db.Column(db.String(100))  # e.g., "1.1 Define the business concept"
    category_level_3 = db.Column(db.String(100))  # e.g., "1.1.1 Assess the external environment"

    # Process classification
    process_category = db.Column(db.String(50))  # e.g., "Strategic", "Operational", "Support"
    industry_domain = db.Column(db.String(100))  # e.g., "Manufacturing", "Finance", "Healthcare"
    process_type = db.Column(db.String(50))  # e.g., "Core", "Supporting", "Management"

    # Process metrics and benchmarking
    benchmark_available = db.Column(db.Boolean, default=False)
    industry_benchmarks = db.Column(db.Text)  # JSON with benchmark data
    kpi_definitions = db.Column(db.Text)  # JSON with KPI definitions

    # Process governance
    process_owner = db.Column(db.String(100))
    process_maturity = db.Column(db.Integer, default=1)  # 1 - 5 maturity scale
    improvement_priority = db.Column(db.String(20))  # critical, high, medium, low

    # Relationships
    parent_process_id = db.Column(db.Integer, db.ForeignKey("apqc_process.id"))

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    parent_process = db.relationship(
        "APQCProcess", remote_side="APQCProcess.id", backref="child_processes"
    )

    def __repr__(self):
        return f"<APQCProcess {self.process_code} - {self.process_name}>"

    @property
    def apqc_level(self):
        """
        Determine APQC hierarchy level based on process_code segments.

        Returns:
            int: Hierarchy level (1 - 5) based on the number of segments in process_code.
            - Level 1 = "4.0" (Category)
            - Level 2 = "4.1" (Process Group)
            - Level 3 = "4.1.1" (Process)
            - Level 4 = "4.1.1.1" (Activity)
            - Level 5 = "4.1.1.1.1" (Task)
            Returns None if process_code is invalid or not set.
        """
        if not self.process_code:
            return None

        # Split by '.' and count segments
        segments = self.process_code.strip().split(".")
        segment_count = len(segments)

        # Handle special case: codes ending with '.0' are Category level (Level 1)
        # e.g., "4.0", "10.0" are Level 1 Categories
        if segment_count == 2 and segments[1] == "0":
            return 1

        # Map segment count to level
        # 2 segments (e.g., "4.1") = Level 2 (Process Group)
        # 3 segments (e.g., "4.1.1") = Level 3 (Process)
        # 4 segments (e.g., "4.1.1.1") = Level 4 (Activity)
        # 5 segments (e.g., "4.1.1.1.1") = Level 5 (Task)
        if segment_count >= 2 and segment_count <= 5:
            return segment_count
        elif segment_count > 5:
            return 5  # Cap at Level 5

        return None

    @property
    def level(self):
        """Alias for ``apqc_level``.

        Several services (semantic/enhanced APQC mapping, gap analysis, the
        application architecture mapper used by /chat/generate-archimate) read
        ``process.level``. APQCProcess has no ``level`` column — the hierarchy
        level is computed from ``process_code`` — so without this alias those
        accesses raise AttributeError and 500 the request.
        """
        return self.apqc_level

    @property
    def description(self):
        """Alias for ``process_description``.

        The semantic/enhanced APQC vector builders read ``process.description``,
        but the column is ``process_description`` — without this alias the access
        raises AttributeError ('APQCProcess' object has no attribute
        'description') and silently breaks APQC vector building / map-apqc.
        """
        return self.process_description

    @property
    def archimate_mapping_level(self):
        """
        Determine the appropriate ArchiMate element type for this APQC process level.

        Returns:
            str or None: ArchiMate element type based on APQC level.
            - 'BusinessFunction' for Level 1 (Category)
            - 'BusinessProcess' for Levels 2 - 3 (Process Group, Process)
            - None for Level 4 - 5 (too granular for main process mapping)
        """
        level = self.apqc_level

        if level is None:
            return None

        if level == 1:
            return "BusinessFunction"
        elif level in (2, 3):
            return "BusinessProcess"
        else:
            # Levels 4 - 5 are too granular for main process mapping
            return None

    @property
    def parent_code(self):
        """
        Get the parent process code by removing the last segment.

        Returns:
            str or None: Parent process code, or None if already at root level.
            Examples:
            - "4.1.1" → "4.1"
            - "4.1" → "4.0"
            - "4.0" → None
        """
        if not self.process_code:
            return None

        segments = self.process_code.strip().split(".")

        # Root level (e.g., "4.0") has no parent
        if len(segments) <= 2 and (len(segments) < 2 or segments[1] == "0"):
            return None

        # For level 2 (e.g., "4.1"), parent is the category (e.g., "4.0")
        if len(segments) == 2:
            return f"{segments[0]}.0"

        # For deeper levels, remove the last segment
        parent_segments = segments[:-1]
        return ".".join(parent_segments)

    @classmethod
    def get_processes_by_level(cls, level):
        """
        Query all APQC processes at a specific hierarchy level.

        Args:
            level (int): The hierarchy level to filter (1 - 5).

        Returns:
            list: List of APQCProcess instances at the specified level.
        """
        if not isinstance(level, int) or level < 1 or level > 5:
            return []

        # Get all processes and filter by level
        # Note: This approach loads all processes; for large datasets,
        # consider adding a database column for level or using raw SQL
        all_processes = cls.query.all()
        return [p for p in all_processes if p.apqc_level == level]

    def to_dict(self):
        """Convert process to dictionary for API responses."""
        return {
            "id": self.id,
            "process_code": self.process_code,
            "process_name": self.process_name,
            "process_description": self.process_description,
            "category_level_1": self.category_level_1,
            "category_level_2": self.category_level_2,
            "category_level_3": self.category_level_3,
            "process_category": self.process_category,
            "industry_domain": self.industry_domain,
            "process_type": self.process_type,
            "benchmark_available": self.benchmark_available,
            "process_maturity": self.process_maturity,
            "improvement_priority": self.improvement_priority,
            "process_owner": self.process_owner,
            "apqc_level": self.apqc_level,
            "archimate_mapping_level": self.archimate_mapping_level,
            "parent_code": self.parent_code,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CapabilityProcessMapping(db.Model):
    """
    Junction table linking business capabilities to APQC processes

    Defines relationships between strategic capabilities and operational processes.
    Enables traceability from strategy to execution.
    """

    __tablename__ = "capability_process_mapping"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"), nullable=False)
    apqc_process_id = db.Column(db.Integer, db.ForeignKey("apqc_process.id"), nullable=False)

    # Relationship type and strength
    relationship_type = db.Column(
        db.String(20), default="enables"
    )  # enables, supports, governs, measures
    relationship_strength = db.Column(db.Integer, default=3)  # 1 - 5 strength scale
    impact_level = db.Column(db.String(20), default="medium")  # critical, high, medium, low

    # Process contribution to capability
    process_contribution = db.Column(db.Integer, default=50)  # 0 - 100% contribution
    process_criticality = db.Column(db.String(20), default="medium")  # critical, high, medium, low

    # Assessment metadata
    last_assessed = db.Column(db.DateTime, default=datetime.utcnow)
    assessor = db.Column(db.String(100))
    assessment_notes = db.Column(db.Text)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    capability = db.relationship("BusinessCapability", backref="process_mappings")
    apqc_process = db.relationship("APQCProcess", backref="capability_mappings")

    def __repr__(self):
        return (
            f"<CapabilityProcessMapping {self.capability.name} -> {self.apqc_process.process_name}>"
        )

    def to_dict(self):
        """Convert mapping to dictionary for API responses."""
        return {
            "id": self.id,
            "capability_id": self.capability_id,
            "apqc_process_id": self.apqc_process_id,
            "capability_name": self.capability.name if self.capability else None,
            "process_name": self.apqc_process.process_name if self.apqc_process else None,
            "process_code": self.apqc_process.process_code if self.apqc_process else None,
            "relationship_type": self.relationship_type,
            "relationship_strength": self.relationship_strength,
            "impact_level": self.impact_level,
            "process_contribution": self.process_contribution,
            "process_criticality": self.process_criticality,
            "last_assessed": self.last_assessed.isoformat() if self.last_assessed else None,
            "assessor": self.assessor,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProcessApplicationMapping(TenantMixin, db.Model):
    """
    Junction table linking APQC processes to applications

    Maps applications to the processes they support, enabling process-centric application analysis.
    """

    __tablename__ = "process_application_mapping"
    __tenant_parent__ = (('application_id', 'application_components'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, nullable=False)  # Links to application_components
    apqc_process_id = db.Column(db.Integer, db.ForeignKey("apqc_process.id"), nullable=False)

    # Application support details
    support_level = db.Column(db.String(20), default="partial")  # full, partial, minimal
    automation_level = db.Column(db.Integer, default=1)  # 1 - 5 automation scale
    process_coverage = db.Column(db.Integer, default=50)  # 0 - 100% process coverage

    # Application role in process
    application_role = db.Column(db.String(50))  # primary, secondary, supporting, enabling
    process_step_coverage = db.Column(db.Text)  # JSON with step-by-step coverage

    # Performance metrics
    cycle_time_reduction = db.Column(db.Integer)  # Percentage reduction
    quality_improvement = db.Column(db.Integer)  # Percentage improvement
    cost_reduction = db.Column(db.Integer)  # Percentage reduction

    # Assessment metadata
    last_assessed = db.Column(db.DateTime, default=datetime.utcnow)
    assessor = db.Column(db.String(100))
    assessment_notes = db.Column(db.Text)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    apqc_process = db.relationship("APQCProcess", backref="application_mappings")

    def __repr__(self):
        return f"<ProcessApplicationMapping Application {self.application_id} -> {self.apqc_process.process_name}>"

    def to_dict(self):
        """Convert mapping to dictionary for API responses."""
        return {
            "id": self.id,
            "application_id": self.application_id,
            "apqc_process_id": self.apqc_process_id,
            "process_name": self.apqc_process.process_name if self.apqc_process else None,
            "process_code": self.apqc_process.process_code if self.apqc_process else None,
            "support_level": self.support_level,
            "automation_level": self.automation_level,
            "process_coverage": self.process_coverage,
            "application_role": self.application_role,
            "cycle_time_reduction": self.cycle_time_reduction,
            "quality_improvement": self.quality_improvement,
            "cost_reduction": self.cost_reduction,
            "last_assessed": self.last_assessed.isoformat() if self.last_assessed else None,
            "assessor": self.assessor,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
