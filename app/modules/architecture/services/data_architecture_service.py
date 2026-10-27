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


# ---------------------------------------------------------------------------
# Data flows: every entity, who uses it, and who handles personal data
# ---------------------------------------------------------------------------
#
# One reader for the data architecture page, the data lineage page and the
# assistant's ask_data_flow tool, so the three agree on every count. The flow
# of one entity, hop by hop, is the answer service's data_flow question; this
# is the list it starts from.


def data_flow_overview(org_id) -> Dict:
    """The organisation's data entities with the applications that use each, and
    the applications that handle personal data.

    ``entities``: one row per entity, by name: ``id``, ``name``, ``domain``,
    ``element_id`` (its data object, the id the flow question takes),
    ``personal_data``, ``classification``, ``system_of_record``, ``owner`` (the
    unit that owns the system of record), ``steward`` (its domain's steward),
    ``applications`` [{``name``, ``application_id``, ``access``}] and ``copies``
    (lineage rows that copy it elsewhere). ``personal_data_applications``: every
    application that uses an entity holding personal data, with those entities.
    """
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_element_types import is_application_kind
    from app.models.process_data import DataEntity
    from app.modules.intelligence.services.query_service import _access_words, _resolve_owners_batch

    entities = (
        DataEntity.query.filter(DataEntity.organization_id == org_id)
        .order_by(DataEntity.name, DataEntity.id)
        .all()
    )
    element_ids = [e.archimate_element_id for e in entities if e.archimate_element_id]
    rels = []
    if element_ids:
        rels = [
            rel for rel in ArchiMateRelationship.query.filter(
                ArchiMateRelationship.target_id.in_(element_ids)
            ).order_by(ArchiMateRelationship.id).all()
            if _is_access(rel)
        ]
    apps_by_element = {}
    if rels:
        apps_by_element = {
            el.id: el for el in ArchiMateElement.query.filter(
                ArchiMateElement.id.in_({rel.source_id for rel in rels}),
                ArchiMateElement.organization_id == org_id,
            ).all()
            if is_application_kind(el.type)
        }
    components = {}
    if apps_by_element:
        for comp in ApplicationComponent.query.filter(
            ApplicationComponent.archimate_element_id.in_(list(apps_by_element))
        ).order_by(ApplicationComponent.id).all():
            components.setdefault(comp.archimate_element_id, comp)
    copies = dict(
        db.session.query(DataLineage.archimate_element_id, db.func.count(DataLineage.id))
        .filter(DataLineage.archimate_element_id.in_(element_ids or [0]))
        .filter(DataLineage.target_archimate_element_id.isnot(None))
        .group_by(DataLineage.archimate_element_id)
        .all()
    )
    sor_elements = {
        e.id: e.system_of_record_application.archimate_element_id
        for e in entities
        if e.system_of_record_application is not None
        and e.system_of_record_application.archimate_element_id
    }
    owners = _resolve_owners_batch(list(sor_elements.values()), org_id)

    uses = {}
    for rel in rels:
        app_element = apps_by_element.get(rel.source_id)
        if app_element is None:
            continue
        comp = components.get(app_element.id)
        uses.setdefault(rel.target_id, []).append({
            "name": comp.name if comp else app_element.name,
            "application_id": comp.id if comp else None,
            "access": _access_words(rel),
        })

    rows = []
    personal = {}
    for entity in entities:
        applications = sorted(uses.get(entity.archimate_element_id, []), key=lambda a: a["name"].lower())
        owner, _reason = owners.get(sor_elements.get(entity.id), (None, None))
        domain = entity.domain
        rows.append({
            "id": entity.id,
            "name": entity.name,
            "domain": domain.name if domain else None,
            "element_id": entity.archimate_element_id,
            "personal_data": bool(entity.contains_pii),
            "classification": entity.data_classification,
            "system_of_record": (
                entity.system_of_record_application.name if entity.system_of_record_application else None
            ),
            "owner": owner["name"] if owner else None,
            "steward": ((domain.data_steward or "").strip() or None) if domain else None,
            "applications": applications,
            "copies": copies.get(entity.archimate_element_id, 0),
        })
        if entity.contains_pii:
            for app in applications:
                entry = personal.setdefault(app["name"], {
                    "name": app["name"], "application_id": app["application_id"], "entities": [],
                })
                entry["entities"].append(entity.name)

    return {
        "entity_count": len(rows),
        "entities": rows,
        "personal_data_applications": sorted(personal.values(), key=lambda a: a["name"].lower()),
    }


def default_flow_entity(overview):
    """The entity whose flow the data architecture page opens on: of the
    personal-data entities something writes, the one with the most to show
    (applications that use it, and its recorded copies, which lead on to more
    hops); else the first entity that is used."""
    used = [row for row in overview["entities"] if row["applications"] and row["element_id"]]
    written = [
        row for row in used
        if row["personal_data"] and any(
            set(app["access"]) & {"creates", "updates", "deletes", "writes"} for app in row["applications"]
        )
    ]
    if written:
        # max keeps the first of equals, so the order of the list breaks ties.
        return max(written, key=lambda row: len(row["applications"]) + 2 * row["copies"])
    return used[0] if used else None


def flow_cascade(entity_row) -> Dict:
    """The data-flow answer for one entity row of ``data_flow_overview``, as the
    answer cascade draws it: one node per hop with its owner and its source.

    Computed by the answer service's own walk and envelope (the same rows,
    truth class and citations the assistant's ask_data_flow stores), without
    storing a new answer on every page view.
    """
    from app.modules.intelligence.services.answer_envelope import KIND_DATA_FLOW, build_envelope
    from app.modules.intelligence.services.query_service import IntelligenceQueryService, _words

    params = {"element_id": entity_row["element_id"], "max_depth": 4}
    envelope = build_envelope(
        KIND_DATA_FLOW, params, IntelligenceQueryService.data_flow(entity_row["element_id"], max_depth=4)
    )
    nodes = []
    for index, row in enumerate(envelope["rows"]):
        relation = row["relation"]
        provenance = relation.get("provenance") or {}
        name = (envelope["elements"].get(str(row["element_id"])) or {}).get("name") or "Unnamed"
        owners = [o["name"] for o in row.get("owners") or [] if o.get("name")]
        if not owners and row.get("owner"):
            owners = [row["owner"]["name"]]
        if owners:
            accountable = ", ".join(owners)
        elif row.get("steward"):
            accountable = f"Steward: {row['steward']}"
        else:
            accountable = "No owner recorded"
        if row["hop"] == "copied to":
            label = f"Copied to {name}"
        else:
            label = f"{name} {_words(row['access'])} it"
        recorded = (provenance.get("recorded_at") or "")[:10]
        nodes.append({
            "key": f"hop-{index}",
            "name": label,
            "depth": row["level"],
            "derived": relation["kind"] == "derived",
            "owner": accountable,
            "sourceName": provenance.get("text") or "Recorded in the model",
            "sourceTimestamp": f"Recorded {recorded}" if recorded else "",
            "kindLabel": "Worked out from the model",
            "hop": row["hop"],
        })
    summary = envelope["summary"]
    return {
        "entity": entity_row,
        "nodes": nodes,
        "truth_class": envelope["truth_class"],
        "recorded": summary["explicit_count"],
        "worked_out": summary["derived_count"],
        "citations": len(envelope["citations"]),
    }
