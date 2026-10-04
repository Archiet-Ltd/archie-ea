"""
DEPRECATED: This file is migrated to app/modules/architecture/.
Registration is now centralized via app.modules.architecture.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Architecture CRUD Routes - Complete Implementation
"""

import os

from flask import (
    Blueprint,
    jsonify,
    render_template,
    request,
    current_app,
    send_file,
)
from flask_login import login_required
from sqlalchemy import func, or_
from werkzeug.wsgi import ClosingIterator

from app.decorators import audit_log, require_roles
from app.extensions import db
from app.models.archimate_core import (
    ArchiMateElement as ArchitectureElement,
    ArchiMateRelationship as Relationship,
)
from app.services.architecture_validation_service import ArchitectureValidator
from app.services.architecture_search_service import ArchitectureSearchService
from app.services.architecture_import_export_service import (
    ArchitectureImportExportService,
)
from app.utils.pagination import safe_int_arg

architecture_crud_bp = Blueprint(
    "architecture_crud",
    __name__,
    url_prefix="/architecture",
)

# Instantiate services
validator = ArchitectureValidator()
search_service = ArchitectureSearchService()
import_export_service = ArchitectureImportExportService()


# ==================== ELEMENT ROUTES ====================


@architecture_crud_bp.route("/elements", methods=["GET"])
@login_required
def list_elements():
    """Render ArchiMate elements list with server-side layer stats (ARC-002).

    URL params forwarded to the Alpine component:
      ?layer=<layer>    — pre-selects the layer filter
      ?q=<term>         — initial search term
    """
    # ArchiMate 3.2 has six layers plus concepts that belong to none of them:
    # Location and Grouping (§4.5). Listing only the six meant those elements
    # existed in the table, appeared in the list below, and were absent from
    # every count above it - a repository holding 74 elements summarised 59 of
    # them, and the 15 Locations from an imported landscape diagram were
    # invisible. Physical is where Location, Equipment, Facility and Material
    # live; Other is Grouping and anything unclassified.
    _LAYER_ORDER = [
        "Motivation", "Strategy", "Business",
        "Application", "Technology", "Implementation",
        "Physical", "Other",
    ]

    # Count per layer
    counts_q = (
        db.session.query(ArchitectureElement.layer, func.count(ArchitectureElement.id))
        .group_by(ArchitectureElement.layer)
        .all()
    )
    count_map = {(row[0] or "").lower(): row[1] for row in counts_q}

    # Top 3 element types per layer
    layer_stats = []
    for layer_name in _LAYER_ORDER:
        top_types_q = (
            db.session.query(
                ArchitectureElement.type,
                func.count(ArchitectureElement.id).label("cnt"),
            )
            .filter(func.lower(ArchitectureElement.layer) == layer_name.lower())
            .group_by(ArchitectureElement.type)
            .order_by(func.count(ArchitectureElement.id).desc())
            .limit(3)
            .all()
        )
        layer_stats.append({
            "name": layer_name,
            "count": count_map.get(layer_name.lower(), 0),
            "top_types": [{"type": t or "", "count": c} for t, c in top_types_q],
        })

    # ARC-005: Data quality counts
    no_desc_count = (
        ArchitectureElement.query.filter(
            or_(
                ArchitectureElement.description.is_(None),
                ArchitectureElement.description == "",
            )
        ).count()
    )

    # Elements with no relationships (neither source nor target) — use raw scalar subquery
    from flask import g as _g
    from sqlalchemy import text as _text
    # Scope raw counts to the caller's org so they match the (org-scoped) total.
    _org = getattr(_g, "current_org_id", None)
    _oc = " AND organization_id = :org" if _org is not None else ""
    _pp = {"org": _org} if _org is not None else {}
    total_count = ArchitectureElement.query.count()
    with_rels_count = db.session.execute(_text(
        "SELECT COUNT(DISTINCT id) FROM archimate_elements WHERE id IN "
        "(SELECT source_id FROM archimate_relationships WHERE source_id IS NOT NULL" + _oc + " "
        "UNION SELECT target_id FROM archimate_relationships WHERE target_id IS NOT NULL" + _oc + ")"
        + _oc
    ), _pp).scalar() or 0
    no_rels_count = max(0, total_count - with_rels_count)

    # Elements not linked to any solution (scope via the element's org)
    with_solutions_count = db.session.execute(_text(
        "SELECT COUNT(DISTINCT sae.element_id) FROM solution_archimate_elements sae "
        "JOIN archimate_elements e ON e.id = sae.element_id "
        "WHERE sae.element_id IS NOT NULL"
        + (" AND e.organization_id = :org" if _org is not None else "")
    ), _pp).scalar() or 0
    no_solutions_count = max(0, total_count - with_solutions_count)

    return render_template("architecture/elements.html",
                           layer_stats=layer_stats,
                           no_desc_count=no_desc_count,
                           no_rels_count=no_rels_count,
                           no_solutions_count=no_solutions_count)


@architecture_crud_bp.route("/elements/create", methods=["GET"])
@login_required
@require_roles("admin", "architect")
def create_element_form():
    """Show create element form."""
    return jsonify({"success": True, "message": "Use POST to create element"})


@architecture_crud_bp.route("/elements", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@audit_log("architecture_element_create")
def create_element():
    """Create new architecture element."""
    data = request.get_json() or {}

    # Validate
    is_valid, errors = validator.validate_element(data)
    if not is_valid:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    # Create
    element = ArchitectureElement(
        name=data["name"],
        type=data["element_type"],  # model column is 'type', not 'element_type'
        layer=data.get("layer"),
        description=data.get("description"),
    )

    db.session.add(element)
    db.session.commit()

    return jsonify(
        {
            "status": "success",
            "element_id": element.id,
            "message": f"Element '{element.name}' created",
        }
    ), 201


@architecture_crud_bp.route("/elements/<int:element_id>", methods=["GET"])
@login_required
def view_element(element_id):
    """View element details."""
    element = ArchitectureElement.query.get_or_404(element_id)

    # Get relationships
    incoming = Relationship.query.filter_by(target_id=element_id).all()
    outgoing = Relationship.query.filter_by(source_id=element_id).all()

    return render_template(
        "architecture/elements.html",
        element=element,
        incoming_relationships=incoming,
        outgoing_relationships=outgoing,
    )


@architecture_crud_bp.route("/elements/<int:element_id>/edit", methods=["GET"])
@login_required
@require_roles("admin", "architect")
def edit_element_form(element_id):
    """Show edit element form."""
    element = ArchitectureElement.query.get_or_404(element_id)
    return jsonify(
        {"success": True, "data": element.to_dict(), "message": "Use PUT to update"}
    )


@architecture_crud_bp.route("/elements/<int:element_id>", methods=["PUT"])
@login_required
@require_roles("admin", "architect")
@audit_log("architecture_element_update")
def update_element(element_id):
    """Update element."""
    element = ArchitectureElement.query.get_or_404(element_id)
    data = request.get_json() or {}

    # Validate
    is_valid, errors = validator.validate_element(data)
    if not is_valid:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    # Update
    element.name = data.get("name", element.name)
    element.type = data.get("element_type", element.type)  # column is 'type'
    element.layer = data.get("layer", element.layer)
    element.description = data.get("description", element.description)

    db.session.commit()

    return jsonify(
        {
            "status": "success",
            "element": element.to_dict(),
            "message": f"Element '{element.name}' updated",
        }
    )


@architecture_crud_bp.route("/elements/<int:element_id>", methods=["DELETE"])
@login_required
@require_roles("admin", "architect")
@audit_log("architecture_element_delete")
def delete_element(element_id):
    """Delete element and all related relationships."""
    element = ArchitectureElement.query.get_or_404(element_id)
    element_name = element.name

    # Delete related relationships
    Relationship.query.filter(
        or_(
            Relationship.source_id == element_id,
            Relationship.target_id == element_id,
        )
    ).delete()

    db.session.delete(element)
    db.session.commit()

    return jsonify(
        {
            "status": "success",
            "message": f"Element '{element_name}' deleted",
        }
    )


# ==================== RELATIONSHIP ROUTES ====================


@architecture_crud_bp.route("/relationships", methods=["GET"])
@login_required
def list_relationships():
    """List all relationships.

    F-05(b), Capgemini dry-run: this used to query `Relationship` /
    `architecture_elements` — a legacy pair abandoned in favour of
    ArchiMateRelationship/ArchiMateElement (the tables the Composer and
    everything else actually write to), empty in every environment checked.
    The page rendered "20 rows of bare numeric IDs with a — type" because the
    only other route sharing this template (unified_low_priority.
    architecture_relationships, a different URL) queried the right table but
    still fed the template `rel.source_element`/`rel.relationship_type` —
    attributes ArchiMateRelationship does not have (it has `source_id`/
    `target_id` FKs and a `type` column, no ORM relationship() to the element).
    Jinja silently treats a missing attribute as falsy and falls back to the
    bare id, which is how BOTH routes produced the same "IDs, no names" bug
    from two different causes. Resolve real names/types here explicitly
    instead of relying on attributes that were never declared.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    page = safe_int_arg('page', 1, minimum=1)
    per_page = 20

    pagination = ArchiMateRelationship.query.order_by(
        ArchiMateRelationship.type
    ).paginate(page=page, per_page=per_page)

    element_ids = {
        eid for rel in pagination.items
        for eid in (rel.source_id, rel.target_id) if eid is not None
    }
    names_by_id = {}
    if element_ids:
        names_by_id = dict(
            db.session.query(ArchiMateElement.id, ArchiMateElement.name)
            .filter(ArchiMateElement.id.in_(element_ids))
        )

    relationships = [
        {
            "id": rel.id,
            "type": rel.type,
            "source_id": rel.source_id,
            "target_id": rel.target_id,
            "source_name": names_by_id.get(rel.source_id),
            "target_name": names_by_id.get(rel.target_id),
        }
        for rel in pagination.items
    ]

    return render_template(
        "architecture/relationships.html",
        relationships=relationships,
        total=pagination.total,
    )


@architecture_crud_bp.route("/relationships", methods=["POST"])
@login_required
@require_roles("admin", "architect")
@audit_log("architecture_relationship_create")
def create_relationship():
    """Create relationship between elements."""
    data = request.get_json() or {}

    # Validate
    is_valid, errors = validator.validate_relationship(data)
    if not is_valid:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    # Check for circular dependency
    if validator.would_create_cycle(data["source_id"], data["target_id"]):
        return jsonify({"error": "Would create circular dependency"}), 400

    relationship = Relationship(
        source_id=data["source_id"],
        target_id=data["target_id"],
        type=data["relationship_type"],  # model column is 'type'
        description=data.get("description"),
    )

    db.session.add(relationship)
    db.session.commit()

    return jsonify(
        {
            "status": "success",
            "relationship_id": relationship.id,
            "message": "Relationship created",
        }
    ), 201


@architecture_crud_bp.route("/relationships/<int:rel_id>", methods=["PUT"])
@login_required
@require_roles("admin", "architect")
@audit_log("architecture_relationship_update")
def update_relationship(rel_id):
    """Update relationship."""
    relationship = Relationship.query.get_or_404(rel_id)
    data = request.get_json() or {}

    # Validate
    is_valid, errors = validator.validate_relationship(data)
    if not is_valid:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    relationship.type = data.get(  # model column is 'type'
        "relationship_type", relationship.type
    )
    relationship.description = data.get("description", relationship.description)

    db.session.commit()

    return jsonify(
        {
            "status": "success",
            "relationship": relationship.to_dict(),
        }
    )


@architecture_crud_bp.route("/relationships/<int:rel_id>", methods=["DELETE"])
@login_required
@require_roles("admin", "architect")
@audit_log("architecture_relationship_delete")
def delete_relationship(rel_id):
    """Delete relationship."""
    relationship = Relationship.query.get_or_404(rel_id)

    db.session.delete(relationship)
    db.session.commit()

    return jsonify({"status": "success", "message": "Relationship deleted"})


# ==================== SEARCH & BROWSE ROUTES ====================


@architecture_crud_bp.route("/search", methods=["GET"])
@login_required
def search_elements():
    """Search elements by name, type, layer."""
    query = request.args.get("q", "")
    element_type = request.args.get("type")
    layer = request.args.get("layer")
    page = safe_int_arg('page', 1, minimum=1)

    results, total = search_service.search(
        query=query,
        element_type=element_type,
        layer=layer,
        page=page,
        per_page=20,
    )

    return render_template(
        "architecture/elements.html",
        results=results,
        total=total,
        query=query,
    )


@architecture_crud_bp.route("/by-layer/<layer>", methods=["GET"])
@login_required
def browse_by_layer(layer):
    """Browse elements by ArchiMate layer."""
    elements = ArchitectureElement.query.filter_by(layer=layer).limit(500).all()

    return render_template(
        "architecture/elements.html",
        layer=layer,
        elements=elements,
    )


# ==================== IMPORT/EXPORT ROUTES ====================


@architecture_crud_bp.route("/import", methods=["GET"])
@login_required
@require_roles("admin")
def import_form():
    """Show import form."""
    return jsonify({"success": True, "message": "Use POST to import architecture"})


@architecture_crud_bp.route("/import", methods=["POST"])
@login_required
@require_roles("admin")
def import_architecture():
    """Import architecture from file."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    file_type = request.args.get("format", "csv")

    try:
        result = import_export_service.import_data(file, file_type)
        return jsonify(
            {
                "status": "success",
                "imported": result["imported"],
                "skipped": result["skipped"],
                "errors": result["errors"],
            }
        )
    except Exception as e:
        current_app.logger.error(f"Architecture import failed: {str(e)}")
        return jsonify(
            {"error": "Import failed. Please check the file format and try again."}
        ), 400


@architecture_crud_bp.route("/export", methods=["GET"])
@login_required
@require_roles("admin")
def export_architecture():
    """Export architecture to file."""
    format_type = request.args.get("format", "csv")
    if format_type not in {"csv", "json"}:
        return jsonify({"error": "Supported export formats are csv and json"}), 400

    try:
        file_path, filename = import_export_service.export_data(format_type)

        # Close the streamed file before unlinking it (required on Windows).
        logger = current_app.logger
        def _cleanup_export_file():
            try:
                if os.path.exists(file_path):
                    os.unlink(file_path)
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to clean up export temp file %s: %s",
                    file_path,
                    cleanup_err,
                )

        response = send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
            mimetype="text/csv" if format_type == "csv" else "application/json",
        )
        response.response = ClosingIterator(response.response, _cleanup_export_file)
        return response
    except Exception as e:
        # O-04: "Export failed. Please try again." told the caller nothing —
        # not the reason, not whether retrying could possibly help, and
        # nothing to give support. Log the real exception under a
        # correlation ID and hand the ID (not the raw exception text, which
        # can leak internals) back to the caller so a support request can be
        # matched to the server-side log line that explains it.
        import uuid

        correlation_id = uuid.uuid4().hex[:12]
        current_app.logger.error(
            "Architecture export failed [correlation_id=%s] format=%s: %s",
            correlation_id, format_type, str(e), exc_info=True,
        )
        return jsonify(
            {
                "error": f"Export failed: {type(e).__name__}. This has been logged "
                         f"(reference {correlation_id}) — include it if you contact support.",
                "correlation_id": correlation_id,
            }
        ), 400


# ==================== API ENDPOINTS ====================


def _element_to_dict(e, rel_count=None, sol_count=None):
    """Serialize an ArchiMateElement to dict."""
    d = {
        "id": e.id,
        "name": e.name,
        "type": e.type,
        "layer": e.layer,
        "description": e.description,
        "scope": getattr(e, "scope", None),
        "parent_id": getattr(e, "parent_id", None),
        "architecture_id": getattr(e, "architecture_id", None),
    }
    if rel_count is not None:
        d["relationship_count"] = rel_count
    if sol_count is not None:
        d["solution_count"] = sol_count
    return d


def _relationship_to_dict(r):
    """Serialize an ArchiMateRelationship to dict."""
    return {
        "id": r.id,
        "type": r.type,
        "source_id": r.source_id,
        "target_id": r.target_id,
        "architecture_id": getattr(r, "architecture_id", None),
    }


@architecture_crud_bp.route("/api/elements", methods=["GET"])
@login_required
def api_list_elements():
    """API: List elements (JSON) with relationship + solution counts (ARCH-002).

    ARCH-052: paginated to match /applications/api/list's envelope
    (page/pages/per_page/total, total == collection total). The legacy
    `elements` + `status` keys are kept for backward compatibility with
    existing callers that do not paginate.
    """
    from sqlalchemy import func

    raw_page = request.args.get("page", "1")
    raw_per_page = request.args.get("per_page", "500")
    try:
        page = int(raw_page)
        if page < 1:
            raise ValueError
    except (TypeError, ValueError):
        return (
            jsonify({"status": "error", "errors": {"page": ["must be a positive integer"]}}),
            400,
        )
    try:
        per_page = int(raw_per_page)
        if per_page < 1:
            raise ValueError
    except (TypeError, ValueError):
        return (
            jsonify(
                {"status": "error", "errors": {"per_page": ["must be a positive integer"]}}
            ),
            400,
        )
    per_page = min(per_page, 500)

    # Subquery: count relationships where element is source or target
    rel_sub = (
        db.session.query(
            Relationship.source_id.label("eid"),
            func.count(Relationship.id).label("cnt"),
        )
        .group_by(Relationship.source_id)
        .subquery()
    )
    rel_sub_t = (
        db.session.query(
            Relationship.target_id.label("eid"),
            func.count(Relationship.id).label("cnt"),
        )
        .group_by(Relationship.target_id)
        .subquery()
    )

    # Subquery: count solutions linked to each element
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        sol_sub = (
            db.session.query(
                SolutionArchiMateElement.element_id.label("eid"),
                func.count(SolutionArchiMateElement.id).label("cnt"),
            )
            .group_by(SolutionArchiMateElement.element_id)
            .subquery()
        )
    except Exception:
        sol_sub = None

    total = ArchitectureElement.query.count()
    pagination = ArchitectureElement.query.order_by(ArchitectureElement.id).paginate(
        page=page, per_page=per_page, error_out=False
    )
    elements = pagination.items

    # Build count lookup dicts for efficiency
    rel_src = {r.eid: r.cnt for r in db.session.query(rel_sub).all()}
    rel_tgt = {r.eid: r.cnt for r in db.session.query(rel_sub_t).all()}
    sol_map = {}
    if sol_sub is not None:
        sol_map = {r.eid: r.cnt for r in db.session.query(sol_sub).all()}

    result = []
    for e in elements:
        rc = (rel_src.get(e.id) or 0) + (rel_tgt.get(e.id) or 0)
        sc = sol_map.get(e.id) or 0
        result.append(_element_to_dict(e, rel_count=rc, sol_count=sc))

    return jsonify(
        {
            "status": "success",
            "elements": result,
            "total": total,
            "page": pagination.page,
            "pages": pagination.pages,
            "per_page": per_page,
        }
    )


@architecture_crud_bp.route("/api/relationships", methods=["GET"])
@login_required
def api_list_relationships():
    """API: List relationships (JSON)."""
    relationships = Relationship.query.limit(500).all()
    return jsonify(
        {
            "status": "success",
            "relationships": [_relationship_to_dict(r) for r in relationships],
        }
    )


@architecture_crud_bp.route(
    "/api/elements/<int:element_id>/relationships", methods=["GET"]
)
@login_required
def api_element_relationships(element_id):
    """API: Get relationships for an element."""
    ArchitectureElement.query.get_or_404(element_id)

    incoming = Relationship.query.filter_by(target_id=element_id).all()
    outgoing = Relationship.query.filter_by(source_id=element_id).all()

    return jsonify(
        {
            "status": "success",
            "incoming": [_relationship_to_dict(r) for r in incoming],
            "outgoing": [_relationship_to_dict(r) for r in outgoing],
        }
    )


@architecture_crud_bp.route("/api/validate/relationships", methods=["POST"])
@login_required
def api_validate_relationships():
    """API: Validate all relationships for integrity."""
    errors = validator.validate_relationships_integrity()

    return jsonify(
        {
            "status": "success" if not errors else "warning",
            "valid": len(errors) == 0,
            "errors": errors,
        }
    )


# ==================== APPLICATION TECHNOLOGY LINKS ====================
# What an application runs on. Each link is a real ArchiMate "realization"
# relationship (node or system software -> application), written through
# ArchiMateRelationshipService, so the impact answer follows it unchanged.


def _technology_link_error(exc):
    return jsonify({"status": "error", "error": exc.message}), exc.status


@architecture_crud_bp.route(
    "/api/applications/<int:application_id>/technology-links", methods=["GET"]
)
@login_required
def api_application_technology_links(application_id):
    """API: the nodes and system software an application is mapped to."""
    from app.modules.architecture.services.application_technology_links import (
        TechnologyLinkError,
        list_links,
    )

    try:
        links = list_links(application_id)
    except TechnologyLinkError as exc:
        return _technology_link_error(exc)
    return jsonify({"status": "success", "links": links})


@architecture_crud_bp.route(
    "/api/applications/<int:application_id>/technology-links", methods=["POST"]
)
@login_required
@require_roles("admin", "architect")
@audit_log("application_technology_link_create")
def api_add_application_technology_link(application_id):
    """API: map an application to a node or system software it runs on."""
    from flask_login import current_user

    from app.modules.architecture.services.application_technology_links import (
        TechnologyLinkError,
        add_link,
    )

    data = request.get_json(silent=True) or {}
    try:
        element_id = int(data.get("element_id"))
    except (TypeError, ValueError):
        element_id = 0
    if element_id <= 0:
        return jsonify({"status": "error", "error": "Choose a node or system software."}), 400

    try:
        link = add_link(application_id, element_id, user_id=getattr(current_user, "id", None))
        db.session.commit()
    except TechnologyLinkError as exc:
        db.session.rollback()
        return _technology_link_error(exc)
    return jsonify({"status": "success", "link": link}), 201


@architecture_crud_bp.route(
    "/api/applications/<int:application_id>/technology-links/<int:relationship_id>",
    methods=["DELETE"],
)
@login_required
@require_roles("admin", "architect")
@audit_log("application_technology_link_delete")
def api_remove_application_technology_link(application_id, relationship_id):
    """API: remove one of an application's technology links."""
    from app.modules.architecture.services.application_technology_links import (
        TechnologyLinkError,
        remove_link,
    )

    try:
        remove_link(application_id, relationship_id)
        db.session.commit()
    except TechnologyLinkError as exc:
        db.session.rollback()
        return _technology_link_error(exc)
    return jsonify({"status": "success"})
