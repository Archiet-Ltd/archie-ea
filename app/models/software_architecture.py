"""
Software Architecture Models for ArchiMate 3.2

This module contains the software architecture models that were missing from the
original implementation, providing comprehensive software design capabilities.

Software Architecture Elements:
- SoftwareModule: Modular software structure below ApplicationComponent
- DesignPattern: Software design patterns (Factory, Strategy, etc.)
- SoftwareDependency: Library and framework dependencies
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .. import db
from app.models.mixins import TenantMixin


class SoftwareModule(TenantMixin, db.Model):
    """
    ArchiMate-inspired Software Module model.

    Represents a modular software structure below the ApplicationComponent level,
    providing detailed software architecture modeling capabilities.

    Examples:
    - "Authentication Module" with login, registration, password reset
    - "Payment Processing Module" with transaction handling and validation
    - "Reporting Engine Module" with data aggregation and visualization
    """

    __tablename__ = "software_modules"
    __tenant_parent__ = (('archimate_element_id', 'archimate_elements'),)

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Module characteristics
    module_type = Column(db.String(50))  # Service, Library, Component, Package
    architecture_layer = Column(db.String(30))  # Presentation, Business, Data, Integration
    programming_language = Column(db.String(50))  # Python, Java, JavaScript, C#, etc.

    # Module structure
    package_name = Column(db.String(255))  # Java package or Python module name
    namespace = Column(db.String(255))  # .NET namespace or Python package
    version = Column(db.String(20))

    # Functionality
    primary_responsibility = Column(db.Text)  # Single responsibility principle description
    public_interfaces = Column(db.JSON)  # List of public APIs/interfaces
    dependencies = Column(db.JSON)  # List of module dependencies

    # Quality attributes
    complexity_score = Column(db.Integer)  # 1 - 10 complexity rating
    test_coverage_percentage = Column(db.Float)
    maintainability_index = Column(db.Float)

    # Deployment
    deployment_artifact = Column(db.String(255))  # JAR, WAR, DLL, package name
    runtime_requirements = Column(db.JSON)  # Memory, CPU, OS requirements

    # Governance
    module_owner = Column(db.String(255))
    code_reviewer = Column(db.String(255))
    security_classification = Column(db.String(30))  # Public, Internal, Confidential

    # Status
    status = Column(
        db.String(30), default="development"
    )  # development, testing, production, deprecated
    last_updated = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    created_by = relationship("User", backref="created_software_modules")

    # Module relationships
    parent_application = relationship(
        "ApplicationComponent", secondary="application_modules", back_populates="software_modules"
    )
    design_patterns = relationship(
        "DesignPattern", secondary="module_design_patterns", back_populates="modules"
    )
    dependencies = relationship(
        "SoftwareDependency", secondary="module_dependencies", back_populates="dependent_modules"
    )

    def __repr__(self):
        return f"<SoftwareModule {self.name} ({self.module_type})>"


class DesignPattern(TenantMixin, db.Model):
    """
    Software Design Pattern model.

    Represents software design patterns that can be applied to solve common
    design problems in software architecture.

    Examples:
    - "Factory Pattern" for object creation
    - "Observer Pattern" for event notification
    - "Strategy Pattern" for algorithm selection
    - "Repository Pattern" for data access
    """

    __tablename__ = "design_patterns"
    __tenant_parent__ = (('archimate_element_id', 'archimate_elements'),)

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Pattern classification
    pattern_category = Column(db.String(50))  # Creational, Structural, Behavioral
    pattern_family = Column(db.String(50))  # Gang of Four, Enterprise, Architectural
    complexity_level = Column(db.String(20))  # Simple, Moderate, Complex

    # Problem and solution
    problem_statement = Column(db.Text)  # What problem does this pattern solve?
    solution_description = Column(db.Text)  # How does the pattern solve it?

    # Applicability
    use_cases = Column(db.JSON)  # Common use cases for this pattern
    anti_patterns = Column(db.JSON)  # When NOT to use this pattern
    related_patterns = Column(db.JSON)  # Related or complementary patterns

    # Implementation details
    key_participants = Column(db.JSON)  # Main classes/objects in the pattern
    collaboration_diagram = Column(db.Text)  # ASCII or description of interactions
    implementation_notes = Column(db.Text)  # Language-specific implementation notes

    # Consequences
    benefits = Column(db.JSON)  # Advantages of using this pattern
    drawbacks = Column(db.JSON)  # Disadvantages or trade-offs
    performance_implications = Column(db.Text)  # Performance considerations

    # Code examples
    code_example_language = Column(db.String(50))  # Python, Java, JavaScript, etc.
    code_example = Column(db.Text)  # Sample implementation
    uml_diagram = Column(db.Text)  # UML class diagram description

    # Quality attributes
    maintainability_impact = Column(db.String(30))  # Positive, Negative, Neutral
    testability_impact = Column(db.String(30))  # Positive, Negative, Neutral
    scalability_impact = Column(db.String(30))  # Positive, Negative, Neutral

    # Governance
    pattern_author = Column(db.String(255))  # Original pattern author
    reference_source = Column(db.String(255))  # Book, paper, or online source
    approval_status = Column(db.String(30), default="draft")  # draft, approved, deprecated

    # Usage tracking
    usage_count = Column(db.Integer, default=0)
    success_rate = Column(db.Float)  # Percentage of successful implementations
    last_used_date = Column(db.Date)

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    created_by = relationship("User", backref="created_design_patterns")

    # Pattern applications
    modules = relationship(
        "SoftwareModule", secondary="module_design_patterns", back_populates="design_patterns"
    )

    def __repr__(self):
        return f"<DesignPattern {self.name} ({self.pattern_category})>"


class SoftwareDependency(TenantMixin, db.Model):
    """
    Software Dependency model.

    Represents library, framework, and package dependencies that software
    modules rely on for functionality.

    Examples:
    - "Spring Framework" dependency for Java applications
    - "React Library" dependency for frontend components
    - "NumPy Package" dependency for data processing
    """

    __tablename__ = "software_dependencies"
    __tenant_parent__ = (('archimate_element_id', 'archimate_elements'),)

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Dependency characteristics
    dependency_type = Column(db.String(50))  # Library, Framework, Package, Service
    technology_stack = Column(db.String(50))  # Java, Python, JavaScript, .NET, etc.
    license_type = Column(db.String(50))  # MIT, Apache, GPL, Commercial, etc.

    # Version management
    current_version = Column(db.String(50))
    latest_version = Column(db.String(50))
    version_constraint = Column(db.String(100))  # Semantic versioning constraint

    # Package information
    package_manager = Column(db.String(50))  # Maven, npm, pip, NuGet, etc.
    package_name = Column(db.String(255))  # Package name in the repository
    repository_url = Column(db.String(500))  # Source repository URL

    # Dependency characteristics
    is_runtime_dependency = Column(db.Boolean, default=True)
    is_development_dependency = Column(db.Boolean, default=False)
    is_test_dependency = Column(db.Boolean, default=False)
    is_optional = Column(db.Boolean, default=False)

    # Security and compliance
    vulnerability_score = Column(db.Integer)  # 0 - 10 vulnerability rating
    known_vulnerabilities = Column(db.JSON)  # List of known CVEs
    compliance_issues = Column(db.JSON)  # License compliance issues

    # Usage statistics
    download_count = Column(db.BigInteger)  # Package download statistics
    github_stars = Column(db.Integer)  # GitHub popularity indicator
    last_updated = Column(db.DateTime)  # Last update of the dependency

    # Governance
    approval_status = Column(db.String(30), default="pending")  # pending, approved, rejected
    approved_by = Column(db.String(255))  # Who approved this dependency
    approval_date = Column(db.Date)
    business_justification = Column(db.Text)  # Why this dependency is needed

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    created_by = relationship("User", backref="created_software_dependencies")

    # Dependency relationships
    dependent_modules = relationship(
        "SoftwareModule", secondary="module_dependencies", back_populates="dependencies"
    )

    def __repr__(self):
        return f"<SoftwareDependency {self.name} ({self.dependency_type})>"


# ============================================================================
# JUNCTION TABLES
# ============================================================================

# ApplicationComponent to SoftwareModule junction
application_modules = db.Table(
    "application_modules",
    db.Column(
        "application_component_id",
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "software_module_id",
        db.Integer,
        db.ForeignKey("software_modules.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("integration_type", db.String(50)),  # 'embedded', 'referenced', 'service'
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# SoftwareModule to DesignPattern junction
module_design_patterns = db.Table(
    "module_design_patterns",
    db.Column(
        "software_module_id",
        db.Integer,
        db.ForeignKey("software_modules.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "design_pattern_id",
        db.Integer,
        db.ForeignKey("design_patterns.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("implementation_level", db.String(30)),  # 'full', 'partial', 'inspired'
    db.Column("customizations", db.Text),  # How the pattern was customized
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)

# SoftwareModule to SoftwareDependency junction
module_dependencies = db.Table(
    "module_dependencies",
    db.Column(
        "software_module_id",
        db.Integer,
        db.ForeignKey("software_modules.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "software_dependency_id",
        db.Integer,
        db.ForeignKey("software_dependencies.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column("dependency_scope", db.String(30)),  # 'compile_time', 'runtime', 'test'
    db.Column("version_specified", db.String(100)),  # Specific version constraint
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)
