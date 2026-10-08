import logging

from flask import Blueprint, g, jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required

from app import db
from app.models.architecture_decision import ArchitectureDecision
from app.models.archimate_core import ArchiMateElement
from app.security.audit import audit_logger, AuditEventType, AuditEventSeverity

_log = logging.getLogger(__name__)

arch_decisions_bp = Blueprint("arch_decisions", __name__, url_prefix="/architecture/decisions")


def _org_id():
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        org_id = getattr(current_user, "organization_id", None)
    return int(org_id) if org_id is not None else None


def _own_elements(element_ids):
    """The caller's organisation's elements among ``element_ids``, in the order given.

    A decision is recorded against elements by id, and the ids arrive from the
    browser. Anything that is not a whole number, or is not an element of the
    caller's organisation, is dropped here rather than stored: a decision must
    never point at another organisation's element.
    """
    wanted = []
    for raw in element_ids or ():
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in wanted:
            wanted.append(value)
    org_id = _org_id()
    if not wanted or org_id is None:
        return []
    rows = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.id.in_(wanted),
            ArchiMateElement.organization_id == org_id,
        )
    ).scalars().all()
    by_id = {row.id: row for row in rows}
    return [by_id[i] for i in wanted if i in by_id]


def _posted_element_ids():
    """Element ids from the form's hidden field, kept only if they are the caller's own."""
    import json

    try:
        raw = json.loads(request.form.get("archimate_element_ids") or "[]")
    except (TypeError, ValueError):
        raw = []
    if not isinstance(raw, list):
        raw = []
    return [el.id for el in _own_elements(raw)]


def _parse_review_date(raw):
    """A posted ``YYYY-MM-DD`` review date, or None for blank/unparseable input --
    never a 500 for a date field left empty, which is the common case."""
    from datetime import datetime

    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


@arch_decisions_bp.route("/")
@login_required
def list_decisions():
    status_filter = request.args.get("status")
    phase_filter = request.args.get("phase")
    element_filter = request.args.get("element_id", type=int)
    filter_element = None
    if element_filter is not None:
        # Decisions recorded against one element: the way a decision is found
        # from the thing it governs.
        own = _own_elements([element_filter])
        filter_element = own[0] if own else None
        decisions = (
            ArchitectureDecision.affecting_elements([filter_element.id], _org_id())
            if filter_element is not None
            else []
        )
        if status_filter:
            decisions = [d for d in decisions if d.status == status_filter]
        if phase_filter:
            decisions = [d for d in decisions if d.adm_phase == phase_filter]
    else:
        query = ArchitectureDecision.query
        if status_filter:
            query = query.filter_by(status=status_filter)
        if phase_filter:
            query = query.filter_by(adm_phase=phase_filter)
        decisions = query.order_by(ArchitectureDecision.created_at.desc()).all()
    return render_template(
        "architecture_decisions/list.html",
        decisions=decisions,
        status_filter=status_filter,
        phase_filter=phase_filter,
        element_filter=element_filter,
        filter_element=filter_element,
    )


@arch_decisions_bp.route("/new", methods=["GET", "POST"])
@login_required
def create_decision():
    if request.method == "POST":
        decision = ArchitectureDecision(
            decision_id=ArchitectureDecision.next_decision_id(),
            title=request.form.get("title"),
            status=request.form.get("status", "proposed"),
            adm_phase=request.form.get("adm_phase"),
            context=request.form.get("context"),
            decision=request.form.get("decision"),
            consequences=request.form.get("consequences"),
            alternatives=request.form.get("alternatives"),
            archimate_element_ids=_posted_element_ids(),
            created_by_id=current_user.id,
            review_date=_parse_review_date(request.form.get("review_date")),
        )
        db.session.add(decision)
        db.session.commit()
        try:
            audit_logger.log_event(
                AuditEventType.DATA_MODIFICATION,
                AuditEventSeverity.MEDIUM,
                "create",
                resource_type="architecture_decision",
                resource_id=str(decision.id),
                details={"decision_id": decision.decision_id, "title": decision.title,
                         "user_id": current_user.id},
                compliance_flags=["SOC2"],
            )
        except Exception as _exc:
            _log.warning("audit log failed for create_decision", _exc)
        flash(f"Architecture Decision {decision.decision_id} created", "success")
        return redirect(url_for("arch_decisions.view_decision", decision_id=decision.id))
    # Opened from an element ("Record a decision"): start with that element
    # already chosen, provided it is one of the caller's own.
    preselected = _own_elements([request.args.get("element_id")])
    return render_template(
        "architecture_decisions/form.html", decision=None, selected_elements=preselected
    )


@arch_decisions_bp.route("/<int:decision_id>")
@login_required
def view_decision(decision_id):
    decision = ArchitectureDecision.query.get_or_404(decision_id)
    elements = _own_elements(decision.archimate_element_ids)
    return render_template(
        "architecture_decisions/detail.html", decision=decision, elements=elements
    )


@arch_decisions_bp.route("/<int:decision_id>/edit", methods=["GET", "POST"])
@login_required
def edit_decision(decision_id):
    decision = ArchitectureDecision.query.get_or_404(decision_id)
    if request.method == "POST":
        decision.title = request.form.get("title") or decision.title
        decision.status = request.form.get("status", "proposed")
        decision.adm_phase = request.form.get("adm_phase")
        decision.context = request.form.get("context")
        decision.decision = request.form.get("decision")
        decision.consequences = request.form.get("consequences")
        decision.alternatives = request.form.get("alternatives")
        decision.archimate_element_ids = _posted_element_ids()
        decision.review_date = _parse_review_date(request.form.get("review_date"))
        db.session.commit()
        try:
            audit_logger.log_event(
                AuditEventType.DATA_MODIFICATION,
                AuditEventSeverity.MEDIUM,
                "update",
                resource_type="architecture_decision",
                resource_id=str(decision.id),
                details={"decision_id": decision.decision_id, "title": decision.title,
                         "status": decision.status, "user_id": current_user.id},
                compliance_flags=["SOC2"],
            )
        except Exception as _exc:
            _log.warning("audit log failed for edit_decision", _exc)
        flash("Decision updated", "success")
        return redirect(url_for("arch_decisions.view_decision", decision_id=decision.id))
    elements = _own_elements(decision.archimate_element_ids)
    return render_template(
        "architecture_decisions/form.html", decision=decision, selected_elements=elements
    )


@arch_decisions_bp.route("/<int:decision_id>/delete", methods=["POST"])
@login_required
def delete_decision(decision_id):
    from app.models.adr import ArchitectureDecisionRecord
    from app.models.solution_architect_models import SolutionADRLink

    decision = ArchitectureDecision.query.get_or_404(decision_id)
    decision_ref = decision.decision_id
    decision_title = decision.title
    # The legacy register stays as read history (lead ruling): a
    # paired ArchitectureDecisionRecord is orphaned, not deleted, so this
    # canonical delete never fails on architecture_decision_records_retired_into_id_fkey.
    paired_records = db.session.execute(
        db.select(ArchitectureDecisionRecord).where(
            ArchitectureDecisionRecord.retired_into_id == decision.id
        )
    ).scalars().all()
    for record in paired_records:
        record.retired_into_id = None
    # retired_into_id is a bare ForeignKey with no relationship(), so the ORM's
    # unit-of-work has no dependency rule between this update and the delete
    # below; without an explicit flush, it can order the DELETE first and hit
    # architecture_decision_records_retired_into_id_fkey.
    db.session.flush()
    # SolutionADRLink.adr_id is NOT NULL with no relationship()/cascade, so a
    # decision with a traceability link to a solution analysis session's
    # workbench or chat history fails the same way on
    # solution_adr_links_adr_id_fkey. The link is metadata about this
    # decision, not an independent record, so it is deleted outright rather
    # than orphaned.
    for link in db.session.execute(
        db.select(SolutionADRLink).where(SolutionADRLink.adr_id == decision.id)
    ).scalars().all():
        db.session.delete(link)
    db.session.flush()
    db.session.delete(decision)
    db.session.commit()
    try:
        audit_logger.log_event(
            AuditEventType.DATA_MODIFICATION,
            AuditEventSeverity.HIGH,
            "delete",
            resource_type="architecture_decision",
            resource_id=str(decision_id),
            details={"decision_id": decision_ref, "title": decision_title,
                     "user_id": current_user.id},
            compliance_flags=["SOC2"],
        )
    except Exception:
        _log.warning("audit log failed for delete_decision", exc_info=True)
    flash(f"Architecture Decision {decision_ref} deleted", "success")
    return redirect(url_for("arch_decisions.list_decisions"))



@login_required
def element_search():
    """Search ArchiMate elements for the element picker."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify([])
    results = (
        ArchiMateElement.query.filter(ArchiMateElement.name.ilike(f"%{q}%"))
        .limit(20)
        .all()
    )
    return jsonify(
        [
            {
                "id": el.id,
                "name": el.name,
                "layer": el.layer if hasattr(el, "layer") else None,
                "element_type": el.element_type if hasattr(el, "element_type") else None,
            }
            for el in results
        ]
    )


@arch_decisions_bp.route("/due-for-review")
@login_required
def due_for_review():
    """Decisions whose review date has arrived with no outcome recorded yet:
    a vendor contract renewal, a deviation granted "for now" -- things an
    architect promised to look at again, listed so that promise is kept
    rather than relying on memory.
    """
    decisions = ArchitectureDecision.due_for_review(_org_id())
    return render_template(
        "architecture_decisions/due_for_review.html", decisions=decisions
    )


@arch_decisions_bp.route("/<int:decision_id>/record-outcome", methods=["GET", "POST"])
@login_required
def record_outcome(decision_id):
    decision = ArchitectureDecision.query.get_or_404(decision_id)
    if request.method == "POST":
        outcome = request.form.get("review_outcome", "").strip()
        if not outcome:
            flash("Describe the outcome of this review before recording it", "error")
            return redirect(url_for("arch_decisions.record_outcome", decision_id=decision.id))
        decision.record_review_outcome(outcome, current_user.id)
        # A review that found the decision needs looking at again later sets
        # its own next review_date in the same form; one left blank means
        # this decision is not due again.
        decision.review_date = _parse_review_date(request.form.get("next_review_date"))
        db.session.commit()
        try:
            audit_logger.log_event(
                AuditEventType.DATA_MODIFICATION,
                AuditEventSeverity.MEDIUM,
                "update",
                resource_type="architecture_decision",
                resource_id=str(decision.id),
                details={"decision_id": decision.decision_id, "action": "record_review_outcome",
                         "user_id": current_user.id},
                compliance_flags=["SOC2"],
            )
        except Exception:
            _log.warning("audit log failed for record_outcome", exc_info=True)
        flash("Review outcome recorded", "success")
        return redirect(url_for("arch_decisions.view_decision", decision_id=decision.id))
    return render_template(
        "architecture_decisions/record_outcome.html", decision=decision
    )


@arch_decisions_bp.route("/precedent-search")
@login_required
def precedent_search():
    """Precedent search over the organisation's decisions by text and, when an
    element is given, narrowed to decisions recorded against it: what did we
    decide last time something like this came up.
    """
    query_text = request.args.get("q", "").strip()
    element_filter = request.args.get("element_id", type=int)
    element_ids = [element_filter] if element_filter is not None else None
    results = ArchitectureDecision.precedent_search(query_text, _org_id(), element_ids=element_ids)
    return render_template(
        "architecture_decisions/precedent_search.html",
        query_text=query_text, element_id=element_filter, results=results,
    )
