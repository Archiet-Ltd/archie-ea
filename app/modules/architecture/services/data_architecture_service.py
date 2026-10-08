"""
ArchiMate 3.2 Data Architecture Service

Provides validation and analysis services for data architecture models
according to ArchiMate 3.2 specifications and relationship rules.
"""

from typing import Dict, List  # dead-code-ok

from sqlalchemy.orm import joinedload

from app.models import (  # dead-code-ok
    ArchiMateElement,
    ArchiMateRelationship,
    ConceptualDataModel,
    DataLineage,
    LogicalDataModel,
    PhysicalDataModel,
)


class DataArchitectureService:
    """
    Service for managing and validating data architecture models
    according to ArchiMate 3.2 specifications.
    """

    def __init__(self):
        pass

    def validate_data_model_hierarchy(
        self, conceptual_id: int, logical_id: int, physical_id: int
    ) -> Dict:
        """
        Validate the conceptual → logical → physical data model hierarchy.

        ArchiMate 3.2 Rules:
        - LogicalDataModel must SPECIALIZE ConceptualDataModel
        - PhysicalDataModel must REALIZE LogicalDataModel
        - No circular dependencies
        - Proper layer separation maintained
        """
        try:
            conceptual = ConceptualDataModel.query.get(conceptual_id)
            logical = LogicalDataModel.query.get(logical_id)
            physical = PhysicalDataModel.query.get(physical_id)

            validation_result = {
                "valid": True,
                "violations": [],
                "warnings": [],
                "compliance_score": 100,
            }

            # Rule 1: Logical must specialize conceptual
            if logical.conceptual_model_id != conceptual_id:
                validation_result["violations"].append(
                    {
                        "rule": "SPECIALIZATION",
                        "message": f'LogicalDataModel "{logical.name}" must specialize ConceptualDataModel "{conceptual.name}"',
                        "severity": "ERROR",
                    }
                )
                validation_result["valid"] = False
                validation_result["compliance_score"] -= 30

            # Rule 2: Physical must realize logical
            if physical.logical_model_id != logical_id:
                validation_result["violations"].append(
                    {
                        "rule": "REALIZATION",
                        "message": f'PhysicalDataModel "{physical.name}" must realize LogicalDataModel "{logical.name}"',
                        "severity": "ERROR",
                    }
                )
                validation_result["valid"] = False
                validation_result["compliance_score"] -= 30

            # Rule 3: Check database type consistency
            if logical.design_pattern == "Star Schema" and physical.database_type not in [
                "PostgreSQL",
                "MySQL",
                "Oracle",
                "SQL Server",
            ]:
                validation_result["warnings"].append(
                    {
                        "rule": "DESIGN_PATTERN_CONSISTENCY",
                        "message": f"Star schema pattern typically used with relational databases, not {physical.database_type}",
                        "severity": "WARNING",
                    }
                )
                validation_result["compliance_score"] -= 10

            # Rule 4: Business domain consistency
            if conceptual.business_domain != logical.business_domain:
                validation_result["warnings"].append(
                    {
                        "rule": "BUSINESS_DOMAIN_CONSISTENCY",
                        "message": f'Business domain mismatch: conceptual="{conceptual.business_domain}" vs logical="{logical.business_domain}"',  # raw-html-ok: JSON API message field, not an HTML string
                        "severity": "WARNING",
                    }
                )
                validation_result["compliance_score"] -= 10

            return validation_result

        except Exception as e:
            return {
                "valid": False,
                "violations": [
                    {"rule": "VALIDATION_ERROR", "message": str(e), "severity": "ERROR"}
                ],
                "warnings": [],
                "compliance_score": 0,
            }

    def analyze_data_lineage_compliance(self, lineage_id: int) -> Dict:
        """
        Analyze data lineage for ArchiMate 3.2 compliance.

        ArchiMate 3.2 Rules:
        - DataLineage must use FLOW relationships
        - DataLineage must ACCESSES BusinessObject/DataObject
        - Proper source/target element types
        - No circular flows
        """
        try:
            lineage = DataLineage.query.options(joinedload(DataLineage.transformations)).get(
                lineage_id
            )

            analysis = {
                "compliant": True,
                "issues": [],
                "recommendations": [],
                "flow_completeness": 0,
            }

            # Check if lineage has proper source/target elements
            if not lineage.source_system or not lineage.target_system:
                analysis["issues"].append(
                    {
                        "type": "MISSING_FLOW_ENDPOINTS",
                        "message": "Data lineage must have defined source and target systems",
                        "severity": "ERROR",
                    }
                )
                analysis["compliant"] = False

            # Check data classification consistency
            if (
                lineage.data_classification == "Confidential"
                and not lineage.compliance_requirements
            ):
                analysis["issues"].append(
                    {
                        "type": "MISSING_COMPLIANCE",
                        "message": "Confidential data requires compliance requirements (GDPR, HIPAA, etc.)",
                        "severity": "ERROR",
                    }
                )
                analysis["compliant"] = False

            # Analyze transformation completeness
            if not lineage.transformations:
                analysis["recommendations"].append(
                    {
                        "type": "MISSING_TRANSFORMATIONS",
                        "message": "Consider adding data transformations for better lineage tracking",
                    }
                )
            else:
                analysis["flow_completeness"] = min(100, len(lineage.transformations) * 20)

            # Check retention policy
            if (
                lineage.data_classification == "Public"
                and lineage.retention_period_days
                and lineage.retention_period_days > 2555
            ):  # 7 years
                analysis["recommendations"].append(
                    {
                        "type": "RETENTION_POLICY",
                        "message": "Public data typically requires shorter retention periods",
                    }
                )

            return analysis

        except Exception as e:
            return {
                "compliant": False,
                "issues": [{"type": "ANALYSIS_ERROR", "message": str(e), "severity": "ERROR"}],
                "recommendations": [],
                "flow_completeness": 0,
            }

    def generate_data_architecture_viewpoint(self, architecture_model_id: int) -> Dict:
        """
        Generate ArchiMate 3.2 Data Architecture viewpoint.

        Includes:
        - All data models (conceptual, logical, physical)
        - Data lineage flows
        - Business object relationships
        - Technology deployment mapping
        """
        try:
            # Get all data models for this architecture
            conceptual_models = ConceptualDataModel.query.filter_by(
                architecture_id=architecture_model_id
            ).all()

            logical_models = LogicalDataModel.query.filter_by(
                architecture_id=architecture_model_id
            ).all()

            physical_models = PhysicalDataModel.query.filter_by(
                architecture_id=architecture_model_id
            ).all()

            data_lineage = DataLineage.query.filter_by(architecture_id=architecture_model_id).all()

            viewpoint = {
                "viewpoint_type": "Data Architecture",
                "archimate_version": "3.2",
                "elements": {
                    "conceptual_models": self._serialize_conceptual_models(conceptual_models),
                    "logical_models": self._serialize_logical_models(logical_models),
                    "physical_models": self._serialize_physical_models(physical_models),
                    "data_lineage": self._serialize_data_lineage(data_lineage),
                },
                "relationships": self._extract_archimate_relationships(architecture_model_id),
                "compliance_metrics": self._calculate_compliance_metrics(architecture_model_id),
            }

            return viewpoint

        except Exception as e:
            return {
                "error": str(e),
                "viewpoint_type": "Data Architecture",
                "archimate_version": "3.2",
                "elements": {},
                "relationships": [],
                "compliance_metrics": {},
            }

    def _serialize_conceptual_models(self, models: List[ConceptualDataModel]) -> List[Dict]:
        """Serialize conceptual models with ArchiMate metadata."""
        return [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "archimate_element_id": m.archimate_element_id,
                "business_domain": m.business_domain,
                "scope": m.scope,
                "business_objects_count": len(m.business_objects)
                if hasattr(m, "business_objects")
                else 0,
                "capabilities_count": len(m.capabilities) if hasattr(m, "capabilities") else 0,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in models
        ]

    def _serialize_logical_models(self, models: List[LogicalDataModel]) -> List[Dict]:
        """Serialize logical models with ArchiMate metadata."""
        return [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "archimate_element_id": m.archimate_element_id,
                "normalization_level": m.normalization_level,
                "design_pattern": m.design_pattern,
                "conceptual_model_id": m.conceptual_model_id,
                "supports_transactions": m.supports_transactions,
                "estimated_entities": m.estimated_entities,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in models
        ]

    def _serialize_physical_models(self, models: List[PhysicalDataModel]) -> List[Dict]:
        """Serialize physical models with ArchiMate metadata."""
        return [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "archimate_element_id": m.archimate_element_id,
                "database_type": m.database_type,
                "database_version": m.database_version,
                "logical_model_id": m.logical_model_id,
                "deployment_environment": m.deployment_environment,
                "estimated_size_gb": m.estimated_size_gb,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in models
        ]

    def _serialize_data_lineage(self, lineage: List[DataLineage]) -> List[Dict]:
        """Serialize data lineage with ArchiMate metadata."""
        return [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "archimate_element_id": item.archimate_element_id,
                "lineage_type": item.lineage_type,
                "source_system": item.source_system,
                "target_system": item.target_system,
                "data_domain": item.data_domain,
                "data_classification": item.data_classification,
                "frequency": item.frequency,
                "transformation_count": len(item.transformations)
                if hasattr(item, "transformations")
                else 0,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in lineage
        ]

    def _extract_archimate_relationships(self, architecture_model_id: int) -> List[Dict]:
        """Extract ArchiMate relationships for data architecture elements."""
        # This would query ArchiMateRelationship table for relationships
        # between data architecture elements
        return []

    def _calculate_compliance_metrics(self, architecture_model_id: int) -> Dict:
        """Calculate ArchiMate 3.2 compliance metrics from actual model data."""
        from app import db

        element_count = db.session.query(ArchiMateElement).filter_by(
            architecture_id=architecture_model_id
        ).count()

        if element_count == 0:
            return {
                "overall_compliance": 0,
                "relationship_compliance": 0,
                "layer_separation": 0,
                "element_coverage": 0,
                "validation_score": 0,
                "data_available": False,
            }

        relationship_count = db.session.query(ArchiMateRelationship).filter_by(
            architecture_id=architecture_model_id
        ).count()

        # Elements with at least one relationship (connected elements)
        connected_elements = db.session.query(
            db.func.count(db.distinct(ArchiMateRelationship.source_element_id))
        ).filter_by(architecture_id=architecture_model_id).scalar() or 0

        element_coverage = round((connected_elements / element_count) * 100, 1) if element_count else 0
        relationship_ratio = round((relationship_count / element_count) * 100, 1) if element_count else 0
        # Compliance is based on whether elements are properly connected
        overall = round((element_coverage + min(100, relationship_ratio)) / 2, 1)

        return {
            "overall_compliance": overall,
            "relationship_compliance": min(100, relationship_ratio),
            "layer_separation": 100 if element_count > 0 else 0,
            "element_coverage": element_coverage,
            "validation_score": overall,
            "data_available": True,
        }


# ---------------------------------------------------------------------------
# Data entity CRUD matrix
# ---------------------------------------------------------------------------
#
# "Which applications create, read, update or delete this data entity" is
# answered from one store: ArchiMate access relationships from an application
# component's element to the data entity's element (both mirror into
# archimate_elements on create). The Composer draws the same relationships, so
# an access drawn there and one recorded on the data entity page are the same
# row. ``crud_operations`` carries the C/R/U/D detail ArchiMate's read/write
# access_mode cannot; access_mode is kept consistent with it.

CRUD_LETTERS = "CRUD"


def normalise_crud(operations) -> str:
    """The distinct C/R/U/D letters in ``operations``, in CRUD order."""
    chosen = {str(op).strip().upper()[:1] for op in (operations or []) if str(op).strip()}
    return "".join(letter for letter in CRUD_LETTERS if letter in chosen)


def access_mode_for(crud: str):
    """ArchiMate's access mode for a set of CRUD letters."""
    reads = "R" in crud
    writes = any(letter in crud for letter in "CUD")
    if reads and writes:
        return "readwrite"
    if writes:
        return "write"
    if reads:
        return "read"
    return None


def _is_access(rel) -> bool:
    return (rel.type or "").strip().lower() == "access"


def entity_access_matrix(entity) -> List[Dict]:
    """One row per application recorded as accessing ``entity``.

    Each row: ``application_id`` (the portfolio record, None when the element
    has none), ``name``, ``cells`` {letter: True | False | None}. None means
    the relationship says it writes but not whether it creates, updates or
    deletes (an access drawn with only an ArchiMate access mode); it is never
    shown as a no.
    """
    from app.models.application_portfolio import ApplicationComponent

    element_id = getattr(entity, "archimate_element_id", None)
    if not element_id:
        return []
    rels = [
        rel for rel in ArchiMateRelationship.query.filter(
            ArchiMateRelationship.target_id == element_id
        ).all()
        if _is_access(rel)
    ]
    if not rels:
        return []
    source_ids = {rel.source_id for rel in rels}
    elements = {
        el.id: el for el in ArchiMateElement.query.filter(ArchiMateElement.id.in_(source_ids)).all()
        if (el.type or "").replace("_", "").lower() == "applicationcomponent"
    }
    apps = {}
    if elements:
        apps = {
            app.archimate_element_id: app for app in ApplicationComponent.query.filter(
                ApplicationComponent.archimate_element_id.in_(list(elements))
            ).all()
        }
    rows = []
    for rel in rels:
        element = elements.get(rel.source_id)
        if element is None:
            continue
        app = apps.get(element.id)
        crud = rel.crud_operations
        if crud:
            cells = {letter: letter in crud for letter in CRUD_LETTERS}
        else:
            mode = (rel.access_mode or "").lower()
            writes = mode in ("write", "readwrite")
            cells = {"R": (mode in ("read", "readwrite")) if mode else None}
            for letter in "CUD":
                cells[letter] = None if (writes or not mode) else False
        rows.append({
            "relationship_id": rel.id,
            "application_id": app.id if app else None,
            "name": app.name if app else element.name,
            "cells": cells,
        })
    rows.sort(key=lambda row: (row["name"] or "").lower())
    return rows


def record_entity_access(entity, application, operations):
    """Record that ``application`` performs ``operations`` on ``entity``.

    Upserts the single access relationship between their elements. Raises
    ValueError when no operation is chosen or either side has no element.
    """
    from app import db

    crud = normalise_crud(operations)
    if not crud:
        raise ValueError("Choose at least one of create, read, update or delete.")
    source_id = getattr(application, "archimate_element_id", None)
    target_id = getattr(entity, "archimate_element_id", None)
    if not source_id or not target_id:
        raise ValueError("This application or data entity is not in the architecture model.")

    rel = next(
        (
            r for r in ArchiMateRelationship.query.filter(
                ArchiMateRelationship.source_id == source_id,
                ArchiMateRelationship.target_id == target_id,
            ).all()
            if _is_access(r)
        ),
        None,
    )
    if rel is None:
        rel = ArchiMateRelationship(source_id=source_id, target_id=target_id, type="access")
        db.session.add(rel)
    rel.crud_operations = crud
    rel.access_mode = access_mode_for(crud)
    db.session.commit()
    return rel
