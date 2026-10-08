"""
ArchiMate 3.2 Missing Elements - Motivation and Business Layers

This module contains missing ArchiMate 3.2 element models:
- Stakeholder: Motivation layer element
- BusinessCollaboration: Business layer element
- BusinessInterface: Business layer element
- BusinessInteraction: Business layer element
- Product: Business layer element
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    event,
)

from .. import db
from app.models.mixins import TenantMixin

# Stakeholder is defined in app.models.motivation — re-export for backward compatibility
from app.models.motivation import Stakeholder  # noqa: F401

# ============================================================================
# ArchiMate 3.2 Missing Business Layer Elements
# ============================================================================


# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class MissingBusinessCollaboration(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Business Collaboration element (Business Layer).

    Represents an aggregate of two or more business internal active structure elements
    that work together to perform collective behavior.

    Examples:
    - "Customer Service Team" (collaboration of Customer Service Representatives)
    - "Order Fulfillment Group" (collaboration of Warehouse and Shipping)
    - "Product Development Team" (collaboration of Product Managers and Engineers)
    """

    __tablename__ = "missing_business_collaborations"
    __table_args__ = {"extend_existing": True}

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Collaboration characteristics
    collaboration_type = Column(db.String(50))  # team, group, committee, working_group
    purpose = Column(db.Text)  # Why this collaboration exists
    scope = Column(db.String(100))  # department, cross-functional, enterprise

    # Governance
    coordinator_id = Column(db.Integer, db.ForeignKey("business_actors.id"))
    meeting_frequency = Column(db.String(30))  # daily, weekly, bi-weekly, monthly, ad-hoc

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    coordinator = db.relationship("BusinessActor", foreign_keys=[coordinator_id])
    created_by = db.relationship("User", backref="created_collaborations")

    def __repr__(self):
        return f"<BusinessCollaboration {self.name}>"


# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class MissingBusinessInterface(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Business Interface element (Business Layer).

    Represents a point of access where business services are made available to the environment.

    Examples:
    - "Customer Portal" (interface for customer-facing services)
    - "Supplier Portal" (interface for supplier interactions)
    - "Employee Self-Service" (interface for HR services)
    """

    __tablename__ = "missing_business_interfaces"
    __table_args__ = {"extend_existing": True}

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Interface characteristics
    interface_type = Column(db.String(50))  # portal, api, channel, touchpoint
    access_method = Column(db.String(50))  # web, mobile, phone, email, in-person
    availability = Column(db.String(50))  # 24/7, business_hours, on-demand

    # Service exposure
    exposed_services = Column(db.Text)  # JSON array of service IDs or names

    # Technical details
    technology_stack = Column(db.Text)  # JSON array of technologies
    authentication_method = Column(db.String(50))  # none, basic, oauth, sso, mfa

    # Usage metrics
    user_count = Column(db.Integer)
    transaction_volume = Column(db.Integer)

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    created_by = db.relationship("User", backref="created_business_interfaces")

    def __repr__(self):
        return f"<BusinessInterface {self.name} ({self.interface_type})>"


# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class MissingBusinessInteraction(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Business Interaction element (Business Layer).

    Represents a unit of collective business behavior performed by (a collaboration of)
    two or more business actors.

    Examples:
    - "Order Negotiation" (interaction between Sales and Customer)
    - "Contract Review" (interaction between Legal and Procurement)
    - "Product Approval" (interaction between Product and Quality teams)
    """

    __tablename__ = "missing_business_interactions"
    __table_args__ = {"extend_existing": True}

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Interaction characteristics
    interaction_type = Column(
        db.String(50)
    )  # negotiation, approval, review, coordination, collaboration
    trigger = Column(db.Text)  # What triggers this interaction
    outcome = Column(db.Text)  # Expected outcome of interaction

    # Process linkage
    supporting_process_id = Column(db.Integer, db.ForeignKey("business_processes.id"))

    # Frequency and duration
    frequency = Column(db.String(30))  # continuous, daily, weekly, monthly, ad-hoc
    average_duration_minutes = Column(db.Integer)

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    supporting_process = db.relationship("BusinessProcess", foreign_keys=[supporting_process_id])
    created_by = db.relationship("User", backref="created_business_interactions")

    def __repr__(self):
        return f"<BusinessInteraction {self.name} ({self.interaction_type})>"


# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class Product(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Product element (Business Layer).

    Represents a coherent collection of services and/or passive structure elements,
    accompanied by a contract/set of agreements, which is offered as a whole to (internal or external) customers.

    Examples:
    - "Customer Portal Product" (bundle of customer-facing services)
    - "Enterprise Software Suite" (bundle of software services)
    - "Managed IT Services" (bundle of IT support services)
    """

    __tablename__ = "products"
    __table_args__ = {"extend_existing": True}

    id = Column(db.Integer, primary_key=True)
    name = Column(db.String(255), nullable=False, index=True)
    description = Column(db.Text)

    # ArchiMate linkage
    archimate_element_id = Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Product characteristics
    product_type = Column(
        db.String(50)
    )  # service_bundle, software_product, physical_product, hybrid
    product_category = Column(db.String(100))  # customer_facing, internal, partner, platform
    target_market = Column(db.String(100))  # enterprise, smb, consumer, internal

    # Business value
    value_proposition = Column(db.Text)  # What value does this product deliver
    pricing_model = Column(db.String(50))  # subscription, one-time, usage-based, freemium
    revenue_model = Column(db.String(50))  # direct_revenue, cost_reduction, strategic

    # Product composition
    included_services = Column(db.Text)  # JSON array of BusinessService IDs
    included_contracts = Column(db.Text)  # JSON array of Contract IDs

    # Lifecycle
    product_status = Column(db.String(30), default="active")  # planning, active, sunset, retired
    launch_date = Column(db.Date)
    retirement_date = Column(db.Date)

    # Ownership
    product_owner_id = Column(db.Integer, db.ForeignKey("business_actors.id"))
    product_manager = Column(db.String(200))

    # Metrics
    customer_count = Column(db.Integer)
    annual_revenue = Column(db.Numeric(15, 2))
    market_share = Column(db.Numeric(5, 2))  # Percentage

    # Metadata
    created_at = Column(db.DateTime, default=datetime.utcnow)
    updated_at = Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    product_owner = db.relationship("BusinessActor", foreign_keys=[product_owner_id])
    created_by = db.relationship("User", backref="created_products")

    def __repr__(self):
        return f"<Product {self.name} ({self.product_type})>"


# Auto-create ArchiMate elements when models are created
@event.listens_for(Stakeholder, "before_insert")
def create_stakeholder_archimate(mapper, connection, target):
    """Auto-create ArchiMateElement for Stakeholder"""
    if not target.archimate_element_id:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="Stakeholder",
                layer="Motivation",
                description=target.description or f"Stakeholder: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(MissingBusinessCollaboration, "before_insert")
def create_collaboration_archimate(mapper, connection, target):
    """Auto-create ArchiMateElement for BusinessCollaboration"""
    if not target.archimate_element_id:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="BusinessCollaboration",
                layer="Business",
                description=target.description or f"Business collaboration: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(MissingBusinessInterface, "before_insert")
def create_interface_archimate(mapper, connection, target):
    """Auto-create ArchiMateElement for BusinessInterface"""
    if not target.archimate_element_id:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="BusinessInterface",
                layer="Business",
                description=target.description or f"Business interface: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(MissingBusinessInteraction, "before_insert")
def create_interaction_archimate(mapper, connection, target):
    """Auto-create ArchiMateElement for BusinessInteraction"""
    if not target.archimate_element_id:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="BusinessInteraction",
                layer="Business",
                description=target.description or f"Business interaction: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]


@event.listens_for(Product, "before_insert")
def create_product_archimate(mapper, connection, target):
    """Auto-create ArchiMateElement for Product"""
    if not target.archimate_element_id:
        from sqlalchemy import insert

        from .archimate_core import ArchiMateElement

        result = connection.execute(
            insert(ArchiMateElement.__table__).values(
                organization_id=target.organization_id,
                name=target.name,
                type="Product",
                layer="Business",
                description=target.description or f"Product: {target.name}",
            )
        )
        target.archimate_element_id = result.inserted_primary_key[0]
