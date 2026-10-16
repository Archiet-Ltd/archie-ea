"""
Software Quality Models for ArchiMate 3.2

This module contains software quality models that enable Software Architects
to track technical debt, code quality metrics, and refactoring.

Models:
- TechnicalDebt: Debt items, prioritization, remediation
- CodeQualityMetrics: Cyclomatic complexity, maintainability over time
- RefactoringTracking: Refactoring history, impact analysis
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import event

from .. import db
from .mixins.core import TenantMixin


class TechnicalDebt(TenantMixin, db.Model):
    """
    Technical Debt model for tracking and managing technical debt.

    Tracks technical debt items, their impact, prioritization, and remediation.
    Inherits ``TenantMixin`` (same as every per-organisation model) so each
    row belongs to exactly one organisation: reads and counts are scoped by
    the tenant isolation layer, and inserts carry the current organisation.

    Examples:
    - "Legacy Authentication Code" debt: High priority, 40 hours to fix
    - "Missing Unit Tests" debt: Medium priority, 20 hours to fix
    """

    __tablename__ = "technical_debt"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # Software relationship
    software_module_id = db.Column(db.Integer, db.ForeignKey("software_modules.id"), index=True)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), index=True
    )

    # Debt characteristics
    debt_type = db.Column(
        db.String(50)
    )  # Code Quality, Architecture, Testing, Documentation, Security
    debt_category = db.Column(
        db.String(50)
    )  # Design Debt, Code Debt, Test Debt, Documentation Debt

    # Impact assessment
    priority = db.Column(db.String(20), default="medium")  # critical, high, medium, low
    impact_level = db.Column(db.String(20))  # high, medium, low
    estimated_effort_hours = db.Column(db.Integer)  # Hours to fix

    # Debt details
    root_cause = db.Column(db.Text)  # Why this debt exists
    consequences = db.Column(db.Text)  # What problems this causes
    remediation_plan = db.Column(db.Text)  # How to fix it

    # Status
    status = db.Column(
        db.String(30), default="identified"
    )  # identified, prioritized, in_progress, resolved
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Dates
    identified_date = db.Column(db.Date, default=date.today)
    target_resolution_date = db.Column(db.Date)
    resolved_date = db.Column(db.Date)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    software_module = db.relationship("SoftwareModule", backref="technical_debt")
    application_component = db.relationship("ApplicationComponent", backref="technical_debt")
    assigned_to = db.relationship(
        "User", foreign_keys=[assigned_to_id], backref="assigned_technical_debt"
    )
    created_by = db.relationship(
        "User", foreign_keys=[created_by_id], backref="created_technical_debt"
    )

    def __repr__(self):
        return f"<TechnicalDebt {self.name} ({self.priority})>"


class CodeQualityMetrics(db.Model):
    """
    Code Quality Metrics model for tracking code quality over time.

    Tracks cyclomatic complexity, maintainability index, and other quality metrics.

    Examples:
    - "Authentication Module" quality: complexity 15, maintainability 75
    - "Payment Module" quality: complexity 8, maintainability 85
    """

    __tablename__ = "code_quality_metrics"

    id = db.Column(db.Integer, primary_key=True)

    # Software relationship
    software_module_id = db.Column(
        db.Integer, db.ForeignKey("software_modules.id"), nullable=False, index=True
    )

    # Quality metrics
    cyclomatic_complexity = db.Column(db.Integer)  # Cyclomatic complexity score
    maintainability_index = db.Column(db.Float)  # Maintainability index (0 - 100)
    code_smells_count = db.Column(db.Integer)  # Number of code smells
    technical_debt_ratio = db.Column(db.Float)  # Technical debt ratio percentage

    # Code coverage
    test_coverage_percentage = db.Column(db.Float)  # Test coverage percentage
    line_coverage_percentage = db.Column(db.Float)  # Line coverage
    branch_coverage_percentage = db.Column(db.Float)  # Branch coverage

    # Code metrics
    lines_of_code = db.Column(db.Integer)  # Total lines of code
    functions_count = db.Column(db.Integer)  # Number of functions
    classes_count = db.Column(db.Integer)  # Number of classes

    # Duplication
    duplication_percentage = db.Column(db.Float)  # Code duplication percentage
    duplicated_lines = db.Column(db.Integer)  # Number of duplicated lines

    # Security
    security_issues_count = db.Column(db.Integer)  # Number of security issues
    vulnerability_count = db.Column(db.Integer)  # Number of vulnerabilities

    # Metrics timestamp
    measured_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    software_module = db.relationship("SoftwareModule", backref="quality_metrics")

    def __repr__(self):
        return f"<CodeQualityMetrics {self.software_module_id} (Complexity: {self.cyclomatic_complexity})>"


class RefactoringTracking(db.Model):
    """
    Refactoring Tracking model for refactoring history and impact analysis.

    Tracks refactoring activities, their impact, and outcomes.

    Examples:
    - "Extract Authentication Service" refactoring: completed, improved maintainability
    - "Replace Legacy Database Layer" refactoring: in progress, high impact
    """

    __tablename__ = "refactoring_tracking"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.Text)

    # Software relationship
    software_module_id = db.Column(db.Integer, db.ForeignKey("software_modules.id"), index=True)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), index=True
    )

    # Refactoring characteristics
    refactoring_type = db.Column(
        db.String(50)
    )  # Extract Method, Extract Class, Replace Legacy, Simplify, Rename
    refactoring_category = db.Column(db.String(50))  # Structural, Behavioral, Data, Design Pattern

    # Impact analysis
    impact_scope = db.Column(db.String(50))  # local, module, component, system
    affected_modules = db.Column(db.Text)  # JSON: List of affected modules
    risk_level = db.Column(db.String(20))  # low, medium, high

    # Refactoring details
    motivation = db.Column(db.Text)  # Why this refactoring is needed
    approach = db.Column(db.Text)  # How the refactoring will be done
    expected_benefits = db.Column(db.Text)  # Expected benefits after refactoring

    # Status
    status = db.Column(
        db.String(30), default="planned"
    )  # planned, in_progress, completed, cancelled
    progress_percentage = db.Column(db.Integer, default=0)  # 0 - 100

    # Dates
    planned_date = db.Column(db.Date)
    started_date = db.Column(db.Date)
    completed_date = db.Column(db.Date)

    # Outcomes
    actual_benefits = db.Column(db.Text)  # Actual benefits achieved
    issues_encountered = db.Column(db.Text)  # Issues encountered during refactoring
    quality_improvement = db.Column(db.Float)  # Quality improvement score (0 - 100)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    software_module = db.relationship("SoftwareModule", backref="refactoring_history")
    application_component = db.relationship("ApplicationComponent", backref="refactoring_history")
    created_by = db.relationship("User", backref="created_refactoring_tracking")

    def __repr__(self):
        return f"<RefactoringTracking {self.name} ({self.status})>"
