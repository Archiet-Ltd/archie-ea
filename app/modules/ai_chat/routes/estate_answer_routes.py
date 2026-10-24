"""Answering a question about the organisation's own estate inside the chat."""

import logging

from flask import jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from app.modules.ai_chat.services.estate_answer import answer_estate_question
from app.modules.ai_chat.services.lens_answer import answer_lens_question
from app.modules.intelligence.services import answer_envelope as envelope

from app.services.conversation_history import persist_turn

from . import unified_ai_chat_bp

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2000

# What each lens's answer is a list of, for the line kept in the conversation.
_LENS_RECORDS = {
    "strategy": ("initiatives", "initiative"),
    "risk": ("risks", "risk"),
    "programme": ("work_packages", "work package"),
    "accountability": ("owners", "owner"),
    "data": ("data_objects", "data object"),
    "compliance": ("controls", "control"),
}


def _remember(thread_id, message, answer_text):
    """Keep the question and its answer in the person's conversation history, as any
    other answer is, and return the thread they were kept in (or None if they could
    not be: the answer on screen is unaffected)."""
    try:
        return persist_turn(current_user.id, thread_id, message, answer_text)
    except Exception:
        logger.warning("estate answer was not saved to the conversation", exc_info=True)
        return None


def _lens_line(lens):
    name = lens["element"].name
    data = lens["data"] or {}
    if lens["lens"] == "portfolio":
        tracked = "is tracked" if data.get("application_component_id") else "is not tracked"
        return "Answer from your records: %s %s by rationalisation planning." % (name, tracked)
    key, noun = _LENS_RECORDS[lens["lens"]]
    count = len(data.get(key) or [])
    return "Answer from your records about %s: %d %s%s recorded." % (name, count, noun, "" if count == 1 else "s")


@unified_ai_chat_bp.route("/estate-answer", methods=["POST"])
@login_required
def estate_answer():
    """The cited answer cards for a question that names an element of the
    active organisation, or ``found: false`` so the chat asks the model.

    The answer is the stored envelope the answers page opens at its own address;
    the cards are that envelope drawn on the server.
    """
    body = request.get_json(silent=True)
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, str) or not message.strip():
        return jsonify({"success": False, "error": "Ask a question first."}), 400
    thread_id = body.get("thread_id") if isinstance(body.get("thread_id"), str) else None
    lens = answer_lens_question(message[:MAX_QUESTION_CHARS])
    if lens is not None:
        component_id = ((lens["data"] or {}).get("application_component_id")) if lens["lens"] == "portfolio" else None
        kept = _remember(thread_id, message, _lens_line(lens)) if lens["data"] is not None else None
        return jsonify(
            {
                "success": True,
                "found": True,
                "thread_id": kept,
                "lens": lens["lens"],
                "html": render_template(
                    "ai_chat/_lens_answer.html",
                    lens=lens["lens"],
                    element=lens["element"],
                    data=lens["data"],
                    planning_url=(
                        url_for("unified_applications.rationalization_planning", app_id=component_id)
                        if component_id
                        else None
                    ),
                ),
            }
        )
    try:
        record = answer_estate_question(message[:MAX_QUESTION_CHARS], user_id=current_user.id)
    except envelope.AnswerNotAskable:
        record = None
    if record is None:
        return jsonify({"success": True, "found": False})
    answer = envelope.present(record)
    # Closest connections first, a recorded one ahead of a worked-out one at the same
    # distance, then by name, as the dashboard's first answer lists them.
    names = {key: (value or {}).get("name") or "" for key, value in answer["elements"].items()}
    answer["rows"] = sorted(
        answer["rows"],
        key=lambda row: (
            (row.get("relation") or row).get("depth") or 0,
            (row.get("relation") or {}).get("kind") == "derived",
            names.get(str(row.get("element_id")), "").lower(),
        ),
    )
    total = answer["summary"].get("total") or 0
    kept = _remember(
        thread_id,
        message,
        "Answer from your records: %d connection%s. [Open this answer](%s)"
        % (total, "" if total == 1 else "s", answer["page_url"]),
    )
    return jsonify(
        {
            "success": True,
            "found": True,
            "thread_id": kept,
            "address": answer["address"],
            "page_url": answer["page_url"],
            "html": render_template(
                "ai_chat/_estate_answer.html",
                answer=answer,
                composer_url=url_for("archimate.composer_page"),
            ),
        }
    )
