"""
APQC API Routes - Core Process Framework API

Provides API endpoints for the APQC process browser and mapping interface.

Migrated from: app/api/apqc_routes.py
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app.decorators import audit_log

from app.services.apqc_hierarchy_service import APQCHierarchyService
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

# Create blueprint
apqc_bp = Blueprint("apqc", __name__, url_prefix="/api/apqc")


@apqc_bp.route("/tree", methods=["GET"])
@login_required
def get_apqc_tree():
    """Get complete APQC hierarchy tree structure."""
    try:
        service = APQCHierarchyService()
        industry = request.args.get("industry")
        level = request.args.get("level", type=int)

        # Get tree structure (level filtering not supported at tree level)
        tree = service.get_tree_structure(industry=industry)

        # Count processes from tree
        def count_tree_nodes(t):
            if not t:
                return 0
            count = 0
            if isinstance(t, dict):
                for v in t.values():
                    if isinstance(v, dict):
                        count += 1
                        if "children" in v:
                            count += count_tree_nodes(v["children"])
            elif isinstance(t, list):
                for item in t:
                    count += count_tree_nodes(item)
            return count

        total_processes = count_tree_nodes(tree)

        return jsonify(
            {
                "success": True,
                "tree": tree,
                "total_processes": total_processes,
                "industry": industry,
                "level": level,
            }
        )

    except Exception as e:
        logger.error(f"Error getting APQC tree: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@apqc_bp.route("/search", methods=["GET"])
@login_required
def search_apqc_processes():
    """Search APQC processes with intelligent matching."""
    try:
        query = request.args.get("q", "").strip()
        level = request.args.get("level", type=int)
        industry = request.args.get("industry")
        limit = safe_int_arg('limit', 50, minimum=1, maximum=500)

        if not query:
            return jsonify({"success": False, "error": "Search query is required"}), 400

        service = APQCHierarchyService()
        matches = service.search_processes(
            query=query, level=level, industry=industry, limit=limit
        )

        return jsonify(
            {
                "success": True,
                "matches": [match.to_dict() for match in matches],
                "total": len(matches),
                "query": query,
                "level": level,
                "industry": industry,
            }
        )

    except Exception as e:
        logger.error(f"Error searching APQC processes: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@apqc_bp.route("/process/<int:process_id>", methods=["GET"])
@login_required
def get_process_details(process_id):
    """Get detailed information about a specific APQC process."""
    try:
        service = APQCHierarchyService()
        process = service.get_process_details(process_id)

        if not process:
            return jsonify({"success": False, "error": "Process not found"}), 404

        # Get hierarchy path (expects process_code string, not int ID)
        hierarchy_path = service.get_hierarchy_path(process.process_code)

        # Get auto-link parents
        auto_link_parents = service.get_auto_link_parents(process_id)

        # Get child processes
        child_processes = service.get_child_processes(process_id)

        return jsonify(
            {
                "success": True,
                "process": process.to_dict(),
                "hierarchy_path": hierarchy_path,
                "auto_link_parents": [parent.to_dict() for parent in auto_link_parents],
                "child_processes": [child.to_dict() for child in child_processes],
            }
        )

    except Exception as e:
        logger.error(f"Error getting process details for {process_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@apqc_bp.route("/auto-link", methods=["POST"])
@login_required
@audit_log("apqc_auto_link")
def auto_link_processes():
    """Auto-link parent processes for a given process."""
    try:
        data = request.get_json()
        if not data or "process_id" not in data:
            return jsonify({"success": False, "error": "process_id is required"}), 400

        process_id = data["process_id"]
        service = APQCHierarchyService()

        # Get auto-link parents
        auto_link_parents = service.get_auto_link_parents(process_id)

        # Create links (this would integrate with application mapping)
        # For now, just return the suggested links
        return jsonify(
            {
                "success": True,
                "process_id": process_id,
                "auto_link_parents": [parent.to_dict() for parent in auto_link_parents],
                "message": f"Found {len(auto_link_parents)} parent processes for auto-linking",
            }
        )

    except Exception as e:
        logger.error(f"Error auto-linking processes: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@apqc_bp.route("/hierarchy", methods=["GET"])
@login_required
def get_hierarchy():
    """Get APQC hierarchy for navigation."""
    try:
        service = APQCHierarchyService()
        industry = request.args.get("industry")

        # Get hierarchy levels
        hierarchy = service.get_tree_structure(industry=industry)

        return jsonify({"success": True, "hierarchy": hierarchy, "industry": industry})

    except Exception as e:
        logger.error(f"Error getting APQC hierarchy: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@apqc_bp.route("/statistics", methods=["GET"])
@login_required
def get_apqc_statistics():
    """Get APQC process statistics."""
    try:
        service = APQCHierarchyService()
        industry = request.args.get("industry")

        # Get statistics
        stats = service.get_statistics(industry=industry)

        return jsonify({"success": True, "statistics": stats, "industry": industry})

    except Exception as e:
        logger.error(f"Error getting APQC statistics: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@apqc_bp.route("/variants", methods=["GET"])
@login_required
def get_industry_variants():
    """Get available industry variants."""
    try:
        service = APQCHierarchyService()
        variants = service.get_industry_variants()

        return jsonify({"success": True, "variants": variants})

    except Exception as e:
        logger.error(f"Error getting industry variants: {e}")
        return (
            jsonify({"success": False, "error": "An internal error occurred"}),
            500,
        )


@apqc_bp.route("/process/<int:process_id>/applications", methods=["GET"])
@login_required
def get_process_applications(process_id):
    """Get all applications with mapping status for an APQC process."""
    try:
        from app.models.apqc_process import APQCProcess, ProcessApplicationMapping
        from app.models.application_layer import ApplicationComponent
        from app.services.apqc_mapping_tenant_fence import (
            fenced_application_mappings_query,
        )

        process = APQCProcess.query.get(process_id)
        if not process:
            return jsonify({"error": f"Process not found: {process_id}"}), 404

        # ProcessApplicationMapping has no organization_id of its own;
        # unfenced, mapped_count below summed every organisation's mappings
        # for this process, leaking an aggregate cross-organisation figure
        # even though the per-row display only ever matches the caller's own
        # (already-fenced) applications (pr303-v2 review, DEFECT D3).
        existing_mappings = fenced_application_mappings_query().filter(
            ProcessApplicationMapping.apqc_process_id == process_id
        ).all()
        mapped_app_ids = {m.application_id for m in existing_mappings}
        mapping_by_app = {m.application_id: m for m in existing_mappings}

        # Get all applications
        all_apps = ApplicationComponent.query.order_by(ApplicationComponent.name).all()

        applications = []
        for app in all_apps:
            mapping = mapping_by_app.get(app.id)
            applications.append(
                {
                    "id": str(app.id),
                    "name": app.name,
                    "type": app.component_type or "Unknown",
                    "description": app.description or "",
                    "domain": app.business_domain or "Not specified",
                    "owner": app.business_owner or "Not specified",
                    "is_mapped": app.id in mapped_app_ids,
                    "mapping_id": str(mapping.id) if mapping else None,
                    "support_level": mapping.support_level if mapping else "partial",
                    "automation_level": mapping.automation_level if mapping else 1,
                    "process_coverage": mapping.process_coverage if mapping else 50,
                    "application_role": mapping.application_role
                    if mapping
                    else "supporting",
                }
            )

        return jsonify(
            {
                "success": True,
                "process": {
                    "id": process.id,
                    "name": process.process_name,
                    "code": process.process_code,
                },
                "applications": applications,
                "total": len(applications),
                "mapped_count": len(mapped_app_ids),
            }
        )

    except Exception as e:
        logger.error(f"Error getting process applications: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@apqc_bp.route("/process-mappings", methods=["GET"])
@login_required
def get_process_mappings():
    """
    List CapabilityProcessMapping or ProcessApplicationMapping records.

    Query params:
    - capability_id: filter by capability
    - apqc_process_id: filter by APQC process
    - type: 'capability' (default) or 'application'
    """
    try:
        from app.models.apqc_process import (
            CapabilityProcessMapping,
            ProcessApplicationMapping,
        )
        from app.services.apqc_mapping_tenant_fence import (
            fenced_application_mappings_query,
            fenced_capability_mappings_query,
        )

        mapping_type = request.args.get("type", "capability")
        capability_id = request.args.get("capability_id", type=int)
        apqc_process_id = request.args.get("apqc_process_id", type=int)

        if mapping_type == "application":
            # ProcessApplicationMapping has no organization_id of its own --
            # unfenced, this returned every organisation's application
            # mappings (pr303-v2 review, DEFECT D2).
            query = fenced_application_mappings_query()
            if apqc_process_id:
                query = query.filter(
                    ProcessApplicationMapping.apqc_process_id == apqc_process_id
                )
            mappings = query.all()
            return jsonify(
                {
                    "success": True,
                    "mappings": [m.to_dict() for m in mappings],
                    "total": len(mappings),
                }
            )
        else:
            # Same fence for CapabilityProcessMapping.
            query = fenced_capability_mappings_query()
            if capability_id:
                query = query.filter(
                    CapabilityProcessMapping.capability_id == capability_id
                )
            if apqc_process_id:
                query = query.filter(
                    CapabilityProcessMapping.apqc_process_id == apqc_process_id
                )
            mappings = query.all()
            return jsonify(
                {
                    "success": True,
                    "mappings": [m.to_dict() for m in mappings],
                    "total": len(mappings),
                }
            )

    except Exception as e:
        logger.error(f"Error getting process mappings: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@apqc_bp.route("/process-mappings", methods=["POST"])
@login_required
@audit_log("apqc_mappings_save")
def save_process_mappings():
    """
    Save process mappings. Handles two payload formats:

    Format 1 (unified modal - application ↔ APQC process):
    {
        "process_id": 123,
        "applications": [{"application_id": "456", "mapping": {...}}],
        "context": "apqc"
    }

    Format 2 (capability ↔ APQC process):
    {
        "capability_id": 123,
        "apqc_process_id": 456,
        "relationship_type": "enables",
        "impact_level": "high"
    }
    """
    try:
        from app import db
        from app.models.apqc_process import (
            APQCProcess,
            CapabilityProcessMapping,
            ProcessApplicationMapping,
        )
        from app.services.apqc_mapping_tenant_fence import (
            application_mapping_in_caller_org,
            application_owned_by_caller,
            fenced_application_mappings_query,
        )

        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        created = 0
        updated = 0

        # Format 1: unified modal sends process_id + applications
        if "process_id" in data and "applications" in data:
            process_id = int(data["process_id"])
            process = APQCProcess.query.get(process_id)
            if not process:
                return jsonify({"error": f"APQC process not found: {process_id}"}), 404

            for app_data in data["applications"]:
                app_id = int(app_data.get("application_id", 0))
                if not app_id:
                    continue

                # ProcessApplicationMapping has no organization_id of its
                # own -- ownership is only reachable via application_id.
                # Neither the existing-mapping lookup nor a new mapping's
                # target application_id was fenced: an update (by
                # caller-supplied mapping_id) could mutate another
                # organisation's row, and a create could point a new
                # mapping at another organisation's application (pr303-v2
                # review, D1/D4 as originally found by PR 303's own first
                # round). Verify app_id belongs to the caller's organisation
                # before either; skip this item rather than fail the batch.
                if not application_owned_by_caller(app_id):
                    continue

                mapping_fields = app_data.get("mapping", {})
                mapping_id = app_data.get("mapping_id")

                # Check for existing mapping. Both lookups go through the
                # shared fence instead of a bare query -- application_id is
                # already proven owned above, but the mapping_id path used
                # to load a foreign row into the session before the
                # application_id comparison rejected it (pr310-v1 review,
                # DEFECT-2/DEFECT-7).
                existing = None
                if mapping_id:
                    existing = application_mapping_in_caller_org(int(mapping_id))
                    if existing is not None and existing.application_id != app_id:
                        # mapping_id names a row outside app_id's (already
                        # proven owned) application -- refuse rather than
                        # mutate a row that turned out to belong elsewhere.
                        existing = None

                if not existing:
                    existing = fenced_application_mappings_query().filter(
                        ProcessApplicationMapping.application_id == app_id,
                        ProcessApplicationMapping.apqc_process_id == process_id,
                    ).first()

                if existing:
                    existing.support_level = mapping_fields.get(
                        "support_level", existing.support_level
                    )
                    existing.automation_level = mapping_fields.get(
                        "automation_level", existing.automation_level
                    )
                    existing.process_coverage = mapping_fields.get(
                        "process_coverage",
                        mapping_fields.get(
                            "process_contribution", existing.process_coverage
                        ),
                    )
                    existing.application_role = mapping_fields.get(
                        "application_role",
                        mapping_fields.get("role", existing.application_role),
                    )
                    updated += 1
                else:
                    new_mapping = ProcessApplicationMapping(
                        application_id=app_id,
                        apqc_process_id=process_id,
                        support_level=mapping_fields.get("support_level", "partial"),
                        automation_level=mapping_fields.get("automation_level", 1),
                        process_coverage=mapping_fields.get(
                            "process_coverage",
                            mapping_fields.get("process_contribution", 50),
                        ),
                        application_role=mapping_fields.get(
                            "application_role",
                            mapping_fields.get("role", "supporting"),
                        ),
                    )
                    db.session.add(new_mapping)
                    created += 1

        # Format 2: direct capability-process mapping
        elif "capability_id" in data and "apqc_process_id" in data:
            from app.services.apqc_mapping_tenant_fence import (
                capability_owned_by_caller,
                fenced_capability_mappings_query,
            )

            capability_id = int(data["capability_id"])
            apqc_process_id = int(data["apqc_process_id"])

            # Same gap as Format 1 above, in the sibling capability-process
            # path: caller-supplied capability_id was never checked against
            # the caller's organisation, so a caller could update or create
            # a mapping against another organisation's capability. Found in
            # this consolidation sweep, not by an earlier round.
            if not capability_owned_by_caller(capability_id):
                return jsonify({"error": f"Capability not found: {capability_id}"}), 404

            existing = fenced_capability_mappings_query().filter(
                CapabilityProcessMapping.capability_id == capability_id,
                CapabilityProcessMapping.apqc_process_id == apqc_process_id,
            ).first()

            if existing:
                existing.relationship_type = data.get(
                    "relationship_type", existing.relationship_type
                )
                existing.impact_level = data.get("impact_level", existing.impact_level)
                existing.relationship_strength = data.get(
                    "relationship_strength", existing.relationship_strength
                )
                updated += 1
            else:
                new_mapping = CapabilityProcessMapping(
                    capability_id=capability_id,
                    apqc_process_id=apqc_process_id,
                    relationship_type=data.get("relationship_type", "enables"),
                    impact_level=data.get("impact_level", "medium"),
                    relationship_strength=data.get("relationship_strength", 3),
                )
                db.session.add(new_mapping)
                created += 1
        else:
            return jsonify({"error": "Invalid payload format"}), 400

        db.session.commit()
        return jsonify({"success": True, "created": created, "updated": updated})

    except Exception as e:
        from app import db

        db.session.rollback()
        logger.error(f"Error saving process mappings: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@apqc_bp.route("/process-mappings/<int:mapping_id>", methods=["DELETE"])
@login_required
@audit_log("apqc_mapping_delete")
def delete_process_mapping(mapping_id):
    """Delete a process-application mapping or capability-process mapping.

    Query params:
    - type: 'application' or 'capability' (required to avoid cross-table ID collision)
    """
    try:
        from app import db
        from app.services.apqc_mapping_tenant_fence import (
            application_mapping_in_caller_org,
            capability_mapping_in_caller_org,
        )

        mapping_type = request.args.get("type")

        if mapping_type == "application":
            # Neither mapping model carries an organization_id of its own;
            # unfenced, any authenticated user could delete any
            # organisation's row by id (pr303-v2 review, DEFECT D1).
            mapping = application_mapping_in_caller_org(mapping_id)
            if not mapping:
                return jsonify({"error": "Application mapping not found"}), 404
            db.session.delete(mapping)
            db.session.commit()
            return jsonify(
                {"success": True, "deleted_id": mapping_id, "type": "application"}
            )

        elif mapping_type == "capability":
            mapping = capability_mapping_in_caller_org(mapping_id)
            if not mapping:
                return jsonify({"error": "Capability mapping not found"}), 404
            db.session.delete(mapping)
            db.session.commit()
            return jsonify(
                {"success": True, "deleted_id": mapping_id, "type": "capability"}
            )

        else:
            return jsonify(
                {"error": "Query parameter 'type' is required (application|capability)"}
            ), 400

    except Exception as e:
        from app import db

        db.session.rollback()
        logger.error(f"Error deleting process mapping: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500
