"""Data-freshness / completeness cockpit — the "is the repository trustworthy?" lens.

An architecture repository is only as good as the data in it, and the architect's
constant, losing battle is keeping that data current when they don't own most of
it. The Fact Sheets already score how complete one object's record is; this rolls
those scores up across the whole estate so the gaps are visible at portfolio
level: how complete the repository is overall, how many records need attention,
which object types are worst, and — most usefully — the specific worst offenders
to go fix (or send a survey about).

Reuses the exact per-object completeness scoring the Fact Sheets use, so the
cockpit and the object pages can never disagree. Reads only; tenant-scoped by the
ORM in a request context.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Cap the scan so a huge estate can't turn the page into a slow scorer; the
# aggregate is representative and the worst-offenders list is what drives action.
_SCAN_LIMIT = 4000


def _score_set(objs, scorer, kind: str) -> List[Dict[str, Any]]:
    out = []
    for o in objs:
        c = scorer(o)
        out.append({
            "id": o.id,
            "name": getattr(o, "name", None) or f"{kind.title()} {o.id}",
            "kind": kind,
            "pct": c["pct"],
            "band": c["band"],
            "missing": c["missing"],
        })
    return out


def _summary(scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(scored)
    buckets = {"good": 0, "warn": 0, "poor": 0}
    for s in scored:
        buckets[s["band"]] = buckets.get(s["band"], 0) + 1
    avg = round(sum(s["pct"] for s in scored) / n) if n else 0
    band = "good" if avg >= 80 else "warn" if avg >= 50 else "poor"
    return {"count": n, "avg": avg, "band": band, "buckets": buckets}


def build_data_freshness() -> Dict[str, Any]:
    """Portfolio-wide completeness roll-up across applications and capabilities."""
    from app import db  # noqa: PLC0415
    from app.models.application_owner import ApplicationOwner  # noqa: PLC0415
    from app.models.application_portfolio import ApplicationComponent  # noqa: PLC0415
    from app.models.business_capabilities import BusinessCapability  # noqa: PLC0415
    from app.services.application_fact_sheet import (  # noqa: PLC0415
        compute_completeness as app_completeness,
    )
    from app.services.capability_fact_sheet import (  # noqa: PLC0415
        _completeness as cap_completeness,
    )

    apps = ApplicationComponent.query.limit(_SCAN_LIMIT).all()
    caps = BusinessCapability.query.limit(_SCAN_LIMIT).all()

    app_ids = [app.id for app in apps]
    org_ids = sorted({app.organization_id for app in apps if getattr(app, "organization_id", None) is not None})
    owner_counts = {}
    if app_ids and org_ids:
        owner_counts = {
            app_id: count
            for app_id, count in db.session.query(
                ApplicationOwner.application_id,
                db.func.count(ApplicationOwner.id),
            )
            .filter(ApplicationOwner.application_id.in_(app_ids))
            .filter(ApplicationOwner.organization_id.in_(org_ids))
            .group_by(ApplicationOwner.application_id)
            .all()
        }

    app_scored = _score_set(
        apps,
        lambda app: app_completeness(app, owner_count=owner_counts.get(app.id, 0)),
        "application",
    )
    cap_scored = _score_set(caps, cap_completeness, "capability")
    everything = app_scored + cap_scored

    # worst first — the records to go fix (and never a full record, so the list
    # is always actionable).
    worst = sorted((s for s in everything if s["pct"] < 100),
                   key=lambda s: (s["pct"], s["kind"]))[:25]

    return {
        "overall": _summary(everything),
        "types": [
            {"kind": "application", "label": "Applications", "endpoint": "unified_applications.application_fact_sheet",
             "summary": _summary(app_scored)},
            {"kind": "capability", "label": "Capabilities", "endpoint": "enterprise_crud.capability_fact_sheet",
             "summary": _summary(cap_scored)},
        ],
        "worst": worst,
        "scanned": len(everything),
        "capped": len(apps) >= _SCAN_LIMIT or len(caps) >= _SCAN_LIMIT,
    }
