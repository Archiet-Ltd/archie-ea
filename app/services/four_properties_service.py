"""The four properties under a list or figure: where it comes from, when it was
worked out, how sure it is, and where to follow it.

The field names are the answer envelope's own (`app/modules/intelligence/
services/answer_envelope.py`): ``computed_at``, ``confidence`` /
``confidence_basis`` / ``confidence_percent`` and ``truth_class``. A screen that
is a count of recorded rows has no model confidence, so it states the envelope's
own ``drawn_facts_only`` basis instead of a percentage; nothing is invented.

One helper per screen returns the four fields from the real source. The
component ``components/four_properties_footer.html`` renders them.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Callable, Dict, Optional

from flask import url_for

from app.extensions import db
from app.middleware.tenant_context import current_org_id

logger = logging.getLogger(__name__)

CONFIDENCE_DRAWN_ONLY = "drawn_facts_only"


def _plural(count: int, one: str, many: str) -> str:
    if count == 0:
        return f"no {many}"
    return f"{count} {one if count == 1 else many}"


def _count(model) -> int:
    org_id = current_org_id()
    if org_id is None:
        return 0
    return db.session.query(db.func.count(model.id)).filter(model.organization_id == org_id).scalar() or 0


def _endpoint_url(endpoint: str) -> Optional[str]:
    from flask import current_app

    if endpoint not in current_app.view_functions:
        return None
    return url_for(endpoint)


def _fields(source: Optional[str], endpoint: str, trail_label: str) -> Dict[str, Any]:
    now = _dt.datetime.utcnow().replace(microsecond=0)
    return {
        "source": source,
        "computed_at": now.isoformat() + "Z",
        "computed_label": now.strftime("%d %b %Y, %H:%M UTC"),
        "confidence": None,
        "confidence_basis": CONFIDENCE_DRAWN_ONLY if source else None,
        "confidence_percent": None,
        "trail_url": _endpoint_url(endpoint),
        "trail_label": trail_label,
    }


def _roadmap() -> Dict[str, Any]:
    from app.services import work_package_service
    from app.services.gap_register_service import count_gaps

    org_id = current_org_id()
    if org_id is None:
        return _fields(None, "enterprise.gap_analysis", "See the recorded gaps")
    gaps = count_gaps(org_id)
    packages = work_package_service.query_for(org_id).count()
    source = (
        f"{_plural(gaps, 'recorded gap', 'recorded gaps')} and "
        f"{_plural(packages, 'work package', 'work packages')} in this organisation"
    )
    return _fields(source, "enterprise.gap_analysis", "See the recorded gaps")


def _gaps() -> Dict[str, Any]:
    from app.services.gap_register_service import count_gaps

    org_id = current_org_id()
    source = (
        f"{_plural(count_gaps(org_id), 'recorded gap', 'recorded gaps')} in this organisation"
        if org_id is not None
        else None
    )
    return _fields(source, "main.capability_roadmap", "See the gaps on the roadmap")


def _risks() -> Dict[str, Any]:
    from app.models.risk import Risk

    source = f"{_plural(_count(Risk), 'recorded risk', 'recorded risks')} in this organisation"
    return _fields(source if current_org_id() is not None else None,
                   "intelligence_ui.value_streams_at_risk", "See which value streams the risks reach")


def _capability_health() -> Dict[str, Any]:
    from app.models.business_capabilities import BusinessCapability

    source = f"{_plural(_count(BusinessCapability), 'recorded capability', 'recorded capabilities')} in this organisation"
    return _fields(source if current_org_id() is not None else None,
                   "intelligence_ui.traceability", "Check how a capability traces to technology")


def _health_scorecard() -> Dict[str, Any]:
    from app.models.risk import Risk

    source = f"{_plural(_count(Risk), 'recorded risk', 'recorded risks')} in this organisation"
    return _fields(source if current_org_id() is not None else None,
                   "risk.risk_register", "Open the risk register")


SCREENS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "roadmap": _roadmap,
    "gaps": _gaps,
    "risks": _risks,
    "capability_health": _capability_health,
    "health_scorecard": _health_scorecard,
}


def four_properties(screen: str) -> Optional[Dict[str, Any]]:
    """The four fields for ``screen``, or None when they cannot be worked out
    (the footer then renders nothing rather than invented values)."""
    helper = SCREENS.get(screen)
    if helper is None:
        return None
    try:
        return helper()
    except Exception:  # a failed count must not break the page it sits under
        logger.exception("four properties for %s could not be worked out", screen)
        db.session.rollback()
        return None
