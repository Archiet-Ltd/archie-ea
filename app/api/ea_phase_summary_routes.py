"""AV-007: ArchiMate summary API — per-phase element counts.

GET /api/ea/phases/archimate-summary
Returns per-phase ArchiMate element_count, relationship_count, primary_layer,
viewpoint_name, last_derived_at, and coverage_percent.  Used by the dashboard
to show live ArchiMate coverage badges on each ADM phase card.

AV-009: Phase viewpoint detail
GET /api/ea/phases/<phase_code>/viewpoint
Returns full binding metadata + live element/relationship data for a single phase.

AV-010: Cross-phase lineage
GET /api/ea/phases/lineage
Returns the TOGAF ADM element flow chain showing how each phase's derived elements
feed the next phase's input types.
"""
import logging

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app.middleware.tenant_context import current_org_id

logger = logging.getLogger(__name__)

ea_phase_summary_bp = Blueprint("ea_phase_summary", __name__)

# Total ArchiMate elements per layer — used to compute coverage_percent.
# Values are soft constants derived from the seeded data (720 elements total).
_LAYER_TOTALS = {
    "motivation": 120,
    "business": 160,
    "application": 180,
    "technology": 140,
    "implementation_migration": 120,
    "unknown": 100,
}

# TOGAF ADM phase execution order — defines the lineage chain direction.
_PHASE_ORDER = [
    "ADM_PHASE_A_VISION",
    "ADM_PHASE_B_BUSINESS",
    "ADM_PHASE_C_IS",
    "ADM_PHASE_D_TECH",
    "ADM_PHASE_E_OPPORTUNITIES",
    "ADM_PHASE_F_MIGRATION",
    "ADM_PHASE_G_GOVERNANCE",
    "ADM_PHASE_H_CHANGE",
]


@ea_phase_summary_bp.route("/api/ea/phases/archimate-summary", methods=["GET"])
@login_required
def get_archimate_summary():
    """Return per-phase ArchiMate element/relationship counts for dashboard badges.

    Response 200::

        {
          "ADM_PHASE_A_VISION": {
            "element_count": 12,
            "relationship_count": 5,
            "primary_layer": "motivation",
            "viewpoint_name": "Stakeholder Viewpoint",
            "last_derived_at": null,
            "coverage_percent": 10.0
          },
          ...
        }
    """
    from app.services.phase_viewpoint_binding_service import PhaseViewpointBindingService
    from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
    from app.services.archimate_viewpoint_render_service import ArchimateViewpointRenderService
    binding_svc = PhaseViewpointBindingService()
    ctx_svc = WorkflowArchiMateContextService()
    render_svc = ArchimateViewpointRenderService()

    summary = {}
    for phase_code in binding_svc.get_all_phases():
        try:
            elements = ctx_svc.get_phase_elements(phase_code)
            element_ids = [e["id"] for e in elements]
            viewpoint = render_svc.render_viewpoint(phase_code, element_ids)

            primary_layer = binding_svc.get_primary_layer(phase_code)
            layer_total = _LAYER_TOTALS.get(primary_layer, 100)
            element_count = viewpoint.get("element_count", 0)
            coverage_pct = min(round(element_count / layer_total * 100, 1), 100.0)

            # Derive last_derived_at from the most recently created element if any
            last_derived_at = None
            if elements:
                try:
                    from app.models.archimate_core import ArchiMateElement
                    latest = (
                        ArchiMateElement.query  # model-safety-ok: batch .in_() not N+1
                        .filter(ArchiMateElement.id.in_(element_ids))
                        .order_by(ArchiMateElement.created_at.desc())
                        .first()
                    )
                    if latest and latest.created_at:
                        last_derived_at = latest.created_at.isoformat()
                except Exception:
                    last_derived_at = None

            summary[phase_code] = {
                "element_count": element_count,
                "relationship_count": viewpoint.get("relationship_count", 0),
                "primary_layer": primary_layer,
                "viewpoint_name": binding_svc.get_viewpoint_name(phase_code),
                "last_derived_at": last_derived_at,
                "coverage_percent": coverage_pct,
            }
        except Exception as exc:
            logger.warning("archimate-summary: phase %s failed: %s", phase_code, exc)
            summary[phase_code] = {
                "element_count": 0,
                "relationship_count": 0,
                "primary_layer": binding_svc.get_primary_layer(phase_code),
                "viewpoint_name": binding_svc.get_viewpoint_name(phase_code),
                "last_derived_at": None,
                "coverage_percent": 0.0,
            }

    # Elements linked to the organisation's ADM cards in each phase (derived
    # from the card to element links), alongside the viewpoint counts.
    from app.services.adm_analytics_service import ADMAnalyticsService

    org_id = current_org_id()
    for phase_code, entry in summary.items():
        letter = phase_code.split("_")[2] if phase_code.count("_") >= 2 else phase_code
        try:
            entry["linked_element_count"] = len(ADMAnalyticsService.elements_for_phase(letter, org_id))
        except Exception as exc:
            logger.warning("archimate-summary: linked elements for %s failed: %s", phase_code, exc)
            entry["linked_element_count"] = 0

    return jsonify(summary), 200


@ea_phase_summary_bp.route("/api/ea/adm-links", methods=["GET"])
@login_required
def get_adm_element_links():
    """Derived card-to-element views, scoped to the active organisation.

    Exactly one query parameter: ``phase`` (ADM phase code) returns the elements
    of that phase, ``deliverable`` (name) the elements of that deliverable,
    ``element_id`` the phases the element belongs to.
    """
    from app.services.adm_analytics_service import ADMAnalyticsService

    org_id = current_org_id()
    if org_id is None:
        return jsonify({"error": "No active organisation"}), 403
    given = [k for k in ("phase", "deliverable", "element_id") if request.args.get(k)]
    if len(given) != 1:
        return jsonify({"error": "Give exactly one of phase, deliverable, element_id"}), 400
    if given[0] == "phase":
        return jsonify({"elements": ADMAnalyticsService.elements_for_phase(request.args["phase"], org_id)}), 200
    if given[0] == "deliverable":
        return jsonify({"elements": ADMAnalyticsService.elements_for_deliverable(request.args["deliverable"], org_id)}), 200
    try:
        element_id = int(request.args["element_id"])
    except ValueError:
        return jsonify({"error": "element_id must be an integer"}), 400
    return jsonify({"phases": ADMAnalyticsService.phase_for_element(element_id, org_id)}), 200


@ea_phase_summary_bp.route("/api/ea/phases/<string:phase_code>/viewpoint", methods=["GET"])
@login_required
def get_phase_viewpoint_detail(phase_code: str):
    """AV-009: Return full viewpoint metadata + live element/relationship data for one phase.

    Args:
        phase_code: e.g. ``ADM_PHASE_A_VISION``

    Response 200::

        {
          "phase_code": "ADM_PHASE_A_VISION",
          "phase_name": "Phase A — Architecture Vision",
          "viewpoint_name": "Stakeholder Viewpoint",
          "primary_layer": "motivation",
          "archimate_concern": "Identify key stakeholders and their concerns",
          "input_types": ["Driver", "Stakeholder", "Goal", "Outcome"],
          "derived_types": ["Principle", "Requirement"],
          "element_count": 12,
          "relationship_count": 5,
          "elements": [...],
          "relationships": [...]
        }

    Response 404 if phase_code is not a valid ADM phase.
    """
    from app.services.phase_viewpoint_binding_service import PhaseViewpointBindingService
    from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
    from app.services.archimate_viewpoint_render_service import ArchimateViewpointRenderService

    binding_svc = PhaseViewpointBindingService()
    binding = binding_svc.get_binding(phase_code.upper())
    if not binding:
        return jsonify({"error": f"Unknown phase code: {phase_code}"}), 404

    ctx_svc = WorkflowArchiMateContextService()
    render_svc = ArchimateViewpointRenderService()
    code_upper = phase_code.upper()

    try:
        elements = ctx_svc.get_phase_elements(code_upper)
        element_ids = [e["id"] for e in elements]
        viewpoint = render_svc.render_viewpoint(code_upper, element_ids)
    except Exception as exc:
        logger.warning("AV-009 viewpoint detail failed for %s: %s", phase_code, exc)
        elements = []
        viewpoint = {"element_count": 0, "relationship_count": 0, "elements": [], "relationships": []}

    return jsonify({
        "phase_code": code_upper,
        "phase_name": binding["phase_name"],
        "viewpoint_name": binding["viewpoint_name"],
        "primary_layer": binding["primary_layer"],
        "archimate_concern": binding.get("archimate_concern", ""),
        "input_types": binding["input_types"],
        "derived_types": binding["derived_types"],
        "element_count": viewpoint.get("element_count", 0),
        "relationship_count": viewpoint.get("relationship_count", 0),
        "elements": viewpoint.get("elements", elements),
        "relationships": viewpoint.get("relationships", []),
    }), 200


@ea_phase_summary_bp.route("/api/ea/phases/lineage", methods=["GET"])
@login_required
def get_phase_lineage():
    """AV-010: Return cross-phase ArchiMate element flow chain (TOGAF ADM lineage).

    Shows how each phase's derived element types conceptually feed the next phase's
    input element types, tracing the full A→B→C→D→E→F→G→H chain.

    Response 200::

        {
          "lineage": [
            {
              "phase_code": "ADM_PHASE_A_VISION",
              "phase_name": "Phase A — Architecture Vision",
              "primary_layer": "motivation",
              "input_types": ["Driver", "Stakeholder", "Goal", "Outcome"],
              "derived_types": ["Principle", "Requirement"],
              "feeds_into": "ADM_PHASE_B_BUSINESS",
              "shared_types": []   // types produced by this phase that are consumed by next
            },
            ...
          ]
        }
    """
    from app.services.phase_viewpoint_binding_service import PhaseViewpointBindingService

    binding_svc = PhaseViewpointBindingService()
    lineage = []
    for i, phase_code in enumerate(_PHASE_ORDER):
        binding = binding_svc.get_binding(phase_code)
        if not binding:
            continue
        # Determine which derived types from THIS phase appear as input_types in NEXT phase
        next_code = _PHASE_ORDER[i + 1] if i + 1 < len(_PHASE_ORDER) else None
        shared_types = []
        if next_code:
            next_binding = binding_svc.get_binding(next_code)
            if next_binding:
                next_inputs = set(next_binding["input_types"])
                shared_types = [t for t in binding["derived_types"] if t in next_inputs]

        lineage.append({
            "phase_code": phase_code,
            "phase_name": binding["phase_name"],
            "primary_layer": binding["primary_layer"],
            "input_types": binding["input_types"],
            "derived_types": binding["derived_types"],
            "feeds_into": next_code,
            "shared_types": shared_types,
        })

    return jsonify({"lineage": lineage}), 200
