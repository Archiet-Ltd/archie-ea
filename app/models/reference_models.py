"""
Manufacturing Reference Model Framework

This module provides standardized capability templates from industry frameworks:
- ISA - 95: Enterprise-control system integration (Production, Quality, Maintenance, Inventory)
- APQC PCF: Process classification framework for manufacturing
- Industry 4.0: Smart manufacturing and digital transformation capabilities

Reference models enable:
1. Systematic capability discovery (vs. ad-hoc definition)
2. Compliance scoring (coverage % against standards)
3. Missing capability identification
4. ArchiMate 3.2 Strategy Layer element generation
"""

import json
from datetime import datetime

from .. import db
from .mixins import HybridTenantMixin, TenantMixin


class ReferenceModel(HybridTenantMixin, db.Model):
    """
    Reference Model Definition (ISA - 95, APQC, Industry 4.0)

    Stores metadata about industry-standard capability frameworks.
    Each reference model contains a hierarchical capability structure.
    """

    __tablename__ = "reference_model"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False, unique=True, index=True)
    code = db.Column(db.String(50), nullable=False, unique=True)  # ISA95, APQC, INDUSTRY40
    version = db.Column(db.String(50))  # e.g., "3.0", "7.3.0", "2024"
    description = db.Column(db.Text)
    industry = db.Column(db.String(100))  # Manufacturing, Discrete, Process, All
    source_url = db.Column(db.String(500))

    # Metadata
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    capabilities = db.relationship(
        "ReferenceModelCapability", back_populates="reference_model", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<ReferenceModel {self.code}: {self.name}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "version": self.version,
            "description": self.description,
            "industry": self.industry,
            "source_url": self.source_url,
            "is_active": self.is_active,
            "capability_count": len(self.capabilities),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ReferenceModelCapability(HybridTenantMixin, db.Model):
    """
    Reference Model Capability Template

    Hierarchical capability structure from reference models.
    Used as templates to create BusinessCapability records.
    Maps to ArchiMate 3.2 Strategy Layer elements.
    """

    __tablename__ = "reference_model_capability"

    id = db.Column(db.Integer, primary_key=True)
    reference_model_id = db.Column(db.Integer, db.ForeignKey("reference_model.id"), nullable=False)

    # Capability identity
    name = db.Column(db.String(256), nullable=False, index=True)
    code = db.Column(db.String(50))  # e.g., "ISA95 - PROD - 01", "APQC - 4.1.1"
    description = db.Column(db.Text)

    # Hierarchy (Level 0=Strategic/Domain, 1=L1, 2=L2, 3=L3/Operational)
    parent_capability_id = db.Column(db.Integer, db.ForeignKey("reference_model_capability.id"))
    level = db.Column(db.Integer, default=0)  # 0=Domain, 1=L1, 2=L2, 3=L3
    sort_order = db.Column(db.Integer, default=0)  # Display order within parent

    # ArchiMate 3.2 Strategy Layer mapping
    archimate_element_type = db.Column(
        db.String(50), default="Capability"
    )  # Capability, Resource, CourseOfAction
    archimate_layer = db.Column(
        db.String(50), default="Strategy"
    )  # Always Strategy for capabilities

    # Maturity benchmarks
    recommended_maturity_level = db.Column(db.Integer)  # CMM 1 - 5 industry benchmark
    criticality = db.Column(db.String(20))  # Critical, High, Medium, Low

    # Metadata
    is_core = db.Column(db.Boolean, default=True)  # Core vs. optional capability
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    reference_model = db.relationship("ReferenceModel", back_populates="capabilities")
    children = db.relationship(
        "ReferenceModelCapability",
        backref=db.backref("parent", remote_side="ReferenceModelCapability.id"),
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<ReferenceModelCapability {self.code}: {self.name}>"

    def to_dict(self, include_children=False):
        data = {
            "id": self.id,
            "reference_model_id": self.reference_model_id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "level": self.level,
            "parent_capability_id": self.parent_capability_id,
            "archimate_element_type": self.archimate_element_type,
            "archimate_layer": self.archimate_layer,
            "recommended_maturity_level": self.recommended_maturity_level,
            "criticality": self.criticality,
            "is_core": self.is_core,
            "sort_order": self.sort_order,
        }

        if include_children:
            data["children"] = [child.to_dict(include_children=True) for child in self.children]

        return data

    def get_hierarchy_path(self):
        """Get full hierarchy path from root to this capability"""
        path = [self.name]
        current = self.parent
        while current:
            path.insert(0, current.name)
            current = current.parent
        return " > ".join(path)


class ReferenceModelImport(TenantMixin, db.Model):
    """
    Reference Model Import History

    Tracks when reference model capabilities were imported into BusinessCapability.
    Enables compliance tracking and auditability.

    Unlike its shared catalogue siblings, an import event is one organisation's
    own action against its own BusinessCapability rows, so it is fenced
    (TenantMixin) rather than left shared (HybridTenantMixin).
    """

    __tablename__ = "reference_model_import"

    id = db.Column(db.Integer, primary_key=True)
    reference_model_id = db.Column(db.Integer, db.ForeignKey("reference_model.id"), nullable=False)

    # Import metadata
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)
    imported_by = db.Column(db.String(256))  # User who initiated import
    capabilities_imported = db.Column(db.Integer, default=0)
    archimate_elements_created = db.Column(db.Integer, default=0)

    # Import settings
    import_settings = db.Column(db.Text)  # JSON: which levels, domains, etc.

    # Status
    status = db.Column(db.String(50), default="completed")  # pending, completed, failed
    error_message = db.Column(db.Text)

    # Relationships
    reference_model = db.relationship("ReferenceModel")

    def __repr__(self):
        return f"<ReferenceModelImport {self.reference_model_id} at {self.imported_at}>"

    def to_dict(self):
        return {
            "id": self.id,
            "reference_model_id": self.reference_model_id,
            "reference_model_name": self.reference_model.name if self.reference_model else None,
            "imported_at": self.imported_at.isoformat() if self.imported_at else None,
            "imported_by": self.imported_by,
            "capabilities_imported": self.capabilities_imported,
            "archimate_elements_created": self.archimate_elements_created,
            "status": self.status,
            "import_settings": json.loads(self.import_settings) if self.import_settings else {},
        }
