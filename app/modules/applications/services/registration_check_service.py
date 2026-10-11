"""Duplicate check shown before a new application is registered.

One matcher answers "is this already in the model": the intelligence matcher
(identifier, then normalised name, then near-duplicate). This module only turns
its answer into application records the person can open, and adds the one thing
the matcher does not know about: an application code, which is the identifier a
person types when registering a purchased product.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app import db
from app.middleware.tenant_context import current_org_id
from app.models.application_portfolio import ApplicationComponent
from app.modules.intelligence.services.matcher_service import MatcherService
from app.utils.duplicate_guard import normalize_name

APPLICATION_ELEMENT_TYPE = "ApplicationComponent"
MAX_MATCHES = 8


def _entry(app: ApplicationComponent, match: str, reason: str) -> Dict[str, Any]:
    return {
        "id": app.id,
        "name": app.name,
        "application_code": app.application_code,
        "match": match,
        "reason": reason,
    }


def find_registration_matches(
    name: Optional[str],
    application_code: Optional[str] = None,
    *,
    org_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return existing applications the new one may duplicate, strongest first.

    ``match`` is ``same_code`` (identifier), ``same_name`` (identical after
    folding case and whitespace) or ``similar_name`` (near-duplicate).
    """
    org_id = org_id if org_id is not None else current_org_id()
    if org_id is None:
        raise RuntimeError("registration check requires an organisation context")

    found: Dict[int, Dict[str, Any]] = {}

    code = (application_code or "").strip()
    if code:
        by_code = (
            db.session.query(ApplicationComponent)
            .filter(
                ApplicationComponent.organization_id == org_id,
                db.func.lower(db.func.btrim(ApplicationComponent.application_code))
                == normalize_name(code),
            )
            .limit(MAX_MATCHES)
            .all()
        )
        for app in by_code:
            found[app.id] = _entry(app, "same_code", "Same application code")

    if name and name.strip():
        result = MatcherService.match_by_name(
            name, APPLICATION_ELEMENT_TYPE, org_id=org_id
        )
        element_ids: List[int] = []
        if result.evidence and result.evidence.get("candidate_ids"):
            element_ids = list(result.evidence["candidate_ids"])
        elif result.matched_element_id is not None:
            element_ids = [result.matched_element_id]
        if element_ids:
            apps = (
                db.session.query(ApplicationComponent)
                .filter(
                    ApplicationComponent.organization_id == org_id,
                    ApplicationComponent.archimate_element_id.in_(element_ids),
                )
                .all()
            )
            order = {eid: i for i, eid in enumerate(element_ids)}
            apps.sort(key=lambda a: order.get(a.archimate_element_id, 0))
            exact = normalize_name(name)
            for app in apps:
                if app.id in found:
                    continue
                if normalize_name(app.name) == exact:
                    found[app.id] = _entry(app, "same_name", "Same name")
                else:
                    found[app.id] = _entry(app, "similar_name", "Similar name")

    rank = {"same_code": 0, "same_name": 1, "similar_name": 2}
    return sorted(found.values(), key=lambda e: rank[e["match"]])[:MAX_MATCHES]
