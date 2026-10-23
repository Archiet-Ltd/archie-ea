"""Business actor and business process reads over the public interface.

The four routes accept a signed-in session or a bearer token carrying
``api:read`` (``require_api_scope``); a token with ``api:write`` or any
``mcp:*`` scope is refused, and a token without an organisation is refused.
The organisation always comes from the token: the bearer loader binds
``g.current_org_id`` to the token's organisation, and every read filters by
it explicitly, so one organisation can never read another organisation's
rows.

BusinessActor and BusinessProcess are tenant-owned rows (TenantMixin), looked
up by id and the caller's organisation. The response exposes only plain facts
about the record: id, name, description, type and status.
"""

from __future__ import annotations

from flask import Blueprint, g

from app.modules.oauth_provider.scopes import API_READ_SCOPE, require_api_scope
from app.utils.api_response import not_found_response, success_response
from app.utils.pagination import get_pagination_params

business_v1_bp = Blueprint("business_v1", __name__)


@business_v1_bp.route("/business-actors", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_business_actors():
    """List the calling organisation's business actors, sorted by name then id."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.business_layer import BusinessActor

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        BusinessActor.query.filter_by(organization_id=g.current_org_id)
        .order_by(BusinessActor.name, BusinessActor.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return success_response(
        {
            "business_actors": [
                {
                    "id": actor.id,
                    "name": actor.name,
                    "description": actor.description or "",
                    "type": actor.actor_type or "",
                    "status": actor.operational_status or "",
                }
                for actor in queried.items
            ],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@business_v1_bp.route("/business-actors/<int:actor_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_business_actor(actor_id):
    """One business actor by id, only when it belongs to the calling organisation."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.business_layer import BusinessActor

    actor = BusinessActor.query.filter_by(
        id=actor_id, organization_id=g.current_org_id
    ).first()
    if actor is None:
        return not_found_response("Business Actor")
    return success_response(
        {
            "id": actor.id,
            "name": actor.name,
            "description": actor.description or "",
            "type": actor.actor_type or "",
            "status": actor.operational_status or "",
        }
    )


@business_v1_bp.route("/business-processes", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_business_processes():
    """List the calling organisation's business processes, sorted by name then id."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.process_data import BusinessProcess

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        BusinessProcess.query.filter_by(organization_id=g.current_org_id)
        .order_by(BusinessProcess.name, BusinessProcess.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return success_response(
        {
            "business_processes": [
                {
                    "id": process.id,
                    "name": process.name,
                    "description": process.description or "",
                    "type": process.process_type or "",
                    "status": process.status or "",
                }
                for process in queried.items
            ],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@business_v1_bp.route("/business-processes/<int:process_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_business_process(process_id):
    """One business process by id, only when it belongs to the calling organisation."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.process_data import BusinessProcess

    process = BusinessProcess.query.filter_by(
        id=process_id, organization_id=g.current_org_id
    ).first()
    if process is None:
        return not_found_response("Business Process")
    return success_response(
        {
            "id": process.id,
            "name": process.name,
            "description": process.description or "",
            "type": process.process_type or "",
            "status": process.status or "",
        }
    )
