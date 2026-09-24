"""Registering AI systems as first-class modelled elements — incl. self-modelling.

An AI system *is* an ``ArchiMateElement`` (CLAUDE.md: "the field IS the element").
``register_ai_system`` upserts one element per (org, name), storing the profile
in ``custom_properties['ai_system']`` — idempotent on name, so re-seeding an
existing org updates the profile in place rather than duplicating it.

``seed_archie_copilot`` models Entelim's OWN AI copilot, honestly:

  * provider/model come from ``DEFAULT_MODELS['anthropic']`` — the same default
    the copilot itself falls back to — not an aspirational guess;
  * autonomy is ``human-in-loop``: the copilot's write tools are gated behind the
    genome-patch approve flow (a human approves each patch), so it proposes and a
    human commits — it is not supervised-autonomous;
  * governance records ``approval_gate=True`` (that approve flow) and, honestly,
    ``human_review`` is left UNRECORDED here rather than asserted — the approval
    gate is the reviewed control we can point at; a blanket "all output human
    reviewed" would overclaim. So the modelled copilot reads as governed but with
    ``human_review='unknown'``, which is the truthful state.

The genome-patch approve flow referenced is
``app/modules/codegen/services/genome_patch_service.py`` (the approve step is the
human gate on copilot-proposed changes).
"""

from __future__ import annotations

from app.modules.ai_chat.services.model_defaults import DEFAULT_MODELS

from .ai_system_profile import (
    AI_SYSTEM_ELEMENT_LAYER,
    AI_SYSTEM_ELEMENT_TYPE,
    AI_SYSTEM_MARKER,
    build_custom_properties,
)

ARCHIE_COPILOT_NAME = "Entelim AI Copilot"


def register_ai_system(
    session,
    org_id: int,
    *,
    name: str,
    provider: str | None = None,
    model_id: str | None = None,
    purpose: str | None = None,
    autonomy_level: str | None = None,
    data_sensitivity: str | None = None,
    approval_gate=None,
    human_review=None,
):
    """Upsert one AI-system ArchiMateElement for *org_id*. Idempotent on name.

    Returns the ``ArchiMateElement``. Sets ``organization_id`` explicitly so the
    row is correctly scoped even from CLI/seed context (no tenant middleware).
    """
    from app.models.models import ArchiMateElement

    ai_props = build_custom_properties(
        provider=provider,
        model_id=model_id,
        purpose=purpose,
        autonomy_level=autonomy_level,
        data_sensitivity=data_sensitivity,
        approval_gate=approval_gate,
        human_review=human_review,
    )

    existing = (
        session.query(ArchiMateElement)
        .filter(ArchiMateElement.organization_id == org_id)
        .filter(ArchiMateElement.name == name)
        .filter(ArchiMateElement.type == AI_SYSTEM_ELEMENT_TYPE)
        .first()
    )
    if existing is not None:
        props = dict(existing.custom_properties or {})
        props[AI_SYSTEM_MARKER] = ai_props[AI_SYSTEM_MARKER]
        existing.custom_properties = props
        session.add(existing)
        session.flush()
        return existing

    element = ArchiMateElement(
        name=name,
        type=AI_SYSTEM_ELEMENT_TYPE,
        layer=AI_SYSTEM_ELEMENT_LAYER,
        description=purpose or None,
        custom_properties=ai_props,
    )
    # Explicit scope: seed/CLI runs outside request context, so the tenant
    # middleware does not auto-set organization_id here.
    element.organization_id = org_id
    session.add(element)
    session.flush()
    return element


def seed_archie_copilot(session, org_id: int):
    """Model Entelim's own AI copilot as a governed, human-in-loop AI system."""
    return register_ai_system(
        session,
        org_id,
        name=ARCHIE_COPILOT_NAME,
        provider="anthropic",
        model_id=DEFAULT_MODELS.get("anthropic"),
        purpose=(
            "In-product architecture copilot: proposes genome patches and "
            "modelling changes for human approval via the genome-patch approve flow."
        ),
        autonomy_level="human-in-loop",
        data_sensitivity="confidential",
        approval_gate=True,
        # human_review deliberately left UNRECORDED — see module docstring.
    )
