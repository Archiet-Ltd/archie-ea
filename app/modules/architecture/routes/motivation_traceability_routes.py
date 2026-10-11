"""Motivation traceability routes — goal trace, driver and assessment write paths.

  GET  /api/v1/motivation/goals/<id>/trace
  POST /api/v1/motivation/drivers
  POST /api/v1/motivation/drivers/<id>/assessments
  GET  /api/v1/motivation/drivers/<id>/suggested-goals
  POST /api/v1/motivation/drivers/<id>/link-goal
"""
from __future__ import annotations

import logging

from flask import Blueprint, request
from flask_login import login_required

from app.utils.api_response import error_response, success_response
from app.utils.tenant import current_organization_id

logger = logging.getLogger(__name__)

motivation_api = Blueprint(
    "motivation_api", __name__, url_prefix="/api/v1/motivation"
)


# --------------------------------------------------------------------------- #
# Goal trace
# --------------------------------------------------------------------------- #


@motivation_api.route("/goals/<int:goal_id>/trace", methods=["GET"])
@login_required
def goal_trace(goal_id: int):
    """Walk the traceability chain from a Goal.

    Goal → Capability (ArchiMate Realization) →
    Initiative (initiative_goals / strategic_initiative_goals) →
    Work Package / Application.

    Each hop cites its relationship row. Shareable by permanent link.
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    from app.modules.architecture.services.motivation_layer_service import (
        MotivationLayerService,
    )

    result = MotivationLayerService.get_goal_trace(goal_id, organization_id)
    if result.get("goal") is None:
        return error_response(
            "Goal not found",
            code="NOT_FOUND",
            status_code=404,
        )

    return success_response(result)


# --------------------------------------------------------------------------- #
# Driver write paths
# --------------------------------------------------------------------------- #


@motivation_api.route("/drivers", methods=["POST"])
@login_required
def create_driver():
    """Create a Driver record with source, date and owner."""
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return error_response(
            "name is required",
            code="INVALID_PARAMETER",
            status_code=400,
        )

    from app.modules.architecture.services.motivation_layer_service import (
        MotivationLayerService,
    )

    try:
        driver = MotivationLayerService.create_driver(data, organization_id)
        from app import db
        db.session.commit()
        return success_response(
            {
                "id": driver.id,
                "name": driver.name,
                "driver_type": driver.driver_type,
                "source": driver.source,
                "identified_date": (
                    driver.identified_date.isoformat()
                    if driver.identified_date
                    else None
                ),
            },
            status_code=201,
        )
    except Exception as exc:
        logger.error("Failed to create driver: %s", exc)
        return error_response(
            str(exc),
            code="CREATE_FAILED",
            status_code=500,
        )


@motivation_api.route("/drivers/<int:driver_id>/assessments", methods=["POST"])
@login_required
def create_assessment(driver_id: int):
    """Create an Assessment row against a Driver."""
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return error_response(
            "name is required",
            code="INVALID_PARAMETER",
            status_code=400,
        )

    from app.modules.architecture.services.motivation_layer_service import (
        MotivationLayerService,
    )

    try:
        assessment = MotivationLayerService.create_assessment(
            driver_id, data, organization_id
        )
        from app import db
        db.session.commit()
        return success_response(
            {
                "id": assessment.id,
                "name": assessment.name,
                "assessment_type": assessment.assessment_type,
                "result_score": assessment.result_score,
                "assessor": assessment.assessor,
                "date_assessed": (
                    assessment.date_assessed.isoformat()
                    if assessment.date_assessed
                    else None
                ),
                "driver_id": assessment.driver_id,
            },
            status_code=201,
        )
    except ValueError as exc:
        return error_response(
            str(exc),
            code="NOT_FOUND",
            status_code=404,
        )
    except Exception as exc:
        logger.error("Failed to create assessment: %s", exc)
        return error_response(
            str(exc),
            code="CREATE_FAILED",
            status_code=500,
        )


@motivation_api.route(
    "/drivers/<int:driver_id>/suggested-goals", methods=["GET"]
)
@login_required
def suggest_goals_for_driver(driver_id: int):
    """Suggest candidate goals for a driver by text similarity.

    Never auto-links; returns candidates for the strategy officer to confirm.
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    from app.modules.architecture.services.motivation_layer_service import (
        MotivationLayerService,
    )

    result = MotivationLayerService.suggest_goals_for_driver(
        driver_id, organization_id
    )
    if result.get("driver") is None:
        return error_response(
            "Driver not found",
            code="NOT_FOUND",
            status_code=404,
        )

    return success_response(result)


@motivation_api.route(
    "/drivers/<int:driver_id>/link-goal", methods=["POST"]
)
@login_required
def link_driver_to_goal(driver_id: int):
    """Link a Driver to a Goal. The strategy officer confirms the link."""
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    data = request.get_json(silent=True) or {}
    goal_id = data.get("goal_id")
    if not goal_id:
        return error_response(
            "goal_id is required",
            code="INVALID_PARAMETER",
            status_code=400,
        )

    from app.modules.architecture.services.motivation_layer_service import (
        MotivationLayerService,
    )

    try:
        result = MotivationLayerService.link_driver_to_goal(
            driver_id, int(goal_id), organization_id
        )
        from app import db
        db.session.commit()
        return success_response(result)
    except ValueError as exc:
        msg = str(exc)
        if "already linked" in msg:
            return error_response(
                msg,
                code="CONFLICT",
                status_code=409,
            )
        return error_response(
            msg,
            code="NOT_FOUND",
            status_code=404,
        )
    except Exception as exc:
        logger.error("Failed to link driver to goal: %s", exc)
        return error_response(
            str(exc),
            code="LINK_FAILED",
            status_code=500,
        )