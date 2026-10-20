"""Recommendations derived from what the platform already knows.

No model is called and nothing is stored. Each recommendation names the thing it
is about and the record it came from:

* an open gap no work package resolves, or a work package with no start date
  (the transformation backlog), and
* a finding in the organisation's stored model-health report.

Both sources record how urgent an item is on the same four words, so the list
is ordered by that recorded urgency (critical, high, medium, low, anything
else last), then the backlog before model-health findings, then by title. No
score is calculated, so the order can be explained by reading the page.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

LEVELS = ("critical", "high", "medium", "low")
TOP = 50

SOURCE_BACKLOG = "backlog"
SOURCE_MODEL_HEALTH = "model_health"
_SOURCE_ORDER = {SOURCE_BACKLOG: 0, SOURCE_MODEL_HEALTH: 1}


def _rank(level: Optional[str]) -> int:
    value = (level or "").strip().lower()
    return LEVELS.index(value) if value in LEVELS else len(LEVELS)


def _names(finding: Dict[str, Any]) -> List[str]:
    return [e.get("name") or "—" for e in finding.get("elements", [])]


def _finding_title(finding: Dict[str, Any]) -> str:
    names = _names(finding)
    first = names[0] if names else "—"
    kind = finding.get("type")
    if kind == "orphaned_element":
        return "Link or retire “%s”" % first
    if kind == "capability_without_application_support":
        return "Support the capability “%s” with an application, or record why none is needed" % first
    if kind == "decommissioned_application_still_mapped":
        return "Replace the retiring application “%s” wherever it still supports a capability" % first
    if kind == "motivation_without_realization":
        return "Realise “%s” with a requirement, work package or capability, or retire it" % first
    if kind == "near_duplicate_elements":
        shown = ", ".join("“%s”" % n for n in names[:3])
        more = len(names) - 3
        return "Check whether %s%s are the same thing and merge them" % (shown, " and %d more" % more if more > 0 else "")
    return "Review “%s”" % first


def _from_backlog() -> List[Dict[str, Any]]:
    from app.modules.portfolio.services.transformation_backlog import transformation_backlog

    items = []
    for entry in transformation_backlog()["items"]:
        if entry["kind"] == "gap":
            title = "Plan a work package for the gap “%s”" % entry["name"]
            why = "This gap is open and no work package resolves it."
        else:
            title = "Give the work package “%s” a start date" % entry["name"]
            why = "This work package is not finished and has no start date."
        items.append({
            "source": SOURCE_BACKLOG,
            "level": entry["priority"],
            "title": title,
            "why": why,
            "evidence": entry["name"],
            "element_ids": [],
        })
    return items


def _model_health(org_id: Optional[int]):
    """(recommendations, state): state is 'current' or 'not_computed'."""
    if org_id is None:
        return [], "not_computed"
    from app import db
    from app.models.drift_report import DriftReport

    stored = db.session.execute(
        db.select(DriftReport).where(DriftReport.organization_id == org_id)
    ).scalar_one_or_none()
    report = stored.report_json if stored is not None else None
    if not isinstance(report, dict):
        return [], "not_computed"
    items = []
    for finding in report.get("findings", []):
        items.append({
            "source": SOURCE_MODEL_HEALTH,
            "level": finding.get("severity"),
            "title": _finding_title(finding),
            "why": finding.get("why"),
            "evidence": ", ".join(_names(finding)),
            "element_ids": [
                e["archimate_element_id"] for e in finding.get("elements", []) if e.get("archimate_element_id") is not None
            ],
        })
    return items, "current"


def recommendations(org_id: Optional[int], limit: int = TOP) -> Dict[str, Any]:
    """The organisation's recommendations in order, capped at ``limit``."""
    health, health_state = _model_health(org_id)
    items = _from_backlog() + health
    items.sort(key=lambda i: (_rank(i["level"]), _SOURCE_ORDER[i["source"]], i["title"].lower()))
    return {
        "items": items[:limit],
        "total": len(items),
        "limit": limit,
        "model_health_state": health_state,
    }
