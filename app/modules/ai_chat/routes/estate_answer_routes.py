"""Answering a question about the organisation's own estate inside the chat."""

import logging
from datetime import datetime

from flask import jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.decorators import audit_log
from app.middleware.tenant_context import current_org_id
from app.modules.ai_chat.services import application_change
from app.modules.ai_chat.services.catalogue_answer import answer_catalogue_question
from app.modules.ai_chat.services.estate_answer import (
    answer_estate_question,
    explanations_for,
    owned_element_id,
    recorded_text,
)
from app.modules.ai_chat.services.lens_answer import (
    answer_for_lens,
    answer_lens_question,
    asks_for_capacity,
    owner_of_element,
)
from app.modules.intelligence.services import answer_envelope as envelope

from app.services.conversation_history import persist_turn
from app.services.result_footer_service import ownership_footer

from . import unified_ai_chat_bp

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2000
MAX_CHANGE_CHARS = 8000
ANSWERED_FROM_RECORDS = "records"  # kept with the saved answer; the chat page reads the same word
SHOWN_LIMIT = 25  # connections drawn as cards; the template and its explanations agree on it

# What each lens's answer is a list of, for the line kept in the conversation.
_LENS_RECORDS = {
    "strategy": ("initiatives", "initiative"),
    "risk": ("risks", "risk"),
    "programme": ("work_packages", "work package"),
    "accountability": ("owners", "owner"),
    "data": ("data_objects", "data object"),
    "compliance": ("controls", "control"),
}


def _remember(thread_id, message, answer_text, *, keep=True):
    """Keep the question and its answer in the person's conversation history, as any
    other answer is, and return the thread they were kept in (or None if they could
    not be: the answer on screen is unaffected)."""
    if not keep:
        return None
    try:
        return persist_turn(current_user.id, thread_id, message, answer_text, answered_from=ANSWERED_FROM_RECORDS)
    except Exception:
        logger.warning("estate answer was not saved to the conversation", exc_info=True)
        return None


def _derivation_status():
    """When the organisation's indirect connections were last worked out, or None when
    that could not be read (the card then says so rather than showing zeros)."""
    try:
        from app.modules.intelligence.services.derived_facts import derivation_status

        return derivation_status(int(current_org_id()))
    except Exception:
        logger.warning("derivation status unavailable", exc_info=True)
        return None


def _lens_line(lens):
    name = lens["element"].name
    data = lens["data"] or {}
    if lens["lens"] == "portfolio":
        tracked = "is tracked" if data.get("application_component_id") else "is not tracked"
        return "Answer from your records: %s %s by rationalization planning." % (name, tracked)
    key, noun = _LENS_RECORDS[lens["lens"]]
    count = len(data.get(key) or [])
    return "Answer from your records about %s: %d %s%s recorded." % (name, count, noun, "" if count == 1 else "s")


def _holds_elements(organization_id):
    """True when the organisation has recorded any element at all."""
    from app.models.archimate_core import ArchiMateElement

    return db.session.query(ArchiMateElement.query.filter_by(organization_id=organization_id).exists()).scalar()


def _empty_steps(answer, organization_id):
    """What a 'cannot answer yet' reply offers as its next step, as buttons: naming
    the one thing the question asked about when that is what is missing, or the
    general next steps (connect a source, or see them working with a sample) when
    the records hold nothing at all yet. ``None`` once there is anything to show
    instead, or a finding that needs no next step (the rule is never to invent data:
    no button is shown unless the person it opens for could actually use it)."""
    if answer["records"] or answer.get("complete"):
        return None
    from app.utils.role_access import may_connect_github, may_load_sample_company

    # Bringing a whole model in is for an organisation that has none: beside a mistyped
    # name in an estate that already holds its systems it would only invite fiction in.
    empty = not _holds_elements(organization_id)
    connect = empty and may_connect_github(current_user, organization_id)
    sample = empty and may_load_sample_company(current_user)
    if answer.get("named_not_found"):
        from app.core.auth.permissions import can as _can

        # Naming the one missing thing is the first step; the way to get data in
        # quickly (connect a source, or the sample) is offered beside it.
        if _can(current_user, "create", "application"):
            return {"kind": "add_application", "name": answer["named_not_found"], "connect": connect, "sample": sample}
    if not connect and not sample:
        return None
    return {"kind": "connect_or_sample", "connect": connect, "sample": sample}


def _catalogue_response(message, thread_id, keep):
    """The catalogue's cited answer to a plain startup question, or ``found: false``."""
    organization_id = current_org_id()
    answer = answer_catalogue_question(message[:MAX_QUESTION_CHARS], int(organization_id)) if organization_id else None
    if answer is None:
        return jsonify({"success": True, "found": False})
    count = len(answer["records"])
    line = "Answer from your records: %s. %s" % (
        answer["title"],
        "%d record%s cited." % (count, "" if count == 1 else "s") if count else "Your records cannot answer this yet.",
    )
    return jsonify(
        {
            "success": True,
            "found": True,
            "thread_id": _remember(thread_id, message, line, keep=keep),
            "entry_id": answer["entry_id"],
            "html": render_template(
                "ai_chat/_catalogue_answer.html",
                answer=answer,
                empty_steps=_empty_steps(answer, organization_id),
            ),
        }
    )


def _application_change_response(change, message, thread_id, keep):
    """The preview card for "set <application>'s <field> to <value>", or the words for why not."""
    organization_id = current_org_id()
    result = application_change.propose(change, current_user, int(organization_id)) if organization_id else {
        "state": "unknown", "label": change["label"], "typed_application": change["application"], "others": []}
    line = {
        "preview": "Change preview: the %s of %s. Nothing is saved until you press Save." % (
            result.get("label"), result.get("application")),
        "refused": "Not changed: your role cannot edit applications.",
    }.get(result["state"], "Not changed: %s." % result["state"])
    return jsonify({
        "success": True,
        "found": True,
        "thread_id": _remember(thread_id, message, line, keep=keep),
        "change_state": result["state"],
        "html": render_template("ai_chat/_application_change.html", result=result),
    })


@unified_ai_chat_bp.route("/application-change/confirm", methods=["POST"])
@login_required
@audit_log("ai_chat_application_change")
def confirm_application_change():
    """Save the change a preview card described, once the person has pressed Save."""
    body = request.get_json(silent=True)
    token = body.get("token") if isinstance(body, dict) else None
    organization_id = current_org_id()
    if not isinstance(token, str) or not organization_id:
        return jsonify({"success": False, "error": "Ask for the change again."}), 400
    result = application_change.confirm(token, current_user, int(organization_id))
    return jsonify({
        "success": result["state"] == "saved",
        "change_state": result["state"],
        "html": render_template("ai_chat/_application_change.html", result=result),
    })


def _what_it_depends_on(answer):
    """The connections an element has the other way round, when nothing depends on it.

    "What breaks if X fails" follows what depends on X. An element whose recorded links
    all point the other way (it runs on, or is part of, something else) has none, yet
    the overview counts those links, so the answer names them with their sources instead
    of saying nothing is connected. Same query service, same organisation, asked in both directions.
    ``None`` when the element has no links in either direction."""
    params = dict(answer.get("params") or {})
    if answer.get("question_kind") != envelope.KIND_IMPACT or not params.get("element_id"):
        return None
    if params.get("direction") == "upstream":
        return None  # asked what it depends on: the answer already is that walk
    # The links as recorded, one step. The count of relationships touching the element is the
    # overview's (``_recorded_links``); this lists the distinct elements those links reach.
    params.update(direction="both", include_derived=False, max_depth=1)
    try:
        upstream = envelope.present(envelope.ask_and_store(envelope.KIND_IMPACT, params, user_id=current_user.id))
    except envelope.AnswerParamError:
        return None
    return upstream if upstream["rows"] else None


def _recorded_links(answer):
    """The recorded relationships touching the element asked about (the total is the figure the
    overview's "most connected" line and the chat's first questions use), with how many lead from it,
    how many lead to it and how many other elements they reach. ``None`` when the question names no
    single element."""
    from app.modules.dashboard.services.overview_figures import relationship_split

    element_id = (answer.get("params") or {}).get("element_id")
    if answer.get("question_kind") != envelope.KIND_IMPACT or not element_id:
        return None
    return relationship_split(int(current_org_id()), int(element_id))


def _lens_card(lens, message=""):
    owned = owner_of_element(lens["element"]) if lens["lens"] == "accountability" and lens["data"] is not None else None
    component_id = ((lens["data"] or {}).get("application_component_id")) if lens["lens"] == "portfolio" else None
    owners = ((lens["data"] or {}).get("owners")) if lens["lens"] == "accountability" else None
    for owner in owners or []:
        # The day the ownership record was made, as people read a date ("12 Nov 2026").
        day = datetime.strptime(owner["since"], "%Y-%m-%d") if owner.get("since") else None
        owner["since_text"] = "%d %s" % (day.day, day.strftime("%b %Y")) if day else None
    return render_template(
        "ai_chat/_lens_answer.html",
        lens=lens["lens"],
        element=lens["element"],
        data=lens["data"],
        owned=owned,
        asked_capacity=asks_for_capacity(message),
        footer=ownership_footer(current_org_id(), owners) if owners else None,
        planning_url=(
            url_for("unified_applications.rationalization_planning", app_id=component_id) if component_id else None
        ),
    )


def _estate_card(record, message, part=None):
    """The cards for one stored answer, with the line kept in the conversation."""
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
    depends_on = _what_it_depends_on(answer) if not total else None
    if depends_on:
        # What the card showed: nothing depends on it, and the connections it has of its own.
        count = depends_on["summary"]["total"]
        line = "Answer from your records: nothing depends on this; it is connected to %d other element%s. [Open this answer](%s)" % (
            count,
            "" if count == 1 else "s",
            depends_on["page_url"],
        )
    else:
        line = "Answer from your records: %d connection%s. [Open this answer](%s)" % (
            total,
            "" if total == 1 else "s",
            answer["page_url"],
        )
    html = render_template(
        "ai_chat/_estate_answer.html",
        answer=answer,
        composer_url=url_for("archimate.composer_page"),
        question=message[:MAX_QUESTION_CHARS],
        part=part,
        derivation=_derivation_status(),
        shown_limit=SHOWN_LIMIT,
        depends_on=depends_on,
        recorded_links=_recorded_links(answer),
        explanations=explanations_for(answer, SHOWN_LIMIT),
        recorded_text=recorded_text,
    )
    return {"html": html, "line": line, "address": answer["address"], "page_url": answer["page_url"]}


# What each distinct card of a question is, in the order the parts are asked. What an
# element runs on and what it depends on are one walk upstream, so they are one card.
_CARD_OF_PART = {
    envelope.PART_OWNER: "owner",
    envelope.PART_RUNS_ON: "upstream",
    envelope.PART_DEPENDS_ON: "upstream",
    envelope.PART_BREAKS: "breaks",
}


def _cards_asked(parts):
    cards = []
    for part in parts:
        card = _CARD_OF_PART[part]
        if card not in cards:
            cards.append(card)
    return cards


def _parts_response(message, thread_id, keep, cards):
    """One reply holding a card for each thing the question asks about its element,
    in the order asked: its owners, what it depends on, what breaks. ``None`` when the
    question names no element of the organisation, so the ordinary answers take over."""
    drawn, lines, first = [], [], None
    for card in cards:
        if card == "owner":
            lens = answer_for_lens("accountability", message[:MAX_QUESTION_CHARS])
            if lens is None:
                return None
            drawn.append(_lens_card(lens, message))
            lines.append(_lens_line(lens))
            continue
        direction = "upstream" if card == "upstream" else "downstream"
        try:
            record = answer_estate_question(message[:MAX_QUESTION_CHARS], user_id=current_user.id, direction=direction)
        except envelope.AnswerNotAskable:
            record = None
        if record is None:
            return None
        made = _estate_card(record, message, part=card)
        first = first or made
        drawn.append(made["html"])
        lines.append(made["line"])
    body = {
        "success": True,
        "found": True,
        "thread_id": _remember(thread_id, message, "\n".join(lines), keep=keep),
        "cards": cards,
        "html": '<div class="space-y-6">%s</div>' % "".join(drawn) if len(drawn) > 1 else drawn[0],
    }
    if first:
        body["address"], body["page_url"] = first["address"], first["page_url"]
    return jsonify(body)


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
    keep = body.get("remember") is not False
    posted_element = body.get("element_id") if isinstance(body.get("element_id"), int) else None
    card_element = owned_element_id(posted_element, current_org_id())
    # A card names its element: the question is about that element, not about a change request,
    # the parts or the lens its wording sounds like, so all three are skipped for it.
    change = None
    if not card_element:
        # Read whole: cutting a long value short would save text the person did not write. The
        # description limit refuses anything this cap leaves too long.
        change = application_change.parse_change_request(message[:MAX_CHANGE_CHARS])
        # Only a request whose subject is an application of this organisation is a change to
        # one; a solution, a risk or a work package goes on to the model as it always did.
        if change is not None and not application_change.names_an_application(change, current_org_id()):
            change = None
    if change is not None:
        return _application_change_response(change, message, thread_id, keep)
    parts = [] if card_element else envelope.classify_estate_parts(message[:MAX_QUESTION_CHARS])
    if set(parts) & {envelope.PART_OWNER, envelope.PART_RUNS_ON, envelope.PART_DEPENDS_ON}:
        cards = _cards_asked(parts)
        only = body.get("part")
        if only in cards:
            cards = [only]
        response = _parts_response(message, thread_id, keep, cards)
        if response is not None:
            return response
    lens = None if card_element else answer_lens_question(message[:MAX_QUESTION_CHARS])
    if lens is not None:
        kept = _remember(thread_id, message, _lens_line(lens), keep=keep) if lens["data"] is not None else None
        return jsonify(
            {
                "success": True,
                "found": True,
                "thread_id": kept,
                "lens": lens["lens"],
                "html": _lens_card(lens, message),
            }
        )
    try:
        record = answer_estate_question(
            message[:MAX_QUESTION_CHARS],
            user_id=current_user.id,
            element_id=posted_element,
        )
    except envelope.AnswerNotAskable:
        record = None
    if record is None:
        return _catalogue_response(message, thread_id, keep)
    made = _estate_card(record, message)
    return jsonify(
        {
            "success": True,
            "found": True,
            "thread_id": _remember(thread_id, message, made["line"], keep=keep),
            "address": made["address"],
            "page_url": made["page_url"],
            "html": made["html"],
        }
    )
