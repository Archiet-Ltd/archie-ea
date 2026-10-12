"""Routes for the AI Systems genome projection (PILLAR 6).

Blueprint: ``ai_systems_genome_bp``, url_prefix ``/genome``.

  GET  /genome/ai-systems        the AI Systems Register
  POST /genome/ai-systems/seed   seed the org's AI systems, incl. Entelim's own
                                 copilot (self-modelling), then re-render.

Login-gated, org-scoped (the tenant middleware scopes the ArchiMateElement reads
by ``g.current_org_id``), CSP-safe form WITH ``csrf_token``. The blueprint is
registered non-fatally from ``_bootstrap/blueprints.py``.
"""

from __future__ import annotations

import logging

from flask import Blueprint, g, redirect, render_template, url_for
from flask_login import login_required

from app import db
from app.modules.enterprise_genome.emit.ai_systems_register import emit_ai_systems_register
from app.modules.enterprise_genome.services.ai_systems_seed import seed_archie_copilot
from app.modules.enterprise_genome.services.ai_systems_slice import build_ai_systems_slice

logger = logging.getLogger(__name__)

ai_systems_genome_bp = Blueprint("ai_systems_genome", __name__, url_prefix="/genome")


def _current_org_id():
    return getattr(g, "current_org_id", None)


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
    """Register Entelim's own copilot (self-model) in the org's register.

    Idempotent — re-running updates the existing element in place. Only systems
    that really exist are registered: estate AI systems are never invented to
    make the register look populated.
    """
    org_id = _current_org_id()
    if org_id is None:
        return redirect(url_for("ai_systems_genome.ai_systems"))

    try:
        # Entelim's own copilot — honest self-model.
        seed_archie_copilot(db.session, org_id)
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("AI systems seed failed for org %s", org_id)

    return redirect(url_for("ai_systems_genome.ai_systems"))
