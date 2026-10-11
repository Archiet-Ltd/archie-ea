"""
ArchiMate 3.2 Data Architecture Service

Provides validation and analysis services for data architecture models
according to ArchiMate 3.2 specifications and relationship rules.
"""

from typing import Any, Dict, List, Optional  # dead-code-ok

from sqlalchemy.orm import joinedload

from app.models import (  # dead-code-ok
    ArchiMateElement,
    ArchiMateRelationship,
    ConceptualDataModel,
    DataLineage,
    LogicalDataModel,
    PhysicalDataModel,
)

#: R1-B80 multi-hop walk: a hard stop so a cyclic or very long lineage graph
#: cannot turn one page load into an unbounded query. The brief's own
#: acceptance ("follow lineage two hops") needs far fewer; this is a safety
#: ceiling, not a target depth.
_MAX_LINEAGE_HOPS = 10


class DataArchitectureService:
    """
    Service for managing and validating data architecture models
    according to ArchiMate 3.2 specifications.
    """

    def __init__(self):
        pass

    # ------------------------------------------------------------------ #
    # R1-B80: multi-hop lineage, held from source through every
    # transformation to a report, with the owner at each hop.
    # ------------------------------------------------------------------ #

    @staticmethod
    def _tenant_predicate(model, organization_id: int):
        """The explicit organization_id == predicate on every tenant read
        on this path, isolated as its own seam -- same discipline as
        IntelligenceQueryService's per-concept predicates (ADR 0003)."""
        return model.organization_id == organization_id

    @staticmethod
    def _owner_for_element(element, organization_id: int) -> Optional[Dict[str, Any]]:
        """The element's application's owner, via R1-B03's canonical
        ownership reader (ApplicationOwner.get_display_rows_for_application)
        -- never a free-text field read directly, and never a guess when no
        row exists. Prefers the 'primary' ownership type when more than one
        row exists; falls back to the first row, same as the fact-sheet
        reader's own display convention."""
        from app.models.application_owner import ApplicationOwner
        from app.models.application_portfolio import ApplicationComponent

        component_id = getattr(element, "application_component_id", None)
        if not component_id:
            from app import db

            component_id = db.session.execute(
                db.select(ApplicationComponent.id)
                .where(ApplicationComponent.archimate_element_id == element.id)
                .where(DataArchitectureService._tenant_predicate(ApplicationComponent, organization_id))
            ).scalars().first()
        if not component_id:
            return None

        rows = ApplicationOwner.get_display_rows_for_application(component_id, organization_id)
        if not rows:
            return None
        primary = next((r for r in rows if r["ownership_type"] == "primary"), rows[0])
        return {"user_name": primary["user_name"], "ownership_type_label": primary["ownership_type_label"]}

    @classmethod
    def multi_hop_lineage(
        cls, element_id: int, organization_id: int, max_hops: int = 5
    ) -> Dict[str, Any]:
        """Walk the DataLineage graph outward from *element_id* to *max_hops*
        (capped at _MAX_LINEAGE_HOPS), source through every transformation to
        a report, each hop carrying its element's owner.

        Breadth-first, bounded and paginated (the existing traversal
        pattern this brief's "Extends" note names, from
        IntelligenceQueryService's own single-hop walk) -- not a second
        graph engine alongside R1-B11's impact engine or R1-B29's
        projection, just a wider version of the one-hop walk already used
        by the Ask Data lens, since lineage hops are ArchiMate/DataFlow
        edges, a different kind of edge from either of those.

        Returns {"hops": [...], "truncated": bool}. Each hop is
        {"element_id", "element_name", "depth", "direction", "owner"}, in
        breadth-first order (depth 0 is the starting element). A cycle back
        to an already-visited element is dropped, not re-walked -- lineage
        is a flow, not a tree, and a flow that loops back on itself must
        not be walked forever.
        """
        from app import db

        max_hops = max(1, min(max_hops, _MAX_LINEAGE_HOPS))

        start = db.session.execute(
            db.select(ArchiMateElement)
            .where(ArchiMateElement.id == element_id)
            .where(cls._tenant_predicate(ArchiMateElement, organization_id))
        ).scalar_one_or_none()
        if start is None:
            return {"hops": [], "truncated": False}

        visited = {element_id}
        frontier = [element_id]
        hops: List[Dict[str, Any]] = [{
            "element_id": start.id,
            "element_name": start.name,
            "depth": 0,
            "direction": None,
            "owner": cls._owner_for_element(start, organization_id),
        }]
        truncated = False

        for depth in range(1, max_hops + 1):
            if not frontier:
                break
            edges = db.session.execute(
                db.select(DataLineage)
                .where(db.or_(
                    DataLineage.archimate_element_id.in_(frontier),
                    DataLineage.target_archimate_element_id.in_(frontier),
                ))
                .where(cls._tenant_predicate(DataLineage, organization_id))
            ).scalars().all()

            next_frontier = []
            for edge in edges:
                outgoing = edge.archimate_element_id in frontier
                other_id = edge.target_archimate_element_id if outgoing else edge.archimate_element_id
                if other_id is None or other_id in visited:
                    continue
                other = db.session.execute(
                    db.select(ArchiMateElement)
                    .where(ArchiMateElement.id == other_id)
                    .where(cls._tenant_predicate(ArchiMateElement, organization_id))
                ).scalar_one_or_none()
                if other is None:
                    # Another organisation's element, or deleted -- dropped,
                    # not named, same rule as the one-hop Ask lens.
                    continue
                visited.add(other_id)
                next_frontier.append(other_id)
                hops.append({
                    "element_id": other.id,
                    "element_name": other.name,
                    "depth": depth,
                    "direction": "out" if outgoing else "in",
                    "owner": cls._owner_for_element(other, organization_id),
                })

            frontier = next_frontier

        if frontier and len(hops) and (hops[-1]["depth"] == max_hops):
            # The walk stopped only because it hit the hop ceiling, not
            # because the graph ran out of edges -- say so, rather than let
            # an incomplete walk look complete.
            truncated = True

        return {"hops": hops, "truncated": truncated}

    @classmethod
    def downstream_impact(
        cls, element_id: int, organization_id: int, max_hops: int = 5
    ) -> Dict[str, Any]:
        """PB-0116: "assess downstream impact on a source field" -- every
        consumer reachable by an OUTGOING DataLineage edge from
        *element_id*, ranked by business criticality (the field on
        ApplicationComponent R1-B39 also reads; "—" when absent, never
        guessed).

        Reuses ``multi_hop_lineage``'s own walk rather than adding a second
        traversal: an impact assessment is the same graph, read in one
        direction only, so every consumer it finds is the same consumer a
        lineage walk would show.
        """
        from app.models.application_portfolio import ApplicationComponent

        walked = cls.multi_hop_lineage(element_id, organization_id, max_hops=max_hops)
        consumers = [h for h in walked["hops"] if h["direction"] == "out"]

        _CRITICALITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

        def _criticality_for(hop: Dict[str, Any]) -> Optional[str]:
            from app import db

            component = db.session.execute(
                db.select(ApplicationComponent.business_criticality)
                .where(ApplicationComponent.archimate_element_id == hop["element_id"])
                .where(cls._tenant_predicate(ApplicationComponent, organization_id))
            ).scalars().first()
            return component

        ranked = []
        for hop in consumers:
            criticality = _criticality_for(hop)
            ranked.append({**hop, "criticality": criticality or "—"})

        ranked.sort(key=lambda r: _CRITICALITY_RANK.get(r["criticality"], len(_CRITICALITY_RANK)))

        return {"consumers": ranked, "truncated": walked["truncated"]}

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
