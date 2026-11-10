"""TD-005: Phase D Technology Architecture API Routes.

Endpoints exposing Phase D analysis data: technology stack audit, per-application
technology debt scores, infrastructure complexity matrix, and workflow orchestration
for the ADM_PHASE_D_TECH workflow.
"""

import logging

from flask import Blueprint, jsonify
from flask_login import current_user, login_required

logger = logging.getLogger(__name__)

phase_d_bp = Blueprint("phase_d", __name__)

_PHASE_D_WORKFLOW_CODE = "ADM_PHASE_D_TECH"


# ---------------------------------------------------------------------------
# GET /api/ea/phase-d/stack-audit
# ---------------------------------------------------------------------------


@phase_d_bp.route("/api/ea/phase-d/stack-audit", methods=["GET"])
@login_required
def stack_audit():
    """Return a full technology stack audit of the application portfolio.

    Response 200::

        {"audit": {hosting_breakdown, apps_with_node_assignment, ...}}
    """
    try:
        from app.services.technology_stack_audit_service import TechnologyStackAuditService

        audit = TechnologyStackAuditService().audit_portfolio()
        return jsonify({"audit": audit}), 200
    except Exception as exc:
        logger.error("stack-audit error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to compute stack audit", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/ea/phase-d/debt-scores
# ---------------------------------------------------------------------------


def _risk_tier(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


_DEBT_INPUTS = ("end-of-life date", "version", "support status")

_OLD_VERSION_RULE_MAX_MAJOR = 7  # keep in step with TechnologyRoadmapService.score_technology_debt


def _plain_list(names):
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _debt_factors(result):
    """Plain names for the inputs that raised an application's debt score."""
    factors = []
    eol = result.get("eol_score", 0)
    if eol >= 100:
        factors.append("End of life has passed")
    elif eol >= 75:
        factors.append("End of life within 12 months")
    elif eol >= 50:
        factors.append("End of life within 24 months")
    if result.get("version_score", 0) > 0:
        major = result.get("major_version")
        factors.append(
            "Major version %s (version %d or lower counts as old)" % (major, _OLD_VERSION_RULE_MAX_MAJOR)
            if major is not None
            else "Version counts as old"
        )
    support = result.get("support_status_score", 0)
    if support >= 80:
        factors.append("Unsupported by the vendor")
    elif support > 0:
        factors.append("Limited vendor support")
    return factors


def _debt_basis(result):
    """Which of the three inputs a score was worked from, which are missing, which are unreadable."""
    missing = [name for name in _DEBT_INPUTS if name in (result.get("missing_data") or [])]
    unreadable = [name for name in _DEBT_INPUTS if name in (result.get("unreadable_data") or [])]
    used = [name for name in _DEBT_INPUTS if name not in missing and name not in unreadable]
    return used, missing, unreadable


def _gaps_sentence(missing, unreadable, support_text=None):
    parts = []
    if missing:
        parts.append("%s not recorded" % _plain_list(missing))
    if unreadable:
        others = [name for name in unreadable if not (name == "support status" and support_text)]
        if others:
            parts.append("%s could not be read" % _plain_list(others))
        if support_text and "support status" in unreadable:
            parts.append("support status not recognised (recorded as \u201c%s\u201d)" % support_text)
    return "; ".join(parts)


def _debt_row(app_id, app_name, result):
    """One Tech Debt row. Never states an input is in good standing unless it was recorded and read."""
    row = {
        "app_id": app_id,
        "app_name": app_name,
        "debt_score": None,
        "risk_tier": None,
        "factors": [],
        "scored_from": [],
        "missing": [],
        "unreadable": [],
        "reason": None,
        "detail": None,
    }
    if not result or result.get("missing_data") is None:
        # The scorer failed or found nothing: say so, not that nothing was recorded.
        row["reason"] = "Not rated: the score could not be worked out. Try again in a moment."
        row["detail"] = row["reason"]
        return row
    used, missing, unreadable = _debt_basis(result)
    support_text = result.get("unrecognised_support_status")
    row.update(scored_from=used, missing=missing, unreadable=unreadable)
    if not used:
        if not unreadable:
            row["reason"] = "Not rated: no " + ", ".join(_DEBT_INPUTS) + " recorded"
        else:
            row["reason"] = "Not rated: " + _gaps_sentence(missing, unreadable, support_text) + ". Record or correct them to get a score."
        row["detail"] = row["reason"]
        return row
    debt_score = result.get("debt_score", 0)
    tier = _risk_tier(float(debt_score))
    factors = _debt_factors(result)
    row.update(debt_score=debt_score, risk_tier=tier, factors=factors)
    if len(used) == len(_DEBT_INPUTS):
        row["detail"] = ", ".join(factors) or (
            "No debt factors: end of life, version and support status are all in good standing"
        )
    else:
        lead = ", ".join(factors) or "No debt factors found in the recorded inputs"
        row["detail"] = "%s. Scored from the %s only; %s. Record the rest for a full score." % (
            lead,
            _plain_list(used),
            _gaps_sentence(missing, unreadable, support_text),
        )
    return row


@phase_d_bp.route("/api/ea/phase-d/debt-scores", methods=["GET"])
@login_required
def debt_scores():
    """Return technology debt scores for every ApplicationComponent.

    Response 200::

        {
            "scores": [{"app_id": int, "app_name": str, "debt_score": float, "risk_tier": str}],
            "summary": {"critical": int, "high": int, "medium": int, "low": int}
        }
    """
    try:
        from app.models.application_portfolio import ApplicationComponent
        from app.services.technology_roadmap_service import TechnologyRoadmapService

        apps = ApplicationComponent.query.all()
        svc = TechnologyRoadmapService()
        scores = []
        summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "not_rated": 0}
        for app in apps:
            row = _debt_row(app.id, app.name, svc.score_technology_debt(app.id) or {})
            if row["debt_score"] is None:
                summary["not_rated"] += 1
            else:
                summary[row["risk_tier"]] += 1
            scores.append(row)
        return jsonify({"scores": scores, "summary": summary}), 200
    except Exception as exc:
        logger.error("debt-scores error: %s", exc, exc_info=True)
        return jsonify({"error": "Could not compute the debt scores. Try again in a moment."}), 500


# ---------------------------------------------------------------------------
# GET /api/ea/phase-d/complexity-matrix
# ---------------------------------------------------------------------------


@phase_d_bp.route("/api/ea/phase-d/complexity-matrix", methods=["GET"])
@login_required
def complexity_matrix():
    """Return infrastructure complexity matrix for the application portfolio.

    Response 200::

        {"matrix": [...]}
    """
    try:
        from app.services.infrastructure_complexity_service import (
            InfrastructureComplexityService,
        )

        matrix = InfrastructureComplexityService().compute_complexity_matrix()
        return jsonify({"matrix": matrix}), 200
    except Exception as exc:
        logger.error("complexity-matrix error: %s", exc, exc_info=True)
        # 500, not 200. An empty matrix at 200 reads as "no infrastructure
        # complexity anywhere", which on a Phase D screen is a finding rather
        # than an outage, and the client could not tell the difference.
        return jsonify({"error": "Could not compute the complexity matrix"}), 500


# ---------------------------------------------------------------------------
# POST /api/ea/phase-d/run-phase-d
# ---------------------------------------------------------------------------


@phase_d_bp.route("/api/ea/phase-d/run-phase-d", methods=["POST"])
@login_required
def run_phase_d():
    """Create and queue a new Phase D Technology Architecture workflow instance.

    Response 201::

        {"instance_id": int, "status": "queued"}
    """
    try:
        from app.services.ea_workflow_engine import EAWorkflowEngine

        engine = EAWorkflowEngine()
        user_id = current_user.id if current_user.is_authenticated else None
        instance = engine.start_workflow(
            workflow_code=_PHASE_D_WORKFLOW_CODE,
            context={},
            triggered_by="api",
            user_id=user_id,
        )
        return jsonify({"instance_id": instance.id, "status": "queued"}), 201
    except ValueError as exc:
        logger.warning("run-phase-d: %s", exc)
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.error("run-phase-d unexpected error: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to start Phase D workflow", "detail": str(exc)}), 500


@phase_d_bp.route("/api/ea/phase-d/viewpoint", methods=["GET"])
@login_required
def get_phase_d_viewpoint():
    """Return live ArchiMate viewpoint for Phase D (Technology Architecture).

    Response 200: {"phase": "D", "viewpoint": {elements_by_layer, relationships,
                   element_count, relationship_count, layer_summary}}
    """
    from app.services.phase_viewpoint_binding_service import PhaseViewpointBindingService
    from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
    from app.services.archimate_viewpoint_render_service import ArchimateViewpointRenderService
    phase_code = "ADM_PHASE_D_TECH"
    elements = WorkflowArchiMateContextService().get_phase_elements(phase_code)
    element_ids = [e["id"] for e in elements]
    viewpoint = ArchimateViewpointRenderService().render_viewpoint(phase_code, element_ids)
    viewpoint["viewpoint_name"] = PhaseViewpointBindingService().get_viewpoint_name(phase_code)
    return jsonify({"phase": "D", "viewpoint": viewpoint}), 200
