"""Stored answers on the public interface: list and fetch by address.

Read-only, read-scope token routes. Every answer belongs to exactly one
organisation: the organisation comes from the token through
``g.current_org_id``, so a token for one organisation sees none of another
organisation's answers, and a foreign address reads exactly like one never
issued (404). A request without an organisation is refused by
``require_api_scope`` before the view runs.
"""

from __future__ import annotations

from flask import Blueprint, g

from app.modules.intelligence.services import answer_envelope as envelope
from app.modules.oauth_provider.scopes import API_READ_SCOPE, require_api_scope
from app.utils.api_response import not_found_response, success_response

answers_v1_bp = Blueprint("answers_v1", __name__)


@answers_v1_bp.route("", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_answers():
    """The calling organisation's most recent stored answers, newest first."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    return success_response(
        {"answers": [envelope.present(r, include_rows=False) for r in envelope.recent_answers()]}
    )


@answers_v1_bp.route("/<address>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_answer(address: str):
    """The stored envelope at its permanent address, only for this
    organisation."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    record = envelope.open_answer(address)
    if record is None:
        return not_found_response("Answer")
    return success_response(envelope.present(record))
