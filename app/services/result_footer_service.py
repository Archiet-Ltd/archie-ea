"""The one footer every ranked or computed result carries: evidence, source, confidence and date.

A result (a ranked risk, a prioritised action) is computed from facts held in the model. This service is
the only place that turns those facts into the footer; templates draw what it returns and compute nothing.

A fact is a plain dict (see ``fact``): a label, a link to the record, where it came from, who put it there,
the date it was last recorded, and whether it is missing.

The four parts
  * evidence    the facts the result was computed from, each with a link to its record;
  * source      where each fact came from (entered by the team, imported, a connector, worked out by the
                product, sample data) and who, one line per distinct origin;
  * confidence  high, medium or low by CONFIDENCE_RULE below, with the reason in words;
  * date        when the result was computed and the newest input's date.

CONFIDENCE_RULE
  * low     an input is missing (a fact flagged missing, or no input facts at all). The footer names it.
  * medium  nothing is missing, but some input is older than 90 days, has no date, has no recorded origin,
            or was entered by hand or is sample data. The reason names which input it is.
  * high    every input has a recorded origin (imported, a connector, or worked out by the product) and a
            date within the last 90 days.

Isolation: facts read from the model are looked up by id AND organisation (``element_facts``; a shared
reference element, which has no organisation, is looked up as such), so another organisation's record can
never appear in a footer, even from a stale or guessed id, and a caller-supplied name is never printed for
an element the lookup did not return.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

STALE_DAYS = 90

CONFIDENCE_RULE = (
    "High when every input has a recorded origin (imported, a connector or worked out by the product) and a "
    "date within 90 days. Medium when an input is older, undated, entered by hand or sample data. "
    "Low when an input is missing."
)

_ORIGIN_TEXT = {
    "import": "Imported",
    "connector": "Synced by a connector",
    "derived": "Worked out by the product",
    "sample": "Sample data",
    "manual": "Entered by the team",
    "unknown": "No recorded origin",
}
NOT_IN_MODEL = "A linked element is not in this organisation's model"
_RECORDED_ORIGINS = ("import", "connector", "derived")


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Stored dates are naive or timezone-aware depending on the table; compare them all as naive UTC."""
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def fact(label: str, href: Optional[str] = None, source_kind: Optional[str] = None,
         source_label: Optional[str] = None, who: Optional[str] = None,
         as_of: Optional[datetime] = None, missing: bool = False,
         subject: Optional[str] = None) -> Dict[str, Any]:
    """One input to a result. ``source_kind`` is manual, import, connector, derived, sample or unknown;
    unrecorded reads as entered by hand. ``subject`` is the short name of what the fact is ("Score",
    "Element"); with it the footer's source line says which fact each origin belongs to."""
    return {"label": label, "subject": (subject or "").strip() or None, "href": href, "source_kind": source_kind or "manual",
            "source_label": (source_label or "").strip() or None, "who": (who or "").strip() or None,
            "as_of": _naive_utc(as_of), "missing": missing}


def _origin_text(item: Dict[str, Any]) -> str:
    text = _ORIGIN_TEXT.get(item["source_kind"], _ORIGIN_TEXT["manual"])
    if item["source_label"] and item["source_kind"] in ("import", "connector"):
        text = "%s from %s" % (text, item["source_label"])
    elif item["source_label"] and item["source_kind"] == "manual":
        text = "%s (%s)" % (text, item["source_label"])
    if item["who"] and item["who"] not in text:
        text = "%s, %s" % (text, item["who"])
    return text


def _day(value: datetime) -> str:
    return "%d %s %d" % (value.day, value.strftime("%b"), value.year)


def _names(items: List[Dict[str, Any]], pool: List[Dict[str, Any]]) -> str:
    """Short names for ``items``: the subject when it is unique among ``pool``, else the full label."""
    names = []
    for item in items:
        subject = item["subject"]
        unique = subject and sum(1 for other in pool if other["subject"] == subject) == 1
        names.append(subject if unique else item["label"])
    shown = ", ".join(names[:3])
    return shown if len(names) <= 3 else "%s and %d more" % (shown, len(names) - 3)


def _weak_inputs(present: List[Dict[str, Any]], cutoff: datetime) -> str:
    """The medium reason: which input is hand-entered, sample data, undated, old or of unknown origin."""
    checks = (
        ("was entered by hand", "were entered by hand", lambda i: i["source_kind"] == "manual"),
        ("is sample data", "are sample data", lambda i: i["source_kind"] == "sample"),
        ("has no recorded origin", "have no recorded origin", lambda i: i["source_kind"] == "unknown"),
        ("has no recorded date", "have no recorded date", lambda i: i["as_of"] is None),
        ("is older than %d days" % STALE_DAYS, "are older than %d days" % STALE_DAYS,
         lambda i: i["as_of"] is not None and i["as_of"] < cutoff),
    )
    parts = []
    for one, many, test in checks:
        hit = [i for i in present if test(i)]
        if hit:
            parts.append("%s %s" % (_names(hit, present), one if len(hit) == 1 else many))
    return "; ".join(parts)


def _source_lines(present: List[Dict[str, Any]]) -> List[str]:
    """One line per distinct origin, each paired with the fact it belongs to when the fact has a subject."""
    origins: Dict[str, set] = {}
    for item in present:
        if item["subject"]:
            origins.setdefault(item["subject"], set()).add(_origin_text(item))
    lines: List[str] = []
    for item in present:
        text = _origin_text(item)
        if item["subject"]:
            lead = item["subject"] if len(origins[item["subject"]]) == 1 else item["label"]
            text = "%s: %s%s" % (lead, text[:1].lower(), text[1:])
        if text not in lines:
            lines.append(text)
    return lines


def build_footer(facts: Iterable[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """The footer for a result computed from ``facts``. Pure: no query, no request."""
    now = now or datetime.utcnow()
    items = list(facts)
    missing = [i["label"] for i in items if i["missing"]]
    present = [i for i in items if not i["missing"]]
    dates = [i["as_of"] for i in present if i["as_of"] is not None]
    newest = max(dates) if dates else None
    cutoff = now - timedelta(days=STALE_DAYS)

    if missing or not items:
        level = "low"
        reason = ("Missing: %s." % "; ".join(missing)) if missing else "Missing: no input facts are recorded."
    else:
        weak = _weak_inputs(present, cutoff)
        level = "medium" if weak else "high"
        reason = (weak[:1].upper() + weak[1:] + ".") if weak else (
            "Every input has a recorded origin and a date within %d days." % STALE_DAYS)

    sources = _source_lines(present)
    return {
        "evidence": [{"label": i["label"], "href": i["href"], "missing": i["missing"]} for i in items],
        "sources": sources,
        "confidence": level,
        "confidence_reason": reason,
        "missing": missing,
        "rule": CONFIDENCE_RULE,
        "computed_at": now,
        "computed_text": _day(now),
        "newest_input": newest,
        "newest_input_text": _day(newest) if newest else None,
    }


def page_link(endpoint: str, **values) -> Optional[str]:
    """The address of a page, or None when it cannot be built (blueprint not registered, or no request):
    a result then shows its evidence unlinked rather than linked to a dead page."""
    from flask import url_for
    from werkzeug.routing import BuildError

    try:
        return url_for(endpoint, **values)
    except (BuildError, RuntimeError):
        return None


def element_facts(organization_id: int, element_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """What the model records about each element: its origin and the date it was recorded.

    Looked up by id AND organisation (or as a shared reference element, which has none); an id from another
    organisation is simply absent from the answer.
    """
    from app import db
    from app.models.archimate_core import ArchiMateElement

    ids = sorted({int(e) for e in element_ids if e is not None})
    if not ids or organization_id is None:
        return {}
    cols = ArchiMateElement
    rows = db.session.execute(
        db.select(cols.id, cols.name, cols.source_kind, cols.source_label, cols.source_recorded_at,
                  cols.recorded_at).where(db.or_(cols.organization_id == organization_id, cols.organization_id.is_(None)),
                       cols.id.in_(ids))
    ).all()
    out = {}
    for row in rows:
        out[row.id] = {"source_kind": row.source_kind, "source_label": row.source_label,
                       "as_of": _naive_utc(row.source_recorded_at or row.recorded_at), "name": row.name}
    return out


def risk_footer(risk, touches: List[Dict[str, Any]], why: Optional[str],
                known: Dict[int, Dict[str, Any]], risk_href: Optional[str],
                touch_href, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for one ranked risk: its recorded score, then each element it is linked to or reaches.

    ``why`` is ``no_links`` or ``not_computed`` when the reach could not be worked out; either is a
    missing input and is named. ``known`` is ``element_facts`` for the touched elements; a touched element
    absent from it (another organisation's or deleted) is a missing input that reads NOT_IN_MODEL, once, and
    is never named: the caller-supplied name is not trusted.
    ``touch_href(touch)`` gives the link for a touched record.
    """
    facts = [fact("Recorded score: likelihood %d of 5, impact %d of 5" % (risk.likelihood, risk.impact),
                  href=risk_href, source_kind="manual", who=risk.owner, subject="Score",
                  as_of=risk.updated_at or risk.created_at)]
    unlisted = False
    for touch in touches:
        if touch.get("element_id") is None:
            facts.append(fact(touch["name"], href=touch_href(touch), subject="Application"))
            continue
        info = known.get(touch["element_id"])
        if info is None:
            if not unlisted:
                facts.append(fact(NOT_IN_MODEL, missing=True))
                unlisted = True
            continue
        facts.append(fact(touch["name"], href=touch_href(touch), source_kind=info["source_kind"] or "unknown",
                          source_label=info["source_label"], as_of=info["as_of"], subject="Element"))
    if why == "no_links":
        facts.append(fact("Linked elements: none recorded", missing=True))
    elif why == "not_computed":
        facts.append(fact("How far it reaches: could not be worked out", missing=True))
    return build_footer(facts, now)
