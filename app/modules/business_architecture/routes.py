"""Compatibility redirect for the discontinued Business Architecture directory."""

from flask import Blueprint, redirect, url_for
from flask_login import login_required


business_architecture_bp = Blueprint(
    "business_architecture", __name__, url_prefix="/business-architecture"
)


@business_architecture_bp.route("/")
@login_required
def redirect_to_architecture_journey():
    """Preserve bookmarked intent while moving work to Architecture Journey."""
    return redirect(
        url_for("architecture_journey.index", intent="business-transformation"),
        code=301,
    )


# ── Business processes: the list, and each process drawn as BPMN 2.0 ───────────────────────────────────

from flask import Response, abort, flash, jsonify, render_template, request  # noqa: E402
from flask_login import current_user  # noqa: E402

from app import db  # noqa: E402
from app.core.auth.permissions import may_change_model_element  # noqa: E402
from app.middleware.tenant_context import current_org_id as _org_id  # noqa: E402
from app.models import ArchiMateElement  # noqa: E402
from app.models.process_data import BusinessProcess  # noqa: E402
from app.modules.business_architecture import bpmn_service  # noqa: E402


def _process_or_404(process_id):
    process = BusinessProcess.query.filter_by(id=process_id, organization_id=_org_id()).first()
    if process is None:
        abort(404)
    return process


def _can_edit():
    return may_change_model_element(current_user, "edit")


def _flow_of(process):
    flow = process.bpmn_flow
    if not flow:
        return bpmn_service.empty_flow(process.name)
    return bpmn_service.drawing_of(flow)


@business_architecture_bp.route("/processes")
@login_required
def processes():
    """Every business process, as a list or as a set of diagrams."""
    org_id = _org_id()
    rows = BusinessProcess.query.filter_by(organization_id=org_id).order_by(BusinessProcess.name).all()
    element_ids = {p.archimate_element_id for p in rows if p.archimate_element_id}
    elements = {e.id for e in ArchiMateElement.query.filter(
        ArchiMateElement.id.in_(element_ids), ArchiMateElement.organization_id == org_id,
        ArchiMateElement.type == "BusinessProcess").all()} if element_ids else set()
    items = []
    for p in rows:
        flow = p.bpmn_flow or {}
        parent = next((r for r in rows if r.id == p.parent_process_id), None)
        lanes = [lane["name"] for pool in flow.get("pools", []) for lane in pool.get("lanes", [])]
        items.append({
            "id": p.id, "name": p.name, "parent": parent.name if parent else None,
            "parent_id": parent.id if parent else None, "owner": p.process_owner, "status": p.status,
            "element_id": p.archimate_element_id if p.archimate_element_id in elements else None,
            "steps": len(flow.get("nodes", [])), "lanes": lanes, "drawn": bool(flow.get("nodes")),
        })
    view = "diagram" if request.args.get("view") == "diagram" else "list"
    can_edit = _can_edit()
    candidates = bpmn_service.elements_without_record(org_id) if can_edit else []
    return render_template("business_architecture/processes.html", items=items, view=view, can_edit=can_edit,
                           candidates=[(e.id, e.name) for e in candidates])


@business_architecture_bp.route("/processes/record", methods=["POST"])
@login_required
def record_process():
    """Make the process record of a chosen business process element, then open its diagram."""
    if not _can_edit():
        abort(403)
    org_id = _org_id()
    element_id = request.form.get("element_id", type=int)
    element = ArchiMateElement.query.filter_by(id=element_id, organization_id=org_id, type="BusinessProcess").first() if element_id else None
    if element is None:
        flash("Choose one of the business process elements in the list.", "error")
        return redirect(url_for("business_architecture.processes"))
    process = bpmn_service.process_for_element(element, org_id)
    db.session.commit()
    return redirect(url_for("business_architecture.process_diagram", process_id=process.id))


@business_architecture_bp.route("/processes/<int:process_id>/diagram")
@login_required
def process_diagram(process_id):
    process = _process_or_404(process_id)
    org_id = _org_id()
    parent = db.session.get(BusinessProcess, process.parent_process_id) if process.parent_process_id else None
    if parent is not None and parent.organization_id != org_id:
        parent = None
    return render_template(
        "business_architecture/process_diagram.html",
        process=process, parent=parent, flow=_flow_of(process), can_edit=_can_edit(),
        node_types=bpmn_service.NODE_LABELS,
    )


@business_architecture_bp.route("/processes/<int:process_id>/flow", methods=["GET"])
@login_required
def process_flow(process_id):
    return jsonify(_flow_of(_process_or_404(process_id)))


@business_architecture_bp.route("/processes/<int:process_id>/flow", methods=["PUT"])
@login_required
def save_process_flow(process_id):
    process = _process_or_404(process_id)
    if not _can_edit():
        return jsonify({"error": "Only an architect can change a process diagram."}), 403
    try:
        flow = bpmn_service.save_flow(process, request.get_json(silent=True), _org_id(), current_user.id)
        db.session.commit()
    except bpmn_service.FlowError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    return jsonify(flow)


@business_architecture_bp.route("/processes/<int:process_id>/bpmn.xml")
@login_required
def export_process_bpmn(process_id):
    process = _process_or_404(process_id)
    xml = bpmn_service.to_xml(_flow_of(process), process.name)
    safe = "".join(c if c.isalnum() else "-" for c in process.name).strip("-").lower() or "process"
    return Response(xml, mimetype="application/xml",
                    headers={"Content-Disposition": 'attachment; filename="%s.bpmn"' % safe})


@business_architecture_bp.route("/processes/<int:process_id>/bpmn", methods=["POST"])
@login_required
def import_process_bpmn(process_id):
    process = _process_or_404(process_id)
    if not _can_edit():
        return jsonify({"error": "Only an architect can change a process diagram."}), 403
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "Choose a BPMN 2.0 file to import."}), 400
    try:
        flow, notes = bpmn_service.from_xml(upload.read(5_000_001), _org_id(), process)
        flow = bpmn_service.save_flow(process, flow, _org_id(), current_user.id)
        db.session.commit()
    except bpmn_service.FlowError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    return jsonify({"flow": flow, "notes": notes})
