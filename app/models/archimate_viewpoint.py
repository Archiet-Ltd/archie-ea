"""ArchiMate 3.2 Viewpoint Catalog Models.

Implements the ArchiMate 3.2 viewpoint mechanism for filtering the full
architecture model to show stakeholder-relevant perspectives.

ArchiMate defines 14 standard viewpoints covering:
- Stakeholder viewpoints (who, what, why)
- Layered viewpoints (business, application, technology)
- Relationship viewpoints (dependencies, flows)

Each viewpoint specifies:
- Purpose and concerns it addresses
- Typical stakeholders who use it
- Allowed element types and relationships
- Which ArchiMate layers are included
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin


class ArchiMateViewpoint(TenantMixin, db.Model):
    """ArchiMate 3.2 Viewpoint Catalog.

    Represents a viewpoint that filters the architecture model to show
    a specific perspective for particular stakeholders.

    Standard viewpoints are seeded from ArchiMate 3.2 specification.
    Custom viewpoints can be created for organization-specific needs.
    """

    __tablename__ = "archimate_viewpoints"

    id = db.Column(db.Integer, primary_key=True)

    # Basic identification
    name = db.Column(db.String(100), nullable=False, unique=True, index=True)
    viewpoint_type = db.Column(
        db.String(50), nullable=False
    )  # 'stakeholder', 'layered', 'relation', 'custom'
    description = db.Column(db.Text)
    purpose = db.Column(db.Text, nullable=False)  # What this viewpoint is designed to show

    # Stakeholder relevance
    concerns = db.Column(
        db.JSON, nullable=False
    )  # List of concern strings (e.g., ["Cost efficiency", "Risk management"])
    typical_stakeholders = db.Column(
        db.JSON, nullable=False
    )  # List of stakeholder roles (e.g., ["CIO", "Enterprise Architect"])

    # ArchiMate layers included in this viewpoint
    includes_strategy_layer = db.Column(db.Boolean, default=False, nullable=False)
    includes_business_layer = db.Column(db.Boolean, default=False, nullable=False)
    includes_application_layer = db.Column(db.Boolean, default=False, nullable=False)
    includes_technology_layer = db.Column(db.Boolean, default=False, nullable=False)
    includes_physical_layer = db.Column(db.Boolean, default=False, nullable=False)
    includes_motivation_layer = db.Column(db.Boolean, default=False, nullable=False)
    includes_implementation_layer = db.Column(db.Boolean, default=False, nullable=False)

    # Element and relationship filtering
    allowed_element_types = db.Column(
        db.JSON, nullable=False
    )  # List of element type names allowed in this viewpoint
    allowed_relationship_types = db.Column(
        db.JSON, nullable=False
    )  # List of relationship types allowed

    # Usage guidance
    typical_usage_scenario = db.Column(db.Text)  # When and how to use this viewpoint
    example_questions = db.Column(db.JSON)  # Questions this viewpoint helps answer
    related_viewpoints = db.Column(db.JSON)  # Names of related/complementary viewpoints

    # Metadata
    archimate_version = db.Column(db.String(10), default="3.2", nullable=False)
    is_standard = db.Column(
        db.Boolean, default=True, nullable=False
    )  # False for custom organization viewpoints
    standard_number = db.Column(
        db.Integer
    )  # ArchiMate spec numbering (1 - 14 for standard viewpoints)

    # Audit fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    created_by = db.relationship("User", backref="created_viewpoints", foreign_keys=[created_by_id])
    stakeholder_mappings = db.relationship(
        "ViewpointStakeholderMapping", back_populates="viewpoint", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<ArchiMateViewpoint {self.name} (Type: {self.viewpoint_type})>"

    def get_included_layers(self):
        """Return list of included layer names."""
        layers = []
        if self.includes_strategy_layer:
            layers.append("Strategy")
        if self.includes_business_layer:
            layers.append("Business")
        if self.includes_application_layer:
            layers.append("Application")
        if self.includes_technology_layer:
            layers.append("Technology")
        if self.includes_physical_layer:
            layers.append("Physical")
        if self.includes_motivation_layer:
            layers.append("Motivation")
        if self.includes_implementation_layer:
            layers.append("Implementation & Migration")
        return layers

    def is_element_allowed(self, element_type):
        """Check if an element type is allowed in this viewpoint."""
        return element_type in (self.allowed_element_types or [])

    def is_relationship_allowed(self, relationship_type):
        """Check if a relationship type is allowed in this viewpoint."""
        return relationship_type in (self.allowed_relationship_types or [])

    def filter_elements(self, elements):
        """Filter a list of ArchiMateElement instances to those allowed in this viewpoint."""
        return [e for e in elements if self.is_element_allowed(e.type)]

    def filter_relationships(self, relationships):
        """Filter a list of ArchiMateRelationship instances to those allowed in this viewpoint."""
        return [r for r in relationships if self.is_relationship_allowed(r.type)]


class ViewpointStakeholderMapping(db.Model):
    """Maps business stakeholder roles to recommended ArchiMate viewpoints.

    Helps stakeholders discover which viewpoints are most relevant to their
    role and concerns. A stakeholder role (e.g., "CIO") may map to multiple
    viewpoints based on their typical concerns.
    """

    __tablename__ = "viewpoint_stakeholder_mappings"

    id = db.Column(db.Integer, primary_key=True)

    # Stakeholder information
    stakeholder_role = db.Column(
        db.String(100), nullable=False, index=True
    )  # e.g., "CIO", "Business Owner", "Security Officer"
    stakeholder_category = db.Column(
        db.String(50)
    )  # e.g., "Executive", "Manager", "Specialist", "External"
    primary_concerns = db.Column(db.JSON)  # List of primary concerns for this role

    # Viewpoint mapping
    viewpoint_id = db.Column(db.Integer, db.ForeignKey("archimate_viewpoints.id"), nullable=False)
    relevance_score = db.Column(
        db.Integer, default=5
    )  # 1 - 10 scale of how relevant this viewpoint is to the role
    is_primary_viewpoint = db.Column(
        db.Boolean, default=False
    )  # True if this is the main viewpoint for this role

    # Usage context
    usage_context = db.Column(db.Text)  # When/why this stakeholder would use this viewpoint
    example_use_cases = db.Column(
        db.JSON
    )  # Specific use cases for this role + viewpoint combination

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    viewpoint = db.relationship("ArchiMateViewpoint", back_populates="stakeholder_mappings")

    # Composite unique constraint: one mapping per role+viewpoint combination
    __table_args__ = (
        db.UniqueConstraint("stakeholder_role", "viewpoint_id", name="uq_stakeholder_viewpoint"),
    )

    def __repr__(self):
        return f"<ViewpointStakeholderMapping {self.stakeholder_role} -> Viewpoint #{self.viewpoint_id}>"

    @staticmethod
    def get_viewpoints_for_role(role_name):
        """Get all recommended viewpoints for a stakeholder role, ordered by relevance."""
        mappings = (
            ViewpointStakeholderMapping.query.filter_by(stakeholder_role=role_name)
            .order_by(ViewpointStakeholderMapping.relevance_score.desc())
            .all()
        )

        return [mapping.viewpoint for mapping in mappings]

    @staticmethod
    def get_stakeholders_for_viewpoint(viewpoint_id):
        """Get all stakeholder roles that should use a given viewpoint."""
        mappings = (
            ViewpointStakeholderMapping.query.filter_by(viewpoint_id=viewpoint_id)
            .order_by(ViewpointStakeholderMapping.relevance_score.desc())
            .all()
        )

        return [mapping.stakeholder_role for mapping in mappings]


class ViewpointView(db.Model):
    """Concrete views created from viewpoints for specific architecture models.

    A view is an instantiation of a viewpoint for a specific architecture model,
    potentially with additional filtering or customization. While a viewpoint is
    a template/specification, a view is an actual diagram or model subset.

    DEPRECATED (composer-diagram-link-mismatches, D3, 2026-09-19): this table
    has no readers anywhere in the codebase — no route, service, template or
    test ever queries ``viewpoint_views``. It also lacks ``TenantMixin``, so it
    was a tenant-isolation gap on top of being a dead store. Its former sole
    writer, ``architect_viewpoints()`` in
    ``app/modules/ai_chat/routes/chat_workflows.py``, now creates
    ``SavedDiagram`` rows via
    ``app.services.archimate_composer_service.create_diagram`` instead, since
    ``SavedDiagram`` is the composer's actual system of record and is already
    served by the ``/archimate/api/saved-viewpoints/*`` endpoints. Retired per
    ADR 0008 (retire, never accumulate) — do not drop the table, and do not add
    a new writer here; extend ``SavedDiagram`` instead.
    """

    __tablename__ = "viewpoint_views"

    id = db.Column(db.Integer, primary_key=True)

    # Basic identification
    name = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text)

    # Viewpoint and model linkage
    viewpoint_id = db.Column(db.Integer, db.ForeignKey("archimate_viewpoints.id"), nullable=False)
    architecture_model_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))

    # View-specific filtering (beyond viewpoint defaults)
    specific_element_ids = db.Column(db.JSON)  # If not null, only show these specific elements
    specific_relationship_ids = db.Column(
        db.JSON
    )  # If not null, only show these specific relationships
    additional_filters = db.Column(
        db.JSON
    )  # Additional custom filters (e.g., {"status": "active", "priority": "high"})

    # Layout and presentation
    layout_data = db.Column(db.JSON)  # Diagram layout information (positions, styles, etc.)
    zoom_level = db.Column(db.Float, default=1.0)
    show_element_labels = db.Column(db.Boolean, default=True)
    show_relationship_labels = db.Column(db.Boolean, default=True)

    # Ownership and sharing
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    is_public = db.Column(db.Boolean, default=False)  # If true, visible to all users
    shared_with_roles = db.Column(db.JSON)  # List of stakeholder roles that can access this view

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    last_viewed_at = db.Column(db.DateTime)
    view_count = db.Column(db.Integer, default=0)

    # Relationships
    viewpoint = db.relationship("ArchiMateViewpoint", backref="views")
    owner = db.relationship("User", backref="owned_views", foreign_keys=[owner_id])

    def __repr__(self):
        return f"<ViewpointView {self.name} (Viewpoint: {self.viewpoint_id})>"

    def increment_view_count(self):
        """Increment view count and update last viewed timestamp."""
        self.view_count = (self.view_count or 0) + 1
        self.last_viewed_at = datetime.utcnow()
        db.session.commit()

    def get_filtered_elements(self):
        """Get all elements that should be displayed in this view."""
        from .models import ArchiMateElement

        # Start with viewpoint's allowed element types
        allowed_types = self.viewpoint.allowed_element_types

        query = ArchiMateElement.query

        # Filter by architecture model if specified
        if self.architecture_model_id:
            query = query.filter_by(architecture_id=self.architecture_model_id)

        # Filter by element type
        if allowed_types:
            query = query.filter(ArchiMateElement.type.in_(allowed_types))

        # If specific elements are specified, further filter
        if self.specific_element_ids:
            query = query.filter(ArchiMateElement.id.in_(self.specific_element_ids))

        # Apply additional custom filters
        if self.additional_filters:
            for key, value in self.additional_filters.items():
                if hasattr(ArchiMateElement, key):
                    query = query.filter(getattr(ArchiMateElement, key) == value)

        return query.all()

    def get_filtered_relationships(self):
        """Get all relationships that should be displayed in this view."""
        from .models import ArchiMateRelationship

        # Get elements in this view
        element_ids = [e.id for e in self.get_filtered_elements()]

        # Get allowed relationship types
        allowed_types = self.viewpoint.allowed_relationship_types

        query = ArchiMateRelationship.query

        # Only include relationships between elements in this view
        query = query.filter(
            ArchiMateRelationship.source_id.in_(element_ids),
            ArchiMateRelationship.target_id.in_(element_ids),
        )

        # Filter by relationship type
        if allowed_types:
            query = query.filter(ArchiMateRelationship.type.in_(allowed_types))

        # If specific relationships are specified, further filter
        if self.specific_relationship_ids:
            query = query.filter(ArchiMateRelationship.id.in_(self.specific_relationship_ids))

        return query.all()


class ArchimateAuditLog(db.Model):
    """Audit log for ArchiMate composer actions."""

    __tablename__ = "archimate_audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    # CMP-03: viewpoint_id is the COMPOSER diagram id (saved_diagrams.id), which
    # is what the composer sends as currentSavedVpId. It previously carried a
    # FOREIGN KEY to archimate_viewpoints.id — a different table — so every audit
    # write from the composer (e.g. removing an element from a saved diagram)
    # raised a FK violation and surfaced as a "Failed to log audit event" toast,
    # silently breaking the governance trail. It is a loose reference (no FK):
    # audit rows are append-only diagnostics and must never fail the user action
    # they record. The matching DB constraint is dropped by
    # `flask drop-audit-log-viewpoint-fk` in the compose boot chain.
    viewpoint_id = db.Column(db.Integer, nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    entity_type = db.Column(db.String(100), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    entity_name = db.Column(db.String(255), nullable=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Set when this row has been copied into the one audit store
    # (soc2_audit_log); points at the copy. NULL until copied.
    retired_into_id = db.Column(db.BigInteger, db.ForeignKey("soc2_audit_log.id"), nullable=True)

    actor = db.relationship("User", foreign_keys=[user_id], lazy="joined")

    def __repr__(self):
        return f"<ArchimateAuditLog {self.action} by user {self.user_id}>"


class ArchimatePattern(db.Model):
    """Custom architecture pattern saved by a user.

    Built-in patterns are defined in BUILTIN_PATTERNS in archimate_routes.py.
    This model stores user-created patterns as a named JSON template.
    """

    __tablename__ = "archimate_patterns"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    # JSON: {"elements": [{role, type, label}], "relationships": [{source_role, target_role, type}]}
    pattern_json = db.Column(db.Text, nullable=False, default="{}")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    creator = db.relationship("User", foreign_keys=[created_by], lazy="joined")

    def __repr__(self):
        return f"<ArchimatePattern {self.name}>"


class ArchimateViewpointTemplate(db.Model):
    """Saved diagram template capturing layout and element structure.

    Allows SAs to save a canvas state as a reusable template for future diagrams.
    """

    __tablename__ = "archimate_viewpoint_templates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    viewpoint_type = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)
    # JSON: {"elements": [...], "relationships": [...], "zones": [...]}
    template_json = db.Column(db.Text, nullable=False, default="{}")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    creator = db.relationship("User", foreign_keys=[created_by], lazy="joined")

    def __repr__(self):
        return f"<ArchimateViewpointTemplate {self.name}>"


class ArchimateViewpointSnapshot(db.Model):
    """Version snapshot of a saved diagram (viewpoint).

    Created by the composer's "Save Snapshot" action.  Stores a full
    serialised copy of the canvas state (elements, relationships and their
    positions) so that previous versions can be reviewed or restored.
    """

    __tablename__ = "archimate_viewpoint_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    viewpoint_id = db.Column(
        db.Integer, db.ForeignKey("saved_diagrams.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = db.Column(db.String(200), nullable=False)
    snapshot_json = db.Column(db.Text, nullable=False, default="{}")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<ArchimateViewpointSnapshot {self.name} vp={self.viewpoint_id}>"


class ArchimateElementComment(db.Model):
    """CMP-024: a comment on an ArchiMate element, optionally scoped to a viewpoint."""

    __tablename__ = "archimate_element_comments"

    id = db.Column(db.Integer, primary_key=True)
    element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=False, index=True
    )
    viewpoint_id = db.Column(db.Integer, nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    comment_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    author = db.relationship("User", foreign_keys=[user_id])

    def to_dict(self):
        return {
            "id": self.id,
            "element_id": self.element_id,
            "viewpoint_id": self.viewpoint_id,
            "user_id": self.user_id,
            "user_name": getattr(self.author, "name", None) if self.author else None,
            "comment_text": self.comment_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<ArchimateElementComment {self.id} el={self.element_id}>"
