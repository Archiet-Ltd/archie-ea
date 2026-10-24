"""Answering a question about the organisation's own estate inside the chat."""

from flask import jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from app.modules.ai_chat.services.estate_answer import answer_estate_question
from app.modules.intelligence.services import answer_envelope as envelope

from . import unified_ai_chat_bp

MAX_QUESTION_CHARS = 2000


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
    return jsonify(
        {
            "success": True,
            "found": True,
            "address": answer["address"],
            "page_url": answer["page_url"],
            "html": render_template(
                "ai_chat/_estate_answer.html",
                answer=answer,
                composer_url=url_for("archimate.composer_page"),
            ),
        }
    )
