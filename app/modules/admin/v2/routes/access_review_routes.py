"""Access review: quarterly recertification and export investigation (R1-B88).

Blueprint ``access_review`` under ``/admin/access``.

Readers are the same people who read the audit trail
(``governance_gate_reader_required``); acting (opening and deciding a review,
scanning, restricting or dismissing an export flag) is for organisation
administrators only. Every record is read with the caller's organisation in
the predicate, and another organisation's id is answered exactly as a missing
one: 404.

* Recertification: ``AccessReviewCycle`` / ``AccessReviewItem`` hold the review;
  the grants under review are the existing ``OrgRole`` rows; "used" comes only
  from the audit trail. Removing a grant goes through
  ``rbac_service.revoke_member_access``, the one place access is reduced.
* Export investigation: flags are entries in the one approval queue, raised by
  ``export_anomaly_service``; the buttons here call the approval service.
"""

import json
import logging
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app import db
from app.decorators import governance_gate_reader_required
from app.models.access_review import (
    CYCLE_CLOSED,
    CYCLE_LIFETIME,
    CYCLE_OPEN,
    DECISION_KEPT,
    DECISION_PENDING,
    DECISION_REMOVED,
    DEFAULT_UNUSED_DAYS,
    AccessReviewCycle,
    AccessReviewItem,
)
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.user_session import UserSession
from app.modules.admin.team_routes import _require_org_id, _require_org_or_platform_admin
from app.middleware.tenant_decorators import is_platform_admin
from app.services import export_anomaly_service
from app.services.rbac_service import MemberAccessError, rbac_service
from app.utils.csv_response import csv_attachment

logger = logging.getLogger(__name__)

access_review_bp = Blueprint("access_review", __name__, url_prefix="/admin/access")

REVIEW_REASON = "access_review_removed"
_VALID_DECISIONS = (DECISION_KEPT, DECISION_REMOVED)


def _can_act(org_id):
    return is_platform_admin(current_user) or rbac_service.is_org_admin(current_user, org_id)


def _cycle_or_404(org_id, cycle_id):
    cycle = AccessReviewCycle.query.filter_by(id=cycle_id, organization_id=org_id).first()
    if cycle is None:
        abort(404)
    return cycle


def _review_members(org_id):
    """The people a review lists: the Team page's members, platform administrators aside."""
    from app.modules.account.services import invitation_service

    members, platform_admins = [], 0
    # tenant-scoping-ok: User is not TenantMixin; organization_id is in the predicate.
    for member in User.query.filter(User.organization_id == org_id).order_by(User.id).all():
        if invitation_service.is_unactivated(member):
            continue
        if is_platform_admin(member):
            platform_admins += 1
            continue
        members.append(member)
    return members, platform_admins


def _last_activity(org_id, user_id):
    return (
        db.session.query(db.func.max(AuditLog.created_at))
        .filter(AuditLog.org_predicate(org_id), AuditLog.user_id == user_id)
        .scalar()
    )


def _last_sign_in(user_id):
    # tenant-scoping-ok: UserSession is keyed by user_id and is deliberately not TenantMixin.
    return (
        db.session.query(db.func.max(UserSession.last_seen_at))
        .filter(UserSession.user_id == user_id)
        .scalar()
    )


def _open_cycle(org_id, opener_id, *, now=None):
    """Open a cycle with one item per member. Raises ``ValueError`` when one is open."""
    now = now or datetime.utcnow()
    existing = AccessReviewCycle.query.filter_by(organization_id=org_id, status=CYCLE_OPEN).first()
    if existing is not None:
        raise ValueError("A review is already open.")
    cycle = AccessReviewCycle(
        organization_id=org_id,
        opened_by_id=opener_id,
        opened_at=now,
        due_at=now + CYCLE_LIFETIME,
        unused_days=DEFAULT_UNUSED_DAYS,
        status=CYCLE_OPEN,
    )
    db.session.add(cycle)
    db.session.flush()
    cutoff = now - timedelta(days=cycle.unused_days)
    members, _ = _review_members(org_id)
    for member in members:
        last = _last_activity(org_id, member.id)
        db.session.add(
            AccessReviewItem(
                organization_id=org_id,
                cycle_id=cycle.id,
                user_id=member.id,
                org_role=rbac_service.get_user_role(org_id, member.id),
                persona=member.enterprise_role,
                last_activity_at=last,
                last_sign_in_at=_last_sign_in(member.id),
                unused=last is None or last < cutoff,
                decision=DECISION_PENDING,
            )
        )
    db.session.commit()
    AuditLog.log(
        action="access_review_open",
        table_name="access_review_cycles",
        record_id=cycle.id,
        organization_id=org_id,
        user_id=opener_id,
        extra_json={"members": len(members), "unused_days": cycle.unused_days},
    )
    return cycle


def _render_cycle(org_id, cycle, error=None, status=200):
    items = sorted(
        cycle.items,
        key=lambda i: (not i.unused, (i.user.full_name() if i.user else "").lower(), i.id),
    )
    _, platform_admins = _review_members(org_id)
    return (
        render_template(
            "admin/access/review_detail.html",
            cycle=cycle,
            items=items,
            pending=sum(1 for i in items if i.decision == DECISION_PENDING),
            platform_admins=platform_admins,
            can_act=_can_act(org_id),
            error=error,
        ),
        status,
    )


def _evidence_rows(cycle):
    for item in sorted(cycle.items, key=lambda i: i.id):
        member = item.user
        decider = item.decided_by
        yield [
            cycle.id,
            member.full_name() if member else "",
            member.email if member else "",
            item.persona or "",
            item.org_role,
            item.last_activity_at.isoformat(sep=" ") if item.last_activity_at else "",
            item.last_sign_in_at.isoformat(sep=" ") if item.last_sign_in_at else "",
            "yes" if item.unused else "no",
            item.decision,
            decider.email if decider else "",
            item.decided_at.isoformat(sep=" ") if item.decided_at else "",
            item.note or "",
        ]


_EVIDENCE_HEADER = [
    "Review", "Member", "Email", "Persona", "Organisation role",
    "Last activity in the audit trail", "Last signed in", "Unused for the review period",
    "Decision", "Decided by", "Decided at", "Note",
]


# ---------------------------------------------------------------- recertification


@access_review_bp.route("/reviews", methods=["GET"])
@login_required
@governance_gate_reader_required
def reviews():
    org_id = _require_org_id()
    cycles = (
        AccessReviewCycle.query.filter_by(organization_id=org_id)
        .order_by(AccessReviewCycle.id.desc())
        .all()
    )
    return render_template(
        "admin/access/reviews.html",
        cycles=cycles,
        has_open=any(c.is_open for c in cycles),
        can_act=_can_act(org_id),
    )


@access_review_bp.route("/reviews", methods=["POST"])
@login_required
@governance_gate_reader_required
def review_open():
    org_id = _require_org_id()
    _require_org_or_platform_admin(org_id)
    try:
        cycle = _open_cycle(org_id, current_user.id)
    except (ValueError, IntegrityError):
        db.session.rollback()
        cycles = (
            AccessReviewCycle.query.filter_by(organization_id=org_id)
            .order_by(AccessReviewCycle.id.desc())
            .all()
        )
        return (
            render_template(
                "admin/access/reviews.html",
                cycles=cycles,
                has_open=True,
                can_act=True,
                error="A review is already open. Close it before starting another.",
            ),
            409,
        )
    return redirect(url_for("access_review.review_detail", cycle_id=cycle.id))


@access_review_bp.route("/reviews/<int:cycle_id>", methods=["GET"])
@login_required
@governance_gate_reader_required
def review_detail(cycle_id):
    org_id = _require_org_id()
    return _render_cycle(org_id, _cycle_or_404(org_id, cycle_id))


@access_review_bp.route("/reviews/<int:cycle_id>/items/<int:item_id>", methods=["POST"])
@login_required
@governance_gate_reader_required
def review_decide(cycle_id, item_id):
    org_id = _require_org_id()
    _require_org_or_platform_admin(org_id)
    cycle = _cycle_or_404(org_id, cycle_id)
    item = AccessReviewItem.query.filter_by(
        id=item_id, cycle_id=cycle.id, organization_id=org_id
    ).first()
    if item is None:
        abort(404)
    decision = (request.form.get("decision") or "").strip()
    note = (request.form.get("note") or "").strip()[:1000] or None
    if decision not in _VALID_DECISIONS:
        return _render_cycle(org_id, cycle, "Choose Keep or Remove.", 400)
    if not cycle.is_open:
        return _render_cycle(org_id, cycle, "This review is closed.", 409)
    if item.decision != DECISION_PENDING:
        return _render_cycle(org_id, cycle, "That grant has already been decided.", 409)

    if decision == DECISION_REMOVED:
        if item.user_id == current_user.id:
            return _render_cycle(org_id, cycle, "You cannot remove your own access.", 400)
        try:
            rbac_service.revoke_member_access(
                org_id,
                item.user_id,
                actor_id=current_user.id,
                reason=REVIEW_REASON,
                end_sessions=True,
            )
        except MemberAccessError as exc:
            db.session.rollback()
            return _render_cycle(org_id, cycle, exc.message, exc.status if exc.status != 404 else 409)

    item.decision = decision
    item.decided_by_id = current_user.id
    item.decided_at = datetime.utcnow()
    item.note = note
    db.session.commit()
    AuditLog.log(
        action="access_item_decided",
        table_name="access_review_items",
        record_id=item.id,
        organization_id=org_id,
        user_id=current_user.id,
        extra_json={
            "cycle_id": cycle.id,
            "member_id": item.user_id,
            "decision": decision,
            "unused": item.unused,
        },
    )
    if decision == DECISION_REMOVED:
        flash("Access reduced to view only and signed out.", "success")
    else:
        flash("Access kept.", "success")
    return redirect(url_for("access_review.review_detail", cycle_id=cycle.id))


@access_review_bp.route("/reviews/<int:cycle_id>/close", methods=["POST"])
@login_required
@governance_gate_reader_required
def review_close(cycle_id):
    org_id = _require_org_id()
    _require_org_or_platform_admin(org_id)
    cycle = _cycle_or_404(org_id, cycle_id)
    if not cycle.is_open:
        return _render_cycle(org_id, cycle, "This review is already closed.", 409)
    pending = sum(1 for i in cycle.items if i.decision == DECISION_PENDING)
    if pending:
        return _render_cycle(
            org_id, cycle, "%d grant(s) still need a decision before the review can close." % pending, 409
        )
    cycle.status = CYCLE_CLOSED
    cycle.closed_at = datetime.utcnow()
    cycle.closed_by_id = current_user.id
    cycle.summary_json = {
        "total": len(cycle.items),
        "kept": sum(1 for i in cycle.items if i.decision == DECISION_KEPT),
        "removed": sum(1 for i in cycle.items if i.decision == DECISION_REMOVED),
        "unused": sum(1 for i in cycle.items if i.unused),
    }
    db.session.commit()
    AuditLog.log(
        action="access_review_close",
        table_name="access_review_cycles",
        record_id=cycle.id,
        organization_id=org_id,
        user_id=current_user.id,
        extra_json=cycle.summary_json,
    )
    flash("Review closed. The evidence pack is ready to download.", "success")
    return redirect(url_for("access_review.review_detail", cycle_id=cycle.id))


@access_review_bp.route("/reviews/<int:cycle_id>/evidence.csv", methods=["GET"])
@login_required
@governance_gate_reader_required
def review_evidence(cycle_id):
    org_id = _require_org_id()
    cycle = _cycle_or_404(org_id, cycle_id)
    if cycle.is_open:
        abort(409)
    return csv_attachment(
        "access-review-%d-evidence.csv" % cycle.id, _EVIDENCE_HEADER, _evidence_rows(cycle)
    )


# ---------------------------------------------------------------- export investigation


def _flag_or_404(org_id, flag_id):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    flag = AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.id == flag_id,
        AIChatCRUDApproval.organization_id == org_id,
        AIChatCRUDApproval.operation_type == export_anomaly_service.FLAG_OPERATION,
        AIChatCRUDApproval.entity_type == export_anomaly_service.FLAG_ENTITY,
    ).first()
    if flag is None:
        abort(404)
    return flag


def _flagged_member(org_id, flag):
    # tenant-scoping-ok: User is not TenantMixin; organization_id is in the predicate.
    return User.query.filter(User.id == flag.entity_id, User.organization_id == org_id).first()


@access_review_bp.route("/exports", methods=["GET"])
@login_required
@governance_gate_reader_required
def exports():
    org_id = _require_org_id()
    flags = []
    for flag in export_anomaly_service.open_flags(org_id):
        flags.append({"flag": flag, "member": _flagged_member(org_id, flag)})
    return render_template(
        "admin/access/exports.html",
        activity=export_anomaly_service.member_activity(org_id),
        flags=flags,
        can_act=_can_act(org_id),
    )


@access_review_bp.route("/exports/scan", methods=["POST"])
@login_required
@governance_gate_reader_required
def exports_scan():
    org_id = _require_org_id()
    _require_org_or_platform_admin(org_id)
    created = export_anomaly_service.scan_organisation(org_id)
    if created:
        flash("%d new flag(s) raised." % len(created), "success")
    else:
        flash("Nothing unusual found.", "success")
    return redirect(url_for("access_review.exports"))


def _evidence_link(member, payload):
    try:
        return url_for(
            "admin.audit_log_viewer",
            export="csv",
            action="export",
            user_email=member.email if member else "",
            date_from=payload.get("window_start", ""),
            date_to=payload.get("window_end", ""),
        )
    except Exception:  # noqa: BLE001 - the audit viewer may not be registered in this tier
        return None


@access_review_bp.route("/exports/<int:flag_id>", methods=["GET"])
@login_required
@governance_gate_reader_required
def export_flag(flag_id):
    org_id = _require_org_id()
    flag = _flag_or_404(org_id, flag_id)
    member = _flagged_member(org_id, flag)
    try:
        payload = json.loads(flag.operation_payload or "{}")
    except ValueError:
        payload = {}
    return render_template(
        "admin/access/export_flag.html",
        flag=flag,
        member=member,
        payload=payload,
        exports=payload.get("exports", []),
        is_open=flag.status.value == "pending",
        evidence_url=_evidence_link(member, payload),
        can_act=_can_act(org_id),
        is_self=member is not None and member.id == current_user.id,
    )


@access_review_bp.route("/exports/<int:flag_id>/decision", methods=["POST"])
@login_required
@governance_gate_reader_required
def export_flag_decide(flag_id):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org_id = _require_org_id()
    _require_org_or_platform_admin(org_id)
    flag = _flag_or_404(org_id, flag_id)
    decision = (request.form.get("decision") or "").strip()
    if decision not in ("restrict", "dismiss"):
        abort(400)
    service = AIChatApprovalService(user_id=current_user.id)
    if decision == "restrict":
        result = service.approve_and_execute(flag.id, approving_user_id=current_user.id)
        done = "Access reduced to view only and signed out."
    else:
        result = service.reject_approval(flag.id, reason="Dismissed: expected behaviour")
        done = "Flag dismissed. Their access is unchanged."
    if result.get("success"):
        flash(done, "success")
    else:
        flash(result.get("error") or "The flag could not be decided.", "error")
    return redirect(url_for("access_review.export_flag", flag_id=flag.id))
