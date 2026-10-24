"""One prioritised action list over the three sources that say what needs doing.

Sources (each read once, tenant-scoped, no per-row queries):
  * open gaps               -> gap_register_service.open_gaps_with_coverage
  * late / blocked / on-hold work packages
                            -> work_package_service (query_for, blocked_by_another_programme)
  * open risks              -> Risk, ranked by their recorded score

Ranking (RANKING_RULE, shown on the screen): a plain lexicographic order, no
weighted score. The sort key is, in order:
  1. severity tier of the item (critical, high, medium, low); an item with no
     recorded severity sorts after all of them;
  2. kind group: gaps with no work package, then late or blocked work packages,
     then risks, then gaps already covered by work;
  3. within the group: work packages by days overdue (most first), risks by
     recorded score (highest first); gaps have no second measure;
  4. name, then id, so ties are stable.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.services import gap_register_service, work_package_service

RANKING_RULE = (
    "Ranked by severity, critical first; within a severity, gaps with no work package come first, "
    "then late or blocked work packages (most overdue first), then risks (highest score first), "
    "then gaps already covered by work; ties go by name, and anything with no recorded severity comes last."
)

KIND_LABELS = {"gap": "Gap", "work_package": "Work package", "risk": "Risk"}
RISK_LIMIT = 5
DEFAULT_LIMIT = 10


def limit_for(args) -> Optional[int]:
    """The row limit a screen should ask for: all rows when the person chose "Show all"."""
    return None if (args.get("all_actions") or "") == "1" else DEFAULT_LIMIT

_TIERS = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_GROUP_GAP_UNCOVERED, _GROUP_WORK, _GROUP_RISK, _GROUP_GAP_COVERED = 0, 1, 2, 3
_EM_DASH = "—"


def _tier(severity: Optional[str]) -> int:
    return _TIERS.get((severity or "").strip().lower(), 0)


def _severity_label(severity: Optional[str]) -> Optional[str]:
    return (severity or "").strip().capitalize() if _tier(severity) else None


def _day(value) -> str:
    if value is None:
        return _EM_DASH
    return "%d %s" % (value.day, value.strftime("%b"))


def _percent(value) -> str:
    if value is None:
        return _EM_DASH
    return "%g" % round(float(value), 1)


def _item(kind, record_id, name, action, severity, group, second, why, **extra) -> Dict[str, Any]:
    tier = _tier(severity)
    if not tier:
        why = "%s No severity is recorded, so it is listed last." % why
    row = {
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "record_id": record_id,
        "name": name or _EM_DASH,
        "action": action,
        "severity": _severity_label(severity),
        "why": why,
        "_key": (-tier, group, -second, (name or "").lower(), int(record_id)),
    }
    row.update(extra)
    return row


def _bare_name(name: Optional[str]) -> str:
    """A gap's name without the "Gap:" label some generators put in front of it.

    The row already says it is a gap, so a stored "Gap: Gap: Billing" reads
    as "Billing" and the sentence names the thing once.
    """
    text = (name or "").strip()
    while text[:4].lower() == "gap:":
        text = text[4:].strip()
    return text or _EM_DASH


def _gap_rows(organization_id: int) -> List[Dict[str, Any]]:
    """One row per gap name and verb.

    The register can hold several open gaps with one name (one per generated
    pass); each would print the same sentence. They are listed once, ranked by
    the most severe of them, and the row says how many records it stands for.
    """
    best: Dict[Any, Dict[str, Any]] = {}
    for gap, addressed in gap_register_service.open_gaps_with_coverage(organization_id):
        name = _bare_name(gap.name)
        key = (name.lower(), bool(addressed))
        sev = _severity_label(gap.severity)
        lead = ("%s severity" % sev) if sev else "Severity not recorded"
        if addressed:
            action = "Close the %s once its work is done" % name
            why = "%s; work is linked but the gap is still open." % lead
            group = _GROUP_GAP_COVERED
        else:
            action = "Convert the %s to a work package" % name
            why = "%s; no work package addresses it yet." % lead
            group = _GROUP_GAP_UNCOVERED
        row = _item("gap", gap.id, name, action, gap.severity, group, 0, why, covered=bool(addressed))
        row["_records"] = 1
        kept = best.get(key)
        if kept is None:
            best[key] = row
            continue
        kept["_records"] += 1
        if row["_key"] < kept["_key"]:
            row["_records"] = kept["_records"]
            best[key] = row
    rows = list(best.values())
    for row in rows:
        records = row.pop("_records")
        if records > 1:
            row["why"] = "%s %d open gap records share this name." % (row["why"], records)
    return rows


def _work_package_rows(organization_id: int, today: date) -> List[Dict[str, Any]]:
    candidates = {}
    for wp in work_package_service.query_for(organization_id).all():
        if work_package_service.is_resolved(wp.status):
            continue
        overdue = 0
        if wp.end_date is not None and wp.end_date.date() < today and (wp.progress_percentage or 0) < 100:
            overdue = (today - wp.end_date.date()).days
        if overdue or work_package_service.status_key(wp.status) == "on_hold":
            candidates[wp.id] = [wp, overdue, []]
    for entry in work_package_service.blocked_by_another_programme(organization_id):
        wp = entry["work_package"]
        slot = candidates.setdefault(wp.id, [wp, 0, []])
        slot[2] = [b.name for b in entry["blocked_by"]]

    rows = []
    for wp, overdue, blockers in candidates.values():
        pct, due = _percent(wp.progress_percentage), _day(wp.end_date)
        reasons = []
        if overdue:
            action = "Finish %s: due %s, %s percent done" % (wp.name, due, pct)
            reasons.append("%d day%s overdue" % (overdue, "" if overdue == 1 else "s"))
        elif blockers:
            action = "Unblock %s: waiting on %s" % (wp.name, ", ".join(blockers))
        else:
            action = "Restart %s: on hold at %s percent done" % (wp.name, pct)
        if blockers:
            reasons.append("waiting on work in another programme")
        if work_package_service.status_key(wp.status) == "on_hold":
            reasons.append("on hold")
        sev = _severity_label(wp.priority)
        lead = ("%s priority" % sev) if sev else "Priority not recorded"
        rows.append(
            _item("work_package", wp.id, wp.name, action, wp.priority, _GROUP_WORK, overdue,
                  "%s; %s." % (lead, ", ".join(reasons)))
        )
    return rows


def _risk_rows(organization_id: int) -> List[Dict[str, Any]]:
    from app.models.risk import Risk, RiskStatus

    risks = Risk.query.filter(
        Risk.organization_id == organization_id, Risk.status == RiskStatus.OPEN
    ).all()
    scored = sorted(risks, key=lambda r: (-r._effective_score, (r.title or "").lower(), r.id))[:RISK_LIMIT]
    return [
        _item(
            "risk", r.id, r.title, "Reduce the risk: %s" % r.title, r.risk_level, _GROUP_RISK,
            r._effective_score,
            "%s risk, recorded score %d of 25." % (r.risk_level.capitalize(), r._effective_score),
        )
        for r in scored
    ]


def prioritised_actions(organization_id: int, limit: Optional[int] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """The ordered rows, the total before any limit, and the one-sentence rule."""
    today = today or datetime.utcnow().date()
    rows = _gap_rows(organization_id) + _work_package_rows(organization_id, today) + _risk_rows(organization_id)
    rows.sort(key=lambda r: r["_key"])
    for position, row in enumerate(rows, start=1):
        row["position"] = position
        del row["_key"]
    return {"rows": rows[:limit] if limit else rows, "total": len(rows), "rule": RANKING_RULE}
