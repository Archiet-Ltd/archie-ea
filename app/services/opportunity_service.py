"""The organisation's ranked opportunities, each with its reason and the records it rests on.

Nothing here is stored: every opportunity is worked out from what the model already holds, so the page
and any other reader of this list give the same answer.

Four kinds, each with one rule:

* capability served twice: a business capability that two or more live applications support;
* candidate pair: a consolidation candidate awaiting review whose two applications share no capability
  the first kind already names (a shared business function, or a near-identical name);
* contract on a retiring system: an active contract on an application marked sunset or decommissioned;
* contracts repeated: two or more active contracts with one vendor on one application;
* unstarted gap: a capability shortfall identified and not yet started, with no work package against it.

Ranking: the annual cost an opportunity puts in play, largest first. An opportunity whose cost cannot be
stated from recorded figures follows, and says so; gaps among those go by severity. No weight is invented:
the order is a recorded amount, or a recorded severity, and nothing else.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.services import result_footer_service as footers
from app.services.result_footer_service import fact

TOP = 50
COST_WITHHELD = "Cost figures are not shown for your role."
_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_RETIRING = ("3.", "5.")

KIND_LABELS = {
    "capability_served_twice": "Capability served twice",
    "candidate_pair": "Candidate pair",
    "retiring_system_contract": "Contract on a retiring system",
    "repeated_contracts": "Contracts repeated",
    "unstarted_gap": "Gap not started",
}


def _live(application) -> bool:
    stage = (application.lifecycle_status or "").strip().lower()
    return not stage.startswith("5.") and not (application.name or "").lower().startswith("(duplicate)")


def _retiring(application) -> bool:
    return (application.lifecycle_status or "").strip().lower().startswith(_RETIRING)


def _app_fact(application, subject: str = "System") -> Dict[str, Any]:
    origin = footers.origin_of(application.data_source)
    return fact(application.name, href=footers.page_link("unified_applications.application_detail", id=application.id),
                source_kind=origin["source_kind"], source_label=origin["source_label"],
                as_of=application.updated_at or application.created_at, subject=subject)


def _cost_of(applications, run_costs: Dict[int, Optional[float]]) -> Tuple[Optional[float], Optional[str]]:
    """(total run cost of these applications, None) or (None, why not): a total never stands on a guess."""
    missing = [a.name for a in applications if run_costs.get(a.id) is None]
    if missing:
        return None, "Run cost not recorded for " + ", ".join(missing)
    return sum(run_costs[a.id] for a in applications), None


def _run_costs(org_id: int, applications) -> Dict[int, Optional[float]]:
    from app.services import vendor_register
    from app.services.application_cost_accessor import get_run_cost_with_source

    ledger = vendor_register.contract_ledger(org_id)
    on_system = vendor_register.counted_by_system(ledger)
    return {a.id: get_run_cost_with_source(a, on_system.get(a.id, []))[0] for a in applications}


def _item(kind: str, title: str, reason: str, href: Optional[str], facts: List[Dict[str, Any]],
          cost: Optional[float], cost_note: Optional[str], severity: Optional[str] = None) -> Dict[str, Any]:
    return {
        "kind": kind, "kind_label": KIND_LABELS[kind], "title": title, "reason": reason, "href": href,
        "cost": cost, "cost_note": cost_note, "severity": severity, "footer": footers.build_footer(facts),
    }


def _capabilities_served_twice(org_id, apps_by_id, run_costs) -> Tuple[List[Dict[str, Any]], set]:
    from app.models.application_capability import ApplicationCapabilityMapping as M
    from app.models.business_capabilities import BusinessCapability as C

    rows = db.session.execute(
        db.select(M.application_component_id, C).join(C, C.id == M.business_capability_id)
        .where(M.organization_id == org_id, M.is_active.is_(True), C.organization_id == org_id)
    ).all()
    served: Dict[int, Tuple[Any, set]] = {}
    for app_id, capability in rows:
        if app_id in apps_by_id and _live(apps_by_id[app_id]):
            served.setdefault(capability.id, (capability, set()))[1].add(app_id)
    items, covered_pairs = [], set()
    for capability, app_ids in served.values():
        if len(app_ids) < 2:
            continue
        applications = sorted((apps_by_id[i] for i in app_ids), key=lambda a: a.name.lower())
        names = ", ".join(a.name for a in applications)
        cost, note = _cost_of(applications, run_costs)
        link = footers.page_link("enterprise_crud.capability_fact_sheet", capability_id=capability.id)
        facts = [fact(capability.name, href=link, source_kind="manual", subject="Capability",
                      as_of=capability.updated_at or capability.created_at)]
        facts += [_app_fact(a) for a in applications]
        items.append(_item(
            "capability_served_twice", capability.name,
            "%d applications support %s: %s. Two systems doing one job is where a consolidation saves most."
            % (len(applications), capability.name, names),
            link, facts, cost, note))
        ids = sorted(app_ids)
        covered_pairs.update((a, b) for i, a in enumerate(ids) for b in ids[i + 1:])
    return items, covered_pairs


def _candidate_pairs(org_id, apps_by_id, run_costs, covered_pairs) -> List[Dict[str, Any]]:
    from app.models.consolidation import ConsolidationCandidate as K

    items = []
    for c in db.session.execute(
            db.select(K).where(K.organization_id == org_id, K.status.in_(("pending_review", "approved")))
            .order_by(K.id)).scalars():
        first, second = apps_by_id.get(c.primary_application_id), apps_by_id.get(c.duplicate_application_id)
        pair = tuple(sorted((c.primary_application_id, c.duplicate_application_id)))
        if first is None or second is None or not (_live(first) and _live(second)) or pair in covered_pairs or not (c.notes or "").strip():
            continue
        cost, note = _cost_of([first, second], run_costs)
        facts = [_app_fact(first), _app_fact(second),
                 fact("Candidate: " + (c.notes or "").strip(),
                      href=footers.page_link("unified_low_priority.consolidation_candidates"),
                      source_kind="derived", subject="Candidate", as_of=c.detected_at)]
        items.append(_item(
            "candidate_pair", "%s and %s" % (first.name, second.name), c.notes.strip(),
            footers.page_link("unified_applications.application_detail", id=first.id), facts, cost, note))
    return items


def _contract_items(org_id, apps_by_id) -> List[Dict[str, Any]]:
    from app.models.application_portfolio import VendorContract
    from app.services.application_cost_accessor import get_reporting_currency

    currency = get_reporting_currency(org_id)
    today = date.today()
    contracts = db.session.execute(
        db.select(VendorContract).where(VendorContract.organization_id == org_id, VendorContract.status == "active",
                                        VendorContract.application_id.isnot(None)).order_by(VendorContract.id)
    ).scalars().all()
    contracts = [c for c in contracts if c.end_date is None or c.end_date >= today]

    def amount(contract):
        if contract.annual_cost is None:
            return None
        return float(contract.annual_cost) if (contract.currency or currency).upper() == currency else None

    def contract_fact(contract):
        return fact("%s (%s)" % (contract.contract_name, "annual cost not recorded" if contract.annual_cost is None else "annual cost recorded"),
                    href=footers.page_link("procurement.contract_detail", contract_id=contract.id),
                    source_kind="manual", subject="Contract", as_of=contract.updated_at or contract.created_at)

    items = []
    for contract in contracts:
        application = apps_by_id.get(contract.application_id)
        if application is None or not _retiring(application):
            continue
        cost = amount(contract)
        stage = "decommissioned" if (application.lifecycle_status or "").strip().startswith("5.") else "sunset"
        items.append(_item(
            "retiring_system_contract", contract.contract_name,
            "%s is marked %s and still carries this active contract." % (application.name, stage),
            footers.page_link("procurement.contract_detail", contract_id=contract.id),
            [contract_fact(contract), _app_fact(application)], cost,
            None if cost is not None else "Annual cost not stated in %s" % currency))
    grouped: Dict[Tuple[Any, Any], list] = {}
    for contract in contracts:
        if contract.vendor_id is not None:
            grouped.setdefault((contract.vendor_id, contract.application_id), []).append(contract)
    for (_vendor, app_id), repeated in grouped.items():
        application = apps_by_id.get(app_id)
        if len(repeated) < 2 or application is None:
            continue
        amounts = [amount(c) for c in repeated]
        cost = None if any(a is None for a in amounts) else sum(amounts)
        items.append(_item(
            "repeated_contracts", "%d contracts on %s" % (len(repeated), application.name),
            "%d active contracts with one vendor cover %s." % (len(repeated), application.name),
            footers.page_link("procurement.contract_detail", contract_id=repeated[0].id),
            [contract_fact(c) for c in repeated] + [_app_fact(application)], cost,
            None if cost is not None else "Annual cost not stated for every contract in %s" % currency))
    return items


def _unstarted_gaps(org_id) -> List[Dict[str, Any]]:
    from app.models.implementation_migration import Gap
    from app.services import work_package_service

    gaps = db.session.execute(
        db.select(Gap).where(Gap.organization_id == org_id, Gap.resolution_status == "identified",
                             db.or_(Gap.gap_kind.is_(None), Gap.gap_kind == "capability_shortfall"))
        .order_by(Gap.id)).scalars().all()
    if not gaps:
        return []
    # The one reader of the gap-to-work-package link, the same one the work-package list and Recommendations use.
    planned = work_package_service.gap_ids_with_work_packages([g.id for g in gaps], org_id)
    items = []
    for gap in gaps:
        if gap.id in planned:
            continue
        severity = (gap.rated_severity or "").strip().lower() or None
        href = footers.page_link("archimate_crud.detail_element", layer="implementation", element_type="Gap", element_id=gap.id)
        when = gap.updated_at or gap.created_at
        facts = [
            fact(gap.name, href=href, source_kind=footers.gap_source_kind(gap), as_of=when, subject="Gap"),
            fact("Severity: " + severity, href=href, source_kind="manual", subject="Severity", as_of=when)
            if severity else fact("Severity: not recorded", missing=True),
            fact("Work packages against it: none", href=href, source_kind="derived", subject="Work packages", as_of=when),
        ]
        items.append(_item(
            "unstarted_gap", gap.name,
            "Identified%s, not started, and no work package is set against it. A work package would close it."
            % ((" as " + severity + " severity") if severity else ""),
            href, facts, None, "A gap carries no run cost", severity))
    return items


def ranked_opportunities(org_id: int, limit: int = TOP, with_cost: bool = True) -> Dict[str, Any]:
    """{"items": the top ``limit`` opportunities, ranked, "total": how many were found, "counts": found per kind} for one organisation.

    ``with_cost`` is the one cost rule's answer for the person asking (``run_cost_line`` is None when it
    says no). When false no amount reaches an item: each says the figure is withheld and the order falls
    to recorded severity, then kind.
    """
    from app.models.application_portfolio import ApplicationComponent as A

    if org_id is None:
        return {"items": [], "total": 0, "counts": {}}
    applications = db.session.execute(db.select(A).where(A.organization_id == org_id)).scalars().all()
    apps_by_id = {a.id: a for a in applications}
    run_costs = _run_costs(org_id, applications)
    served, covered = _capabilities_served_twice(org_id, apps_by_id, run_costs)
    items = served + _candidate_pairs(org_id, apps_by_id, run_costs, covered) + _contract_items(org_id, apps_by_id)
    items += _unstarted_gaps(org_id)
    if not with_cost:
        for item in items:
            item["cost"], item["cost_note"] = None, COST_WITHHELD
    items.sort(key=lambda i: (i["cost"] is None, -(i["cost"] or 0), _SEVERITY.get(i["severity"], 4),
                              i["kind_label"], i["title"].lower()))
    for rank, item in enumerate(items, 1):
        item["rank"] = rank
    counts: Dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {"items": items[:limit], "total": len(items), "counts": counts}
