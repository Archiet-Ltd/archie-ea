"""
ADR (Architecture Decision Record) Routes

Provides UI for managing architecture decisions with ARB workflow.

Routes:
- GET /architecture/adrs - Redirects to the canonical `arch_decisions` listing
- GET /architecture/adrs/new - Redirects to the canonical creation form
- POST /architecture/adrs - Create new ADR
- GET /architecture/adrs/<id> - Redirects to the canonical detail page
- GET /architecture/adrs/<id>/edit - Redirects to the canonical edit form

None of this blueprint's GET *pages* render their own template any more. All
four pointed at `app/templates/architecture/adrs/`, a directory that does not
exist in the tree, so each one raised TemplateNotFound and returned 500. The
`arch_decisions` blueprint (app/main/routes_architecture_decisions.py) serves
the same `architecture_decisions` table through the TenantMixin mapping and
has real templates, so it is both the working and the tenant-safe surface —
these URLs are kept as redirects into it rather than deleted, so existing
links and bookmarks keep working.
- POST /architecture/adrs/<id> - Retired: redirects to the canonical edit form (see `update_adr`)
- POST /architecture/adrs/<id>/approve - Approve ADR
- POST /architecture/adrs/<id>/reject - Reject ADR

architecture_decisions is the only writer (lead ruling): approve and
reject already wrote only the canonical `ArchitectureDecision` row through
`ADRService`, never `ArchitectureDecisionRecord`, so they needed no change.
`update_adr` did duplicate `arch_decisions.edit_decision` over that same
canonical row through a second code path and is now a redirect, like the
GET routes above it.
"""

import logging

from flask import Blueprint, abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from app import db
from app.decorators import admin_required
from app.models.adr import ArchitectureDecisionRecord
from app.services.adr_service import ADRService

logger = logging.getLogger(__name__)

adr_bp = Blueprint("adrs", __name__, url_prefix="/architecture/adrs")


@adr_bp.route("/", methods=["GET"])
@login_required
def list_adrs():
    """Redirect to the canonical Architecture Decisions list (S-11).

    Two listings existed over the *same* `architecture_decisions` table:
    this one and `arch_decisions.list_decisions`
    (app/main/routes_architecture_decisions.py). They are backed by two
    model classes mapping that table via `extend_existing` —
    `app/models/architecture_decisions.py:ArchitectureDecision`, used here
    through ADRService, is a plain `db.Model` with **no TenantMixin**, so
    this listing was not tenant-filtered; `app/models/architecture_decision.py`
    (singular), used by `arch_decisions`, carries TenantMixin. That makes
    `arch_decisions.list_decisions` the canonical, tenant-safe listing, and
    it is the one every template links to — nothing linked here.

    The ADR module's remaining routes (create/detail/edit/approve/reject/
    statistics) are untouched; only the duplicate *listing* is retired.
    """
    return redirect(url_for("arch_decisions.list_decisions", **request.args.to_dict()))


@adr_bp.route("/new", methods=["GET"])
@login_required
def new_adr():
    """Redirect to the canonical Architecture Decision creation form.

    This view used to render `architecture/adrs/form.html`, a template that
    does not exist anywhere in the tree — so every GET /architecture/adrs/new
    raised TemplateNotFound and 500'd. There is nothing to restore: the form
    was never shipped, and the duplicate *listing* on this blueprint was
    already retired in favour of `arch_decisions` (see `list_adrs` above),
    whose `architecture_decisions/form.html` is the real, tenant-safe
    creation form over the same `architecture_decisions` table. Sending the
    URL there keeps the entry point working instead of leaving a dead one.
    """
    return redirect(url_for("arch_decisions.create_decision"))


@adr_bp.route("/", methods=["POST"])
@login_required
def create_adr():
    """Create a new ADR."""
    try:
        # Parse alternatives and constraints from form
        alternatives = []
        constraints = []
        
        # Get dynamic form fields (alternatives_*)
        for key in request.form:
            if key.startswith("alt_name_"):
                idx = key.split("_")[-1]
                alt = {
                    "name": request.form.get(f"alt_name_{idx}", ""),
                    "pros": request.form.get(f"alt_pros_{idx}", "").split("\n"),
                    "cons": request.form.get(f"alt_cons_{idx}", "").split("\n"),
                    "rejected_reason": request.form.get(f"alt_reason_{idx}", "")
                }
                alternatives.append(alt)
            elif key.startswith("constraint_name_"):
                idx = key.split("_")[-1]
                constraint = {
                    "constraint_name": request.form.get(f"constraint_name_{idx}", ""),
                    "impact": request.form.get(f"constraint_impact_{idx}", "")
                }
                constraints.append(constraint)
        
        adr = ADRService.create_adr(
            solution_id=int(request.form.get("solution_id", 0)) or None,
            title=request.form["title"],
            context=request.form["context"],
            decision=request.form["decision"],
            rationale=request.form["rationale"],
            decision_type=request.form.get("decision_type", "technology_choice"),
            alternatives=alternatives,
            constraints=constraints,
            consequences=request.form.get("consequences"),
            decided_by_id=current_user.id
        )
        
        flash(f"ADR '{adr.title}' created successfully", "success")
        return redirect(url_for("adrs.view_adr", adr_id=adr.id))
        
    except Exception as e:
        logger.error(f"Failed to create ADR: {e}", exc_info=True)
        flash(f"Failed to create ADR: {str(e)}", "error")
        return redirect(url_for("adrs.new_adr"))


@adr_bp.route("/<int:adr_id>", methods=["GET"])
@login_required
def view_adr(adr_id: int):
    """Redirect to the canonical Architecture Decision detail page.

    Same defect and same fix as `new_adr` above: this rendered
    `architecture/adrs/detail.html`, which does not exist, so every request
    raised TemplateNotFound. The id carries across unchanged — both
    `ArchitectureDecision` classes map the *same* `architecture_decisions`
    table via `extend_existing`, so `adr_id` is the same row `arch_decisions`
    addresses as `decision_id`.

    Redirecting also closes a tenancy hole rather than just a 500: the
    `ADRService.get_adr` lookup this replaces goes through the **non-**
    TenantMixin mapping of that table, so it was never org-scoped, while
    `arch_decisions.view_decision` resolves the row through the TenantMixin
    mapping and 404s a foreign id.
    """
    return redirect(url_for("arch_decisions.view_decision", decision_id=adr_id))


@adr_bp.route("/records/<int:adr_id>", methods=["GET"])
@login_required
def view_record(adr_id: int):
    """This record's own detail, plus, when paired, the one canonical
    register's id so a caller can also reach it at
    `arch_decisions.view_decision`.

    architecture_decisions is the only writer (lead ruling): once
    this record is paired (`retired_into_id` set), its shared fields
    (title/status/context/decision/rationale/consequences) are a frozen
    snapshot from pairing time, not live state -- the canonical register at
    `canonical_decision_url` is what changes after that. It stays the
    system of record only for the review-board-only fields the canonical
    register has no columns for (capability/process links,
    governance_decision_id, implementation_plan, risk_register,
    cost_analysis, arb_review, decision_matrix and the rest), which this
    consolidation never touches.

    Redirects were not possible here (this is a JSON API, not a page GET) --
    the pairing is surfaced in the response instead, completing "ADR routes
    point at the one register" for the one route that still reads
    architecture_decision_records directly.
    """
    adr = db.session.execute(
        db.select(ArchitectureDecisionRecord).where(
            ArchitectureDecisionRecord.id == adr_id,
            ArchitectureDecisionRecord.organization_id == current_user.organization_id,
        )
    ).scalar_one_or_none()
    if adr is None:
        abort(404)
    payload = adr.to_dict(include_content=True)
    payload["canonical_decision_id"] = adr.retired_into_id
    payload["canonical_decision_url"] = (
        url_for("arch_decisions.view_decision", decision_id=adr.retired_into_id)
        if adr.retired_into_id
        else None
    )
    payload["historical_snapshot"] = adr.retired_into_id is not None
    return jsonify({"adr": payload})


@adr_bp.route("/<int:adr_id>/edit", methods=["GET"])
@login_required
def edit_adr(adr_id: int):
    """Redirect to the canonical Architecture Decision edit form.

    `architecture/adrs/form.html` does not exist (see `new_adr`), so this
    500'd on every request. `arch_decisions.edit_decision` renders the real
    `architecture_decisions/form.html` over the same row, tenant-scoped.
    """
    return redirect(url_for("arch_decisions.edit_decision", decision_id=adr_id))


@adr_bp.route("/<int:adr_id>", methods=["POST"])
@login_required
def update_adr(adr_id: int):
    """Retired: redirect to the canonical Architecture Decision edit form.

    Same defect and same fix as `edit_adr`/`view_adr` below: this used to
    write core fields through `ADRService.update_adr` straight onto the
    canonical `ArchitectureDecision` row, a second writable implementation
    of the same job `arch_decisions.edit_decision` already does over the
    same row. architecture_decisions is the only writer (lead ruling) --
    not just the only table, the one code path -- so this route
    no longer processes form data itself.
    """
    return redirect(url_for("arch_decisions.edit_decision", decision_id=adr_id))


@adr_bp.route("/<int:adr_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_adr(adr_id: int):
    """Approve an ADR (admin only)."""
    try:
        adr = ADRService.approve_adr(adr_id, current_user.id)
        flash(f"ADR '{adr.title}' approved", "success")
        
        if request.is_json:
            return jsonify({"success": True, "adr": adr.to_dict()})
        return redirect(url_for("adrs.view_adr", adr_id=adr.id))
        
    except Exception as e:
        logger.error(f"Failed to approve ADR {adr_id}: {e}", exc_info=True)
        
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"Failed to approve ADR: {str(e)}", "error")
        return redirect(url_for("adrs.view_adr", adr_id=adr_id))


@adr_bp.route("/<int:adr_id>/reject", methods=["POST"])
@login_required
@admin_required
def reject_adr(adr_id: int):
    """Reject an ADR (admin only)."""
    try:
        rejection_reason = request.form.get("rejection_reason") or request.json.get("rejection_reason")
        
        if not rejection_reason:
            raise ValueError("Rejection reason is required")
        
        adr = ADRService.reject_adr(adr_id, rejection_reason)
        flash(f"ADR '{adr.title}' rejected", "warning")
        
        if request.is_json:
            return jsonify({"success": True, "adr": adr.to_dict()})
        return redirect(url_for("adrs.view_adr", adr_id=adr.id))
        
    except Exception as e:
        logger.error(f"Failed to reject ADR {adr_id}: {e}", exc_info=True)
        
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"Failed to reject ADR: {str(e)}", "error")
        return redirect(url_for("adrs.view_adr", adr_id=adr_id))


@adr_bp.route("/statistics", methods=["GET"])
@login_required
def get_statistics():
    """Get ADR statistics (API endpoint)."""
    stats = ADRService.get_adr_statistics()
    return jsonify(stats)
