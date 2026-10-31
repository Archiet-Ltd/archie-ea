"""R1-B30: asking a question and opening the stored answer at its address.

Registered on the two existing intelligence blueprints (not a new blueprint on
either prefix -- ADR 0008 rule 3):

  POST /api/v1/intelligence/answers             ask, store, return the envelope
  GET  /api/v1/intelligence/answers             the organisation's recent answers
  GET  /api/v1/intelligence/answers/<address>   the stored envelope
  GET  /intelligence/answers                    page: ask and list
  GET  /intelligence/answers/element/<id>       ask about one element, open the answer
  GET  /intelligence/answers/<address>          page: one stored answer

No logic lives here: the question is computed by the query service, wrapped and
stored by ``answer_envelope``. Every route names the caller's ACTIVE
organisation and refuses (400, as /ask does) or reads nothing when there is none. An address
that belongs to another organisation answers exactly as one never issued (404).
"""

from __future__ import annotations

from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.middleware.tenant_context import current_org_id
from app.modules.intelligence.routes.api import intelligence_api
from app.modules.intelligence.routes.ui import intelligence_ui
from app.modules.intelligence.services import answer_envelope as envelope
from app.modules.intelligence.services.reason_codes import validate_reason_code
from app.utils.api_response import error_response, success_response

_NO_TENANT_CONTEXT_REASON = validate_reason_code("no_tenant_context")
_ELEMENT_NOT_FOUND_REASON = validate_reason_code("element_not_found")
_NO_SEED_ELEMENTS_REASON = validate_reason_code("no_seed_elements")

_ANSWER_NOT_FOUND = "Answer not found"


@intelligence_api.route("/answers", methods=["POST"])
@login_required
def ask_answer():
    """Ask a question, store the answer at a new permanent address, return it."""
    if current_org_id() is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )
    if (request.content_length or 0) > envelope.MAX_BODY_BYTES:
        return error_response(
            "the question is too large", code="INVALID_PARAMETER", status_code=400
        )
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error_response(
            "the request body must be a JSON object", code="INVALID_PARAMETER", status_code=400
        )
    kind = body.get("question")
    try:
        record = envelope.ask_and_store(
            kind if isinstance(kind, str) else "",
            {key: value for key, value in body.items() if key != "question"},
            user_id=getattr(current_user, "id", None),
        )
    except envelope.AnswerParamError as exc:
        return error_response(str(exc), code="INVALID_PARAMETER", status_code=400)
    except envelope.AnswerNotAskable as exc:
        if exc.reason == _NO_SEED_ELEMENTS_REASON:
            return error_response(
                "name at least one element, or a filter that matches one",
                code="INVALID_PARAMETER",
                details={"reason": exc.reason},
                status_code=400,
            )
        if exc.reason == _NO_TENANT_CONTEXT_REASON:
            return error_response(
                "no tenant context for this request",
                code="NO_TENANT_CONTEXT",
                details={"reason": exc.reason},
                status_code=400,
            )
        return error_response(
            "Element not found",
            code="NOT_FOUND",
            details={"reason": _ELEMENT_NOT_FOUND_REASON},
            status_code=404,
        )
    return success_response(envelope.present(record), status_code=201)


@intelligence_api.route("/answers", methods=["GET"])
@login_required
def list_answers():
    """The organisation's most recent stored answers (without their rows)."""
    if current_org_id() is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )
    return success_response(
        {"answers": [envelope.present(r, include_rows=False) for r in envelope.recent_answers()]}
    )


@intelligence_api.route("/answers/<address>", methods=["GET"])
@login_required
def get_answer(address: str):
    """The stored envelope at its permanent address."""
    if current_org_id() is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            details={"reason": _NO_TENANT_CONTEXT_REASON},
            status_code=400,
        )
    record = envelope.open_answer(address)
    if record is None:
        return error_response(_ANSWER_NOT_FOUND, code="NOT_FOUND", status_code=404)
    return success_response(envelope.present(record))


@intelligence_ui.route("/answers", methods=["GET"])
@login_required
def answers_page():
    """Ask what breaks if elements fail together, or which risks reach one, and
    list the organisation's stored answers."""
    org_id = current_org_id()
    records = envelope.recent_answers() if org_id is not None else []
    return render_template(
        "intelligence/answers.html",
        answers=[envelope.present(r, include_rows=False) for r in records],
        has_organisation=org_id is not None,
    )


@intelligence_ui.route("/answers/element/<int:element_id>", methods=["GET"])
@login_required
def answer_for_element(element_id: int):
    """Open the cited answer for one element: what breaks if it fails, worked out
    now and stored at its own address. Alerts link here, so a person who opens
    one lands on a current answer with its citations. An element of another
    organisation answers as one that does not exist (404)."""
    if current_org_id() is None:
        abort(404)
    try:
        record = envelope.ask_and_store(
            envelope.KIND_IMPACT,
            {"element_id": element_id},
            user_id=getattr(current_user, "id", None),
        )
    except (envelope.AnswerParamError, envelope.AnswerNotAskable):
        abort(404)
    return redirect(url_for("intelligence_ui.answer_page", address=record.address))


@intelligence_ui.route("/answers/<address>", methods=["GET"])
@login_required
def answer_page(address: str):
    """One stored answer, rendered on the server from the stored snapshot."""
    record = envelope.open_answer(address)
    if record is None:
        abort(404)
    return render_template("intelligence/answer.html", answer=envelope.present(record))


__all__ = ["ask_answer", "list_answers", "get_answer", "answers_page", "answer_for_element", "answer_page"]
