"""The architecture health-check report, assembled from the analyses the product already runs.

``build_report(org_id)`` reads one organisation through the same services the
product screens read and returns a plain dictionary of six sections plus an
answer-first summary. Nothing is computed here that a screen does not already
compute: it calls the owner, cost, duplicate, scoring, impact and contract
accessors and arranges what they return. Every sentence is a template filled
from data, so the report builds when every model provider is down.

A missing value is returned as ``None`` and shown as a gap by the template,
never filled in. Each finding carries ``source``: the name of the record the
figure came from, in words a client reads.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional

from app import db
from app.services.application_cost_accessor import get_reporting_currency

# The inventory fields a client is told to fill, in the order they are reported.
# (key, label)
INVENTORY_FIELDS = [
    ("owner", "Owner"),
    ("annual_cost", "Annual cost"),
    ("lifecycle", "Lifecycle stage"),
    ("criticality", "Business criticality"),
    ("business_unit", "Business unit"),
    ("vendor", "Vendor"),
]

_CRITICAL_LEVELS = ("critical", "high")
_CRITICALITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
IMPACT_DEPTH = 3
IMPACT_LISTED = 8
RENEWAL_WINDOW_DAYS = 90
DUPLICATE_THRESHOLD = 0.55
NO_OWNER = "No owner recorded"


def _text(value) -> Optional[str]:
    value = (value or "").strip() if isinstance(value, str) else value
    return value or None


def _money(value) -> Optional[float]:
    return None if value is None else float(value)


def _owner_name(application) -> Optional[str]:
    """The recorded accountable owner's name, or None when nobody is recorded."""
    from app.services.element_ownership_service import application_is_owned, application_owner_text

    if not application_is_owned(application):
        return None
    return (
        _text(application_owner_text(application, "application_owner"))
        or _text(application_owner_text(application, "business_owner"))
        or _text(application.legacy_application_owner)
        or _text(application.legacy_business_owner)
    )


def _criticality(application) -> Optional[str]:
    value = _text(application.business_criticality) or _text(application.criticality)
    return value.lower() if value else None


def _applications() -> list:
    from app.models.application_portfolio import ApplicationComponent
    from app.services.element_ownership_service import preload_application_owner_text

    apps = ApplicationComponent.query.order_by(ApplicationComponent.name).all()
    preload_application_owner_text(apps)
    return apps


def _inventory(apps: list) -> Dict[str, Any]:
    from app.services.application_cost_accessor import get_annual_cost_with_source, has_recorded_cost

    rows = []
    for app in apps:
        cost, _source = get_annual_cost_with_source(app)
        values = {
            "owner": _owner_name(app),
            "annual_cost": cost if has_recorded_cost(app) else None,
            "lifecycle": _text(app.lifecycle_status),
            "criticality": _criticality(app),
            "business_unit": _text(app.business_domain),
            "vendor": _text(app.vendor_name),
        }
        rows.append({
            "name": app.name,
            "source": "Application record: %s" % app.name,
            **values,
            "missing": [label for key, label in INVENTORY_FIELDS if values[key] is None],
        })
    total = len(rows)
    completeness = []
    for key, label in INVENTORY_FIELDS:
        filled = sum(1 for r in rows if r[key] is not None)
        completeness.append({
            "field": label,
            "filled": filled,
            "missing": total - filled,
            "total": total,
            "percent": round(100 * filled / total) if total else None,
        })
    return {"rows": rows, "total": total, "completeness": completeness}


def _capability_owners(element_ids: list) -> Dict[int, str]:
    """{ArchiMate element id: owner name} for capabilities, from the one ownership record."""
    from app.models.application_owner import ApplicationOwner
    from app.models.unified_capability import UnifiedCapability
    from app.models.user import User
    from app.services.capability_ownership_service import ELEMENT_TYPE_CAPABILITY
    from app.services.element_ownership_service import owner_label

    if not element_ids:
        return {}
    caps = {c.id: c.archimate_element_id for c in UnifiedCapability.query.filter(
        UnifiedCapability.archimate_element_id.in_(element_ids)).all()}
    if not caps:
        return {}
    out: Dict[int, str] = {}
    rows = (db.session.query(ApplicationOwner.element_id, User.first_name, User.last_name, User.email)
            .join(User, User.id == ApplicationOwner.user_id)
            .filter(ApplicationOwner.element_type == ELEMENT_TYPE_CAPABILITY,
                    ApplicationOwner.element_id.in_(list(caps)))
            .order_by(ApplicationOwner.id).all())
    for cap_id, first, last, email in rows:
        out.setdefault(caps[cap_id], owner_label(first, last, email))
    return out


def _impact(apps: list) -> Dict[str, Any]:
    """What would stop if each critical system failed, nearest first, with owners."""
    from app.models.archimate_core import ArchiMateRelationship
    from app.services.impact_graph import build_impact_graph

    by_element = {a.archimate_element_id: a for a in apps if a.archimate_element_id}
    critical = [a for a in apps if _criticality(a) in _CRITICAL_LEVELS]
    critical.sort(key=lambda a: (_CRITICALITY_ORDER.get(_criticality(a), 9), a.name.lower()))

    graphs = {a.id: build_impact_graph(a.archimate_element_id, IMPACT_DEPTH)
              for a in critical if a.archimate_element_id}
    capability_owner = _capability_owners(sorted({
        i["id"] for g in graphs.values() if g for i in g["consumers"] if i["id"] not in by_element}))
    systems = []
    for app in critical:
        entry = {"name": app.name, "criticality": _criticality(app), "owner": _owner_name(app),
                 "source": "Application record: %s" % app.name,
                 "modelled": False, "affected_total": 0, "affected": [], "by_layer": {}}
        graph = graphs.get(app.id)
        if graph is not None:
            entry["modelled"] = True
            derived_ends = set()
            for rel in ArchiMateRelationship.query.filter(
                db.or_(ArchiMateRelationship.source_id == app.archimate_element_id,
                       ArchiMateRelationship.target_id == app.archimate_element_id)
            ).all():
                if rel.derived_from:
                    derived_ends.update({rel.source_id, rel.target_id})
            for item in graph["consumers"]:
                owner_app = by_element.get(item["id"])
                entry["affected"].append({
                    "name": item["name"],
                    "layer": item["layer"],
                    "hops": item["hops"],
                    "owner": _owner_name(owner_app) if owner_app is not None else capability_owner.get(item["id"]),
                    "basis": "Derived from the model" if item["id"] in derived_ends else "Stated in your data",
                })
                entry["by_layer"][item["layer"]] = entry["by_layer"].get(item["layer"], 0) + 1
            entry["affected_total"] = len(entry["affected"])
            entry["affected"] = entry["affected"][:IMPACT_LISTED]
        systems.append(entry)
    return {"systems": systems, "critical_total": len(critical), "listed_per_system": IMPACT_LISTED}


def _contracts_by_application() -> Dict[int, list]:
    from app.models.application_portfolio import VendorContract

    out: Dict[int, list] = {}
    for contract in VendorContract.query.all():
        if contract.application_id:
            out.setdefault(contract.application_id, []).append(contract)
    return out


def _contract_view(contract) -> Dict[str, Any]:
    end = contract.renewal_date or contract.end_date
    return {
        "name": contract.contract_name,
        "end": end,
        "annual_cost": _money(contract.annual_cost),
        "value": _money(contract.contract_value),
        "auto_renewal": bool(contract.auto_renewal) if contract.auto_renewal is not None else None,
        "source": "Contract: %s" % contract.contract_name,
    }


def _duplicates(apps: list, contracts: Dict[int, list], org_id: int) -> Dict[str, Any]:
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability
    from app.services.detection_strategies import FastDetectionStrategy

    by_id = {a.id: a for a in apps}

    def contracts_for(ids) -> Dict[str, Any]:
        rows = [_contract_view(c) for i in ids for c in contracts.get(i, [])]
        ends = [r["end"] for r in rows if r["end"]]
        costs = [r["annual_cost"] for r in rows if r["annual_cost"] is not None]
        return {"items": rows,
                "combined_annual_cost": sum(costs) if costs else None,
                "nearest_end": min(ends) if ends else None}

    groups: List[Dict[str, Any]] = []
    if apps:
        # The same detector the duplicate screen's "fast" run uses, read-only: the
        # screen's run replaces the stored groups of every organisation.
        result = FastDetectionStrategy(threshold=DUPLICATE_THRESHOLD).detect(apps)
        for group in result.groups:
            members = [by_id[a["id"]] for a in group["applications"] if a["id"] in by_id]
            if len(members) < 2:
                continue
            groups.append({
                "kind": group.get("duplicate_type"),
                "similarity": group.get("similarity_score"),
                "applications": [m.name for m in members],
                "contracts": contracts_for([m.id for m in members]),
                "source": "Duplicate detection over the application inventory",
            })

    overlaps: List[Dict[str, Any]] = []
    mapped: Dict[int, set] = {}
    for m in ApplicationCapabilityMapping.query.filter_by(organization_id=org_id).all():
        if m.application_component_id in by_id:
            mapped.setdefault(m.business_capability_id, set()).add(m.application_component_id)
    names = {c.id: c.name for c in BusinessCapability.query.filter(BusinessCapability.id.in_(list(mapped))).all()} if mapped else {}
    for cap_id, app_ids in sorted(mapped.items(), key=lambda kv: (-len(kv[1]), names.get(kv[0], ""))):
        if len(app_ids) < 2 or cap_id not in names:
            continue
        overlaps.append({
            "capability": names[cap_id],
            "applications": sorted(by_id[i].name for i in app_ids),
            "contracts": contracts_for(app_ids),
            "source": "Capability record: %s" % names[cap_id],
        })
    return {"groups": groups, "overlaps": overlaps}


def _unowned(apps: list) -> Dict[str, Any]:
    rows = []
    units: Dict[str, Dict[str, int]] = {}
    for app in apps:
        unit = _text(app.business_domain)
        label = unit or "No business unit recorded"
        bucket = units.setdefault(label, {"total": 0, "owned": 0})
        bucket["total"] += 1
        if _owner_name(app) is not None:
            bucket["owned"] += 1
        else:
            rows.append({"name": app.name, "criticality": _criticality(app), "business_unit": unit,
                         "source": "Application record: %s" % app.name})
    rows.sort(key=lambda r: (_CRITICALITY_ORDER.get(r["criticality"], 9), r["name"].lower()))
    coverage = [
        {"business_unit": label, "owned": b["owned"], "total": b["total"],
         "percent": round(100 * b["owned"] / b["total"]) if b["total"] else None}
        for label, b in sorted(units.items())
    ]
    return {"rows": rows, "coverage": coverage, "total": len(apps)}


def _scores(apps: list) -> Dict[str, Any]:
    """TIME recommendation and 0 to 100 score per application, from the product's own scoring.

    Each application is scored by ``calculate_app_score``, the call every score
    screen makes; its readiness gate decides which applications lack the
    evidence to be scored. The portfolio-wide run is not used: it admits only
    lifecycle spellings in upper case and so skips whole estates that record
    them otherwise.
    """
    from app.models.application_rationalization import READINESS_DIMENSIONS
    from app.services.rationalization_scoring_service import RationalizationScoringService

    scored, unscored = [], []
    for app in apps:
        source = "Application record: %s" % app.name
        score = RationalizationScoringService.calculate_app_score(app.id, app=app)
        if score is None:
            unscored.append({"name": app.name, "missing": None, "time": None, "score": None, "source": source})
        elif score.disposition_action == "insufficient_evidence":
            readiness = RationalizationScoringService.evaluate_readiness(app)
            unscored.append({
                "name": app.name,
                "missing": [READINESS_DIMENSIONS[d]["label"] for d in readiness["missing_critical"]],
                "time": score.rationalization_action, "score": score.overall_health_score, "source": source,
            })
        else:
            scored.append({"name": app.name, "time": score.rationalization_action,
                           "score": score.overall_health_score, "source": source})
    scored.sort(key=lambda r: (r["score"], r["name"].lower()))
    distribution = {k: sum(1 for r in scored if r["time"] == k) for k in ("TOLERATE", "INVEST", "MIGRATE", "ELIMINATE")}
    return {"scored": scored, "unscored": unscored, "distribution": distribution}


def _renewals(apps: list, today: _dt.date) -> Dict[str, Any]:
    """Active contracts renewing in the next quarter, from the accessor the renewals screen uses.

    Contracts already past their renewal date are included and marked, since
    they are the most urgent.
    """
    from app.models.application_portfolio import VendorContract
    from app.modules.procurement.services import get_expiring_contracts

    names = {a.id: a.name for a in apps}
    cutoff = today + _dt.timedelta(days=RENEWAL_WINDOW_DAYS)
    rows = []
    for c in get_expiring_contracts(RENEWAL_WINDOW_DAYS):
        view = _contract_view(c)
        view["application"] = names.get(c.application_id)
        view["overdue"] = bool(view["end"] and view["end"] < today)
        rows.append(view)
    costs = [r["annual_cost"] for r in rows if r["annual_cost"] is not None]
    return {"rows": rows, "contracts_total": VendorContract.query.count(),
            "annual_cost": sum(costs) if costs else None,
            "window_start": today, "window_end": cutoff}


def _summary(sections: Dict[str, Any]) -> List[str]:
    inv, imp, dup, own, sco, ren = (sections[k] for k in ("inventory", "impact", "duplicates", "unowned", "scores", "renewals"))
    lines = []
    total = inv["total"]
    if not total:
        return ["No applications are recorded for this organisation yet, so there is nothing to report on."]
    lines.append("%d %s recorded. %d %s no accountable owner." % (total, "application is" if total == 1 else "applications are", len(own["rows"]), "has" if len(own["rows"]) == 1 else "have"))
    weakest = min((c for c in inv["completeness"] if c["total"]), key=lambda c: c["percent"], default=None)
    if weakest and weakest["missing"]:
        lines.append("%s is the least complete field: %d of %d applications have none recorded."
                     % (weakest["field"], weakest["missing"], weakest["total"]))
    if imp["critical_total"]:
        lines.append("%d critical systems were traced for what would stop if they failed." % imp["critical_total"])
    else:
        lines.append("No application is marked critical or high, so no failure trace could be run.")
    lines.append("%d groups of overlapping applications share a capability, and %d groups were flagged as duplicates."
                 % (len(dup["overlaps"]), len(dup["groups"])))
    lines.append("%d of %d applications could be scored; %d need more evidence first."
                 % (len(sco["scored"]), total, len(sco["unscored"])))
    if ren["contracts_total"]:
        lines.append("%d contracts renew in the next quarter." % len(ren["rows"]))
    else:
        lines.append("No contracts are recorded, so renewals could not be reported.")
    return lines


def _recommendations(sections: Dict[str, Any]) -> Dict[str, str]:
    inv, imp, dup, own, sco, ren = (sections[k] for k in ("inventory", "impact", "duplicates", "unowned", "scores", "renewals"))
    rec = {}
    gaps = [c for c in inv["completeness"] if c["missing"]]
    rec["inventory"] = (
        "Fill %s first; they are the fields most often missing." % ", ".join(
            c["field"].lower() for c in sorted(gaps, key=lambda c: -c["missing"])[:3])
        if gaps else "The inventory is complete for every field reported. Keep it current as applications change.")
    if not imp["critical_total"]:
        rec["impact"] = "Mark which applications are critical, then re-run this trace."
    elif any(not s["modelled"] for s in imp["systems"]):
        rec["impact"] = "Record the connections for the critical systems that are not yet modelled, so their failure can be traced."
    else:
        rec["impact"] = "Confirm each affected owner can take the call when its supplier system fails."
    if dup["groups"] or dup["overlaps"]:
        rec["duplicates"] = "Review each overlapping group with its contracts and decide which application stays."
    else:
        rec["duplicates"] = "No overlap was found. Re-check after the next import."
    rec["unowned"] = (
        "Name an accountable owner for the %d applications listed, starting with the most critical." % len(own["rows"])
        if own["rows"] else "Every application has an accountable owner.")
    if sco["unscored"]:
        rec["scores"] = "Record the missing facts for the %d applications not scored, then re-run the scoring." % len(sco["unscored"])
    else:
        rec["scores"] = "Act on the Migrate and Eliminate recommendations first."
    rec["renewals"] = (
        "Decide each renewal before its notice date."
        if ren["rows"] else
        ("No contract renews in the next quarter." if ren["contracts_total"]
         else "Share your contract list (vendor, annual cost, renewal date, auto-renewal) to produce this section."))
    return rec


def build_report(org_id: int, today: Optional[_dt.date] = None) -> Dict[str, Any]:
    """The report for ``org_id``: the one place the six sections are assembled."""
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.organization import Organization

    today = today or _dt.date.today()
    with tenant_scope(org_id):
        organisation_name = getattr(db.session.get(Organization, org_id), "name", None)
        apps = _applications()
        contracts = _contracts_by_application()
        sections = {
            "inventory": _inventory(apps),
            "impact": _impact(apps),
            "duplicates": _duplicates(apps, contracts, org_id),
            "unowned": _unowned(apps),
            "scores": _scores(apps),
            "renewals": _renewals(apps, today),
        }
        return {
            "organisation": organisation_name or "Your organisation",
            "generated_on": today,
            "currency": get_reporting_currency(org_id),
            "summary": _summary(sections),
            "recommendations": _recommendations(sections),
            **sections,
        }


# ── Output: HTML and PDF ─────────────────────────────────────────────────────

REPORT_BASENAME = "architecture-health-check"
_STYLESHEETS = ("css/shadcn_tokens.css", "css/tailwind-output.css", "css/health_check_report.css")


def _stylesheet() -> str:
    from pathlib import Path

    from flask import current_app

    static = Path(current_app.static_folder)
    css = "\n".join((static / name).read_text(encoding="utf-8") for name in _STYLESHEETS)
    # The comments name source files; a client's copy carries none.
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def render_html(report: Dict[str, Any]) -> str:
    """The report as one self-contained HTML document."""
    from flask import current_app

    # Straight from the template environment: the report is written outside any request,
    # so no signed-in user or request context is needed or wanted.
    template = current_app.jinja_env.get_template("health_check/report.html")
    return template.render(report=report, stylesheet=_stylesheet())


def render_pdf(html: str) -> bytes:
    """The HTML document as a PDF, with the HTML-to-PDF renderer the app ships."""
    from weasyprint import HTML

    return HTML(string=html).write_pdf()


def write_report(org_id: int, out_dir) -> Dict[str, Any]:
    """Build the report for ``org_id`` and write the HTML and PDF files into ``out_dir``."""
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = build_report(org_id)
    html = render_html(report)
    html_path = out / (REPORT_BASENAME + ".html")
    pdf_path = out / (REPORT_BASENAME + ".pdf")
    html_path.write_text(html, encoding="utf-8")
    pdf_path.write_bytes(render_pdf(html))
    return {"report": report, "html": html_path, "pdf": pdf_path}


# ── The committed sample report ──────────────────────────────────────────────

SAMPLE_DIR = "docs/sample-report"
SAMPLE_DATA_FILE = "sample-report.json"


def _revive_dates(obj):
    for key, value in obj.items():
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            obj[key] = _dt.date.fromisoformat(value)
    return obj


def dump_report_data(report: Dict[str, Any]) -> str:
    """The report dictionary as JSON, dates as ISO text."""
    import json

    return json.dumps(report, indent=1, default=str, sort_keys=True) + "\n"


def load_sample_report() -> Optional[Dict[str, Any]]:
    """The committed sample report for the sample company, or None if it is not there."""
    import json
    from pathlib import Path

    from flask import current_app

    path = Path(current_app.root_path).parent / SAMPLE_DIR / SAMPLE_DATA_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"), object_hook=_revive_dates)
