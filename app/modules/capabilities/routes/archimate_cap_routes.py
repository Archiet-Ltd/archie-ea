"""
Capability Map — ArchiMate elements, APQC requirements & mappings.

Extracted from app/routes/capability_map_routes.py (lines 5319-6093).

Routes (8):
    - api_archimate_elements()                                    GET "/api/archimate/elements"
    - api_create_archimate_element()                              POST "/api/archimate/elements"
    - api_generate_apqc_requirements(app_id)                      POST "/api/applications/<int:app_id>/apqc-requirements"
    - api_application_archimate_summary(app_id)                   GET "/api/applications/<int:app_id>/archimate-summary"
    - api_save_apqc_mappings()                                    POST "/api/save-apqc-mappings"
    - api_save_archimate_mappings()                               POST "/api/save-archimate-mappings"
    - api_capability_archimate_mappings(capability_id)            GET "/api/capabilities/<capability_id>/archimate-mappings"
    - api_delete_archimate_mapping(mapping_id)                    DELETE "/api/archimate-mappings/<int:mapping_id>"
"""

from app.services.archimate_backbone import sync_archimate_element
from flask import current_app, jsonify, request
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.decorators import audit_log

from . import capability_map
from app.utils.pagination import safe_int_arg


# ============================================================================
# ArchiMate Element Management Endpoints
# ============================================================================


@capability_map.route("/api/archimate/elements", methods=["GET"])
@login_required
def api_archimate_elements():
    """
    Get ArchiMate elements filtered by layer and type.

    Query parameters:
        - layer: business, application, technology, implementation
        - element_type: specific element type (optional)
        - application_id: filter by application (optional)

    Returns:
        JSON array of ArchiMate elements with their properties
    """
    try:
        from app.models.archimate_core import ArchiMateElement

        layer = request.args.get("layer", "all")
        element_type = request.args.get("element_type")

        query = ArchiMateElement.query
        if layer != "all":
            query = query.filter(ArchiMateElement.layer.ilike(layer))
        if element_type:
            query = query.filter(ArchiMateElement.type == element_type)

        results = query.order_by(ArchiMateElement.layer, ArchiMateElement.name).limit(200).all()
        elements = [
            {
                "id": str(el.id),
                "name": el.name,
                "type": el.type or "",
                "layer": el.layer or "",
                "description": el.description or "",
            }
            for el in results
        ]

        return jsonify(
            {"success": True, "elements": elements, "count": len(elements), "layer": layer}
        )

    except Exception as e:
        current_app.logger.error(f"Error fetching ArchiMate elements: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/archimate/elements", methods=["POST"])
@login_required
@audit_log("create_archimate_element")
def api_create_archimate_element():
    """
    Create a new ArchiMate element.

    Request body:
    {
        "name": "Customer Service API",
        "type": "ApplicationInterface",
        "layer": "application",
        "description": "REST API for customer service operations",
        "application_id": 123,
        "properties": {
            "interface_type": "REST",
            "protocol": "HTTPS",
            ...
        }
    }
    """
    try:
        data = request.get_json()

        if not data or not data.get("name") or not data.get("type"):
            return jsonify({"success": False, "error": "Missing required fields: name, type"}), 400

        element_type = data.get("type")
        layer = data.get("layer")
        application_id = data.get("application_id")
        properties = data.get("properties", {})

        # Import appropriate model based on type
        element = None

        if element_type == "BusinessActor":
            from app.models.business_layer import BusinessActor

            element = BusinessActor(
                name=data["name"],
                description=data.get("description"),
                application_component_id=application_id,
                actor_type=properties.get("actor_type"),
                location=properties.get("location"),
            )
        elif element_type == "BusinessRole":
            from app.models.business_layer import BusinessRole

            element = BusinessRole(
                name=data["name"],
                description=data.get("description"),
                application_component_id=application_id,
                role_type=properties.get("role_type"),
            )
        elif element_type == "ApplicationInterface":
            from app.models.application_layer import ApplicationInterface

            element = ApplicationInterface(
                name=data["name"],
                description=data.get("description"),
                application_component_id=application_id,
                interface_type=properties.get("interface_type"),
                protocol=properties.get("protocol"),
            )
        elif element_type == "ApplicationEvent":
            from app.models.application_layer import ApplicationEvent

            element = ApplicationEvent(
                name=data["name"],
                description=data.get("description"),
                application_component_id=application_id,
                event_type=properties.get("event_type"),
            )
        elif element_type == "Node":
            from app.models.technology_layer import Node

            element = Node(
                name=data["name"],
                description=data.get("description"),
                application_component_id=application_id,
                node_type=properties.get("node_type"),
            )
        elif element_type == "SystemSoftware":
            from app.models.technology_layer import SystemSoftware

            element = SystemSoftware(
                name=data["name"],
                description=data.get("description"),
                application_component_id=application_id,
                software_type=properties.get("software_type"),
                version=properties.get("version"),
            )
        elif element_type == "WorkPackage":
            from app.models.implementation_migration import WorkPackage

            element = WorkPackage(
                name=data["name"],
                description=data.get("description"),
                application_component_id=application_id,
                status=properties.get("status", "planned"),
                priority=properties.get("priority", "medium"),
            )
        elif element_type == "ImplementationEvent":
            from app.models.implementation_migration import ImplementationEvent

            element = ImplementationEvent(
                name=data["name"],
                description=data.get("description"),
                application_component_id=application_id,
                event_type=properties.get("event_type"),
            )
        else:
            return (
                jsonify({"success": False, "error": f"Unsupported element type: {element_type}"}),
                400,
            )

        db.session.add(element)
        sync_archimate_element(element)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "element": {
                    "id": str(element.id),
                    "name": element.name,
                    "type": element_type,
                    "layer": layer,
                },
                "message": f"{element_type} created successfully",
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating ArchiMate element: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# APQC Requirements Generation Endpoints
# ============================================================================


@capability_map.route("/api/applications/<int:app_id>/apqc-requirements", methods=["POST"])
@login_required
@audit_log("generate_apqc_requirements")
def api_generate_apqc_requirements(app_id):
    """
    Generate solution requirements from APQC process mappings.

    Request body:
    {
        "apqc_process_ids": [123, 456, 789]
    }

    Returns:
        List of generated requirements with priorities and effort estimates
    """
    try:
        from app.models.apqc_process import APQCProcess
        from app.services.application_capability_mapper import ApplicationCapabilityMapperService

        data = request.get_json()
        apqc_process_ids = data.get("apqc_process_ids", [])

        if not apqc_process_ids:
            return jsonify({"success": False, "error": "No APQC process IDs provided"}), 400

        # Fetch APQC processes
        apqc_processes = APQCProcess.query.filter(APQCProcess.id.in_(apqc_process_ids)).all()

        if not apqc_processes:
            return jsonify({"success": False, "error": "No APQC processes found"}), 404

        # Generate requirements
        requirements = ApplicationCapabilityMapperService.generate_solution_requirements_from_apqc(
            apqc_mappings=apqc_processes, application_id=app_id
        )

        return jsonify(
            {
                "success": True,
                "requirements": requirements,
                "count": len(requirements),
                "message": f"Generated {len(requirements)} requirements from {len(apqc_processes)} APQC processes",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error generating APQC requirements: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/applications/<int:app_id>/archimate-summary", methods=["GET"])
@login_required
def api_application_archimate_summary(app_id):
    """
    Get summary of ArchiMate elements for an application across all layers.

    Returns:
        {
            "business_layer": {"actors": 5, "roles": 3, "services": 8},
            "application_layer": {"interfaces": 12, "events": 6},
            "technology_layer": {"nodes": 4, "software": 7},
            "implementation_layer": {"work_packages": 15, "events": 8}
        }
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.utils.route_guards import require_entity

    require_entity(ApplicationComponent, app_id, description="Application not found")

    try:
        from app.models.application_layer import ApplicationEvent, ApplicationInterface
        from app.models.business_layer import BusinessActor, BusinessRole, BusinessService
        from app.models.implementation_migration import ImplementationEvent, WorkPackage
        from app.models.technology_layer import Node, SystemSoftware

        # Helper function to count entities. Models with no
        # application_component_id genuinely have 0 elements attached to an
        # application; a *query failure* does not, so it is not caught here.
        # Swallowing it returned 0 for that layer and then summed it into
        # total_elements, publishing an undercount as a measured figure. The
        # route handler below logs and returns an explicit error instead.
        def safe_count(model, app_id):
            if hasattr(model, "application_component_id"):  # model-safety-ok: polymorphic - checking multiple model classes for column existence
                return model.query.filter_by(application_component_id=app_id).count()
            return 0

        summary = {
            "business_layer": {
                "actors": safe_count(BusinessActor, app_id),
                "roles": safe_count(BusinessRole, app_id),
                "services": safe_count(BusinessService, app_id),
            },
            "application_layer": {
                "interfaces": safe_count(ApplicationInterface, app_id),
                "events": safe_count(ApplicationEvent, app_id),
            },
            "technology_layer": {
                "nodes": safe_count(Node, app_id),
                "software": safe_count(SystemSoftware, app_id),
            },
            "implementation_layer": {
                "work_packages": safe_count(WorkPackage, app_id),
                "events": safe_count(ImplementationEvent, app_id),
            },
        }

        # Calculate totals
        summary["total_elements"] = sum(
            sum(layer.values()) for layer in summary.values() if isinstance(layer, dict)
        )

        return jsonify({"success": True, "application_id": str(app_id), "summary": summary})

    except Exception as e:
        current_app.logger.error(f"Error fetching ArchiMate summary: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# APQC and ArchiMate Mapping Endpoints
# ============================================================================


@capability_map.route("/api/save-apqc-mappings", methods=["POST"])
@login_required
@audit_log("save_apqc_mappings")
def api_save_apqc_mappings():
    """
    Save capability to APQC process mappings.

    Request body:
    {
        "mappings": [
            {
                "capability_id": 123,
                "apqc_process_id": 456,
                "relationship_type": "enables",
                "impact_level": "high",
                "relationship_strength": 4
            }
        ]
    }
    """
    try:
        from app.models.apqc_process import CapabilityProcessMapping
        from app.services.apqc_mapping_tenant_fence import (
            capability_owned_by_caller,
            fenced_capability_mappings_query,
        )

        data = request.get_json()
        if not data or "mappings" not in data:
            return jsonify({"success": False, "error": "Missing mappings data"}), 400

        mappings_data = data["mappings"]
        created = 0
        updated = 0

        # Batch-prefetch existing mappings to avoid N+1 queries.
        # CapabilityProcessMapping has no organization_id of its own; a
        # caller could read, update or create mappings against another
        # organisation's capability_id (pr310-v1 review, DEFECT-1).
        _apqc_req_pairs = set()
        for md in mappings_data:
            c_id = md.get("capability_id")
            p_id = md.get("apqc_process_id")
            if c_id and p_id and capability_owned_by_caller(c_id):
                _apqc_req_pairs.add((c_id, p_id))

        _existing_apqc_mappings = {}
        if _apqc_req_pairs:
            _apqc_cap_ids = [p[0] for p in _apqc_req_pairs]
            _apqc_proc_ids = [p[1] for p in _apqc_req_pairs]
            _existing_apqc_rows = fenced_capability_mappings_query().filter(
                CapabilityProcessMapping.capability_id.in_(_apqc_cap_ids),
                CapabilityProcessMapping.apqc_process_id.in_(_apqc_proc_ids),
            ).all()
            for row in _existing_apqc_rows:
                _existing_apqc_mappings[(row.capability_id, row.apqc_process_id)] = row

        for mapping_data in mappings_data:
            capability_id = mapping_data.get("capability_id")
            apqc_process_id = mapping_data.get("apqc_process_id")

            if (
                not capability_id
                or not apqc_process_id
                or (capability_id, apqc_process_id) not in _apqc_req_pairs
            ):
                # Missing ids, or capability_id did not belong to the
                # caller's organisation and was excluded from the owned set
                # built above -- skip rather than guess.
                continue

            # Check if mapping already exists using prefetched data
            existing = _existing_apqc_mappings.get((capability_id, apqc_process_id))

            if existing:
                # Update existing mapping
                existing.relationship_type = mapping_data.get("relationship_type", "enables")
                existing.impact_level = mapping_data.get("impact_level", "medium")
                existing.relationship_strength = mapping_data.get("relationship_strength", 3)
                updated += 1
            else:
                # Create new mapping
                new_mapping = CapabilityProcessMapping(
                    capability_id=capability_id,
                    apqc_process_id=apqc_process_id,
                    relationship_type=mapping_data.get("relationship_type", "enables"),
                    impact_level=mapping_data.get("impact_level", "medium"),
                    relationship_strength=mapping_data.get("relationship_strength", 3),
                )
                db.session.add(new_mapping)
                created += 1

        db.session.commit()

        return jsonify(
            {"success": True, "created": created, "updated": updated, "total": created + updated}
        )

    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Database error saving APQC mappings: {e}")
        return jsonify({"success": False, "error": "Database error occurred"}), 500
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving APQC mappings: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/save-archimate-mappings", methods=["POST"])
@login_required
@audit_log("save_archimate_mappings")
def api_save_archimate_mappings():
    """
    Save capability to ArchiMate element mappings.

    Request body:
    {
        "mappings": [
            {
                "capability_id": 123,
                "archimate_element_id": 456,
                "relationship_type": "realization",
                "confidence_score": 0.8,
                "archimate_layer": "business",
                "archimate_element_type": "BusinessProcess"
            }
        ]
    }
    """
    try:
        from app.models.capability_archimate_mapping import CapabilityArchiMateClassification

        data = request.get_json()
        if not data or "mappings" not in data:
            return jsonify({"success": False, "error": "Missing mappings data"}), 400

        mappings_data = data["mappings"]
        created = 0
        updated = 0

        # Batch-prefetch existing mappings to avoid N+1 queries
        _archimate_req_pairs = set()
        for md in mappings_data:
            c_id = md.get("capability_id")
            e_id = md.get("archimate_element_id")
            if c_id and e_id:
                _archimate_req_pairs.add((c_id, e_id))

        _existing_archimate_mappings = {}
        if _archimate_req_pairs:
            _archimate_cap_ids = [p[0] for p in _archimate_req_pairs]
            _archimate_elem_ids = [p[1] for p in _archimate_req_pairs]
            _existing_archimate_rows = CapabilityArchiMateClassification.query.filter(
                CapabilityArchiMateClassification.capability_id.in_(_archimate_cap_ids),
                CapabilityArchiMateClassification.archimate_element_id.in_(_archimate_elem_ids),
            ).all()
            for row in _existing_archimate_rows:
                _existing_archimate_mappings[(row.capability_id, row.archimate_element_id)] = row

        for mapping_data in mappings_data:
            capability_id = mapping_data.get("capability_id")
            archimate_element_id = mapping_data.get("archimate_element_id")

            if not capability_id or not archimate_element_id:
                continue

            # Check if mapping already exists using prefetched data
            existing = _existing_archimate_mappings.get((capability_id, archimate_element_id))

            if existing:
                # Update existing mapping
                existing.archimate_layer = mapping_data.get(
                    "archimate_layer", existing.archimate_layer
                )
                existing.archimate_element_type = mapping_data.get(
                    "archimate_element_type", existing.archimate_element_type
                )
                existing.confidence_score = mapping_data.get(
                    "confidence_score", existing.confidence_score
                )
                existing.classification_method = "manual"
                updated += 1
            else:
                # Create new mapping
                new_mapping = CapabilityArchiMateClassification(
                    capability_id=capability_id,
                    archimate_element_id=archimate_element_id,
                    archimate_layer=mapping_data.get("archimate_layer", "business"),
                    archimate_element_type=mapping_data.get("archimate_element_type", "Capability"),
                    confidence_score=mapping_data.get("confidence_score", 0.8),
                    classification_method="manual",
                )
                db.session.add(new_mapping)
                created += 1

        db.session.commit()

        return jsonify(
            {"success": True, "created": created, "updated": updated, "total": created + updated}
        )

    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Database error saving ArchiMate mappings: {e}")
        return jsonify({"success": False, "error": "Database error occurred"}), 500
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving ArchiMate mappings: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@capability_map.route("/api/capabilities/<capability_id>/archimate-mappings")
@login_required
def api_capability_archimate_mappings(capability_id):
    """
    Get ArchiMate classifications/mappings for a specific capability.

    Returns all CapabilityArchiMateClassification records linked to this capability,
    with resolved ArchiMateElement details where available.
    """
    try:
        from app.models.business_capabilities import BusinessCapability
        from app.models.capability_archimate_mapping import CapabilityArchiMateClassification
        from app.models.models import ArchiMateElement

        try:
            capability_id_int = int(str(capability_id).strip())
        except (ValueError, TypeError):
            return jsonify({"error": f"Invalid capability ID: {capability_id}"}), 400

        # Verify capability exists
        capability = BusinessCapability.query.get(capability_id_int)
        if not capability:
            return jsonify({"error": f"Capability not found: {capability_id}"}), 404

        # Get all classifications for this capability
        classifications = CapabilityArchiMateClassification.query.filter_by(
            capability_id=capability_id_int
        ).all()

        # Pre-fetch ArchiMate elements
        element_ids = {c.archimate_element_id for c in classifications if c.archimate_element_id}
        elements = (
            ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()
            if element_ids
            else []
        )
        elements_by_id = {e.id: e for e in elements}

        mappings_data = []
        for classification in classifications:
            element = elements_by_id.get(classification.archimate_element_id)
            mapping_dict = classification.to_dict()
            if element:
                mapping_dict["element"] = {
                    "id": element.id,
                    "name": element.name,
                    "type": element.type,
                    "layer": element.layer,
                    "description": element.description,
                }
            mappings_data.append(mapping_dict)

        # Also include direct archimate_element FK from BusinessCapability
        direct_element = None
        if capability.archimate_element_id:
            direct_el = ArchiMateElement.query.get(capability.archimate_element_id)
            if direct_el:
                direct_element = {
                    "id": direct_el.id,
                    "name": direct_el.name,
                    "type": direct_el.type,
                    "layer": direct_el.layer,
                }

        return jsonify(
            {
                "capability": {
                    "id": str(capability.id),
                    "name": capability.name,
                    "archimate_id": capability.archimate_id,
                    "archimate_element_id": capability.archimate_element_id,
                },
                "direct_element": direct_element,
                "classifications": mappings_data,
                "total": len(mappings_data),
            }
        )

    except Exception as e:
        current_app.logger.error(
            f"Error getting ArchiMate mappings for capability {capability_id}: {e}",
            exc_info=True,
        )
        return jsonify({"error": "An internal error occurred"}), 500


@capability_map.route("/api/archimate-mappings/<int:mapping_id>", methods=["DELETE"])
@login_required
@audit_log("delete_archimate_mapping")
def api_delete_archimate_mapping(mapping_id):
    """Delete a capability-ArchiMate classification mapping."""
    try:
        from app.models.capability_archimate_mapping import CapabilityArchiMateClassification

        mapping = CapabilityArchiMateClassification.query.get(mapping_id)
        if not mapping:
            return jsonify({"error": "Mapping not found"}), 404

        db.session.delete(mapping)
        db.session.commit()

        return jsonify({"success": True, "deleted_id": mapping_id})

    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Database error deleting ArchiMate mapping: {e}")
        return jsonify({"error": "Database error occurred"}), 500


# ============================================================================
# Relationship Health Metrics (BPP-005)
# ============================================================================


@capability_map.route("/api/archimate/relationship-health", methods=["GET"])
@login_required
def api_archimate_relationship_health():
    """Return ArchiMate relationship coverage metrics.

    Response JSON:
        total_elements: int
        total_relationships: int
        zero_relationship_count: int
        average_per_element: float
        by_layer: [{layer, element_count, relationship_count, avg}]
    """
    try:
        from sqlalchemy import func

        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

        # Total elements
        total_elements = db.session.query(func.count(ArchiMateElement.id)).scalar() or 0

        # Total relationships (legacy + inference)
        #
        # E2E-I: neither ArchiMateRelationship nor
        # ArchitectureInferenceRelationship carries an organization_id (no
        # TenantMixin), so a raw count() here summed every tenant's rows,
        # not this one's -- confirmed live 6 Sep 2026
        # (Archie-E2E-Workflow-Test-Report.md E2E-I): a from-empty tenant
        # with 1 element read "34 relationships", a figure close to another
        # tenant's real total. ArchiMateElement IS tenant-scoped (TenantMixin),
        # so joining through it and letting the ORM's automatic tenant filter
        # apply to that side of the join scopes the relationship count
        # correctly without a schema change. Retrofitting TenantMixin onto
        # the relationship tables themselves (a real column + backfill) is a
        # separate, bigger remediation -- this closes the reported leak now.
        from sqlalchemy import or_

        legacy_rels = (
            db.session.query(func.count(func.distinct(ArchiMateRelationship.id)))
            .join(ArchiMateElement, or_(
                ArchiMateRelationship.source_id == ArchiMateElement.id,
                ArchiMateRelationship.target_id == ArchiMateElement.id,
            ))
            .scalar() or 0
        )
        inference_rels = (
            db.session.query(func.count(func.distinct(ArchitectureInferenceRelationship.id)))
            .join(ArchiMateElement, or_(
                ArchitectureInferenceRelationship.source_id == ArchiMateElement.id,
                ArchitectureInferenceRelationship.target_id == ArchiMateElement.id,
            ))
            .scalar() or 0
        )
        total_relationships = legacy_rels + inference_rels

        # Elements with 0 relationships (union both tables)
        source_ids = set(
            r[0] for r in db.session.query(ArchiMateRelationship.source_id).distinct().all()
        )
        target_ids = set(
            r[0] for r in db.session.query(ArchiMateRelationship.target_id).distinct().all()
        )
        # Also include inference relationship participants
        inf_source_ids = set(
            r[0] for r in db.session.query(ArchitectureInferenceRelationship.source_id).distinct().all()
        )
        inf_target_ids = set(
            r[0] for r in db.session.query(ArchitectureInferenceRelationship.target_id).distinct().all()
        )
        # Intersect with REAL element ids — relationship rows can reference
        # ids outside the element set (orphaned/external refs), which made the
        # raw union larger than total_elements and produced a NEGATIVE
        # "unconnected" count in the UI.
        all_element_ids = set(
            r[0] for r in db.session.query(ArchiMateElement.id).all()
        )
        connected_ids = (
            source_ids | target_ids | inf_source_ids | inf_target_ids
        ) & all_element_ids
        zero_relationship_count = max(0, total_elements - len(connected_ids))

        # Average relationships per element
        average = round(total_relationships / total_elements, 2) if total_elements > 0 else 0.0

        # Per-layer breakdown
        # Count elements per layer
        layer_element_counts = dict(
            db.session.query(ArchiMateElement.layer, func.count(ArchiMateElement.id))
            .group_by(ArchiMateElement.layer).all()
        )

        # Count relationships per layer (legacy via source element's layer)
        layer_rel_counts = dict(
            db.session.query(ArchiMateElement.layer, func.count(ArchiMateRelationship.id))
            .join(ArchiMateElement, ArchiMateRelationship.source_id == ArchiMateElement.id)
            .group_by(ArchiMateElement.layer).all()
        )
        # Add inference relationships per layer
        inf_layer_rels = dict(
            db.session.query(ArchiMateElement.layer, func.count(ArchitectureInferenceRelationship.id))
            .join(ArchiMateElement, ArchitectureInferenceRelationship.source_id == ArchiMateElement.id)
            .group_by(ArchiMateElement.layer).all()
        )
        for layer_name, count in inf_layer_rels.items():
            layer_rel_counts[layer_name] = layer_rel_counts.get(layer_name, 0) + count

        by_layer = []
        for layer_name in sorted(set(list(layer_element_counts.keys()) + list(layer_rel_counts.keys()))):
            el_count = layer_element_counts.get(layer_name, 0)
            rel_count = layer_rel_counts.get(layer_name, 0)
            by_layer.append({
                "layer": layer_name,
                "element_count": el_count,
                "relationship_count": rel_count,
                "avg": round(rel_count / el_count, 2) if el_count > 0 else 0.0,
            })

        return jsonify({
            "total_elements": total_elements,
            "total_relationships": total_relationships,
            "zero_relationship_count": zero_relationship_count,
            "average_per_element": average,
            "by_layer": by_layer,
        })

    except SQLAlchemyError as e:
        current_app.logger.error(f"Error computing relationship health: {e}")
        return jsonify({"error": "Database error occurred"}), 500


# ============================================================================
# Relationship Suggestion Review Queue (BPP-007)
# ============================================================================


@capability_map.route("/api/archimate/relationship-suggestions", methods=["GET"])
@login_required
def api_relationship_suggestions():
    """List relationship suggestions with optional filters.

    Query parameters:
        status: pending|accepted|rejected (default: pending)
        min_confidence: float 0.0-1.0 (default: 0.3)
        limit: int (default: 50, max 100)
        sort: confidence|layer|type (default: confidence)
    """
    try:
        from app.models.archimate_core import ArchiMateElement, RelationshipSuggestion

        status = request.args.get("status", "pending")
        min_confidence = float(request.args.get("min_confidence", 0.3))
        limit = min(safe_int_arg('limit', 50, minimum=1, maximum=500), 100)

        query = RelationshipSuggestion.query.filter_by(status=status)
        if status == "pending":
            query = query.filter(RelationshipSuggestion.confidence >= min_confidence)
        query = query.order_by(RelationshipSuggestion.confidence.desc())
        suggestions = query.limit(limit).all()

        # Build response with element details
        element_ids = set()
        for s in suggestions:
            element_ids.add(s.source_element_id)
            element_ids.add(s.target_element_id)

        elements_by_id = {}
        if element_ids:
            elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(element_ids)
            ).all()
            elements_by_id = {e.id: e for e in elements}

        result = []
        for s in suggestions:
            src = elements_by_id.get(s.source_element_id)
            tgt = elements_by_id.get(s.target_element_id)
            result.append({
                "id": s.id,
                "source_element": {
                    "id": s.source_element_id,
                    "name": src.name if src else "Unknown",
                    "type": src.type if src else None,
                    "layer": src.layer if src else None,
                } if src else {"id": s.source_element_id},
                "target_element": {
                    "id": s.target_element_id,
                    "name": tgt.name if tgt else "Unknown",
                    "type": tgt.type if tgt else None,
                    "layer": tgt.layer if tgt else None,
                } if tgt else {"id": s.target_element_id},
                "relationship_type": s.relationship_type,
                "confidence": s.confidence,
                "source_method": s.source_method,
                "status": s.status,
                "reason": s.reason,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            })

        return jsonify({"suggestions": result, "total": len(result)})

    except (SQLAlchemyError, ValueError) as e:
        current_app.logger.error(f"Error listing suggestions: {e}")
        return jsonify({"error": str(e)}), 500


@capability_map.route("/archimate/review-queue")
@login_required
def archimate_review_queue():
    """Render the relationship suggestion review queue page."""
    from flask import render_template
    return render_template("archimate/review_queue.html")

@capability_map.route(
    "/api/archimate/relationship-suggestions/<int:suggestion_id>/accept",
    methods=["POST"],
)
@login_required
def api_accept_suggestion(suggestion_id):
    """Accept a relationship suggestion — creates an ArchiMateRelationship."""
    try:
        from flask_login import current_user

        from app.modules.architecture.services.archimate_relationship_service import (
            ArchiMateRelationshipService,
        )

        relationship = ArchiMateRelationshipService.accept_suggestion(
            suggestion_id, current_user.id
        )
        return jsonify({
            "success": True,
            "relationship": {
                "id": relationship.id,
                "source_id": relationship.source_id,
                "target_id": relationship.target_id,
                "type": relationship.type,
            },
        })

    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Error accepting suggestion: {e}")
        return jsonify({"error": "Database error occurred"}), 500


@capability_map.route(
    "/api/archimate/relationship-suggestions/<int:suggestion_id>/reject",
    methods=["POST"],
)
@login_required
def api_reject_suggestion(suggestion_id):
    """Reject a relationship suggestion without creating a relationship."""
    try:
        from flask_login import current_user

        from app.modules.architecture.services.archimate_relationship_service import (
            ArchiMateRelationshipService,
        )

        ArchiMateRelationshipService.reject_suggestion(
            suggestion_id, current_user.id
        )
        return jsonify({"success": True})

    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Error rejecting suggestion: {e}")
        return jsonify({"error": "Database error occurred"}), 500


@capability_map.route(
    "/api/archimate/relationship-suggestions/bulk-accept",
    methods=["POST"],
)
@login_required
def api_bulk_accept_suggestions():
    """Bulk-accept all pending suggestions above a confidence threshold."""
    try:
        from flask_login import current_user

        from app.modules.architecture.services.archimate_relationship_service import (
            ArchiMateRelationshipService,
        )

        data = request.get_json(silent=True) or {}
        min_confidence = float(data.get("min_confidence", 0.8))

        accepted_count = ArchiMateRelationshipService.bulk_accept(
            min_confidence, current_user.id
        )
        return jsonify({
            "success": True,
            "accepted_count": accepted_count,
        })

    except (SQLAlchemyError, ValueError) as e:
        db.session.rollback()
        current_app.logger.error(f"Error in bulk accept: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Capability → Application Sankey Flow (migrated from legacy capability_map_routes.py)
# ============================================================================


@capability_map.route("/api/flow-sankey")
@login_required
def api_flow_sankey():
    """Capability → Application flow as D3 Sankey data.

    Returns nodes across three columns:
      Col 0 — Business Domain (L1 capability grouping, by business_domain field)
      Col 1 — Capability (L2/L3 capability)
      Col 2 — Application

    Links: Domain → Capability (via parent hierarchy) and Capability → Application
    (via application_capability_mapping).
    """
    from sqlalchemy import text as sa_text

    try:
        caps_sql = sa_text("""
            SELECT
                c.id,
                c.name,
                c.level,
                c.business_domain,
                c.parent_capability_id
            FROM business_capability c
            WHERE c.level IN (1, 2, 3)
            ORDER BY c.level, c.name
        """)
        caps_rows = db.session.execute(caps_sql).fetchall()

        mappings_sql = sa_text("""
            SELECT
                acm.business_capability_id,
                ac.id   AS app_id,
                ac.name AS app_name
            FROM application_capability_mapping acm
            JOIN application_components ac ON ac.id = acm.application_component_id
            WHERE ac.name IS NOT NULL
        """)
        mapping_rows = db.session.execute(mappings_sql).fetchall()

        if not mapping_rows:
            return jsonify({"nodes": [], "links": [], "has_code": False,
                            "synthesized": False, "column_labels": []})

        cap_by_id = {r.id: r for r in caps_rows}
        mapped_cap_ids = {r.business_capability_id for r in mapping_rows}

        def get_l1_ancestor(cap_id):
            visited = set()
            while cap_id and cap_id not in visited:
                visited.add(cap_id)
                cap = cap_by_id.get(cap_id)
                if cap is None:
                    return None
                if cap.level == 1:
                    return cap_id
                cap_id = cap.parent_capability_id
            return None

        nodes = []
        node_index = {}

        domain_ids = set()
        for cap_id in mapped_cap_ids:
            anc = get_l1_ancestor(cap_id)
            if anc:
                domain_ids.add(anc)

        for cap in caps_rows:
            if cap.level == 1 and cap.id in domain_ids:
                key = f"domain_{cap.id}"
                node_index[key] = len(nodes)
                nodes.append({
                    "id": key,
                    "name": cap.name,
                    "type": "BusinessDomain",
                    "layer": "strategy",
                    "column": 0,
                    "has_spec": False,
                    "has_code": False,
                })

        for cap_id in sorted(mapped_cap_ids):
            cap = cap_by_id.get(cap_id)
            if cap is None or cap.level == 1:
                continue
            key = f"cap_{cap_id}"
            if key in node_index:
                continue
            node_index[key] = len(nodes)
            nodes.append({
                "id": key,
                "name": cap.name,
                "type": "BusinessCapability",
                "layer": "business",
                "column": 1,
                "has_spec": False,
                "has_code": False,
            })

        app_seen = {}
        for row in mapping_rows:
            key = f"app_{row.app_id}"
            if key not in node_index:
                node_index[key] = len(nodes)
                app_seen[key] = True
                nodes.append({
                    "id": key,
                    "name": row.app_name,
                    "type": "ApplicationComponent",
                    "layer": "application",
                    "column": 2,
                    "has_spec": False,
                    "has_code": False,
                })

        links = []
        link_seen = set()

        for cap_id in mapped_cap_ids:
            cap = cap_by_id.get(cap_id)
            if cap is None or cap.level == 1:
                continue
            anc_id = get_l1_ancestor(cap_id)
            if anc_id is None:
                continue
            src_key = f"domain_{anc_id}"
            tgt_key = f"cap_{cap_id}"
            if src_key not in node_index or tgt_key not in node_index:
                continue
            link_key = (node_index[src_key], node_index[tgt_key])
            if link_key not in link_seen:
                link_seen.add(link_key)
                links.append({"source": node_index[src_key], "target": node_index[tgt_key], "value": 1})

        for row in mapping_rows:
            cap = cap_by_id.get(row.business_capability_id)
            if cap is None or cap.level == 1:
                continue
            src_key = f"cap_{row.business_capability_id}"
            tgt_key = f"app_{row.app_id}"
            if src_key not in node_index or tgt_key not in node_index:
                continue
            link_key = (node_index[src_key], node_index[tgt_key])
            if link_key not in link_seen:
                link_seen.add(link_key)
                links.append({"source": node_index[src_key], "target": node_index[tgt_key], "value": 1})

        return jsonify({
            "nodes": nodes,
            "links": links,
            "has_code": False,
            "synthesized": False,
            "column_labels": [
                {"name": "Business Domain"},
                {"name": "Capability"},
                {"name": "Application"},
            ],
        })

    except Exception as e:
        current_app.logger.exception("capability flow sankey error")
        return jsonify({"error": str(e)}), 500
