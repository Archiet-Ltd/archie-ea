"""Who is told when something changes: the rules, as plain data.

Each rule says which event type it reads, which changed field fires it, who is
told and what the message says. There is no model call and no code in a rule,
so adding a rule is adding a row here. ``candidates_for`` turns one event from
the log into the notifications the rules ask for; the consumer in
``app/services/change_feed_consumer.py`` writes them. Rules about a date that
can pass without anyone editing anything also name a ``daily_scan``; the daily
check in ``app/services/date_alerts.py`` runs those through ``alert_for``.

Keys of a rule:
  name                (alerts) the rule's name; its notifications carry the
                      event type ``alert.<name>``, which is how the bell and
                      the daily email tell an alert from other notifications
  event_type          the event type the rule reads (None: daily check only)
  when_field_changed  the changed field that fires it (a name in ``changes``)
  when                (optional) a condition on the change:
                        date_within_days  the new date falls between today and
                                          that many days ahead
                        change_over_percent  the new number differs from the
                                          old by more than this per cent
  tell                who is told: ``owners`` (the event's ``owner_user_ids``)
                      or ``responsible`` (the person a condition names)
  message             template; fields: name, subject, field, before, after,
                      percent, number, due
  url                 template for the link; fields: archimate_element_id,
                      entity_id, review_item_id
  fallback_url        used when the url template needs a value the event lacks
  alert_key           (optional) template naming one alert, so the same date
                      or condition never alerts twice, whichever path finds it
  daily_scan          (optional) the daily check that also finds it
  enabled             (optional, default True) False ships the rule off ...
  disabled_reason     ... with the reason, in plain words, beside it
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from string import Formatter
from typing import Any, Dict, Iterable, List, Optional

# Every alert opens the cited answer for the element it is about: what breaks if
# it fails, worked out when the person opens it, with its own address.
ANSWER_URL = "/intelligence/answers/element/{archimate_element_id}"
ANSWERS_PAGE = "/intelligence/answers"

RULES: List[Dict[str, Any]] = [
    {
        "event_type": "application_component.updated",
        "when_field_changed": "lifecycle_status",
        "tell": "owners",
        "message": "{name} moved from {before} to {after}.",
        "url": "/intelligence/traceability?element={archimate_element_id}",
        "fallback_url": "/intelligence/answers",
    },
    {
        # The support date the change feed carries for an application: its
        # end-of-life date, the one edited on the application form.
        "name": "support_date_within_180_days",
        "event_type": "application_component.updated",
        "when_field_changed": "end_of_life_date",
        "when": {"date_within_days": 180},
        "tell": "owners",
        "message": "{subject} reaches end of support on {after}, within 180 days. "
                   "You are told because you own it.",
        "url": ANSWER_URL,
        "fallback_url": ANSWERS_PAGE,
        "alert_key": "support-{entity_id}-{after_raw}",
        "daily_scan": "support_dates",
    },
    {
        "name": "owner_removed",
        "event_type": "application_owner.deleted",
        "when_field_changed": "user_id",
        "tell": "owners",
        "message": "An owner was removed from {subject}. "
                   "You are told because you still own it.",
        "url": ANSWER_URL,
        "fallback_url": ANSWERS_PAGE,
    },
    {
        "name": "cost_change_over_20_per_cent",
        "event_type": "cost_fact.updated",
        "when_field_changed": "amount",
        "when": {"change_over_percent": 20},
        "tell": "owners",
        "message": "The cost of {subject} changed from {before} to {after}, "
                   "{percent} per cent. You are told because you own it.",
        "url": ANSWER_URL,
        "fallback_url": ANSWERS_PAGE,
    },
    {
        "name": "risk_reaches_revenue_critical_system",
        "event_type": "risk.updated",
        "when_field_changed": "likelihood",
        "tell": "owners",
        "message": "{subject} now reaches a revenue-critical system. "
                   "You are told because you own it.",
        "url": ANSWER_URL,
        "fallback_url": ANSWERS_PAGE,
        "enabled": False,
        "disabled_reason": "Nothing records which systems are revenue-critical, and the "
                           "impact derivation does not say whether a risk reaches one. The "
                           "rule turns on when both exist; until then it would have to guess.",
    },
    {
        "name": "decision_condition_overdue",
        "event_type": None,
        "when_field_changed": None,
        "tell": "responsible",
        "message": "Condition {number} of the review {subject} was due on {due} and is "
                   "still open. You are told because you are responsible for it.",
        "url": "/arb/reviews/{review_item_id}",
        "fallback_url": "/arb/reviews",
        "alert_key": "condition-{entity_id}-{due_raw}",
        "daily_scan": "overdue_conditions",
    },
]

_BLANK = "not set"


def _shown(value: Any) -> str:
    return _BLANK if value is None or value == "" else str(value)


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_number(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _day(value: Any) -> str:
    """A date as a person reads it: 1 March 2027."""
    found = _as_date(value)
    return f"{found.day} {found:%B %Y}" if found else _shown(value)


def _amount(value: Any) -> str:
    number = _as_number(value)
    if number is None:
        return _shown(value)
    text = f"{number:,.2f}"
    return text[:-3] if text.endswith(".00") else text


def is_enabled(rule: Dict[str, Any]) -> bool:
    return rule.get("enabled", True) is not False


def notification_type(rule: Dict[str, Any], event_type: Optional[str]) -> Optional[str]:
    """The event type a rule's notification carries."""
    return f"alert.{rule['name']}" if rule.get("name") else event_type


def _template_fields(template: str) -> List[str]:
    return [name for _, name, _, _ in Formatter().parse(template) if name]


def _link(rule: Dict[str, Any], fields: Dict[str, Any]) -> str:
    needed = _template_fields(rule["url"])
    if any(fields.get(name) is None for name in needed):
        return rule["fallback_url"]
    return rule["url"].format(**fields)


def _key(rule: Dict[str, Any], fields: Dict[str, Any]) -> Optional[str]:
    template = rule.get("alert_key")
    if not template:
        return None
    if any(fields.get(name) is None for name in _template_fields(template)):
        return None
    return template.format(**fields)


def _holds(rule: Dict[str, Any], entry: Dict[str, Any], today: date) -> Optional[Dict[str, Any]]:
    """Whether the rule's condition holds for this change; the extra message
    fields it worked out when it does, None when it does not."""
    when = rule.get("when") or {}
    if "date_within_days" in when:
        after = _as_date(entry.get("after"))
        if after is None or not today <= after <= today + timedelta(days=when["date_within_days"]):
            return None
    if "change_over_percent" in when:
        before, after = _as_number(entry.get("before")), _as_number(entry.get("after"))
        if before is None or after is None or before == 0:
            return None
        percent = abs(after - before) / abs(before) * 100
        if percent <= when["change_over_percent"]:
            return None
        return {"percent": f"{percent:.0f}"}
    return {}


def alert_for(
    rule: Dict[str, Any],
    *,
    recipients: Iterable[int],
    fields: Dict[str, Any],
    event_type: Optional[str],
    entity_type: Optional[str],
    entity_id: Optional[int],
) -> List[Dict[str, Any]]:
    """The notifications one rule asks for, one per person told.

    ``fields`` fills the message, link and key templates. Returns dicts with
    ``user_id``, ``message``, ``url``, ``alert_key``, ``event_type``,
    ``entity_type``, ``entity_id``. No database access.
    """
    values = {
        "name": fields.get("name") or "An item you own",
        "subject": fields.get("subject") or fields.get("name") or "an item you own",
        "field": fields.get("field"),
        "before": _shown(fields.get("before")),
        "after": _shown(fields.get("after")),
        "percent": fields.get("percent"),
        "number": _shown(fields.get("number")),
        "due": _shown(fields.get("due")),
    }
    message = rule["message"].format(**values)
    url = _link(rule, fields)
    key = _key(rule, fields)
    found = []
    for user_id in dict.fromkeys(recipients):
        found.append({
            "user_id": user_id,
            "message": message,
            "url": url,
            "alert_key": key,
            "event_type": notification_type(rule, event_type),
            "entity_type": entity_type,
            "entity_id": entity_id,
        })
    return found


def candidates_for(event: Dict[str, Any], *, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """The notifications the rules ask for, for one event from the log.

    Returns one dict per person told: ``user_id``, ``message``, ``url``,
    ``alert_key``, ``event_type``, ``entity_type``, ``entity_id``. No database
    access.
    """
    today = today or date.today()
    payload = event.get("payload") or {}
    changed = {entry.get("field"): entry for entry in payload.get("changes") or []}
    found: List[Dict[str, Any]] = []
    for rule in RULES:
        if not is_enabled(rule) or rule["event_type"] is None:
            continue
        if rule["event_type"] != event.get("event_type"):
            continue
        entry = changed.get(rule["when_field_changed"])
        if entry is None:
            continue
        extra = _holds(rule, entry, today)
        if extra is None:
            continue
        is_date = bool((rule.get("when") or {}).get("date_within_days"))
        is_amount = bool((rule.get("when") or {}).get("change_over_percent"))
        show = _day if is_date else _amount if is_amount else (lambda value: value)
        fields = {
            "name": payload.get("name"),
            "subject": payload.get("subject_name") or payload.get("name"),
            "field": entry.get("field"),
            "before": show(entry.get("before")) if entry.get("before") is not None else None,
            "after": show(entry.get("after")) if entry.get("after") is not None else None,
            "after_raw": entry.get("after"),
            "archimate_element_id": payload.get("archimate_element_id"),
            "entity_id": event.get("entity_id"),
            **extra,
        }
        recipients = (payload.get("owner_user_ids") or []) if rule["tell"] == "owners" else []
        found.extend(alert_for(
            rule,
            recipients=recipients,
            fields=fields,
            event_type=event["event_type"],
            entity_type=event.get("entity_type"),
            entity_id=event.get("entity_id"),
        ))
    return found


def daily_rules() -> List[Dict[str, Any]]:
    """The enabled rules the daily check also runs."""
    return [rule for rule in RULES if is_enabled(rule) and rule.get("daily_scan")]


def day_shown(value: Any) -> str:
    """A date as the alerts write it, for the daily check's messages."""
    return _day(value)


__all__ = ["RULES", "alert_for", "candidates_for", "daily_rules", "day_shown", "is_enabled"]
