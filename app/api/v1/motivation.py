"""Motivation reads: drivers and goals for the calling
organisation.

The four routes accept a signed-in session or a bearer token carrying
``api:read`` (``require_api_scope``); a token with ``api:write`` or any
``mcp:*`` scope is refused, and a token without an organisation is refused.
The organisation always comes from the token: the bearer loader binds
``g.current_org_id`` to the token's organisation, and every read filters by
it explicitly, so one organisation can never read another organisation's
rows.

Driver and Goal are tenant-owned rows, looked up by id and the caller's
organisation.
"""

from __future__ import annotations

from flask import Blueprint, g

from app.modules.oauth_provider.scopes import API_READ_SCOPE, require_api_scope
from app.utils.api_response import not_found_response, success_response
from app.utils.pagination import get_pagination_params

motivation_v1_bp = Blueprint("motivation_v1", __name__)


@motivation_v1_bp.route("/drivers", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_drivers():
    """List the calling organisation's drivers, sorted by name then id."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.motivation import Driver

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        Driver.query.filter_by(organization_id=g.current_org_id)
        .order_by(Driver.name, Driver.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return success_response(
        {
            "drivers": [
                {
                    "id": driver.id,
                    "name": driver.name,
                    "description": driver.description or "",
                    "type": driver.driver_type or "",
                    "status": driver.status or "",
                }
                for driver in queried.items
            ],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@motivation_v1_bp.route("/drivers/<int:driver_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_driver(driver_id):
    """One driver by id, only when it belongs to the calling organisation."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.motivation import Driver

    driver = Driver.query.filter_by(
        id=driver_id, organization_id=g.current_org_id
    ).first()
    if driver is None:
        return not_found_response("Driver")
    return success_response(
        {
            "id": driver.id,
            "name": driver.name,
            "description": driver.description or "",
            "type": driver.driver_type or "",
            "status": driver.status or "",
        }
    )


@motivation_v1_bp.route("/goals", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_goals():
    """List the calling organisation's goals, sorted by name then id."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.motivation import Goal

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        Goal.query.filter_by(organization_id=g.current_org_id)
        .order_by(Goal.name, Goal.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return success_response(
        {
            "goals": [
                {
                    "id": goal.id,
                    "name": goal.name,
                    "description": goal.description or "",
                    "type": goal.goal_type or "",
                    "status": goal.status or "",
                }
                for goal in queried.items
            ],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@motivation_v1_bp.route("/goals/<int:goal_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_goal(goal_id):
    """One goal by id, only when it belongs to the calling organisation."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.motivation import Goal

    goal = Goal.query.filter_by(
        id=goal_id, organization_id=g.current_org_id
    ).first()
    if goal is None:
        return not_found_response("Goal")
    return success_response(
        {
            "id": goal.id,
            "name": goal.name,
            "description": goal.description or "",
            "type": goal.goal_type or "",
            "status": goal.status or "",
        }
    )