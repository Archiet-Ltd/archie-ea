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

Login-gated, org-scoped (the tenant middleware scopes the ArchiMateElement reads
by ``g.current_org_id``), CSP-safe form WITH ``csrf_token``. The blueprint is
registered non-fatally from ``_bootstrap/blueprints.py``.
"""

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from app.decorators import admin_required
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
    return ArchiMateElement.query.filter_by(
        id=element_id,
        organization_id=org_id,
    ).first()


@ai_systems_genome_bp.route("/ai-systems")
@login_required
def ai_systems():
    """Render the AI Systems Register for the current org."""
    org_id = _current_org_id()
    register_html = None
    slice_data = None
    if org_id is not None:
        slice_data = build_ai_systems_slice(org_id, db.session)
        register_html = emit_ai_systems_register(slice_data)
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

    Limited to the administrator role, like the approval-queue accept that
    records the classification and the assessment. Refuses with the named
    reason (HTTP 409) when the gate is shut — a high-risk system with no
    accepted assessment — and leaves the system unreleased. The refusal
    message is exactly what the register page shows for the system.
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
        return jsonify(
            {"success": False, "error": result.get("reason", "Release refused")}
        ), 409

    db.session.commit()
    return redirect(url_for("ai_systems_genome.ai_systems"))


@ai_systems_genome_bp.route("/ai-systems/<int:element_id>/assess", methods=["POST"])
@login_required
def ai_system_attach_assessment(element_id):
    """Queue an assessment proposal for one AI system.

    Org-scoped like the other routes here: another organisation's element id
    is refused. The proposal lands in the same approval queue the
    classification uses; only an administrator's accept records the
    assessment (the accepted approval row IS the record — no new table).
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
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("AI system assessment proposal failed for org %s", org_id)

    return redirect(url_for("ai_systems_genome.ai_systems"))
