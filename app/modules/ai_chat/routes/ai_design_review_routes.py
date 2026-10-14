"""R1-B55: AI design conformance review -- submit an AI design, see its
deviations from the pattern list, the fencing rule and the oversight
requirements, and record the board's decision.

The decision is an ``ArchitectureDecision`` row linked to the AI system (see
``ai_design_conformance_service``); there is no second decision store.

Who may do what:
  * read the list and a review, and submit a design: the "governance" section
    (the architects, board members, CTO, security, data, compliance and risk);
  * record the decision: the board member persona (platform administrators are
    admitted by ``requires_role``), and never the person who submitted it.
"""

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from app.decorators.requires_role import requires_role
from app.modules.ai_chat.services import ai_design_conformance_service as svc
from app.utils.role_access import can_access_section

ai_design_review_bp = Blueprint(
    "ai_design_review", __name__, url_prefix="/architecture/ai-design-reviews"
)


def _org_id():
    return getattr(g, "current_org_id", None)


def _guard():
    if not can_access_section(current_user, "governance"):
        return render_template("errors/403.html"), 403
    return None


def _tri(raw):
    """``"yes"`` / ``"no"`` -> True / False; anything else (blank, absent) is
    unanswered, never a guess."""
    return {"yes": True, "no": False}.get((raw or "").strip().lower())


@ai_design_review_bp.route("/")
@login_required
def index():
    guard = _guard()
    if guard:
        return guard
    org_id = _org_id()
    from app.models.models import ArchiMateElement
    from app.modules.enterprise_genome.services.ai_system_profile import is_ai_system

    systems = []
    if org_id is not None:
        systems = [
            e for e in ArchiMateElement.query.filter_by(
                organization_id=org_id, type="ApplicationComponent"
            ).order_by(ArchiMateElement.name).all()
            if is_ai_system(e)
        ]
    return render_template(
        "ai_chat/ai_design_review/list.html",
        reviews=svc.list_reviews(org_id),
        systems=systems,
        patterns=svc.APPROVED_PATTERNS,
    )


@ai_design_review_bp.route("/submit", methods=["POST"])
@login_required
def submit():
    guard = _guard()
    if guard:
        return guard
    org_id = _org_id()
    if org_id is None:
        abort(403)
    try:
        row = svc.submit_design(
            element_id=request.form.get("ai_system_id", type=int),
            organization_id=org_id,
            submitter_id=current_user.id,
            title=request.form.get("title"),
            context=request.form.get("context"),
            declaration={
                "pattern": request.form.get("pattern"),
                "ingests_untrusted_content": _tri(request.form.get("ingests_untrusted_content")),
                "untrusted_input_fenced": _tri(request.form.get("untrusted_input_fenced")),
            },
        )
        db.session.commit()
        flash("Design submitted for review.", "success")
        return redirect(url_for("ai_design_review.detail", review_id=row.id))
    except svc.DesignReviewError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("ai_design_review.index"))


@ai_design_review_bp.route("/<int:review_id>")
@login_required
def detail(review_id):
    guard = _guard()
    if guard:
        return guard
    org_id = _org_id()
    review = svc.get_review(review_id, org_id)
    if review is None:
        abort(404)
    from app.modules.ai_chat.services.ai_evaluation_service import scoped_ai_system
    from app.modules.enterprise_genome.services.ai_system_profile import profile_from_element

    element = scoped_ai_system(review.ai_system_id, org_id)
    declaration = (review.ai_design_review or {}).get("declaration") or {}
    if review.status in svc.OPEN_STATUSES:
        deviations = svc.review_deviations(element, declaration) if element else []
    else:
        deviations = (review.ai_design_review or {}).get("deviations_at_decision") or []
    return render_template(
        "ai_chat/ai_design_review/detail.html",
        review=review,
        system=profile_from_element(element) if element else None,
        declaration=declaration,
        deviations=deviations,
        decided=review.status not in svc.OPEN_STATUSES,
        can_decide=_may_decide(current_user, review),
    )


def _may_decide(user, review):
    from app.middleware.tenant_decorators import is_platform_admin
    from app.utils.role_access import get_user_role

    if review.created_by_id == getattr(user, "id", None):
        return False
    return is_platform_admin(user) or get_user_role(user) == "arb_member"


@ai_design_review_bp.route("/<int:review_id>/decision", methods=["POST"])
@login_required
@requires_role(["arb_member"])
def decision(review_id):
    org_id = _org_id()
    if org_id is None:
        abort(403)
    outcome = {"accept": svc.STATUS_ACCEPTED, "reject": svc.STATUS_REJECTED}.get(
        (request.form.get("decision") or "").strip().lower()
    )
    try:
        svc.record_decision(
            decision_id=review_id,
            organization_id=org_id,
            decider_id=current_user.id,
            outcome=outcome,
            rationale=request.form.get("rationale"),
        )
        db.session.commit()
        # Written only after the decision committed: a refused decision leaves
        # no "success" trail.
        from app.models.audit_log import AuditLog

        AuditLog.log(
            action="ai_design_decision",
            table_name="architecture_decisions",
            record_id=review_id,
            organization_id=org_id,
            user_id=current_user.id,
            new_value={"decision": outcome},
        )
        flash("Decision recorded.", "success")
    except svc.DesignReviewError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ai_design_review.detail", review_id=review_id))
