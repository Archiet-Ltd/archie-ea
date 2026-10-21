"""Who is told when something changes: the rules, as plain data.

Each rule says which event type it reads, which changed field fires it, who is
told and what the message says. There is no model call and no code in a rule,
so adding a rule is adding a row here. ``candidates_for`` turns one event from
the log into the notifications the rules ask for; the consumer in
``app/services/change_feed_consumer.py`` writes them.

Keys of a rule:
  event_type          the event type the rule reads
  when_field_changed  the changed field that fires it (a name in ``changes``)
  tell                who is told: ``owners`` (the event's ``owner_user_ids``)
  message             template; fields: name, field, before, after
  url                 template for the link; fields: archimate_element_id, entity_id
  fallback_url        used when the url template needs a value the event lacks
"""

from __future__ import annotations

from string import Formatter
from typing import Any, Dict, List

RULES: List[Dict[str, Any]] = [
    {
        "event_type": "application_component.updated",
        "when_field_changed": "lifecycle_status",
        "tell": "owners",
        "message": "{name} moved from {before} to {after}.",
        "url": "/intelligence/traceability?element={archimate_element_id}",
        "fallback_url": "/intelligence/answers",
    },
]

_BLANK = "not set"


def _shown(value: Any) -> str:
    return _BLANK if value is None or value == "" else str(value)


def _link(rule: Dict[str, Any], event: Dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    fields = {
        "archimate_element_id": payload.get("archimate_element_id"),
        "entity_id": event.get("entity_id"),
    }
    needed = [name for _, name, _, _ in Formatter().parse(rule["url"]) if name]
    if any(fields.get(name) is None for name in needed):
        return rule["fallback_url"]
    return rule["url"].format(**fields)


def candidates_for(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The notifications the rules ask for, for one event from the log.

    Returns one dict per person told: ``user_id``, ``message``, ``url``,
    ``event_type``, ``entity_type``, ``entity_id``. No database access.
    """
    payload = event.get("payload") or {}
    changed = {entry.get("field"): entry for entry in payload.get("changes") or []}
    found: List[Dict[str, Any]] = []
    for rule in RULES:
        if rule["event_type"] != event.get("event_type"):
            continue
        entry = changed.get(rule["when_field_changed"])
        if entry is None:
            continue
        message = rule["message"].format(
            name=payload.get("name") or "An item you own",
            field=entry.get("field"),
            before=_shown(entry.get("before")),
            after=_shown(entry.get("after")),
        )
        url = _link(rule, event)
        recipients = (payload.get("owner_user_ids") or []) if rule["tell"] == "owners" else []
        for user_id in dict.fromkeys(recipients):
            found.append({
                "user_id": user_id,
                "message": message,
                "url": url,
                "event_type": event["event_type"],
                "entity_type": event.get("entity_type"),
                "entity_id": event.get("entity_id"),
            })
    return found


__all__ = ["RULES", "candidates_for"]
