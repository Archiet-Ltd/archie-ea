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
from app.modules.ai_chat.services.ai_chat_approval_service import (
    AIChatApprovalService,
)
from app.modules.enterprise_genome.services.ai_system_grounding import (
    propose_grounding_source,
)
from app.modules.enterprise_genome.services.ai_system_lineage import (
    get_data_entities_for_system,
    get_providers_for_system,
    get_systems_using_provider,
)
from app.modules.enterprise_genome.services.ai_system_obligations import (
    get_obligations_for_system,
)
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


def _approval_payload_element(approval_id: int, org_id: int):
    """The element id a grounding-source approval's payload names, or ``None``
    when no such approval exists in this organisation or its payload is not
    readable JSON.

    Read-only. The accept and refuse routes use it to answer 404 before any
    state is touched when the URL element and the approval's own element
    disagree, so an approval can only ever be decided through the element it
    was proposed for.
    """
    import json

    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.modules.enterprise_genome.services.ai_system_grounding import (
        ENTITY_GROUNDING_SOURCE,
    )

    approval = AIChatCRUDApproval.query.filter_by(
        id=approval_id,
        organization_id=org_id,
        entity_type=ENTITY_GROUNDING_SOURCE,
    ).first()
    if approval is None:
        return None
    try:
        payload = json.loads(approval.operation_payload)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload.get("element_id")


@ai_systems_genome_bp.route("/ai-systems")
@login_required
def ai_systems():
    """Render the AI Systems Register for the current org."""
    org_id = _current_org_id()
    register_html = None
    slice_data = None
    if org_id is not None:
        slice_data = build_ai_systems_slice(org_id, db.session)
        # Enrich each system with lineage and obligations data
        users_by_provider: dict = {}
        for s in slice_data.get("systems", []):
            el_id = s.get("archimate_element_id")
            if el_id:
                s["providers"] = get_providers_for_system(el_id, org_id)
                s["data_entities"] = get_data_entities_for_system(el_id, org_id)
                s["obligations"] = get_obligations_for_system(el_id, org_id)
                # For each provider, the organisation's systems that use it
                # (one query per distinct provider on the page).
                provider_systems = {}
                for p in s.get("providers", []):
                    name = p.get("name")
                    if name:
                        if name not in users_by_provider:
                            users_by_provider[name] = get_systems_using_provider(
                                name, org_id
                            )
                        provider_systems[name] = users_by_provider[name]
                s["provider_systems"] = provider_systems
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


# --- Grounding source routes -------------------------------------------------


@ai_systems_genome_bp.route(
    "/ai-systems/<int:element_id>/grounding-source/propose", methods=["POST"]
)
@login_required
@permission_required(Permission.GENERAL)
def ai_system_propose_grounding_source(element_id):
    """Propose a data entity as a grounding source for an AI system.

    Creates a pending approval record in the approval queue. The source is NOT
    applied — another user with general permission accepts it in the approval
    queue. Org-scoped: another organisation's element id is refused.
    """
    org_id = _current_org_id()
    if org_id is None:
        return redirect(url_for("ai_systems_genome.ai_systems"))

    element = _org_scoped_element(element_id, org_id)
    if element is None:
        abort(404)

    data_entity_id = request.form.get("data_entity_id", type=int)
    if not data_entity_id:
        flash("A data entity id is required to propose a grounding source.", "error")
        return redirect(url_for("ai_systems_genome.ai_systems"))

    try:
        propose_grounding_source(
            element=element,
            data_entity_id=data_entity_id,
            organization_id=org_id,
            proposed_by_user_id=current_user.id,
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception(
            "Grounding source proposal failed for org %s element %s",
            org_id, element_id,
        )
        flash("The grounding source proposal could not be recorded.", "error")

    return redirect(url_for("ai_systems_genome.ai_systems"))


@ai_systems_genome_bp.route(
    "/ai-systems/<int:element_id>/grounding-source/<int:approval_id>/accept",
    methods=["POST"],
)
@login_required
@permission_required(Permission.GENERAL)
def ai_system_accept_grounding_source(element_id, approval_id):
    """Accept a proposed grounding source.

    Reads the proposal payload from the approval record and writes the
    grounding source property onto the element's acm_properties. Org-scoped.
    Answers 404 when the approval's payload names a different element than the
    URL — an approval is decided only through its own element.
    """
    org_id = _current_org_id()
    if org_id is None:
        return redirect(url_for("ai_systems_genome.ai_systems"))

    element = _org_scoped_element(element_id, org_id)
    if element is None:
        abort(404)

    # An approval is decided through the element it was proposed for: refuse
    # with 404 when the URL element and the approval's payload element differ.
    if _approval_payload_element(approval_id, org_id) != element_id:
        abort(404)

    # Decide through the one approval service, as the approval queue does: it
    # records the approver, claims the approval once and writes the approval
    # audit trail before dispatching to accept_grounding_source.
    try:
        result = AIChatApprovalService(user_id=current_user.id).approve_and_execute(
            approval_id, current_user.id
        )
        if result.get("success"):
            flash("Grounding source approved.", "success")
        else:
            flash(
                result.get("error") or "Could not approve grounding source.",
                "error",
            )
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception(
            "Grounding source accept failed for org %s approval %s",
            org_id, approval_id,
        )
        flash("The grounding source could not be approved.", "error")

    return redirect(url_for("ai_systems_genome.ai_systems"))


@ai_systems_genome_bp.route(
    "/ai-systems/<int:element_id>/grounding-source/<int:approval_id>/refuse",
    methods=["POST"],
)
@login_required
@permission_required(Permission.GENERAL)
def ai_system_refuse_grounding_source(element_id, approval_id):
    """Refuse a proposed grounding source.

    The system stays unchanged; no property is written. Org-scoped.
    Answers 404 when the approval's payload names a different element than the
    URL — an approval is decided only through its own element.
    """
    org_id = _current_org_id()
    if org_id is None:
        return redirect(url_for("ai_systems_genome.ai_systems"))

    element = _org_scoped_element(element_id, org_id)
    if element is None:
        abort(404)

    # An approval is decided through the element it was proposed for: refuse
    # with 404 when the URL element and the approval's payload element differ.
    if _approval_payload_element(approval_id, org_id) != element_id:
        abort(404)

    try:
        result = AIChatApprovalService(user_id=current_user.id).reject_approval(
            approval_id, reason="Refused by reviewer"
        )
        if result.get("success"):
            flash("Grounding source refused.", "success")
        else:
            flash(
                result.get("error") or "Could not refuse grounding source.",
                "error",
            )
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception(
            "Grounding source refuse failed for org %s approval %s",
            org_id, approval_id,
        )
        flash("The grounding source could not be refused.", "error")

    return redirect(url_for("ai_systems_genome.ai_systems"))
