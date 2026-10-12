"""Routes for the AI Systems genome projection (PILLAR 6).

Blueprint: ``ai_systems_genome_bp``, url_prefix ``/genome``.

  GET  /genome/ai-systems                   the AI Systems Register
  POST /genome/ai-systems/seed              seed the org's AI systems, incl.
                                            Entelim's own copilot
                                            (self-modelling), then re-render.
  POST /genome/ai-systems/<id>/release      release one system (admin-only);
                                            refuses with the named reason when
                                            a high-risk system has no accepted
                                            assessment.
  POST /genome/ai-systems/<id>/assess       queue an assessment proposal for
                                            one system (org-scoped).
  POST /genome/ai-systems/<id>/classify     queue a risk classification proposal.
  GET/POST/DELETE /genome/api/ai-systems/<id>/owners[/<owner_id>]
                                            read, set and remove owners.

Every route acts only on AI-system elements of the caller's organisation; any
other element id answers 404.

Login-gated, org-scoped (the tenant middleware scopes the ArchiMateElement reads
by ``g.current_org_id``), CSP-safe form WITH ``csrf_token``. The blueprint is
registered non-fatally from ``_bootstrap/blueprints.py``.
"""

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from app.decorators import admin_required
from app._decorators_base import permission_required
from app.models import Permission
from app.modules.enterprise_genome.emit.ai_systems_register import emit_ai_systems_register
from app.modules.enterprise_genome.services.ai_systems_seed import (
    register_ai_system,
    seed_archie_copilot,
)
from app.modules.enterprise_genome.services.ai_systems_slice import build_ai_systems_slice

logger = logging.getLogger(__name__)

ai_systems_genome_bp = Blueprint("ai_systems_genome", __name__, url_prefix="/genome")


def _current_org_id():
    return getattr(g, "current_org_id", None)


def _org_scoped_element(element_id: int, org_id: int):
    """Load one AI-system element scoped to *org_id*, or ``None`` if it does
    not belong to this organisation — never load, name or change another
    organisation's system."""
    from app.models.models import ArchiMateElement

    if org_id is None:
        return None
    from app.modules.enterprise_genome.services.ai_system_profile import is_ai_system

    element = ArchiMateElement.query.filter_by(
        id=element_id,
        organization_id=org_id,
    ).first()
    return element if element is not None and is_ai_system(element) else None


def _wants_json() -> bool:
    """True for API clients. A browser form submit sends ``*/*`` among its
    accepted types and must get a redirect, not JSON."""
    return request.is_json or request.accept_mimetypes.best == "application/json"


def _may_release(user, org_id) -> bool:
    """The same predicate the ``@admin_required`` release route enforces:
    Permission.ADMINISTER plus platform-admin or active-org admin authority."""
    if not getattr(user, "is_authenticated", False):
        return False
    if not user.can(Permission.ADMINISTER):
        return False
    from app.middleware.tenant_decorators import is_platform_admin
    from app.services.rbac_service import rbac_service

    return is_platform_admin(user) or rbac_service.is_org_admin(user, org_id)


def _may_write(user) -> bool:
    """The predicate ``@permission_required(Permission.GENERAL)`` enforces on
    the classify, assess and owner routes."""
    return getattr(user, "is_authenticated", False) and user.can(Permission.GENERAL)


@ai_systems_genome_bp.route("/ai-systems")
@login_required
def ai_systems():
    """Render the AI Systems Register for the current org."""
    org_id = _current_org_id()
    register_html = None
    slice_data = None
    if org_id is not None:
        slice_data = build_ai_systems_slice(org_id, db.session)
        register_html = emit_ai_systems_register(
            slice_data,
            can_release=_may_release(current_user, org_id),
            can_write=_may_write(current_user),
        )
    return render_template(
        "enterprise_genome/ai_systems.html",
        register_html=register_html,
        slice_data=slice_data,
    )


@ai_systems_genome_bp.route("/ai-systems/seed", methods=["POST"])
@login_required
def ai_systems_seed():
    """Seed the org's AI systems, including Entelim's own copilot (self-model).

    Idempotent — re-running updates the existing elements in place. Also seeds a
    couple of illustrative estate systems so the register is not empty on a
    fresh org; each is a real, modelled ArchiMateElement, not fabricated UI data.
    """
    org_id = _current_org_id()
    if org_id is None:
        return redirect(url_for("ai_systems_genome.ai_systems"))

    try:
        # Entelim's own copilot — honest self-model.
        seed_archie_copilot(db.session, org_id)

        # Illustrative estate AI systems (real modelled elements).
        register_ai_system(
            db.session,
            org_id,
            name="Fraud Scoring Engine",
            provider="openai",
            model_id="gpt-4o",
            purpose="Real-time transaction fraud scoring.",
            autonomy_level="supervised-autonomous",
            data_sensitivity="regulated",
            approval_gate=False,
            human_review=False,
        )
        register_ai_system(
            db.session,
            org_id,
            name="Legacy Ticket Classifier",
            provider="anthropic",
            model_id="claude-3-5-sonnet-20241022",  # on the retired denylist  stale-model-ok: demo AI system intentionally on a retired model to exercise the retired-model risk flag
            purpose="Routes inbound support tickets to queues.",
            autonomy_level="assisted",
            data_sensitivity="internal",
            approval_gate=True,
            human_review=True,
        )
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("AI systems seed failed for org %s", org_id)

    return redirect(url_for("ai_systems_genome.ai_systems"))


@ai_systems_genome_bp.route("/ai-systems/<int:element_id>/release", methods=["POST"])
@admin_required
def ai_system_release(element_id):
    """Release one AI system.

    Limited to the administrator role — unlike the approval-queue accept that
    records the classification and the assessment, which any user with general
    permission may perform. Refuses with the named
    reason when the gate is shut and leaves the system unreleased: HTTP 409
    JSON to an API client, a redirect back to the register with the reason
    flashed to a browser form. The refusal message is exactly what the
    register page shows for the system.
    """
    org_id = _current_org_id()
    if org_id is None:
        return redirect(url_for("ai_systems_genome.ai_systems"))

    from app.modules.enterprise_genome.services.ai_system_classification import (
        mark_released,
    )

    element = _org_scoped_element(element_id, org_id)
    if element is None:
        abort(404)

    result = mark_released(element, org_id)
    if not result.get("success"):
        reason = result.get("reason", "Release refused")
        db.session.rollback()
        if _wants_json():
            return jsonify(
                {"success": False, "error": reason}
            ), 409
        flash(reason, "error")
        return redirect(url_for("ai_systems_genome.ai_systems"))

    db.session.commit()
    return redirect(url_for("ai_systems_genome.ai_systems"))


@ai_systems_genome_bp.route("/ai-systems/<int:element_id>/assess", methods=["POST"])
@login_required
@permission_required(Permission.GENERAL)
def ai_system_attach_assessment(element_id):
    """Queue an assessment proposal for one AI system.

    Org-scoped like the other routes here: another organisation's element id
    is refused. The proposal lands in the same approval queue the
    classification uses; another user with general permission accepts it in
    the approval queue (the accepted approval row IS the record — no new
    table).
    """
    org_id = _current_org_id()
    if org_id is None:
        return redirect(url_for("ai_systems_genome.ai_systems"))

    from app.modules.enterprise_genome.services.ai_system_classification import (
        propose_assessment,
    )

    element = _org_scoped_element(element_id, org_id)
    if element is None:
        abort(404)

    try:
        propose_assessment(
            element=element,
            organization_id=org_id,
            proposed_by_user_id=current_user.id,
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("AI system assessment proposal failed for org %s", org_id)
        flash("The assessment proposal could not be recorded.", "error")

    return redirect(url_for("ai_systems_genome.ai_systems"))


@ai_systems_genome_bp.route("/ai-systems/<int:element_id>/classify", methods=["POST"])
@login_required
@permission_required(Permission.GENERAL)
def ai_system_classify(element_id):
    """Propose a risk classification for one AI system.

    Accepts ``risk_class`` from the form, validates it against the four EU AI
    Act risk tiers, and creates a pending approval-queue record. Org-scoped:
    another organisation's element id is refused.
    """
    from app.modules.enterprise_genome.services.ai_system_classification import (
        RISK_TIERS,
        propose_classification,
    )

    org_id = _current_org_id()
    if org_id is None:
        return redirect(url_for("ai_systems_genome.ai_systems"))

    element = _org_scoped_element(element_id, org_id)
    if element is None:
        abort(404)

    risk_class = request.form.get("risk_class", "").strip()
    if risk_class not in RISK_TIERS:
        flash(f"Invalid risk tier '{risk_class}'. Must be one of {', '.join(RISK_TIERS)}", "error")
        return redirect(url_for("ai_systems_genome.ai_systems"))

    try:
        propose_classification(
            element=element,
            proposed_risk_class=risk_class,
            organization_id=org_id,
            proposed_by_user_id=current_user.id,
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("AI system classification proposal failed for org %s", org_id)
        flash("The classification proposal could not be recorded.", "error")

    return redirect(url_for("ai_systems_genome.ai_systems"))


# --- Owner API for AI systems (reuses ApplicationOwner with element_type="ai_system") ---


@ai_systems_genome_bp.route("/api/ai-systems/<int:element_id>/owners", methods=["GET"])
@login_required
def ai_system_owners(element_id):
    """Get owners for an AI system element."""
    from app.models.application_owner import ApplicationOwner

    org_id = _current_org_id()
    if org_id is None:
        return jsonify({"owners": []})

    element = _org_scoped_element(element_id, org_id)
    if element is None:
        return jsonify({"message": "AI system not found"}), 404

    rows = ApplicationOwner.get_display_rows_for_element("ai_system", element_id, org_id)
    return jsonify({"owners": rows})


@ai_systems_genome_bp.route("/api/ai-systems/<int:element_id>/owners", methods=["POST"])
@login_required
@permission_required(Permission.GENERAL)
def ai_system_set_owner(element_id):
    """Set an owner for an AI system element (a new primary replaces the old)."""
    from app.services.element_ownership_service import set_element_owner

    org_id = _current_org_id()
    if org_id is None:
        return jsonify({"message": "No organisation context"}), 400

    element = _org_scoped_element(element_id, org_id)
    if element is None:
        return jsonify({"message": "AI system not found"}), 404

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"message": "user_id is required"}), 400

    try:
        record = set_element_owner(
            element_type="ai_system",
            element_id=element_id,
            user_id=user_id,
            organization_id=org_id,
            ownership_type=data.get("ownership_type", "primary"),
            assigned_by=current_user.id,
            replace_primary=True,
        )
    except ValueError as exc:  # includes CrossOrganisationElementOwner
        db.session.rollback()
        return jsonify({"message": str(exc)}), 400
    return jsonify({"owner": record.to_dict()}), 201


@ai_systems_genome_bp.route("/api/ai-systems/<int:element_id>/owners/<int:owner_id>", methods=["DELETE"])
@login_required
@permission_required(Permission.GENERAL)
def ai_system_delete_owner(element_id, owner_id):
    """Remove an owner record from an AI system element."""
    from app.models.application_owner import ApplicationOwner

    org_id = _current_org_id()
    if org_id is None:
        return jsonify({"message": "No organisation context"}), 400

    if _org_scoped_element(element_id, org_id) is None:
        return jsonify({"message": "AI system not found"}), 404

    record = ApplicationOwner.query.filter_by(
        id=owner_id,
        element_type="ai_system",
        element_id=element_id,
        organization_id=org_id,
    ).first()
    if record is None:
        return jsonify({"message": "Owner record not found"}), 404

    db.session.delete(record)
    db.session.commit()
    return "", 204
