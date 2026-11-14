"""
ArchiMate 3.2 Business Layer - Missing Element Types

This module provides the remaining ArchiMate 3.2 Business Layer elements that complement
the existing business_layer.py models (BusinessActor, BusinessRole, BusinessService,
BusinessObject, BusinessEvent).

Models:
- BusinessCollaboration: Aggregate of business internal active elements working together
- BusinessInterface: Point of access where business services are made available
- BusinessInteraction: Unit of collective behavior performed by multiple business roles
- Contract: Formal/informal specification of agreement between provider and consumer
- Representation: Perceptible form of information in a business object

Design Pattern:
- Each domain model has archimate_element_id foreign key linking to ArchiMateElement
- model_id links to architecture_models for model-scoped elements
- layer = 'business' as constant for ArchiMate layer classification
- Auto-creates ArchiMateElement on insert via SQLAlchemy event listeners
"""

from datetime import date, datetime

from sqlalchemy import event
from sqlalchemy.orm import relationship

from app.models.mixins import TenantMixin

from .. import db

# ============================================================================
# BusinessCollaboration Domain Model
# ============================================================================


class BusinessCollaboration(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Business Collaboration - Aggregate of two or more business internal
    active structure elements that work together to perform collective behavior.

    Examples:
    - "Order Fulfillment Team" (Sales + Warehouse + Shipping)
    - "Product Development Committee" (Engineering + Marketing + Finance)
    - "Quality Review Board" (QA + Production + Compliance)

    Usage:
        collaboration = BusinessCollaboration(
            name="Order Fulfillment Team",
            collaboration_type="Cross-functional",
            participant_count=12,
            description="Collaboration between sales, warehouse, and shipping teams"
        )
    """

    __tablename__ = "business_collaborations"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Identifiers
    archimate_id = db.Column(
        db.String(50), unique=True, index=True
    )  # External ArchiMate identifier
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )

    # Layer Classification (constant for Business layer)
    layer = db.Column(db.String(20), default="business", nullable=False)
    element_type = db.Column(db.String(50), default="BusinessCollaboration", nullable=False)

    # Link to ArchiMate metamodel representation
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Collaboration-specific attributes
    collaboration_type = db.Column(
        db.String(50)
    )  # Cross-functional, Departmental, External, Virtual, Project-based
    participant_count = db.Column(db.Integer)  # Number of participants/roles involved

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = db.relationship("ArchitectureModel", foreign_keys=[model_id])

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "collaboration_type": self.collaboration_type,
            "participant_count": self.participant_count,
            "layer": self.layer,
            "element_type": self.element_type,
        }

    def __repr__(self):
        return f"<BusinessCollaboration {self.name} ({self.collaboration_type})>"


# ============================================================================
# BusinessInterface Domain Model
# ============================================================================


class BusinessInterface(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Business Interface - Point of access where a business service
    is made available to the environment.

    Examples:
    - "Customer Service Desk" (Phone/Email interface for support)
    - "Self-Service Portal" (Web interface for customer orders)
    - "Partner API Gateway" (Technical interface for B2B integration)

    Usage:
        interface = BusinessInterface(
            name="Customer Service Desk",
            interface_type="Human",
            protocol="Phone/Email",
            description="Primary point of contact for customer inquiries"
        )
    """

    __tablename__ = "business_interfaces"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Identifiers
    archimate_id = db.Column(db.String(50), unique=True, index=True)
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )

    # Layer Classification
    layer = db.Column(db.String(20), default="business", nullable=False)
    element_type = db.Column(db.String(50), default="BusinessInterface", nullable=False)

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Interface-specific attributes
    interface_type = db.Column(db.String(50))  # Human, Digital, Physical, API, Hybrid
    protocol = db.Column(db.String(100))  # Phone, Email, Web, REST, SOAP, Face-to-face, etc.

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = db.relationship("ArchitectureModel", foreign_keys=[model_id])

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "interface_type": self.interface_type,
            "protocol": self.protocol,
            "layer": self.layer,
            "element_type": self.element_type,
        }

    def __repr__(self):
        return f"<BusinessInterface {self.name} ({self.interface_type})>"


# ============================================================================
# BusinessInteraction Domain Model
# ============================================================================


class BusinessInteraction(db.Model):
    """
    ArchiMate 3.2 Business Interaction - Unit of collective business behavior
    performed by (a collaboration of) two or more business roles.

    Examples:
    - "Contract Negotiation" (Sales + Legal + Customer)
    - "Quality Review Meeting" (QA + Production + Engineering)
    - "Budget Approval Process" (Finance + Department Heads + Executive)

    Usage:
        interaction = BusinessInteraction(
            name="Contract Negotiation",
            interaction_type="Negotiation",
            trigger="New customer request",
            description="Collaborative process for finalizing contracts"
        )
    """

    __tablename__ = "business_interactions"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Identifiers
    archimate_id = db.Column(db.String(50), unique=True, index=True)
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )

    # Layer Classification
    layer = db.Column(db.String(20), default="business", nullable=False)
    element_type = db.Column(db.String(50), default="BusinessInteraction", nullable=False)

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Interaction-specific attributes
    interaction_type = db.Column(
        db.String(50)
    )  # Negotiation, Review, Approval, Collaboration, Handoff
    trigger = db.Column(db.String(256))  # What initiates this interaction

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = db.relationship("ArchitectureModel", foreign_keys=[model_id])

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "interaction_type": self.interaction_type,
            "trigger": self.trigger,
            "layer": self.layer,
            "element_type": self.element_type,
        }

    def __repr__(self):
        return f"<BusinessInteraction {self.name} ({self.interaction_type})>"


# ============================================================================
# BusinessEvent Domain Model (Enhanced version for this module)
# Note: A basic BusinessEvent exists in business_layer.py - this is kept for
# compatibility but the existing one in business_layer.py should be used
# ============================================================================

# BusinessEvent is already defined in business_layer.py
# This comment is here to document that it's part of the ArchiMate 3.2 spec


# ============================================================================
# Contract Domain Model
# ============================================================================


class Contract(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Contract - Formal or informal specification of an agreement
    between a provider and a consumer that specifies the rights and obligations
    associated with a product or service.

    Examples:
    - "Master Service Agreement" (Legal contract with vendor)
    - "Service Level Agreement" (SLA for IT services)
    - "Employment Contract" (HR agreement with employees)
    - "Partnership Agreement" (Terms with business partners)

    Usage:
        contract = Contract(
            name="SAP Support Agreement",
            contract_type="Service Agreement",
            effective_date=date(2024, 1, 1),
            expiry_date=date(2026, 12, 31),
            description="Annual support and maintenance agreement"
        )
    """

    __tablename__ = "archimate_contracts"  # Using prefixed name to avoid conflicts

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Identifiers
    archimate_id = db.Column(db.String(50), unique=True, index=True)
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )

    # Layer Classification
    layer = db.Column(db.String(20), default="business", nullable=False)
    element_type = db.Column(db.String(50), default="Contract", nullable=False)

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Contract-specific attributes
    contract_type = db.Column(
        db.String(50)
    )  # Service Agreement, License, Employment, Partnership, NDA, SLA
    effective_date = db.Column(db.Date)  # When contract becomes active
    expiry_date = db.Column(db.Date)  # When contract expires

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = db.relationship("ArchitectureModel", foreign_keys=[model_id])

    @property
    def is_active(self):
        """Check if contract is currently active"""
        today = date.today()
        if self.effective_date and self.expiry_date:
            return self.effective_date <= today <= self.expiry_date
        elif self.effective_date:
            return self.effective_date <= today
        return True

    @property
    def days_until_expiry(self):
        """Calculate days until contract expires"""
        from app.services.vendor_register import days_until

        return days_until(self.expiry_date)

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "contract_type": self.contract_type,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "is_active": self.is_active,
            "layer": self.layer,
            "element_type": self.element_type,
        }

    def __repr__(self):
        return f"<Contract {self.name} ({self.contract_type})>"


# ============================================================================
# Representation Domain Model
# ============================================================================


class Representation(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Representation - Perceptible form of the information carried
    by a business object.

    A representation represents the tangible form in which a business object
    is presented, such as a document, message, or diagram.

    Examples:
    - "Purchase Order Form" (PDF representation of Order)
    - "Invoice Document" (Printable invoice format)
    - "Quality Report" (Standard QA report template)
    - "Contract Document" (Legal contract format)

    Usage:
        representation = Representation(
            name="Purchase Order Form",
            representation_type="Document",
            format="PDF",
            description="Standardized purchase order template"
        )
    """

    __tablename__ = "archimate_representations"

    id = db.Column(db.Integer, primary_key=True)

    # Core Identity
    name = db.Column(db.String(256), nullable=False, index=True)
    description = db.Column(db.Text)

    # ArchiMate Identifiers
    archimate_id = db.Column(db.String(50), unique=True, index=True)
    model_id = db.Column(
        db.Integer, db.ForeignKey("architecture_models.id"), nullable=True, index=True
    )

    # Layer Classification
    layer = db.Column(db.String(20), default="business", nullable=False)
    element_type = db.Column(db.String(50), default="Representation", nullable=False)

    # Link to ArchiMate metamodel
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
    )

    # Representation-specific attributes
    representation_type = db.Column(
        db.String(50)
    )  # Document, Message, Diagram, Form, Report, Template
    format = db.Column(db.String(50))  # PDF, XML, JSON, HTML, Word, Excel, Image

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    architecture_model = db.relationship("ArchitectureModel", foreign_keys=[model_id])

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "archimate_id": self.archimate_id,
            "representation_type": self.representation_type,
            "format": self.format,
            "layer": self.layer,
            "element_type": self.element_type,
        }

    def __repr__(self):
        return f"<Representation {self.name} ({self.format})>"


# ============================================================================
# SQLAlchemy Event Listeners - Auto-create ArchiMateElements
# ============================================================================


@event.listens_for(BusinessCollaboration, "before_insert")
def create_collaboration_archimate_element(mapper, connection, target):
    """Automatically create ArchiMateElement when BusinessCollaboration is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="BusinessCollaboration",
                layer="Business",
                description=target.description
                or f"{target.collaboration_type or 'Business'} collaboration",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(BusinessInterface, "before_insert")
def create_interface_archimate_element(mapper, connection, target):
    """Automatically create ArchiMateElement when BusinessInterface is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="BusinessInterface",
                layer="Business",
                description=target.description
                or f"{target.interface_type or 'Business'} interface",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(BusinessInteraction, "before_insert")
def create_interaction_archimate_element(mapper, connection, target):
    """Automatically create ArchiMateElement when BusinessInteraction is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                name=target.name,
                type="BusinessInteraction",
                layer="Business",
                description=target.description
                or f"{target.interaction_type or 'Business'} interaction",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(Contract, "before_insert")
def create_contract_archimate_element(mapper, connection, target):
    """Automatically create ArchiMateElement when Contract is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="Contract",
                layer="Business",
                description=target.description or f"{target.contract_type or 'Business'} contract",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(Representation, "before_insert")
def create_representation_archimate_element(mapper, connection, target):
    """Automatically create ArchiMateElement when Representation is created"""
    if target.archimate_element_id is None:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="Representation",
                layer="Business",
                description=target.description
                or f"{target.representation_type or 'Business'} representation",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    "BusinessCollaboration",
    "BusinessInterface",
    "BusinessInteraction",
    "Contract",
    "Representation",
]
