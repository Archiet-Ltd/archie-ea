"""API routes for compliance framework management."""

from datetime import datetime

from flask import current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.application_mgmt import application_mgmt
from app.decorators import audit_log, require_roles
from app.middleware.tenant_context import current_org_id
from app.models.application_compliance import ApplicationComplianceControl
from app.models.compliance_models import ComplianceControl, RegulatoryFramework
from app.models.control_test import CONTROL_TEST_KINDS, CONTROL_TEST_RESULTS, ControlTest
from app.models.user import User
from app.modules.compliance.services.control_evidence import control_effectiveness, naive_utc
from app.modules.compliance.services.evidence_gap_service import evidence_gaps, valid_evidence_url
from app.utils.route_guards import require_entity

# Who may record evidence and tests on an adopted control: the roles that adopt a
# framework, plus the compliance persona (matched through its enterprise role).
CONTROL_EVIDENCE_ROLES = (
    "admin",
    "architect",
    "business_architect",
    "security_architect",
    "compliance",
    "compliance_officer",
)


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

    framework_rows = [
        {
            "id": f.id,
            "code": f.code,
            "name": f.name,
            "category": f.category,
            "jurisdiction": f.jurisdiction,
            "enforcement_level": f.enforcement_level,
            "penalty_risk": f.penalty_risk,
            "control_count": f.controls.count(),
        }
        for f in frameworks
    ]

    total_controls = ComplianceControl.query.filter_by(is_active=True).count()

    # Org-scoped mapping coverage (TenantMixin auto-filters inside the request).
    mappings = ApplicationComplianceControl.query.all()
    mapped_by_status = {}
    for m in mappings:
        status = m.implementation_status or "planned"
        mapped_by_status[status] = mapped_by_status.get(status, 0) + 1

    gaps, gaps_error = [], False
    org_id = current_org_id()
    if org_id is not None:
        try:
            gaps = evidence_gaps(org_id)
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Could not build the evidence-gap list")
            gaps_error = True

    return render_template(
        "compliance/frameworks_dashboard.html",
        frameworks=framework_rows,
        total_frameworks=len(framework_rows),
        total_controls=total_controls,
        total_mappings=len(mappings),
        mapped_by_status=mapped_by_status,
        evidence_gaps=gaps,
        evidence_gaps_error=gaps_error,
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

    controls = query.order_by(ComplianceControl.control_code).all()
    return jsonify(
        [
            {
                "id": c.id,
                "control_code": c.control_code,
                "title": c.title,
                "description": c.description,
                "category": c.category,
                "priority": c.priority,
            }
            for c in controls
        ]
    )


@application_mgmt.route("/api/applications/<string:app_id>/compliance", methods=["GET"])
@login_required
def get_application_compliance(app_id):
    """Get compliance control mappings for an application."""
    mappings = ApplicationComplianceControl.query.filter_by(application_id=app_id).all()

    result = {
        "application_id": app_id,
        "mappings": [m.to_dict() for m in mappings],
        "summary": {
            "total_controls": len(mappings),
            "implemented": len([m for m in mappings if m.status == "implemented"]),
            "planned": len([m for m in mappings if m.status == "planned"]),
            "not_implemented": len([m for m in mappings if m.status == "not_implemented"]),
        },
    }

    return jsonify(result)


@application_mgmt.route("/api/applications/<string:app_id>/compliance/map", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect")
def map_compliance_control(app_id):
    """Map a compliance control to an application component."""
    data = request.get_json()

    if not data or "control_id" not in data:
        return jsonify({"error": "control_id required"}), 400

    # Check if mapping already exists
    existing = ApplicationComplianceControl.query.filter_by(
        control_id=data["control_id"], application_id=app_id
    ).first()

    if existing:
        return jsonify({"error": "Mapping already exists"}), 409

    try:
        mapping = ApplicationComplianceControl(
            application_id=app_id,
            control_id=data["control_id"],
            status=data.get("status", "not_implemented"),
            coverage_percentage=data.get("coverage_percentage", 0),
            implementation_notes=data.get("implementation_notes"),
            evidence=data.get("evidence"),
        )

        db.session.add(mapping)
        db.session.commit()

        return jsonify({"success": True, "mapping": mapping.to_dict()})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating compliance mapping: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


def _own_mapping(mapping_id):
    """The adopted control row with this id in the caller's active organisation, else None.
    Looked up directly by id and organisation; with no active organisation nothing is found."""
    org_id = current_org_id()
    if org_id is None:
        return None
    return db.session.execute(
        db.select(ApplicationComplianceControl)
        .where(ApplicationComplianceControl.id == mapping_id)
        .where(ApplicationComplianceControl.organization_id == org_id)
    ).scalar_one_or_none()


def _parse_moment(raw, *, end_of_day=False):
    """A date (YYYY-MM-DD) or an ISO date-time as naive UTC; ValueError when it is neither.
    With ``end_of_day`` a bare date means the end of that day, so "valid until 30 June"
    holds through 30 June."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("a date is required")
    text = raw.strip()
    moment = naive_utc(datetime.fromisoformat(text))
    if end_of_day and len(text) == 10:
        moment = moment.replace(hour=23, minute=59, second=59)
    return moment


def _full_name(user):
    name = " ".join(p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p).strip()
    return name or "—"


@application_mgmt.route("/api/compliance/mappings/<int:mapping_id>", methods=["PUT"])
@login_required
@require_roles(*CONTROL_EVIDENCE_ROLES)
def update_compliance_mapping(mapping_id):
    """Update a compliance mapping, including its evidence link, validity date and owner."""
    mapping = _own_mapping(mapping_id)
    if mapping is None:
        return jsonify({"error": "Control not found"}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON body is required"}), 400

    # Validate everything before changing anything. A field that is absent is left as it was;
    # an explicit blank clears it.
    changes = {}
    try:
        if "evidence_url" in data:
            raw = data["evidence_url"]
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                changes["evidence_url"] = None
            elif valid_evidence_url(raw):
                changes["evidence_url"] = raw.strip()
                changes["evidence_source"] = "person"
                changes["evidence_collected_at"] = (
                    _parse_moment(data["evidence_collected_at"])
                    if data.get("evidence_collected_at")
                    else naive_utc()
                )
            else:
                return jsonify({"error": "Evidence must be a web link (http or https)"}), 400
        if "evidence_valid_until" in data:
            raw = data["evidence_valid_until"]
            changes["evidence_valid_until"] = (
                None
                if raw is None or (isinstance(raw, str) and not raw.strip())
                else _parse_moment(raw, end_of_day=True)
            )
        if "evidence_owner_id" in data:
            raw = data["evidence_owner_id"]
            if raw is None or raw == "":
                changes["evidence_owner_id"] = None
            else:
                owner_id = int(raw)
                owner = db.session.execute(
                    db.select(User.id)
                    .where(User.id == owner_id)
                    .where(User.organization_id == mapping.organization_id)
                ).scalar_one_or_none()
                if owner is None:
                    return jsonify({"error": "Owner not found in this organisation"}), 400
                changes["evidence_owner_id"] = owner
    except (ValueError, TypeError):
        return jsonify({"error": "A date must be YYYY-MM-DD and an owner a user id"}), 400

    try:
        for field, value in changes.items():
            setattr(mapping, field, value)
        if "status" in data:
            mapping.status = data["status"]
        if "coverage_percentage" in data:
            mapping.coverage_percentage = data["coverage_percentage"]
        if "implementation_notes" in data:
            mapping.implementation_notes = data["implementation_notes"]
        if "evidence" in data:
            mapping.evidence = data["evidence"]

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


def _test_json(test, tester):
    return {
        "id": test.id,
        "test_kind": test.test_kind,
        "result": test.result,
        "tested_at": test.tested_at.isoformat() if test.tested_at else None,
        "tested_by": _full_name(tester) if tester is not None else "—",
        "notes": test.notes,
        "evidence_url": test.evidence_url,
    }


@application_mgmt.route("/api/compliance/mappings/<int:mapping_id>/tests", methods=["GET"])
@login_required
def list_control_tests(mapping_id):
    """The tests recorded against one adopted control, newest first, and its effectiveness."""
    mapping = _own_mapping(mapping_id)
    if mapping is None:
        return jsonify({"error": "Control not found"}), 404
    tests = list(
        db.session.execute(
            db.select(ControlTest)
            .where(ControlTest.organization_id == mapping.organization_id)
            .where(ControlTest.application_control_id == mapping.id)
            .order_by(ControlTest.tested_at.desc(), ControlTest.id.desc())
        ).scalars()
    )
    tester_ids = sorted({t.tested_by_id for t in tests if t.tested_by_id is not None})
    testers = {}
    if tester_ids:
        testers = {
            u.id: u
            for u in db.session.execute(
                db.select(User).where(User.organization_id == mapping.organization_id).where(User.id.in_(tester_ids))
            ).scalars()
        }
    return jsonify({
        "mapping_id": mapping.id,
        "effectiveness": control_effectiveness(tests),
        "tests": [_test_json(t, testers.get(t.tested_by_id)) for t in tests],
    })


@application_mgmt.route("/api/compliance/mappings/<int:mapping_id>/tests", methods=["POST"])
@login_required
@require_roles(*CONTROL_EVIDENCE_ROLES)
@audit_log("control_test_record")
def record_control_test(mapping_id):
    """Record a design or operating test of one adopted control."""
    mapping = _own_mapping(mapping_id)
    if mapping is None:
        return jsonify({"error": "Control not found"}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON body is required"}), 400

    kind, result = data.get("test_kind"), data.get("result")
    if kind not in CONTROL_TEST_KINDS or result not in CONTROL_TEST_RESULTS:
        return jsonify({"error": "A test is a design or operating test with a result of effective, ineffective or exception"}), 400
    link = data.get("evidence_url")
    if link not in (None, "") and not valid_evidence_url(link):
        return jsonify({"error": "Evidence must be a web link (http or https)"}), 400
    notes = data.get("notes")
    if notes is not None and not isinstance(notes, str):
        return jsonify({"error": "Notes must be text"}), 400
    try:
        tested_at = _parse_moment(data["tested_at"]) if data.get("tested_at") else naive_utc()
    except (ValueError, TypeError):
        return jsonify({"error": "The test date must be YYYY-MM-DD"}), 400

    try:
        test = ControlTest(
            organization_id=mapping.organization_id,
            application_control_id=mapping.id,
            test_kind=kind,
            result=result,
            tested_at=tested_at,
            tested_by_id=current_user.id,
            notes=(notes or "").strip() or None,
            evidence_url=(link or "").strip() or None,
        )
        db.session.add(test)
        db.session.commit()
        return jsonify({"success": True, "test": _test_json(test, current_user)}), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            "Failed to record control test mapping_id=%s user=%s: %s",
            mapping_id,
            getattr(current_user, "id", None),
            e,
            exc_info=True,
        )
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/compliance/mappings/<int:mapping_id>", methods=["DELETE"])
@login_required
@require_roles("admin", "architect", "business_architect")
def delete_compliance_mapping(mapping_id):
    """Remove a compliance mapping."""
    mapping = ApplicationComplianceControl.query.get_or_404(mapping_id)

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
