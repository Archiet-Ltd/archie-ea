"""AI-system reads over the public interface.

The two routes accept a signed-in session or a bearer token carrying
``api:read`` (``require_api_scope``); a token with ``api:write`` or any
``mcp:*`` scope is refused, and a token without an organisation is refused.
The organisation always comes from the token: the bearer loader binds
``g.current_org_id`` to the token's organisation, and every read filters by
it explicitly, so one organisation can never read another organisation's
rows.

AI systems are not a table: they are genome elements, and the one reader is the
genome's AI-systems slice builder (``build_ai_systems_slice``), so the public
read and the product screen show the same values. This module never queries
elements itself.

The agent reads (listAgents, getAgent) are not served yet: the contract
declares them under the ``agent:run`` scope, which is not a read scope, and
the interface-contract gate refuses an ``api:read`` route for them.

The response exposes only plain facts: id, name,
purpose, provider, model, autonomy, human oversight, risk class and release
state as the profile states them. Owners, prompts, keys, tool lists and notes
are not exposed.
"""

from __future__ import annotations

import math

from flask import Blueprint, g

from app.modules.oauth_provider.scopes import API_READ_SCOPE, require_api_scope
from app.utils.api_response import not_found_response, success_response
from app.utils.pagination import get_pagination_params

ai_v1_bp = Blueprint("ai_v1", __name__)


def _ai_system_payload(profile):
    governance = profile.get("governance") or {}
    return {
        "id": profile["archimate_element_id"],
        "name": profile["name"],
        "purpose": profile["purpose"],
        "provider": profile["provider"],
        "model_id": profile["model_id"],
        "autonomy_level": profile["autonomy_level"],
        "human_oversight": governance.get("human_review", "unknown"),
        "risk_class": profile["risk_class"],
        "release_state": profile["release_state"],
        "assessment_accepted": profile["assessment_accepted"],
        "lifecycle_state": profile["release_state"],
    }


def _organisation_ai_systems(org_id):
    """The organisation's AI-system profiles from the one slice builder."""
    from app import db
    from app.modules.enterprise_genome.services.ai_systems_slice import (
        build_ai_systems_slice,
    )

    systems = build_ai_systems_slice(org_id, db.session)["systems"]
    return sorted(systems, key=lambda s: (s["name"], s["archimate_element_id"]))


@ai_v1_bp.route("/ai-systems", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_ai_systems():
    """List the calling organisation's AI systems, sorted by name then id."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    systems = _organisation_ai_systems(g.current_org_id)
    total = len(systems)
    start = (page - 1) * per_page
    return success_response(
        {
            "ai_systems": [
                _ai_system_payload(s) for s in systems[start : start + per_page]
            ],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": math.ceil(total / per_page) if per_page else 0,
            },
        }
    )


@ai_v1_bp.route("/ai-systems/<int:system_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_ai_system(system_id):
    """One AI system by id, only when it belongs to the calling organisation."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    for profile in _organisation_ai_systems(g.current_org_id):
        if profile["archimate_element_id"] == system_id:
            return success_response(_ai_system_payload(profile))
    return not_found_response("AI System")
