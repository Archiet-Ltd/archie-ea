"""
API v1 Impact Analysis Endpoints

Canonical shared route for running impact analysis on an application or ArchiMate element.
All UI components (strategic dashboard, application detail, composer, chat, ARB) should
call POST /api/v1/impact/analyze instead of calling impact services directly.

See docs/impact_analysis_api_contract.md for the full request/response contract.
"""

import logging

from flask import Blueprint, request
from flask_login import login_required

from app.utils.api_response import error_response, success_response

impact_bp = Blueprint("impact_v1", __name__)
logger = logging.getLogger(__name__)

VALID_SCENARIOS = {"modification", "retirement", "upgrade", "cloud_migration", "vendor_switch", "custom"}


@impact_bp.route("/analyze", methods=["POST"])
@login_required
def analyze_impact():
    """
    Run impact analysis for an application or ArchiMate element.

    Request body (JSON):
        app_id (int, optional): ApplicationComponent ID. Required if element_id is omitted.
        element_id (int, optional): ArchiMate element ID. Required if app_id is omitted.
        scenario (str, required): Change scenario. One of: modification, retirement,
            upgrade, cloud_migration, vendor_switch, custom.

    Returns:
        200: {risk_level, total_score, breakdown, affected_elements, summary}
        400: Validation error
        404: app/element not found
    """
    data = request.get_json(silent=True) or {}
    app_id = data.get("app_id")
    element_id = data.get("element_id")
    scenario = data.get("scenario")

    # T-004 (API-3/DE-10): additive optional parameters. include_derived
    # defaults False so an unchanged request (no include_derived key at all)
    # never receives a populated derived_elements list. MINOR-1 correction:
    # this does NOT make the whole response byte-identical to before this
    # change -- the element_id branch unconditionally gains
    # derived_elements/derivation_state keys regardless of include_derived,
    # because derivation_state (current/stale/not_computed) is a real,
    # always-computed measurement (see B1/M9: it must never be fabricated or
    # left stale just because include_derived=False was requested). The
    # app_id branch is genuinely untouched -- see D2 below.
    include_derived_raw = data.get("include_derived", False)
    if not isinstance(include_derived_raw, bool):
        return error_response("include_derived must be a boolean", status_code=400)
    include_derived = include_derived_raw

    max_depth_raw = data.get("max_depth", 3)
    try:
        max_depth = int(max_depth_raw)
    except (TypeError, ValueError):
        return error_response("max_depth must be an integer between 1 and 10", status_code=400)
    if not (1 <= max_depth <= 10):
        return error_response("max_depth must be between 1 and 10", status_code=400)

    # Validate mutually exclusive identifier fields
    if not app_id and not element_id:
        return error_response("Exactly one of app_id or element_id is required", status_code=400)
    if app_id and element_id:
        return error_response("Provide only one of app_id or element_id, not both", status_code=400)

    # T-004 (M5 fix): include_derived/max_depth only apply to the element_id
    # branch's additive projection (_derived_elements_for_element below).
    # Silently accepting them on the app_id branch would 200 with no
    # derived_elements/derivation_state and no signal the params were
    # dropped -- indistinguishable from "derivation ran and found nothing".
    # Reject explicitly instead, matching this task's additive-only scope.
    if app_id and "include_derived" in data:
        return error_response(
            "include_derived is only supported when element_id is used", status_code=400
        )
    if app_id and "max_depth" in data:
        return error_response(
            "max_depth is only supported when element_id is used", status_code=400
        )
    if not scenario:
        return error_response("scenario is required", status_code=400)
    if scenario not in VALID_SCENARIOS:
        return error_response(
            f"scenario must be one of: {', '.join(sorted(VALID_SCENARIOS))}",
            status_code=400,
        )

    try:
        app_id = int(app_id) if app_id is not None else None
        element_id = int(element_id) if element_id is not None else None
    except (ValueError, TypeError):
        return error_response("app_id and element_id must be integers", status_code=400)

    try:
        from app.modules.ai_chat.services.ai_impact_analysis_service import AIImpactAnalysisService

        svc = AIImpactAnalysisService()

        if app_id is not None:
            result = svc.analyze_application_impact(
                app_id=app_id,
                scenario=scenario,
            )
        else:
            # NEW-1 fix: ImpactAnalysisService.analyze_change_impact never
            # checks the element exists -- for an unknown or cross-tenant
            # element_id it still returns a fully-formed dict (zero
            # dependencies, risk_level "LOW"), which _normalise_element_result
            # turns into a truthy result the ``if not result`` check below
            # never catches. Verify the element exists AND belongs to this
            # tenant first (ArchiMateElement carries TenantMixin, so this
            # select is already ORM-fenced to the caller's org) and return a
            # real 404 rather than a 200 that fabricates
            # derivation_state="not_computed" for something that was never
            # analysed at all.
            from app.extensions import db
            from app.models import ArchiMateElement

            element = db.session.execute(
                db.select(ArchiMateElement).where(ArchiMateElement.id == element_id)
            ).scalar_one_or_none()
            if element is None:
                return error_response("Element not found", code="NOT_FOUND", status_code=404)

            # Element-level analysis: delegate to canonical v2 service
            from app.modules.solutions_strategic.v2.services.impact_analysis_service import (
                ImpactAnalysisService,
            )

            change_type = _scenario_to_change_type(scenario)
            raw = ImpactAnalysisService.analyze_change_impact(
                element_id=element_id,
                change_type=change_type,
                scenario=scenario,
            )
            result = _normalise_element_result(raw)

        if not result:
            return error_response("Impact analysis returned no result", status_code=404)

        contract = _to_contract_shape(result)

        # T-004 (API-3/DE-10): additive only, and only on the element_id
        # branch -- the app_id branch has no equivalent projection today and
        # stays untouched (D2 in 00-verification-notes.md; a characterisation
        # test in app/modules/intelligence/tests/ pins its response keys).
        if element_id is not None:
            derived_elements, derivation_state = _derived_elements_for_element(
                element_id, include_derived=include_derived, max_depth=max_depth
            )
            contract["derived_elements"] = derived_elements
            contract["derivation_state"] = derivation_state

        return success_response(contract)

    except Exception as exc:
        logger.error("impact/analyze error app_id=%s element_id=%s: %s", app_id, element_id, exc, exc_info=True)
        return error_response("Impact analysis failed", status_code=500)


def _scenario_to_change_type(scenario: str) -> str:
    """Map API scenario names to internal ImpactAnalysisService change_type strings."""
    mapping = {
        "modification": "MODIFY",
        "retirement": "RETIRE",
        "upgrade": "MODIFY",
        "cloud_migration": "REPLACE",
        "vendor_switch": "REPLACE",
        "custom": "MODIFY",
    }
    return mapping.get(scenario, "MODIFY")


def _normalise_element_result(raw: dict) -> dict:
    """Convert element-level ImpactAnalysisService output to contract shape."""
    all_deps = list(raw.get("direct_dependencies") or []) + list(raw.get("indirect_dependencies") or [])
    return {
        "risk_level": raw.get("risk_level", "LOW"),
        "total_score": raw.get("total_affected", 0),
        "breakdown": {
            "direct_dependencies": len(raw.get("direct_dependencies") or []),
            "indirect_dependencies": len(raw.get("indirect_dependencies") or []),
            "weighted_score": raw.get("weighted_score"),
            "estimated_financial_risk": raw.get("estimated_financial_risk"),
        },
        "affected_elements": [
            {"id": d.get("id"), "name": d.get("name"), "type": d.get("type"), "level": d.get("level")}
            for d in all_deps
        ],
        "summary": (
            f"{raw.get('change_type', 'Change')} to element {raw.get('element_id')} "
            f"affects {raw.get('total_affected', 0)} elements. "
            f"Risk: {raw.get('risk_level', 'LOW')}."
        ),
        "analysis_id": raw.get("analysis_id"),
    }


def _derived_elements_for_element(element_id: int, *, include_derived: bool, max_depth: int):
    """Read derived edges for an element from the shared query service.

    No parallel scoring logic (DESIGN.md) -- this reads
    ``IntelligenceQueryService.cross_layer_impact``, the single accessor,
    rather than recomputing anything. ``include_derived=False`` (the default,
    matching an unchanged request) returns an empty list and whatever real
    ``derivation_state`` the service measured, never a fabricated one.

    Deliberately does NOT catch exceptions here: ``not_computed`` is a real,
    meaningful ``derivation_state`` (derivation not yet computed, with a
    one-click run action) and must never be produced by a crashed lookup. A
    real failure here (DB error, ``MultipleResultsFound``, etc.) propagates
    to the caller's own ``except Exception`` block (``analyze_impact``,
    above), which returns a proper 500 -- a bug looks like a bug, not like
    an un-run derivation.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.cross_layer_impact(
        element_id,
        include_derived=include_derived,
        include_stale=False,
        max_depth=max_depth,
        direction="downstream",
        layer=None,
        with_owner=False,
    )

    summary = result.get("summary") or {}
    # NEW-2 fix: a literal string fallback here silently fabricates a
    # legitimate-looking state if the key is ever genuinely missing -- the
    # exact defect class B1 fixed one call below. cross_layer_impact always
    # sets this key on every branch, so a missing key here is a real bug and
    # should raise (surfacing as a 500 via the caller's except block), not be
    # papered over with an invented value.
    derivation_state = summary["derivation_state"]
    derived_rows = [row for row in (result.get("rows") or []) if row["relation"]["kind"] == "derived"]
    return derived_rows, derivation_state


def _to_contract_shape(result: dict) -> dict:
    """Ensure the result matches the API contract regardless of which service produced it."""
    return {
        "risk_level": (result.get("risk_level") or result.get("overall_risk_level") or "LOW").upper(),
        "total_score": result.get("total_score") or result.get("total_affected") or 0,
        "breakdown": result.get("breakdown") or result.get("impact_breakdown") or {},
        "affected_elements": result.get("affected_elements") or result.get("affected_applications") or [],
        "diagram": result.get("diagram") or result.get("graph_visualization"),
        "summary": result.get("summary") or result.get("executive_summary"),
        "analysis_id": result.get("analysis_id") or result.get("id"),
    }
