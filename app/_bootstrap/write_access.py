"""Refuse a write from a person who holds no write permission.

A member whose organisation role is viewer used to create, rename and delete applications through
the API, and to delete them through neighbouring pages: the browser hid the buttons, and the
route's only guard was a sign-in check. One hook asks the one write answer (``may_write``) before
any state-changing request (POST, PUT, PATCH or DELETE, inside or outside ``/api/``) reaches its
view, so a new route is covered the day it is registered.
"""

import re

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# Endpoints a person without write permission may still call, each with the reason. None writes to
# the model: each changes only the signed-in person's own record, or computes an answer and stores
# nothing, or runs before a person is signed in.
READ_ONLY_ALLOWED = {
    # The person's own session and sign-in.
    "api_logout": "Ends the person's own session.",
    "api_login": "Starts a session; it changes nothing in the model.",
    "api_set_bu_scope": "Stores the business unit the person chose to look at, in their own session only.",
    "api_v1.llm.set_user_preference": "Saves the person's own choice of assistant model.",
    "dashboard.api_welcome_dismiss": "Records that the person closed their own welcome panel.",
    "dashboard.api_onboarding_complete": "Records the person's own onboarding state and view preference.",
    # The person's own notifications.
    "api_notification_mark_read": "Marks one of the person's own notifications as read.",
    "api_notifications_mark_all_read": "Marks the person's own notifications as read.",
    "main.api_mark_notification_read": "Marks one of the person's own notifications as read.",
    # Error reports from the person's own browser.
    "error_events.client_error": "Records an error the person's own browser met, so it reaches monitoring.",
    "csp_report": "Records a browser security report from the person's own browser, so it reaches monitoring.",
    "intelligence_api.ask_nl_question": "Answers a plain-language question from the read-only query catalogue; stores nothing.",
    "api_v1.impact_v1.analyze_impact": "Computes an impact answer from the model and stores nothing.",
    # Two things a person who cannot change the model still does with their own analysis.
    "application_mgmt.api_submit_stakeholder_scores": (
        "An invited stakeholder submits their own scores; the route checks it is the invited person or an administrator."
    ),
    "application_mgmt.api_invite_stakeholder": (
        "The owner of an analysis invites a stakeholder to it; the route checks the owner or an administrator."
    ),
    "application_mgmt.api_remove_stakeholder": (
        "The owner of an analysis removes a stakeholder from it; the route checks the owner or an administrator."
    ),
}

# The same, for pages and forms outside ``/api/``.
READ_ONLY_ALLOWED_PAGES = {
    # The person's own session, sign-in and account.
    "account.login": "Starts a session; it changes nothing in the model.",
    "account.register": "Creates the person's own account before sign-in.",
    "account.reset_password_request": "Sends the person a link to reset their own password.",
    "account.reset_password": "Sets the person's own password from the link they were sent.",
    "account.mfa_challenge": "Checks the person's own second sign-in step.",
    "account.mfa_enrol": "Sets up the person's own second sign-in step.",
    "account.confirm_request": "Sends the person a link to confirm their own account.",
    "account.switch_organization": "Changes which of the person's own organisations is open in their session.",
    # The person's own profile and preferences.
    "account.manage": "Saves the person's own name and details.",
    "account.change_password": "Changes the person's own password.",
    "account.change_email_request": "Sends the person a link to confirm their own new email address.",
    "account.change_email": "Confirms the person's own new email address.",
    "account.save_preferences": "Saves the person's own display and notification preferences.",
    "account.join": "Accepts the person's own invitation.",
    "account.join_from_invite": "Accepts the person's own invitation.",
    "account.accept_invitation": "Accepts the person's own invitation.",
    "account.decline_invitation": "Declines the person's own invitation.",
    "service_status.update_subscription": "Saves the person's own choice of service status emails.",
    "main.product_inquiry_submit": "Sends the person's own enquiry to the team; it changes nothing in the model.",
    # Asking questions: the conversation belongs to the person, and a change the assistant is asked
    # to make is refused by the assistant's own write check.
    "unified_ai_chat.send_message": "Adds the person's own question to their own conversation.",
    "unified_ai_chat.send_message_stream": "Adds the person's own question to their own conversation.",
    "unified_ai_chat.legacy_send_message": "Adds the person's own question to their own conversation.",
    "unified_ai_chat.send_page_guide_message": "Adds the person's own question to their own page guide.",
    "unified_ai_chat.clear_page_guide_history": "Clears the person's own page guide history.",
    "unified_ai_chat.clear_chat_history": "Clears the person's own conversation.",
    "unified_ai_chat.save_chat_session": "Saves the person's own conversation.",
    "unified_ai_chat.delete_conversation_thread": "Deletes one of the person's own conversations.",
    "unified_ai_chat.submit_message_feedback": "Records the person's own rating of an answer.",
    "unified_ai_chat.estate_answer": "Answers a question from the model; stores nothing.",
    "unified_ai_chat.natural_language_query": "Answers a plain-language question from the model; stores nothing.",
}

# Routes that look the record up in the caller's organisation first, so another organisation's id
# answers "not found" and does not reveal that it exists, and then ask the same write rule
# themselves (``refuse_read_only``). The hook leaves these to that answer.
ANSWERS_FOR_ITSELF = {
    "unified_ai_chat.entity_matching_message": (
        "Reads the text the person sent; a link it would store asks the assistant's write rule, which refuses and records it."
    ),
    "unified_ai_chat.generate_archimate_for_application": (
        "A preview stores nothing; saving the elements asks the matrix for creating relationships first."
    ),
    "unified_ai_chat.map_apqc_for_application": (
        "A preview stores nothing; applying the mappings asks the matrix for creating relationships first."
    ),
    "unified_ai_chat.bulk_process_applications": (
        "With every write switched off it stores nothing; any write asks the matrix for creating relationships first."
    ),
    "solution_design.api_set_raci": "Looks the solution up in the caller's organisation, then asks the write rule.",
    "solution_design.create_solution_stakeholder": "Looks the solution up in the caller's organisation, then asks the write rule.",
    "solution_design.delete_solution_stakeholder": "Looks the solution up in the caller's organisation, then asks the write rule.",
}

# A refusal on these screens gives the screen's own reason instead of the general one.
READ_ONLY_REASONS = (
    ("capability_map.api_roadmap_", "Your role may read the roadmap but not change it."),
)

GENERAL_REASON = "Your role is read-only, so it cannot make this change."


def _reason_for(endpoint):
    for prefix, reason in READ_ONLY_REASONS:
        if (endpoint or "").startswith(prefix):
            return reason
    return GENERAL_REASON


def _refusal(reason):
    from flask import jsonify, request

    from app.core.auth.permissions import READ_ONLY_CODE
    from app.middleware.tenant_decorators import _wants_json

    if _wants_json() or request.is_json:
        return jsonify({"success": False, "error": reason, "code": READ_ONLY_CODE}), 403
    from app.utils.plain_errors import render_plain_error

    return render_plain_error(403, title="You can view this, but not change it", message=reason)


def refuse_read_only(user=None):
    """The guard's answer, for a route that asks it itself after its own lookup: a refusal, or None.

    A person who may change the model is let through. A person whose only write grant is a
    governance persona is let through on the same terms as the guard gives: the change is stored
    only if the route's own check grants it.
    """
    from flask import g, request
    from flask_login import current_user

    from app.core.auth.permissions import holds_governance_write, is_read_only_member, may_write

    person = user or current_user
    if may_write(person):
        return None
    if holds_governance_write(person) and not is_read_only_member(person):
        g.write_admitted_for_route_check = True
        return None
    return _refusal(_reason_for(request.endpoint))


def commit_unless_read_only():
    """Store what a read computed along the way, unless the reader may not change the model.

    A page that reads and also saves a derived figure (a score, a recommended action) keeps saving
    it for a person who may write; for anyone else the figure is still returned and nothing is stored.
    """
    from flask_login import current_user

    from app import db
    from app.core.auth.permissions import may_write

    if may_write(current_user):
        db.session.commit()
        return True
    db.session.rollback()
    return False


def _route_answers_for_itself(app, endpoint):
    """Whether the view behind ``endpoint`` gives its own answer to a person who may not write.

    A route that already asks the permission matrix for the very record it changes, that shows
    everyone but a platform administrator "not found", whose role check refuses this person, or
    that looks the record up in the caller's organisation first and then asks the write rule,
    gives that answer itself.
    """
    if endpoint in ANSWERS_FOR_ITSELF:
        return True
    view = app.view_functions.get(endpoint)
    from flask_login import current_user

    from app.core.auth.permissions import _READ_ACTIONS, is_read_only_member

    # Whichever check would refuse a person whose role is viewer, the read-only reason answers.
    if is_read_only_member(current_user):
        return False
    matrix = getattr(view, "matrix_guard", None)
    # Only a write cell of the matrix answers for itself; a read cell never opens a write.
    if matrix and matrix[0] not in _READ_ACTIONS:
        return True
    if getattr(view, "answers_non_platform_admins", False):
        return True
    # A route behind a role check refuses, in its own words, a person that check refuses.
    roles = getattr(view, "role_guard", None)
    if roles:
        from app._decorators_base import _effective_roles

        if current_user.is_authenticated and not (_effective_roles(current_user) & roles):
            return True
    return False


# Tables the request machinery writes for anyone (the session registry, the audit trail of the
# request itself, browser error reports). A change to any other table is a change to the model.
BOOKKEEPING_TABLES = frozenset({"user_sessions", "soc2_audit_log", "error_events"})


class ChangeNotGrantedByRoute(Exception):
    """A person admitted only for their governance persona tried to store a change no route granted."""


def _model_changes(session):
    for obj in list(session.new) + list(session.deleted):
        yield obj
    for obj in list(session.dirty):
        if session.is_modified(obj, include_collections=False):
            yield obj


def _refuse_ungranted_change(session, _flush_context, _instances):
    from flask import g, has_request_context

    if not has_request_context() or not getattr(g, "write_admitted_for_route_check", False):
        return
    if getattr(g, "route_granted_write", False):
        return
    for obj in _model_changes(session):
        table = getattr(obj, "__tablename__", None)
        if table not in BOOKKEEPING_TABLES:
            # Noted as well as raised: a route that catches every error still answers with the refusal.
            g.ungranted_change_refused = True
            raise ChangeNotGrantedByRoute(GENERAL_REASON)


_STORES_TO = re.compile(
    r'^\s*(?:INSERT\s+INTO|UPDATE(?:\s+ONLY)?|DELETE\s+FROM(?:\s+ONLY)?)\s+"?(?:\w+"?\.)?"?(\w+)',
    re.IGNORECASE,
)


def _table_stored_to(statement):
    """The table a raw INSERT, UPDATE or DELETE statement changes, or None for anything else."""
    match = _STORES_TO.match(statement or "")
    return match.group(1).lower() if match else None


def _refuse_ungranted_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
    """The same route-level check as ``_refuse_ungranted_change``, for a route that writes with raw SQL.

    A statement written by hand never passes through the session's flush, so the flush check cannot
    see it; this asks the one question at the point the statement is sent.
    """
    from flask import g, has_request_context

    if not has_request_context() or not getattr(g, "write_admitted_for_route_check", False):
        return
    if getattr(g, "route_granted_write", False):
        return
    table = _table_stored_to(statement)
    if table is not None and table not in BOOKKEEPING_TABLES:
        g.ungranted_change_refused = True
        raise ChangeNotGrantedByRoute(GENERAL_REASON)


def init_write_access(app):
    from flask import g, request
    from flask_login import current_user
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    # A person whose only write grant is a governance persona reaches the route, and the route's
    # own check (through ``can`` or its role check) decides; a change the route did not grant is
    # never stored, whatever route it came through.
    if not event.contains(Session, "before_flush", _refuse_ungranted_change):
        event.listen(Session, "before_flush", _refuse_ungranted_change)
    from sqlalchemy.engine import Engine

    if not event.contains(Engine, "before_cursor_execute", _refuse_ungranted_statement):
        event.listen(Engine, "before_cursor_execute", _refuse_ungranted_statement)

    @app.errorhandler(ChangeNotGrantedByRoute)
    def _change_not_granted(_error):
        from app import db

        db.session.rollback()
        return _refusal(_reason_for(request.endpoint))

    @app.after_request
    def _answer_a_refused_change(response):
        if getattr(g, "ungranted_change_refused", False):
            from app import db

            db.session.rollback()
            refusal, status = _refusal(_reason_for(request.endpoint))
            if isinstance(refusal, str):
                from flask import make_response

                refusal = make_response(refusal)
            refusal.status_code = status
            return refusal
        return response

    @app.teardown_request
    def _forget_write_admission(_error=None):
        # The notes belong to one request; work done after it in the same context is not admitted.
        g.write_admitted_for_route_check = False
        g.route_granted_write = False
        g.ungranted_change_refused = False

    @app.before_request
    def _note_route_that_answers_for_itself():
        # Runs first; the guard below reads the notes. Each is set on every request, so a value
        # left on ``g`` by an earlier request in the same context never carries over.
        g.write_answered_by_route = request.method in WRITE_METHODS and _route_answers_for_itself(
            app, request.endpoint
        )
        g.write_admitted_for_route_check = False
        g.route_granted_write = False
        g.ungranted_change_refused = False
        return None

    @app.before_request
    def _refuse_api_write_without_permission():
        if request.method not in WRITE_METHODS:
            return None
        endpoint = request.endpoint
        if endpoint in READ_ONLY_ALLOWED or endpoint in READ_ONLY_ALLOWED_PAGES:
            return None
        if not current_user.is_authenticated:
            return None
        if getattr(g, "write_answered_by_route", False):
            return None
        return refuse_read_only(current_user)
