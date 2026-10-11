"""R1-B85 (PB-0216): CTO scorecard.

Three read-only panels, each reading an existing canonical store rather than
computing a parallel figure:

- supported-version share: ApplicationComponent/VendorProduct, the same
  end_of_life_date field deadline_alert_service scans.
- open architecture exceptions: ArchitectureChangeRequest (R1-B09's decision
  register), disposition == "exception" and not yet closed.
- surfaces disagreeing: scripts/check_store_agreement.py's own observe/compare
  functions, called read-only -- the comparison logic is not reimplemented
  here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.architecture_decision import ArchitectureChangeRequest
from app.models.vendor.vendor_organization import VendorProduct


def supported_version_share(organization_id: int) -> Dict[str, Any]:
    """Share of this organisation's technology-dependent applications whose
    vendor product is not past end of support. An application with no
    vendor_product_id recorded counts toward neither side -- it is not known
    to be unsupported, so counting it as supported would be invented data."""
    now = datetime.utcnow()
    rows = (
        db.session.query(VendorProduct.end_of_life_date)
        .join(ApplicationComponent, ApplicationComponent.vendor_product_id == VendorProduct.id)
        .filter(ApplicationComponent.organization_id == organization_id)
        .all()
    )
    total = len(rows)
    if total == 0:
        return {"total": 0, "supported": 0, "share_pct": None}
    supported = sum(1 for (eol,) in rows if eol is None or eol > now)
    return {
        "total": total,
        "supported": supported,
        "share_pct": round(100.0 * supported / total, 1),
    }


def open_exceptions(organization_id: int) -> List[Dict[str, Any]]:
    """Open exceptions, oldest first -- the order the brief's own "escalate
    the oldest two" acceptance criterion needs."""
    rows = (
        ArchitectureChangeRequest.query.filter(
            ArchitectureChangeRequest.organization_id == organization_id,
            ArchitectureChangeRequest.disposition == "exception",
            ArchitectureChangeRequest.closed_at.is_(None),
        )
        .order_by(ArchitectureChangeRequest.raised_at.asc())
        .all()
    )
    now = datetime.utcnow()
    out = []
    for row in rows:
        age_days = (now - row.raised_at).days if row.raised_at else None
        out.append(
            {
                "id": row.id,
                "acr_reference": row.acr_reference,
                "title": row.title,
                "raised_at": row.raised_at.isoformat() if row.raised_at else None,
                "age_days": age_days,
                "escalated_at": row.escalated_at.isoformat() if row.escalated_at else None,
            }
        )
    return out


def escalate(organization_id: int, change_request_id: int) -> bool:
    """Mark an open exception escalated. Returns False (no row touched,
    never a fabricated success) when the row is not this organisation's own
    or is not an open exception."""
    row = ArchitectureChangeRequest.query.filter_by(
        id=change_request_id, organization_id=organization_id, disposition="exception",
    ).first()
    if row is None or row.closed_at is not None:
        return False
    row.escalated_at = datetime.utcnow()
    db.session.commit()
    return True


def store_agreement_findings(organization_id: int) -> Dict[str, Any]:
    """Read-only reuse of the store-agreement gate's own comparison engine.

    Returns {"findings": [...], "notes": [...], "error": str|None}. An
    import or runtime failure is reported as an error string, never silently
    swallowed into an empty (and therefore falsely reassuring) list.
    """
    try:
        from flask import current_app, g

        from scripts.check_store_agreement import compare, observe_tenant

        prior_org_id = getattr(g, "current_org_id", None)
        g.current_org_id = organization_id
        try:
            observations, notes = observe_tenant(
                current_app._get_current_object(), db, organization_id, http=False,
            )
        finally:
            g.current_org_id = prior_org_id
        findings, compare_notes = compare(observations)
        return {"findings": findings, "notes": notes + compare_notes, "error": None}
    except Exception as exc:  # noqa: BLE001 - surfaced to the page, not swallowed
        return {"findings": [], "notes": [], "error": str(exc)[:300]}
