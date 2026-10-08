import json
from datetime import datetime

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import requires_role
from app.decorators.requires_role import DATA_SUBJECT_REQUEST_ROLES
from app.extensions import db
from app.middleware.tenant_decorators import platform_admin_required
from app.models.gdpr_request import REQUEST_TYPES, GDPRRequest
from app.models.user import User
from app.services.gdpr_service import DataSubjectRequestError, GDPRService
from app.utils.route_guards import require_entity


gdpr_bp = Blueprint("gdpr_bp", __name__)


def _subject_org_id(user_id):
    """The organisation a request about ``user_id`` belongs to: the subject's
    own, or the caller's when the subject does not exist."""
    # tenant-scoping-ok: only the subject's organisation id is read, after the
    # caller has been authorised as the subject or a platform admin.
    subject = User.query.get(user_id)
    return subject.organization_id if subject is not None else current_user.organization_id


def _forbid_unless_self_or_platform_admin(user_id):
    """A data-subject request may be actioned by a platform_admin (any user_id)
    or by the subject themselves (their own user_id only). Returns a Flask
    response to short-circuit with, or None if the caller is authorized."""
    if current_user.id == user_id:
        return None
    if not getattr(current_user, "is_platform_admin", False):
        return jsonify({"error": "Platform admin access required"}), 403
    return None


@gdpr_bp.route("/api/gdpr/export/<int:user_id>", methods=["GET"])
@login_required
def export_user_data(user_id):
    denied = _forbid_unless_self_or_platform_admin(user_id)
    if denied:
        return denied
    # Log GDPR export request, in the subject's own organisation.
    req = GDPRRequest(user_id=user_id, request_type="export", status="pending", requested_at=datetime.utcnow(),
                      organization_id=_subject_org_id(user_id))
    db.session.add(req)
    db.session.commit()
    data = GDPRService.export_user_data(user_id)
    if data is None:
        req.status = "failed"
        req.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"error": "User not found"}), 404
    req.status = "completed"
    req.completed_at = datetime.utcnow()
    db.session.commit()
    return jsonify(data)

@gdpr_bp.route("/api/gdpr/delete/<int:user_id>", methods=["POST"])
@platform_admin_required
def delete_user_data(user_id):
    # Cross-user permanent deletion: platform_admin only, no self-service —
    # deliberately not allowed for user_id == current_user.id either, since an
    # unconfirmed self-delete is its own hazard.
    requester_id = current_user.id
    req = GDPRRequest(user_id=user_id, request_type="delete", status="pending", requested_at=datetime.utcnow(),
                      organization_id=_subject_org_id(user_id), requested_by_id=requester_id)
    db.session.add(req)
    db.session.commit()
    ok = GDPRService.delete_user_data(user_id, requester_id, gdpr_request=req)
    if not ok:
        req.status = "failed"
        req.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"error": "User not found"}), 404
    return jsonify({"status": "deleted"})

@gdpr_bp.route("/api/gdpr/status/<int:user_id>", methods=["GET"])
@login_required
def gdpr_status(user_id):
    denied = _forbid_unless_self_or_platform_admin(user_id)
    if denied:
        return denied
    # Existence is probed only AFTER authorisation, so this discloses nothing a
    # caller could not already read: a nonexistent subject must 404 rather than
    # return an empty status that reads as "no GDPR requests on file".
    require_entity(User, user_id, description="User not found")
    status = GDPRService.get_request_status(user_id)
    return jsonify(status)


# ---------------------------------------------------------------------------
# Data-subject requests for the Data Protection Officer
#
# Every route below acts only inside the caller's own organisation: requests,
# subjects and assignees from another organisation read as not found.
# The Data Protection Officer works as the security architect persona, which
# owns the compliance section; platform administrators also pass.
# ---------------------------------------------------------------------------

_DPO_ROLES = DATA_SUBJECT_REQUEST_ROLES


def _org_id():
    return current_user.organization_id


def _org_people():
    return (
        User.query.filter(User.organization_id == _org_id(), User.email.isnot(None))
        .order_by(User.email)
        .all()
    )


def _request_or_404(request_id):
    req = GDPRService.get_request(_org_id(), request_id)
    if req is None:
        abort(404, description="Request not found")
    return req


@gdpr_bp.route("/compliance/data-subject-requests", methods=["GET"])
@login_required
@requires_role(_DPO_ROLES)
def dsr_index():
    return render_template(
        "compliance/data_subject_requests.html",
        requests=GDPRService.list_requests(_org_id()),
        request_types=REQUEST_TYPES,
        people=_org_people(),
        categories=GDPRService.personal_data_categories(_org_id()),
    )


@gdpr_bp.route("/compliance/data-subject-requests", methods=["POST"])
@login_required
@requires_role(_DPO_ROLES)
def dsr_create():
    subject_user_id = request.form.get("subject_user_id", type=int)
    category_ids = [int(v) for v in request.form.getlist("category_ids") if v.isdigit()]
    try:
        req = GDPRService.create_request(
            _org_id(),
            current_user,
            (request.form.get("request_type") or "").strip(),
            subject_user_id=subject_user_id,
            subject_reference=request.form.get("subject_reference"),
            category_ids=category_ids or None,
        )
    except DataSubjectRequestError as exc:
        flash(str(exc), "error")
        return redirect(url_for("gdpr_bp.dsr_index"))
    flash("Request recorded and scoped.", "success")
    return redirect(url_for("gdpr_bp.dsr_detail", request_id=req.id))


@gdpr_bp.route("/compliance/data-subject-requests/<int:request_id>", methods=["GET"])
@login_required
@requires_role(_DPO_ROLES)
def dsr_detail(request_id):
    req = _request_or_404(request_id)
    people = _org_people()
    subject = GDPRService.subject_in_org(_org_id(), req.user_id) if req.has_platform_account else None
    preview = None
    if req.canonical_type == "erasure" and subject is not None and not (req.erasure_evidence_json or {}).get("result"):
        preview = GDPRService.erasure_preview(_org_id(), req.user_id)
    return render_template(
        "compliance/data_subject_request_detail.html",
        req=req,
        scope=req.scope_json or {},
        people=people,
        people_by_id={p.id: p for p in people},
        subject=subject,
        preview=preview,
        evidence=GDPRService.evidence_for(_org_id(), req),
    )


def _act(request_id, action):
    req = _request_or_404(request_id)
    try:
        message = action(req)
    except DataSubjectRequestError as exc:
        flash(str(exc), "error")
    else:
        flash(message, "success")
    return redirect(url_for("gdpr_bp.dsr_detail", request_id=req.id))


@gdpr_bp.route("/compliance/data-subject-requests/<int:request_id>/assign", methods=["POST"])
@login_required
@requires_role(_DPO_ROLES)
def dsr_assign(request_id):
    def _do(req):
        item = GDPRService.assign_search(
            _org_id(), req, request.form.get("key", ""),
            request.form.get("assignee_id", type=int), current_user,
        )
        return "Search of %s assigned." % item["name"]

    return _act(request_id, _do)


@gdpr_bp.route("/compliance/data-subject-requests/<int:request_id>/complete", methods=["POST"])
@login_required
@requires_role(_DPO_ROLES)
def dsr_complete(request_id):
    def _do(req):
        item = GDPRService.complete_search(_org_id(), req, request.form.get("key", ""), current_user)
        return "Search of %s marked done." % item["name"]

    return _act(request_id, _do)


@gdpr_bp.route("/compliance/data-subject-requests/<int:request_id>/access", methods=["POST"])
@login_required
@requires_role(_DPO_ROLES)
def dsr_access(request_id):
    req = _request_or_404(request_id)
    try:
        data = GDPRService.fulfil_access(_org_id(), req, current_user)
    except DataSubjectRequestError as exc:
        flash(str(exc), "error")
        return redirect(url_for("gdpr_bp.dsr_detail", request_id=req.id))
    body = json.dumps(data, default=str, indent=2)
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=subject-access-%s.json" % req.id},
    )


@gdpr_bp.route("/compliance/data-subject-requests/<int:request_id>/erasure/review", methods=["POST"])
@login_required
@requires_role(_DPO_ROLES)
def dsr_erasure_review(request_id):
    def _do(req):
        GDPRService.review_erasure(_org_id(), req, current_user)
        return "Review recorded. The erasure can now run."

    return _act(request_id, _do)


@gdpr_bp.route("/compliance/data-subject-requests/<int:request_id>/erasure/run", methods=["POST"])
@login_required
@requires_role(_DPO_ROLES)
def dsr_erasure_run(request_id):
    def _do(req):
        if request.form.get("confirm") != "erase":
            raise DataSubjectRequestError("Tick the confirmation to run the erasure.")
        GDPRService.run_erasure(_org_id(), req, current_user)
        return "Erasure complete. The evidence is recorded below."

    return _act(request_id, _do)


@gdpr_bp.route("/compliance/personal-data-trace", methods=["GET"])
@login_required
@requires_role(_DPO_ROLES)
def dsr_trace():
    categories = GDPRService.personal_data_categories(_org_id())
    category_id = request.args.get("category_id", type=int)
    selected = next((c for c in categories if c.id == category_id), None)
    traced = GDPRService.trace_categories(_org_id(), [selected])[0] if selected else None
    return render_template(
        "compliance/personal_data_trace.html",
        categories=categories,
        selected=selected,
        traced=traced,
    )
