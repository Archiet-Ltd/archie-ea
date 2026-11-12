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

import json
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
    label = item["source_label"]
    if label and label.strip().lower() == text.lower():
        label = None  # the label only says the origin again
    if label and item["source_kind"] in ("import", "connector"):
        text = "%s from %s" % (text, label)
    elif label and item["source_kind"] == "manual":
        text = "%s (%s)" % (text, label)
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
        db.select(cols.id, cols.name, cols.source_kind, cols.source_label, cols.source_ref, cols.source_recorded_at,
                  cols.recorded_at).where(db.or_(cols.organization_id == organization_id, cols.organization_id.is_(None)),
                       cols.id.in_(ids))
    ).all()
    out = {}
    for row in rows:
        info = {"source_kind": row.source_kind, "source_label": row.source_label, "source_ref": row.source_ref}
        if row.source_kind == "connector":
            info["source_label"] = _connector_label(info)
        out[row.id] = {"source_kind": info["source_kind"], "source_label": info["source_label"],
                       "as_of": _naive_utc(row.source_recorded_at or row.recorded_at), "name": row.name}
    return out


def risk_footer(risk, touches: List[Dict[str, Any]], why: Optional[str],
                known: Dict[int, Dict[str, Any]], risk_href: Optional[str],
                touch_href, now: Optional[datetime] = None,
                score_line: Optional[str] = None) -> Dict[str, Any]:
    """Footer for one ranked risk: its score (``score_line``, naming the pair it comes from), then each element it is linked to or reaches.

    ``why`` is ``no_links`` or ``not_computed`` when the reach could not be worked out; either is a
    missing input and is named. ``known`` is ``element_facts`` for the touched elements; a touched element
    absent from it (another organisation's or deleted) is a missing input that reads NOT_IN_MODEL, once, and
    is never named: the caller-supplied name is not trusted.
    ``touch_href(touch)`` gives the link for a touched record.
    """
    facts = [fact(score_line or "Recorded score: likelihood %d of 5, impact %d of 5" % (risk.likelihood, risk.impact),
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


# ---------------------------------------------------------------------------------------------------
# Footers for the records the portfolio, vendor, work package and capability pages list. Each builder
# reads the columns a record already carries (its origin, owner, dates), looked up once for the whole
# page by id AND organisation, never one query per row.
# ---------------------------------------------------------------------------------------------------

_CONNECTOR_SOURCES = {"abacus": "Abacus", "power_platform_coe": "Power Platform CoE"}
_MAX_LISTED = 5


def origin_of(data_source: Optional[str] = None, sample: bool = False) -> Dict[str, Optional[str]]:
    """The origin a record's own columns state: entered by the team, a connector, an import or sample data.
    A record whose origin is not stated reads as entered by hand, which is what every form writes."""
    text = (data_source or "").strip().lower()
    if sample:
        return {"source_kind": "sample", "source_label": None}
    if text in _CONNECTOR_SOURCES:
        return {"source_kind": "connector", "source_label": _CONNECTOR_SOURCES[text]}
    if text.startswith("import") or text in ("csv", "excel", "xlsx", "lucid", "lucidchart"):
        return {"source_kind": "import", "source_label": data_source}
    if text in ("ai", "derived", "capability", "gap", "application"):
        return {"source_kind": "derived", "source_label": None}
    return {"source_kind": "manual", "source_label": None}


def render_footer(footer: Dict[str, Any]) -> str:
    """The one footer as markup, for a page that draws its results in the browser from data the server sent."""
    from flask import render_template_string

    return render_template_string(
        "{% from 'components/provenance.html' import result_footer %}{{ result_footer(footer) }}", footer=footer)


def application_rows(organization_id: int, application_ids: Iterable[int]) -> Dict[int, Any]:
    """The organisation's applications by id, one query. Another organisation's id is simply absent."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    ids = sorted({int(i) for i in application_ids if i is not None})
    if not ids or organization_id is None:
        return {}
    cols = ApplicationComponent
    rows = db.session.execute(
        db.select(cols.id, cols.name, cols.data_source, cols.archimate_element_id, cols.organization_id,
                  cols.updated_at, cols.created_at).where(
            cols.organization_id == organization_id, cols.id.in_(ids))
    ).all()
    return {r.id: r for r in rows}


def application_origins(organization_id: Optional[int], applications: Iterable[Any]) -> Dict[int, Dict[str, Any]]:
    """What the model records about the elements these applications stand for, read once for the whole page
    through ``element_facts`` (by id and organisation). Pass the answer to ``application_fact`` and friends."""
    return element_facts(organization_id, [getattr(a, "archimate_element_id", None) for a in applications])


def application_origin(row, origins: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Optional[str]]:
    """Where an application row came from. The model element it stands for states a connector as its source
    (a repository a person approved as an application keeps that origin), named as the connector and
    repository; a row whose element states none falls back to its own ``data_source`` and, failing that,
    reads as entered by hand. ``origins`` is ``application_origins`` for the page."""
    info = (origins or {}).get(getattr(row, "archimate_element_id", None))
    if info and info["source_kind"] == "connector":
        return {"source_kind": "connector", "source_label": info["source_label"]}
    return origin_of(row.data_source)


def _connector_label(info: Dict[str, Any]) -> Optional[str]:
    """The connector and repository in words a reader knows ("GitHub: acme/x"); the commit and file stay on
    the element's own page."""
    try:
        ref = json.loads(info.get("source_ref") or "")
    except (TypeError, ValueError):
        ref = None
    if isinstance(ref, dict) and ref.get("system") and ref.get("repository"):
        from app.modules.intelligence.services.answer_envelope import connector_system

        return "%s: %s" % (connector_system(ref), ref["repository"])
    return info.get("source_label")


def application_fact(row, subject: str = "Application",
                     origins: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    origin = application_origin(row, origins)
    return fact(row.name, href=page_link("unified_applications.application_detail", id=row.id),
                source_kind=origin["source_kind"], source_label=origin["source_label"],
                as_of=row.updated_at or row.created_at, subject=subject)


def application_footer(app, owner_names: Optional[List[str]], now: Optional[datetime] = None,
                       origins: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Footer for one listed application: the record itself, then who owns it."""
    facts = [application_fact(app, "Record", origins)]
    if owner_names:
        facts.append(fact("Owner: %s" % ", ".join(owner_names),
                          href=page_link("unified_applications.application_detail", id=app.id),
                          source_kind="manual", subject="Owner", as_of=app.updated_at or app.created_at))
    else:
        facts.append(fact("Owner: none recorded", missing=True))
    return build_footer(facts, now)


def systems_facts(organization_id: int, system_ids: List[int], total: Optional[int] = None,
                  more_href: Optional[str] = None) -> List[Dict[str, Any]]:
    """Facts for the first few systems named in a result; the rest are counted, not listed."""
    known = application_rows(organization_id, system_ids[:_MAX_LISTED])
    origins = application_origins(organization_id, known.values())
    facts = [application_fact(known[i], "System", origins) for i in system_ids[:_MAX_LISTED] if i in known]
    rest = (total if total is not None else len(system_ids)) - len(facts)
    if rest > 0:
        facts.append(fact("%d more system%s" % (rest, "" if rest == 1 else "s"), href=more_href,
                          source_kind="derived", subject="Systems"))
    return facts


def vendor_footers(organization_id: int, rows: List[Dict[str, Any]],
                   now: Optional[datetime] = None) -> Dict[int, Dict[str, Any]]:
    """Footer for each vendor row of the register: the vendor record, its systems and the cost worked out
    from them. ``rows`` are ``vendor_register.register()`` rows."""
    from app import db
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.services import vendor_register

    ids = [r["id"] for r in rows]
    vendors = {}
    if ids and organization_id is not None:
        # A vendor is shared across organisations; this one's are those its applications and contracts name.
        seen = set(vendor_register.vendor_ids(organization_id))
        ids = [i for i in ids if i in seen]
    if ids:
        vendors = {v.id: v for v in db.session.execute(
            db.select(VendorOrganization.id, VendorOrganization.is_seed_data, VendorOrganization.updated_at,
                      VendorOrganization.created_at).where(VendorOrganization.id.in_(ids))).all()}
    every = [s["id"] for r in rows for s in r["systems"][:_MAX_LISTED]]
    known = application_rows(organization_id, every)
    origins = application_origins(organization_id, known.values())
    out = {}
    for r in rows:
        v = vendors.get(r["id"])
        record = fact(r["name"], href=page_link("unified_applications.vendor_detail", vendor_id=r["id"]),
                      source_kind="sample" if (v is not None and v.is_seed_data) else "manual",
                      as_of=(v.updated_at or v.created_at) if v is not None else None, subject="Vendor")
        facts = [record]
        detail = page_link("unified_applications.vendor_detail", vendor_id=r["id"])
        for s in r["systems"][:_MAX_LISTED]:
            if s["id"] in known:
                facts.append(application_fact(known[s["id"]], "System", origins))
        rest = r["system_count"] - len([f for f in facts if f["subject"] == "System"])
        if rest > 0:
            facts.append(fact("%d more system%s" % (rest, "" if rest == 1 else "s"),
                              href=detail and detail + "#vendor-systems", source_kind="derived", subject="Systems"))
        if not r["system_count"]:
            facts.append(fact("Systems: none name this vendor", missing=True))
        out[r["id"]] = build_footer(facts, now)
    return out


def vendor_detail_footer(organization_id: int, vendor, systems: List[Any], contracts: List[Any],
                         now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for a vendor's Systems supplied answer: the vendor, its systems and its contracts. It sits on the
    vendor's own page, under the vendor's name, so it names the record rather than the vendor again, and never
    links to itself."""
    facts = [fact("Vendor record", source_kind="sample" if getattr(vendor, "is_seed_data", False) else "manual",
                  as_of=vendor.updated_at or vendor.created_at, subject="Vendor")]
    facts += systems_facts(organization_id, [s.id for s in systems], total=len(systems))
    for contract in contracts[:_MAX_LISTED]:
        facts.append(fact(contract.contract_name or "Contract", source_kind="manual", subject="Contract",
                          as_of=getattr(contract, "updated_at", None) or getattr(contract, "created_at", None)))
    if not systems and not contracts:
        facts.append(fact("Systems and contracts: none recorded", missing=True))
    return build_footer(facts, now)


def work_package_facts(wp, link_self: bool = True, name_shown: bool = False) -> List[Dict[str, Any]]:
    """The facts one work package's progress and priority rest on. ``name_shown`` is for a row that already
    prints the package's name and links it: the footer then names the record, not the package again."""
    origin = origin_of(getattr(wp, "source_type", None))
    when = getattr(wp, "updated_at", None) or getattr(wp, "created_at", None)
    # The package's own page never links to itself.
    href = page_link("implementation_planning.work_package_detail", work_package_id=wp.id) if link_self and not name_shown else None
    facts = [fact("Work package record" if name_shown else wp.name, href=href, source_kind=origin["source_kind"], source_label=origin["source_label"],
                  as_of=when, subject="Work package")]
    # The figure every screen shows: the one work_package_service computes from status, dates and the recorded value.
    from app.services.work_package_service import progress_percent

    progress = progress_percent(wp)
    if progress is None:
        facts.append(fact("Progress: not recorded", missing=True))
    else:
        facts.append(fact("Progress: %d%%" % progress, href=href, source_kind="manual", subject="Progress", as_of=when))
    if not (getattr(wp, "priority", None) or "").strip():
        facts.append(fact("Priority: not recorded", missing=True))
    return facts


def work_package_footer(wp, now: Optional[datetime] = None, name_shown: bool = False) -> Dict[str, Any]:
    return build_footer(work_package_facts(wp, name_shown=name_shown), now)


def summary_footer(facts: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for a page's single answer (a coverage figure, a count): the facts are the caller's."""
    return build_footer(facts, now)


def gap_source_kind(gap) -> str:
    """A gap's own origin: derived when it was produced from another record (``source_table``), entered by
    hand otherwise; unknown when the row handed over does not carry the column at all."""
    if isinstance(gap, dict):
        if "source_table" not in gap:
            return "unknown"
        return "derived" if gap["source_table"] else origin_of(None)["source_kind"]
    return "derived" if getattr(gap, "source_table", None) else origin_of(None)["source_kind"]


def gap_rows(organization_id: int, gap_ids: Iterable[int]) -> Dict[int, Any]:
    """The organisation's gaps by id, one query, with the columns a footer states. Another organisation's id
    is simply absent."""
    from app import db
    from app.models.implementation_migration import Gap

    ids = sorted({int(i) for i in gap_ids if i is not None})
    if not ids or organization_id is None:
        return {}
    cols = Gap
    rows = db.session.execute(
        db.select(cols.id, cols.name, cols.source_table, cols.updated_at, cols.created_at).where(
            cols.organization_id == organization_id, cols.id.in_(ids))
    ).all()
    return {r.id: r for r in rows}


def gap_footer(gap, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for one listed gap: the gap record, then the severity it is ranked by."""
    kind = gap_source_kind(gap)
    when = getattr(gap, "updated_at", None) or getattr(gap, "created_at", None)
    href = page_link("archimate_crud.detail_element", layer="implementation", element_type="Gap", element_id=gap.id)
    facts = [fact(gap.name, href=href, source_kind=kind, as_of=when, subject="Gap")]
    from app.services.gap_register_service import effective_severity

    severity = (effective_severity(gap) or "").strip()
    if severity:
        facts.append(fact("Severity: %s" % severity, href=href, source_kind="manual", subject="Severity", as_of=when))
    else:
        facts.append(fact("Severity: not recorded", missing=True))
    return build_footer(facts, now)


def work_package_detail_footer(wp, owner_name: Optional[str], gaps: List[Any], capability=None,
                               now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for one work package's page: the package, its progress and priority, owner, the gaps it closes
    and the capability it serves. ``gaps`` and ``capability`` are the organisation's own, already fenced."""
    facts = work_package_facts(wp, link_self=False)
    if owner_name:
        facts.append(fact("Owner: %s" % owner_name, source_kind="manual", subject="Owner",
                          as_of=getattr(wp, "updated_at", None)))
    else:
        facts.append(fact("Owner: none recorded", missing=True))
    for gap in gaps[:_MAX_LISTED]:
        name = gap.get("name") if isinstance(gap, dict) else getattr(gap, "name", None)
        gap_id = gap.get("id") if isinstance(gap, dict) else getattr(gap, "id", None)
        if name:
            stamp = gap.get("updated_at") or gap.get("created_at") if isinstance(gap, dict) else \
                (getattr(gap, "updated_at", None) or getattr(gap, "created_at", None))
            facts.append(fact(name, href=page_link("archimate_crud.detail_element", layer="implementation",
                                                   element_type="Gap", element_id=gap_id),
                              source_kind=gap_source_kind(gap), subject="Gap", as_of=stamp))
    if capability is not None:
        facts.append(fact(capability.name, source_kind="manual", subject="Capability",
                          as_of=getattr(capability, "updated_at", None) or getattr(capability, "created_at", None)))
    return build_footer(facts, now)


def requirements_backlog_footer(organization_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for the requirements backlog: the requirements the organisation holds, how many are written by
    the product's assistant, and how many carry a definition of done. Counted from the one backlog store."""
    from app import db
    from app.models.solution_architect_models import SolutionRequirement as Req

    base = (Req.organization_id == organization_id, Req.deleted_at.is_(None))
    total = db.session.execute(db.select(db.func.count(Req.id)).where(*base)).scalar() or 0
    if not total:
        return build_footer([fact("Requirements: none recorded", missing=True)], now)
    drafted = db.session.execute(
        db.select(db.func.count(Req.id)).where(*base, Req.ai_generated.is_(True))).scalar() or 0
    rows = db.session.execute(db.select(Req.dod_checklist).where(*base)).scalars().all()
    with_dod = sum(1 for checklist in rows if checklist)
    facts = [
        fact("Requirements: %d in the backlog" % total, source_kind="manual", subject="Requirements"),
        fact("Written by the assistant: %d" % drafted, source_kind="derived", subject="Assistant drafts"),
    ]
    if with_dod:
        facts.append(fact("Definition of done: %d of %d requirements" % (with_dod, total), source_kind="manual",
                          subject="Definition of done"))
    else:
        facts.append(fact("Definition of done: none recorded", missing=True))
    return build_footer(facts, now)


_WSJF_INPUT_LABELS = (
    ("business_value", "business value"), ("time_criticality", "time criticality"),
    ("risk_reduction", "risk reduction"), ("job_size", "job size"),
)


def missing_wsjf_inputs(req) -> List[str]:
    """The WSJF inputs a requirement has not recorded, in words, in the order the page lists them."""
    return [label for key, label in _WSJF_INPUT_LABELS if getattr(req, key, None) is None]


def backlog_score_footer(req, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for a requirement's WSJF score: the inputs recorded, each missing input named, and the date they
    were last recorded. The score itself is worked out by ``Requirement.wsjf_score``, never typed in."""
    href = page_link("enterprise_crud.requirements_backlog")
    when = getattr(req, "updated_at", None) or getattr(req, "created_at", None)
    facts = [fact("%s %s" % (label.capitalize(), getattr(req, key)), href=href, source_kind="manual", as_of=when)
             for key, label in _WSJF_INPUT_LABELS if getattr(req, key, None) is not None]
    for label in missing_wsjf_inputs(req):
        facts.append(fact("%s: not recorded" % label.capitalize(), missing=True))
    return build_footer(facts, now)


def roadmap_footer(work_packages: List[Any], total: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for a roadmap drawn from work packages: the first few, then a count of the rest. ``total`` is
    every package the roadmap draws; none at all is a missing input."""
    facts = [work_package_facts(wp)[0] for wp in work_packages[:_MAX_LISTED]]
    rest = total - len(facts)
    if rest > 0:
        facts.append(fact("%d more work package%s" % (rest, "" if rest == 1 else "s"), source_kind="derived",
                          subject="Work packages"))
    if not total:
        facts.append(fact("Work packages: none recorded", missing=True))
    return build_footer(facts, now)


def capability_footers(organization_id: int, scores: List[Dict[str, Any]],
                       now: Optional[datetime] = None) -> Dict[int, Dict[str, Any]]:
    """Footer for each scored capability of the investment matrix: the capability record, the importance and
    maturity its score rests on, the applications that cover it and the budget behind its cost. One query for
    the page, by id AND organisation (or as shared reference data, which has no organisation)."""
    from app import db
    from app.models.unified_capability import UnifiedCapability

    ids = sorted({int(s["capability_id"]) for s in scores if s.get("capability_id") is not None})
    if not ids or organization_id is None:
        return {}
    cols = UnifiedCapability
    rows = {r.id: r for r in db.session.execute(
        db.select(cols.id, cols.source_table, cols.updated_at, cols.created_at, cols.strategic_importance,
                  cols.current_maturity_level, cols.target_maturity_level, cols.maturity_assessment_date).where(
            db.or_(cols.organization_id == organization_id, cols.organization_id.is_(None)), cols.id.in_(ids))
    ).all()}
    out = {}
    for s in scores:
        row = rows.get(s.get("capability_id"))
        if row is None:
            out[s.get("capability_id")] = build_footer([fact(NOT_IN_MODEL, missing=True)], now)
            continue
        when = row.updated_at or row.created_at
        facts = [fact(s["capability_name"], source_kind="derived" if row.source_table else "manual", as_of=when,
                      subject="Capability")]
        if row.strategic_importance:
            facts.append(fact("Strategic importance: %s" % row.strategic_importance, source_kind="manual",
                              subject="Importance", as_of=when))
        else:
            facts.append(fact("Strategic importance: not recorded", missing=True))
        if row.current_maturity_level is not None and row.target_maturity_level is not None:
            facts.append(fact("Maturity: level %d now, level %d target" % (row.current_maturity_level,
                                                                           row.target_maturity_level),
                              source_kind="manual", subject="Maturity", as_of=row.maturity_assessment_date or when))
        else:
            facts.append(fact("Maturity: not assessed", missing=True))
        facts.append(fact("Applications covering it: %d" % s["coverage_count"], source_kind="derived",
                          subject="Coverage", as_of=when))
        if s.get("has_real_budget"):
            facts.append(fact("Budget: initiative %s" % s["linked_initiative_name"], source_kind="manual",
                              subject="Budget", as_of=when))
        elif (s.get("investment_need") or {}).get("estimated_cost") is not None:
            facts.append(fact("Budget: no linked initiative, cost is the estimate on %s" % s["investment_need"]["source"],
                              source_kind="derived", subject="Budget", as_of=when))
        else:
            facts.append(fact("Budget: no linked initiative or costed work, not estimated", missing=True))
        out[s["capability_id"]] = build_footer(facts, now)
    return out


def attach_capability_footers(organization_id: int, scores: List[Dict[str, Any]]) -> None:
    """Give each scored capability its footer as markup (``footer_html``), for the pages that draw the matrix's
    rows in the browser. The one place this is done, so the matrix and the priorities page cannot differ."""
    footers = capability_footers(organization_id, scores)
    for score in scores:
        footer = footers.get(score["capability_id"])
        score["footer_html"] = render_footer(footer) if footer else ""


def plateau_footer(plateau, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for one plateau of the roadmap: the plateau and the target date it is placed by."""
    when = getattr(plateau, "updated_at", None) or getattr(plateau, "created_at", None)
    facts = [fact(plateau.name, source_kind="manual", subject="Plateau", as_of=when)]
    target = getattr(plateau, "target_date", None)
    if target:
        facts.append(fact("Target date: %s" % _day(target), source_kind="manual", subject="Target date", as_of=when))
    else:
        facts.append(fact("Target date: not recorded", missing=True))
    return build_footer(facts, now)


def roadmap_summary_footer(organization_id: int, capability_count: int, domain_count: int,
                           now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for the roadmap's headline counts. The capability and domain counts are the ones the cards show
    (passed in, so footer and card cannot disagree); gaps, plateaus and work packages are counted for this
    organisation. Each is dated by the newest record behind it."""
    from app import db
    from app.models.implementation_migration import GAP_KIND_PLATEAU_TRANSITION, Gap, Plateau
    from app.services.gap_register_service import count_gaps
    from app.models.unified_capability import BusinessDomain, UnifiedCapability
    from app.models.unified_work_package import UnifiedWorkPackage

    def named(label, count, subject, endpoint, newest):
        return fact("%s: %s" % (label, count if count else "none recorded"),
                    href=page_link(endpoint) if count else None, source_kind="derived", subject=subject,
                    as_of=newest, missing=not count)

    def counted(model, label, subject, endpoint):
        count, newest = db.session.execute(
            db.select(db.func.count(model.id), db.func.max(model.updated_at)).where(
                model.organization_id == organization_id)).one()
        return named(label, count, subject, endpoint, newest)

    # Gaps are the register's count (plateau transitions left out), the figure the Recorded gaps card shows.
    gaps_newest = db.session.execute(db.select(db.func.max(Gap.updated_at)).where(
        Gap.organization_id == organization_id, Gap.gap_kind != GAP_KIND_PLATEAU_TRANSITION)).scalar()
    gap_count_fact = named("Gaps", count_gaps(organization_id), "Gaps", "enterprise.gap_analysis", gaps_newest)
    capabilities_newest = db.session.execute(db.select(db.func.max(UnifiedCapability.updated_at))).scalar()
    domains_newest = db.session.execute(db.select(db.func.max(BusinessDomain.updated_at))).scalar()
    return build_footer([
        named("Capabilities", capability_count, "Capabilities", "capability_map.index", capabilities_newest),
        named("Domains", domain_count, "Domains", "capability_map.index", domains_newest),
        gap_count_fact,
        counted(Plateau, "Plateaus", "Plateaus", "implementation_planning.plateaus_list"),
        counted(UnifiedWorkPackage, "Work packages", "Work packages", "implementation_planning.work_packages_list"),
    ], now)


def connector_repository(element) -> Optional[Dict[str, Any]]:
    """The repository a connector read this element from: its label in words ("GitHub: acme/shop") and the
    address of the repository, or None when the element did not come from a repository."""
    if getattr(element, "source_kind", None) != "connector":
        return None
    try:
        ref = json.loads(getattr(element, "source_ref", None) or "")
    except (TypeError, ValueError):
        return None
    if not (isinstance(ref, dict) and ref.get("system") and ref.get("repository")):
        return None
    label = _connector_label({"source_ref": element.source_ref, "source_label": element.source_label})
    href = None
    if str(ref["system"]).strip().lower() == "github":
        href = "https://github.com/%s" % str(ref["repository"]).strip("/")
    return {"label": label, "href": href}


def product_footer(organization_id: int, product_id: int, member_ids: List[int], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for a product's summary: the product, the first few elements it is made of, and the count of the rest."""
    listed = member_ids[:_MAX_LISTED]
    known = element_facts(organization_id, [product_id] + listed)

    def element(element_id, subject):
        info = known.get(element_id)
        if info is None:
            return None
        return fact(info["name"], href=page_link("archimate.element_impact_graph_page", element_id=element_id),
                    source_kind=info["source_kind"] or "unknown", source_label=info["source_label"],
                    as_of=info["as_of"], subject=subject)

    facts = [element(product_id, "Product") or fact(NOT_IN_MODEL, missing=True)]
    for element_id in listed:
        one = element(element_id, "Connected element")
        if one is not None:
            facts.append(one)
    rest = len(member_ids) - len(listed)
    if rest > 0:
        facts.append(fact("%d more connected element%s" % (rest, "" if rest == 1 else "s"), source_kind="derived",
                          subject="Connected elements"))
    if not member_ids:
        facts.append(fact("Connected elements: none recorded", missing=True))
    return build_footer(facts, now)


def impact_footer(organization_id: int, analysis: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Footer for an impact analysis: the element being changed, the elements it reaches and the cost at risk.

    Elements are looked up by id AND organisation; one the organisation cannot see is a single missing input and
    is never named. The first few reached elements are listed, the rest counted.
    """
    reached = list(analysis.get("direct_dependencies") or []) + list(analysis.get("indirect_dependencies") or [])
    listed = reached[:_MAX_LISTED]
    ids = [analysis.get("element_id")] + [d.get("id") for d in listed]
    known = element_facts(organization_id, [i for i in ids if isinstance(i, int)])

    def element(element_id, subject):
        info = known.get(element_id)
        if info is None:
            return None
        return fact(info["name"], href=page_link("archimate.element_impact_graph_page", element_id=element_id),
                    source_kind=info["source_kind"] or "unknown", source_label=info["source_label"],
                    as_of=info["as_of"], subject=subject)

    facts = []
    changed = element(analysis.get("element_id"), "Changed element")
    facts.append(changed or fact(NOT_IN_MODEL, missing=True))
    unlisted = False
    for dep in listed:
        one = element(dep.get("id"), "Reached element")
        if one is not None:
            facts.append(one)
        elif not unlisted:
            facts.append(fact(NOT_IN_MODEL, missing=True))
            unlisted = True
    rest = int(analysis.get("total_affected") or len(reached)) - len(listed)
    if rest > 0:
        facts.append(fact("%d more element%s reached" % (rest, "" if rest == 1 else "s"), source_kind="derived",
                          subject="Reach"))
    if not reached:
        facts.append(fact("Reached elements: none recorded", missing=True))
    if analysis.get("estimated_financial_risk") is None:
        facts.append(fact("Cost at risk: could not be worked out", missing=True))
    else:
        facts.append(fact("Cost at risk: worked out from the yearly running cost of the applications at stake", source_kind="derived",
                          subject="Cost at risk"))
    return build_footer(facts, now)
