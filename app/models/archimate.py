"""
ArchiMate 3.2 Model Definitions

Core ArchiMate 3.2 enumerations and model classes for enterprise architecture modeling.
Provides the foundation for ArchiMate element and relationship management.
"""

from datetime import datetime
from enum import Enum
from typing import Optional  # dead-code-ok

from .. import db
from .mixins import TenantMixin

# =============================================================================
# ArchiMate 3.2 Enumerations
# =============================================================================


class ElementType(str, Enum):
    """ArchiMate 3.2 Element Types by Layer."""

    # Business Layer
    BUSINESS_ACTOR = "business_actor"
    BUSINESS_ROLE = "business_role"
    BUSINESS_COLLABORATION = "business_collaboration"
    BUSINESS_INTERFACE = "business_interface"
    BUSINESS_PROCESS = "business_process"
    BUSINESS_FUNCTION = "business_function"
    BUSINESS_INTERACTION = "business_interaction"
    BUSINESS_EVENT = "business_event"
    BUSINESS_SERVICE = "business_service"
    BUSINESS_OBJECT = "business_object"
    CONTRACT = "contract"
    REPRESENTATION = "representation"
    PRODUCT = "product"

    # Application Layer
    APPLICATION_COMPONENT = "application_component"
    APPLICATION_COLLABORATION = "application_collaboration"
    APPLICATION_INTERFACE = "application_interface"
    APPLICATION_FUNCTION = "application_function"
    APPLICATION_INTERACTION = "application_interaction"
    APPLICATION_PROCESS = "application_process"
    APPLICATION_EVENT = "application_event"
    APPLICATION_SERVICE = "application_service"
    DATA_OBJECT = "data_object"

    # Technology Layer
    NODE = "node"
    DEVICE = "device"
    SYSTEM_SOFTWARE = "system_software"
    TECHNOLOGY_COLLABORATION = "technology_collaboration"
    TECHNOLOGY_INTERFACE = "technology_interface"
    PATH = "path"
    COMMUNICATION_NETWORK = "communication_network"
    TECHNOLOGY_FUNCTION = "technology_function"
    TECHNOLOGY_PROCESS = "technology_process"
    TECHNOLOGY_INTERACTION = "technology_interaction"
    TECHNOLOGY_EVENT = "technology_event"
    TECHNOLOGY_SERVICE = "technology_service"
    ARTIFACT = "artifact"

    # Physical Layer
    EQUIPMENT = "equipment"
    FACILITY = "facility"
    DISTRIBUTION_NETWORK = "distribution_network"
    MATERIAL = "material"

    # Motivation Layer
    STAKEHOLDER = "stakeholder"
    DRIVER = "driver"
    ASSESSMENT = "assessment"
    GOAL = "goal"
    OUTCOME = "outcome"
    PRINCIPLE = "principle"
    REQUIREMENT = "requirement"
    CONSTRAINT = "constraint"
    MEANING = "meaning"
    VALUE = "value"

    # Strategy Layer
    RESOURCE = "resource"
    CAPABILITY = "capability"
    COURSE_OF_ACTION = "course_of_action"
    VALUE_STREAM = "value_stream"

    # Implementation & Migration Layer
    WORK_PACKAGE = "work_package"
    DELIVERABLE = "deliverable"
    IMPLEMENTATION_EVENT = "implementation_event"
    PLATEAU = "plateau"
    GAP = "gap"


class Layer(str, Enum):
    """ArchiMate 3.2 Layers."""

    BUSINESS = "business"
    APPLICATION = "application"
    TECHNOLOGY = "technology"
    PHYSICAL = "physical"
    MOTIVATION = "motivation"
    STRATEGY = "strategy"
    IMPLEMENTATION = "implementation"


class RelationshipType(str, Enum):
    """ArchiMate 3.2 Relationship Types."""

    # Structural Relationships
    COMPOSITION = "composition"
    AGGREGATION = "aggregation"
    ASSIGNMENT = "assignment"
    REALIZATION = "realization"

    # Dependency Relationships
    SERVING = "serving"
    ACCESS = "access"
    INFLUENCE = "influence"

    # Dynamic Relationships
    TRIGGERING = "triggering"
    FLOW = "flow"

    # Other Relationships
    SPECIALIZATION = "specialization"
    ASSOCIATION = "association"


# =============================================================================
# ArchiMate Model Classes
# =============================================================================


class ArchitectureElement(TenantMixin, db.Model):
    """
    Architecture Element Model

    Represents an ArchiMate element (business, application, technology, etc.)
    """

    __tablename__ = "architecture_elements"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    element_type = db.Column(db.String(50), nullable=False)
    layer = db.Column(db.String(50))
    description = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ArchitectureElement {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "element_type": self.element_type,
            "layer": self.layer,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Relationship(TenantMixin, db.Model):
    """
    Relationship Model

    Represents a relationship between two architecture elements.
    """

    __tablename__ = "architecture_relationships"
    __tenant_parent__ = (('source_id', 'architecture_elements'),)
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey("architecture_elements.id"), nullable=False)
    target_id = db.Column(db.Integer, db.ForeignKey("architecture_elements.id"), nullable=False)
    relationship_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Relationship {self.source_id}-{self.relationship_type}->{self.target_id}>"

    def to_dict(self):
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship_type": self.relationship_type,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ArchiMateView(TenantMixin, db.Model):
    """
    ArchiMate View Model

    Represents views/diagrams containing ArchiMate elements and relationships.
    """

    __tablename__ = "archimate_views"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    view_type = db.Column(db.String(50))  # e.g., "business", "application", "technology"

    # View properties
    viewpoint = db.Column(db.String(100))  # ArchiMate viewpoint
    properties = db.Column(db.Text)  # JSON string for view properties

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ArchiMateView {self.name} ({self.view_type})>"
