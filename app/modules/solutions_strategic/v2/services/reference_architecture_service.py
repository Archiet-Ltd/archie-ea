"""Reference architectures from the pattern catalogue: recommend one for a
stated solution context, and apply it to a solution.

A reference architecture is an ``IntegrationPattern`` row with
``is_reference_architecture`` set (app/models/integration_pattern.py) -- the one
pattern library, not a second table. It carries the context it fits (data
handled, latency, hosting), the controls it provides, and the components it
adds to a solution.

Recommending is deterministic and explained: each stated context fact is
compared with what the pattern says it fits, and the answer shows which facts
match, which do not, and which the pattern states nothing about. Nothing is
applied until the architect chooses Apply. Applying links each component to
the solution as an architecture element (found by name and type in the
organisation, or created there), marked for review, and records which
reference architecture it came from, so the choice is still there after a
reload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app import db
from app.models.integration_pattern import (
    REFERENCE_CONTEXT_LABELS,
    REFERENCE_CONTEXT_OPTIONS,
    IntegrationPattern,
)

SOURCE = "reference_architecture"

NONE_CATALOGUED_REASON = (
    "No reference architecture is in the pattern catalogue yet, so there is "
    "nothing to recommend."
)


def clean_context(raw: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Keep only known context values; anything else is treated as not stated."""
    out: Dict[str, Optional[str]] = {}
    for dimension, options in REFERENCE_CONTEXT_OPTIONS.items():
        value = (raw.get(dimension) or "").strip().lower()
        out[dimension] = value if value in options else None
    return out


def _catalogue() -> List[IntegrationPattern]:
    # tenant-scoping-ok: the pattern catalogue is shared reference data with no organisation column
    return (
        IntegrationPattern.query.filter(IntegrationPattern.is_reference_architecture.is_(True))
        .order_by(IntegrationPattern.name)
        .all()
    )


def _usable(pattern: IntegrationPattern) -> bool:
    return (pattern.approval_status or "").strip().lower() not in ("blocked", "deprecated")


def _fit(pattern: IntegrationPattern, context: Dict[str, Optional[str]]) -> Dict[str, Any]:
    fit_context = pattern.fit_context or {}
    facts = []
    matched = stated = unknown = 0
    for dimension in REFERENCE_CONTEXT_OPTIONS:
        value = context.get(dimension)
        label = REFERENCE_CONTEXT_LABELS[dimension]
        supported = [v for v in (fit_context.get(dimension) or []) if v]
        if value is None:
            facts.append({
                "dimension": label, "stated": None, "matches": None,
                "explanation": f"{label}: not stated, so not used in the recommendation.",
            })
            continue
        stated += 1
        value_label = REFERENCE_CONTEXT_LABELS.get(value, value)
        if not supported:
            unknown += 1
            facts.append({
                "dimension": label, "stated": value_label, "matches": None,
                "explanation": f"{label}: the pattern states nothing about this, so fit is unknown.",
            })
        elif value in supported:
            matched += 1
            facts.append({
                "dimension": label, "stated": value_label, "matches": True,
                "explanation": f"{label}: {value_label} is within what the pattern is designed for.",
            })
        else:
            facts.append({
                "dimension": label, "stated": value_label, "matches": False,
                "explanation": (
                    f"{label}: {value_label} is outside what the pattern is designed for ("
                    + ", ".join(REFERENCE_CONTEXT_LABELS.get(v, v) for v in supported) + ")."
                ),
            })
    if stated == 0:
        confidence = None
    elif matched == stated:
        confidence = "high"
    elif matched * 2 >= stated:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "facts": facts,
        "matched": matched,
        "stated": stated,
        "unknown": unknown,
        "confidence": confidence,
    }


def pattern_summary(pattern: IntegrationPattern) -> Dict[str, Any]:
    return {
        "id": pattern.id,
        "name": pattern.name,
        "description": pattern.description,
        "approval_status": pattern.approval_status,
        "approval_notes": pattern.approval_notes,
        "controls": [c for c in (pattern.applies_to_controls or []) if isinstance(c, dict) and c.get("name")],
        "components": [
            c for c in (pattern.components or [])
            if isinstance(c, dict) and c.get("name") and c.get("type")
        ],
    }


def recommend(context: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """Rank the catalogue's reference architectures against the stated context.

    Returns ``{"context", "stated", "candidates": [...], "reason"}``; the first
    candidate is the recommendation. With nothing stated, candidates are listed
    without a ranking and ``stated`` is 0.
    """
    catalogue = [p for p in _catalogue() if _usable(p)]
    stated = sum(1 for v in context.values() if v)
    if not catalogue:
        return {"context": context, "stated": stated, "candidates": [], "reason": NONE_CATALOGUED_REASON}

    candidates = []
    for pattern in catalogue:
        fit = _fit(pattern, context)
        candidates.append({**pattern_summary(pattern), "fit": fit})
    candidates.sort(key=lambda c: (-c["fit"]["matched"], c["fit"]["unknown"], c["name"]))
    return {"context": context, "stated": stated, "candidates": candidates, "reason": None}


def _solution_in_tenant(solution_id: int):
    from app.models.solution_models import Solution

    return Solution.query.filter(Solution.id == solution_id).first()


def applied(solution_id: int) -> List[Dict[str, Any]]:
    """Reference architectures already applied to a solution, read from the
    solution's element links, each with the components it added."""
    from app.models.solution_models import SolutionArchiMateElement

    # tenant-scoping-ok: callers pass a solution_id already checked against the caller's organisation
    links = (
        SolutionArchiMateElement.query.filter(SolutionArchiMateElement.solution_id == solution_id)
        .order_by(SolutionArchiMateElement.id)
        .all()
    )
    by_pattern: Dict[Any, Dict[str, Any]] = {}
    for link in links:
        spec = link.spec_data if isinstance(link.spec_data, dict) else {}
        if spec.get("source") != SOURCE:
            continue
        key = spec.get("pattern_id")
        group = by_pattern.setdefault(key, {
            "pattern_id": key,
            "pattern_name": spec.get("pattern_name") or "—",
            "controls": spec.get("controls") or [],
            "context": spec.get("context") or {},
            "applied_at": spec.get("applied_at"),
            "components": [],
        })
        group["components"].append({"name": link.element_name, "layer": link.layer_type})
    return list(by_pattern.values())


def apply(solution_id: int, pattern_id: int, context: Dict[str, Optional[str]], user_id: int) -> Dict[str, Any]:
    """Add a reference architecture's components to a solution.

    Idempotent: a component already linked to the solution is skipped.
    Returns ``{"success", "added", "skipped", "pattern_name"}`` or
    ``{"success": False, "error"}``.
    """
    from app.models.solution_models import SolutionArchiMateElement
    from app.services.solution_template_service import _get_or_create_element

    if _solution_in_tenant(solution_id) is None:
        return {"success": False, "error": "Solution not found."}
    # tenant-scoping-ok: the pattern catalogue is shared reference data with no organisation column
    pattern = IntegrationPattern.query.filter(IntegrationPattern.id == pattern_id).first()
    if pattern is None or not pattern.is_reference_architecture:
        return {"success": False, "error": "Not a reference architecture in the catalogue."}
    if not _usable(pattern):
        return {"success": False, "error": f"'{pattern.name}' is {pattern.approval_status} and cannot be applied."}
    summary = pattern_summary(pattern)
    if not summary["components"]:
        return {"success": False, "error": f"'{pattern.name}' lists no components to add."}

    # tenant-scoping-ok: solution_id was checked against the caller's organisation above
    existing = {
        link.element_id
        for link in SolutionArchiMateElement.query.filter(
            SolutionArchiMateElement.solution_id == solution_id
        ).all()
    }
    now = datetime.utcnow().isoformat()
    added = skipped = 0
    for component in summary["components"]:
        layer = (component.get("layer") or "application").strip().lower()
        element = _get_or_create_element(component["name"], component["type"], layer)
        if element.id in existing:
            skipped += 1
            continue
        db.session.add(SolutionArchiMateElement(
            solution_id=solution_id,
            element_id=element.id,
            element_table="archimate_elements",
            element_name=element.name,
            layer_type=layer,
            element_role="primary",
            notes=f"Added from the reference architecture '{pattern.name}'.",
            spec_data={
                "source": SOURCE,
                "pattern_id": pattern.id,
                "pattern_name": pattern.name,
                "controls": [c["name"] for c in summary["controls"]],
                "context": {k: v for k, v in context.items() if v},
                "pending_review": True,
                "applied_by_user_id": user_id,
                "applied_at": now,
            },
        ))
        existing.add(element.id)
        added += 1
    db.session.commit()
    return {"success": True, "added": added, "skipped": skipped, "pattern_name": pattern.name}
