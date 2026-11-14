"""Changing an application's Overview fields from the AI chat.

A person types "set Ifiok's description to ..." and the chat shows what will
change, then saves it when they press Save. The save is the Overview form's own
(``save_overview``), after the same permission check the form's route applies
(``can(user, "edit", "application")``); nothing here writes the model.

The preview carries a signed, short-lived token naming the person, their
organisation, the application and the one value, so the Save press can neither
be replayed by someone else nor changed to another value.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

TOKEN_SALT = "ai-chat-application-change"
TOKEN_MAX_AGE_SECONDS = 15 * 60

# What a person calls a field -> (the Overview form's key, the label shown).
FIELDS = {
    "description": ("description", "description"),
    "version": ("version", "version"),
    "category": ("application_category", "category"),
    "business domain": ("businessDomain", "business domain"),
    "domain": ("businessDomain", "business domain"),
    "technology stack": ("technology_stack", "technology stack"),
    "tech stack": ("technology_stack", "technology stack"),
    "status": ("deployment_status", "deployment status"),
    "deployment status": ("deployment_status", "deployment status"),
    "business owner": ("businessOwner", "business owner"),
    "owner": ("businessOwner", "business owner"),
    "technical lead": ("techOwner", "technical lead"),
    "development team": ("devTeam", "development team"),
    "support team": ("supportLevel", "support team"),
}

# Field names sorted longest first for the alternation (longest match wins).
_FIELD_ALT = "|".join(re.escape(name) for name in sorted(FIELDS.keys(), key=len, reverse=True))
_VERB = r"(?:please\s+)?(?:set|change|update|edit)\s+(?:the\s+)?"
# Possessive: "set <app>'s <field> to <value>"
# Field is matched against known names; app and value are greedy tails stripped afterwards.
_POSSESSIVE = re.compile(
    r"^\s*" + _VERB + r"(?P<app>.+?)(?:'s|\u2019s)\s+(?P<field>" + _FIELD_ALT + r")\s+(?:to|as)\s+(?P<value>.+)\s*$",
    re.I | re.S,
)
# Of-form: "set <field> of <app> to <value>"
_OF = re.compile(
    r"^\s*" + _VERB + r"(?P<field>" + _FIELD_ALT + r")\s+(?:of|for|on)\s+(?P<app>.+?)\s+(?:to|as)\s+(?P<value>.+)\s*$",
    re.I | re.S,
)
_QUOTES = "\"'\u201c\u201d\u2018\u2019`"


def _original_value(message: str, tokens: list, collapsed: str, value_start: int) -> str:
    """The value as typed: the original message from the token the value starts at, stripped at both ends.

    Matching runs on the collapsed text, which keeps the parse linear; the value is then taken from
    the original so line breaks and runs of spaces the person typed are saved as typed. The value
    always starts at a token boundary, so its token index is the count of spaces before it."""
    index = collapsed.count(" ", 0, value_start)
    for position, token in enumerate(re.finditer(r"\S+", message)):
        if position == index:
            return message[token.start():].strip()
    return " ".join(tokens[index:])


def parse_change_request(message: str) -> Optional[Dict[str, str]]:
    """{application, key, label, value} for "set <app>'s <field> to <value>", else ``None``."""
    if not message:
        return None
    # Collapse whitespace first so the regex never sees runs of spaces that cause backtracking.
    tokens = message.split()
    collapsed = " ".join(tokens)
    for pattern in (_POSSESSIVE, _OF):
        found = pattern.match(collapsed)
        if not found:
            continue
        field = found.group("field").lower()
        if field not in FIELDS:
            continue
        value = _original_value(message, tokens, collapsed, found.start("value"))
        if len(value) >= 2 and value[0] in _QUOTES and value[-1] in _QUOTES:
            value = value[1:-1].strip()
        application = found.group("app").strip().strip(_QUOTES)
        if not application:
            continue
        key, label = FIELDS[field]
        return {"application": application, "key": key, "label": label, "value": value}
    return None


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALT)


def _current_text(application, key: str) -> str:
    from app.services.application_overview_fields import OVERVIEW_TEXT_FIELDS

    if key == "deployment_status":
        return application.deployment_status or ""
    column = OVERVIEW_TEXT_FIELDS[key][0]
    return str(getattr(application, column, None) or "")


def may_edit_applications(user) -> bool:
    """The Overview form's own permission rule."""
    from app.core.auth.permissions import can

    return bool(can(user, "edit", "application"))


def _named_exactly(organization_id: int, name: str):
    """The application of this organisation called exactly ``name`` (any case, spacing folded), else ``None``.

    The same exact-name lookup the capability questions use."""
    from app.models.application_portfolio import ApplicationComponent
    from app.utils.duplicate_guard import find_duplicate_by_name

    return find_duplicate_by_name(ApplicationComponent, name, organization_id=organization_id)


def _closest_names(organization_id: int, name: str):
    """Names of applications a partial name fits: what the chat offers when nothing is called exactly that."""
    from app.modules.intelligence.services.query_catalogue import _resolve_application

    application, others = _resolve_application(organization_id, name)
    return [application.name] if application is not None else [a.name for a in others]


def names_an_application(request: Dict[str, str], organization_id: Optional[int]) -> bool:
    """Whether the request's subject is an application of this organisation (or says
    which of several it means), so the chat takes it as a change to an application."""
    if not organization_id:
        return False
    return (_named_exactly(int(organization_id), request["application"]) is not None
            or bool(_closest_names(int(organization_id), request["application"])))


def propose(request: Dict[str, str], user, organization_id: int) -> Dict[str, Any]:
    """What the chat shows for a change request: a preview, or why there is none.

    Returns ``{"state": "preview" | "refused" | "unchanged" | "unknown" | "ambiguous" | "invalid", ...}``.
    """
    from app.modules.ai_chat.tools.executor import record_refused_tool_call
    from app.modules.applications.routes.update_routes import _validate_overview
    base = {"label": request["label"], "typed_application": request["application"]}
    if not may_edit_applications(user):
        record_refused_tool_call(
            user, "update_application_overview",
            {"application": request["application"], "field": request["label"]}, via="chat",
        )
        return dict(base, state="refused")

    application = _named_exactly(organization_id, request["application"])
    if application is None:
        # A partial name is never taken for an application: the person is shown the closest names.
        return dict(base, state="unknown", others=_closest_names(organization_id, request["application"]))

    value = request["value"]
    if request["key"] == "deployment_status":
        value = value.lower()
    form = {request["key"]: value}
    _values, errors = _validate_overview(application, form)
    if errors:
        return dict(base, state="invalid", application=application.name, application_id=application.id,
                    problem=next(iter(errors.values())))
    before = _current_text(application, request["key"])
    if before.strip() == value.strip():
        return dict(base, state="unchanged", application=application.name, application_id=application.id,
                    value=value)
    token = _serializer().dumps({
        "u": user.id, "o": organization_id, "a": application.id, "k": request["key"], "v": value,
    })
    return dict(base, state="preview", application=application.name, application_id=application.id,
                before=before, after=value, token=token)


def confirm(token: str, user, organization_id: int) -> Dict[str, Any]:
    """Save the change a preview described. ``{"state": "saved" | "refused" | "invalid" | "expired"}``."""
    from app import db
    from app.modules.applications.routes.update_routes import save_overview
    from app.modules.ai_chat.tools.executor import record_refused_tool_call
    from app.models.application_portfolio import ApplicationComponent

    try:
        data = _serializer().loads(token or "", max_age=TOKEN_MAX_AGE_SECONDS)
    except SignatureExpired:
        return {"state": "expired"}
    except BadSignature:
        return {"state": "invalid", "problem": "That change could not be checked. Ask again."}
    if data.get("u") != user.id or data.get("o") != organization_id:
        return {"state": "invalid", "problem": "That change was not asked for by you. Ask again."}
    if not may_edit_applications(user):
        record_refused_tool_call(user, "update_application_overview", {"application_id": data["a"]}, via="chat")
        return {"state": "refused"}

    application = ApplicationComponent.query.filter_by(id=data["a"], organization_id=organization_id).first()
    if application is None:
        return {"state": "invalid", "problem": "That application is no longer in your model."}
    label = next(label for key, label in FIELDS.values() if key == data["k"])
    before = _current_text(application, data["k"])
    errors = save_overview(application, {data["k"]: data["v"]}, user.id)
    if errors:
        return {"state": "invalid", "application": application.name, "label": label,
                "problem": next(iter(errors.values()))}
    db.session.refresh(application)
    after = _current_text(application, data["k"]) if data["k"] != "businessOwner" else (application.business_owner or "")
    return {"state": "saved", "application": application.name, "application_id": application.id,
            "label": label, "before": before, "after": after}