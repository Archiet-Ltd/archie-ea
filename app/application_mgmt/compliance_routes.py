"""API routes for compliance framework management."""

from flask import abort, current_app, g, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.application_mgmt import application_mgmt
from app.models.application_compliance import ApplicationComplianceControl
from app.models.compliance_models import ComplianceControl, RegulatoryFramework
from app.core.auth.permissions import can
from app.models.application_portfolio import ApplicationComponent
from app.modules.compliance.services.applicability_service import ApplicabilityService, FrameworkNotAdopted, NotFoundInOrganisation
from app.seed_data.standard_control_totals import controls_loaded_text
from app.services.compliance.earlier_catalogue_seeds import article_order
from app.utils.route_guards import require_entity


@application_mgmt.route("/compliance", methods=["GET"])
@login_required
def compliance_frameworks_dashboard():
    """Read-only regulatory-compliance dashboard (G6).

    Surfaces the built-but-previously-orphaned RegulatoryFramework /
    ComplianceControl model — until now reachable only through the JSON API in
    this module, with no door in any persona's navigation. It is the
    security_architect's compliance surface (see role_access.py).

    RegulatoryFramework and ComplianceControl are GLOBAL master data (no
    organization_id); the app<->control mappings are org-scoped via
    ApplicationComplianceControl (TenantMixin), so its counts filter to the
    caller's organisation mechanically inside a request context. Every number
    below is a real query result — an empty estate renders zeros/— rather than a
    fabricated figure.
    """
    frameworks = (
        RegulatoryFramework.query.filter_by(status="active")
        .order_by(RegulatoryFramework.code)
        .all()
    )

    from flask import g

    from app.models.regulatory_framework import FrameworkAdoption

    adopted = {
        a.framework_id: a.source
        for a in FrameworkAdoption.query.filter_by(
            organization_id=getattr(g, "current_org_id", None), status="active"
        ).all()
    }
    counts = {f.id: f.controls.count() for f in frameworks}
    framework_rows = [
        {
            "adopted": f.id in adopted,
            "source": adopted.get(f.id),
            "id": f.id,
            "code": f.code,
            "name": f.name,
            "category": f.category,
            "jurisdiction": f.jurisdiction,
            "enforcement_level": f.enforcement_level,
            "penalty_risk": f.penalty_risk,
            "control_count": counts[f.id],
            "partly_loaded": controls_loaded_text(f.code, counts[f.id]),
        }
        for f in frameworks
    ]

    total_controls = ComplianceControl.query.filter_by(is_active=True).count()

    # Org-scoped mapping coverage (TenantMixin auto-filters inside the request).
    # A control adopted with its framework is not yet mapped to an application, so it is not counted here.
    mappings = ApplicationComplianceControl.query.filter(ApplicationComplianceControl.application_id.isnot(None)).all()
    mapped_by_status = {}
    for m in mappings:
        status = m.implementation_status or "planned"
        mapped_by_status[status] = mapped_by_status.get(status, 0) + 1

    return render_template(
        "compliance/frameworks_dashboard.html",
        frameworks=framework_rows,
        total_frameworks=len(framework_rows),
        total_controls=total_controls,
        total_mappings=len(mappings),
        mapped_by_status=mapped_by_status,
        open_framework_id=request.args.get("framework", type=int),
        load_error=False,
    )


@application_mgmt.route("/api/compliance/frameworks", methods=["GET"])
@login_required
def get_compliance_frameworks():
    """Get all active compliance frameworks."""
    frameworks = RegulatoryFramework.query.filter_by(status="active").all()
    return jsonify(
        [
            {
                "id": f.id,
                "code": f.code,
                "name": f.name,
                "description": f.description,
                "category": f.category,
                "enforcement_level": f.enforcement_level,
                "control_count": f.controls.count(),
            }
            for f in frameworks
        ]
    )


@application_mgmt.route("/api/compliance/frameworks/<int:framework_id>/controls", methods=["GET"])
@login_required
def get_framework_controls(framework_id):
    """Get all controls for a framework."""
    # An unknown framework must 404: an empty control list is otherwise
    # indistinguishable from a framework that has no controls.
    require_entity(RegulatoryFramework, framework_id, description="Framework not found")

    category = request.args.get("category")

    query = ComplianceControl.query.filter_by(framework_id=framework_id)

    if category:
        query = query.filter_by(category=category)

    controls = sorted(query.all(), key=lambda c: article_order(c.control_code, c.id))
    return jsonify(
        [
            {
                "id": c.id,
                "control_code": c.control_code,
                "title": c.title,
                "description": c.description,
                "category": c.category,
                "priority": c.priority,
                "official_reference": c.official_reference,
            }
            for c in controls
        ]
    )


@application_mgmt.route("/api/applications/<int:app_id>/compliance", methods=["GET"])
@login_required
def get_application_compliance(app_id):
    """Get compliance control mappings for an application."""
    if not ApplicationComponent.query.filter_by(id=app_id, organization_id=g.current_org_id).first():
        abort(404, description="Application not found")
    mappings = ApplicationComplianceControl.query.filter_by(
        application_id=app_id, organization_id=g.current_org_id
    ).all()

    def _count(status):
        return len([m for m in mappings if m.implementation_status == status])

    result = {
        "application_id": app_id,
        "mappings": [m.to_dict() for m in mappings],
        "summary": {
            "total_controls": len(mappings),
            **{status: _count(status) for status in MAPPING_STATUSES},
        },
    }

    return jsonify(result)


NOT_ALLOWED = "Your role cannot link requirements to applications."


def _own_application_mapping(mapping_id):
    """The organisation's own application-to-control mapping, or 404. The adoption row, which has no
    application, is not a mapping and answers as a missing one."""
    mapping = ApplicationComplianceControl.query.filter(
        ApplicationComplianceControl.id == mapping_id,
        ApplicationComplianceControl.organization_id == g.current_org_id,
        ApplicationComplianceControl.application_id.isnot(None),
    ).first()
    if mapping is None:
        abort(404, description="Mapping not found")
    return mapping


MAPPING_STATUSES = ("planned", "in_progress", "implemented", "verified", "not_applicable", "waived")


def _mapping_fields(data):
    """Pick the stored columns out of a request body, or return an error message."""
    fields = {}
    if "implementation_status" in data:
        if data["implementation_status"] not in MAPPING_STATUSES:
            return None, "implementation_status must be one of: " + ", ".join(MAPPING_STATUSES)
        fields["implementation_status"] = data["implementation_status"]
    for key in ("notes", "evidence_url"):
        if key in data:
            fields[key] = data[key]
    return fields, None


@application_mgmt.route("/api/applications/<int:app_id>/compliance/map", methods=["POST"])
@login_required
def map_compliance_control(app_id):
    """Map a compliance control to an application component."""
    if not can(current_user, "edit", "application"):
        return jsonify({"error": NOT_ALLOWED}), 403
    data = request.get_json(silent=True)

    if not data or "control_id" not in data:
        return jsonify({"error": "control_id required"}), 400
    control_id = data["control_id"]
    if not isinstance(control_id, int) or isinstance(control_id, bool):
        return jsonify({"error": "control_id must be a whole number"}), 400

    fields, error = _mapping_fields(data)
    if error:
        return jsonify({"error": error}), 400

    if ComplianceControl.query.get(control_id) is None:
        return jsonify({"error": "Unknown control"}), 404

    # An application of another organisation answers exactly as a missing one does.
    if not ApplicationComponent.query.filter_by(id=app_id, organization_id=g.current_org_id).first():
        return jsonify({"error": "Application not found"}), 404

    existing = ApplicationComplianceControl.query.filter_by(
        control_id=control_id, application_id=app_id
    ).first()

    if existing:
        return jsonify({"error": "Mapping already exists"}), 409

    try:
        # The one writer of application-to-requirement links, shared with the application page.
        mapping = ApplicabilityService.link_requirement(
            g.current_org_id,
            app_id,
            control_id,
            implementation_status=fields.get("implementation_status", "planned"),
            notes=fields.get("notes"),
            evidence_url=fields.get("evidence_url"),
        )
        return jsonify({"success": True, "mapping": mapping.to_dict()})

    except NotFoundInOrganisation:
        return jsonify({"error": "Application not found"}), 404
    except FrameworkNotAdopted:
        return jsonify({"error": "Adopt this framework first."}), 409
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating compliance mapping: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/compliance/mappings/<int:mapping_id>", methods=["PUT"])
@login_required
def update_compliance_mapping(mapping_id):
    """Update a compliance mapping."""
    if not can(current_user, "edit", "application"):
        return jsonify({"error": NOT_ALLOWED}), 403
    mapping = _own_application_mapping(mapping_id)
    data = request.get_json(silent=True) or {}

    fields, error = _mapping_fields(data)
    if error:
        return jsonify({"error": error}), 400

    try:
        if "implementation_status" in fields:
            mapping.implementation_status = fields["implementation_status"]
        if "notes" in fields:
            mapping.notes = fields["notes"]
        if "evidence_url" in fields:
            mapping.evidence_url = fields["evidence_url"]

        db.session.commit()

        return jsonify({"success": True, "mapping": mapping.to_dict()})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            "Failed to update compliance mapping mapping_id=%s user=%s: %s",
            mapping_id,
            getattr(current_user, "id", None),
            e,
            exc_info=True,
        )
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/compliance/mappings/<int:mapping_id>", methods=["DELETE"])
@login_required
def delete_compliance_mapping(mapping_id):
    """Remove a compliance mapping."""
    if not can(current_user, "edit", "application"):
        return jsonify({"error": NOT_ALLOWED}), 403
    mapping = _own_application_mapping(mapping_id)

    try:
        db.session.delete(mapping)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            "Failed to delete compliance mapping mapping_id=%s user=%s: %s",
            mapping_id,
            getattr(current_user, "id", None),
            e,
            exc_info=True,
        )
        return jsonify({"error": "An internal error occurred"}), 500
