"""Strategy reads: capabilities and value streams for the calling
organisation.

The four routes accept a signed-in session or a bearer token carrying
``api:read`` (``require_api_scope``); a token with ``api:write`` or any
``mcp:*`` scope is refused, and a token without an organisation is refused.
The organisation always comes from the token: the bearer loader binds
``g.current_org_id`` to the token's organisation, and every read filters by
it explicitly, so one organisation can never read another organisation's
rows.

Capability detail goes through ``UnifiedCapability.visible_to_organization``
(the one visibility lookup for a capability) so a shared reference row is
visible exactly as the model defines it, and nothing else is. Value streams
are tenant-owned rows, looked up by id and the caller's organisation.
"""

from __future__ import annotations

from flask import Blueprint, g

from app.modules.oauth_provider.scopes import API_READ_SCOPE, require_api_scope
from app.utils.api_response import not_found_response, success_response
from app.utils.pagination import get_pagination_params

strategy_v1_bp = Blueprint("strategy_v1", __name__)


@strategy_v1_bp.route("/capabilities", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_capabilities():
    """List the calling organisation's capabilities, sorted by name then id."""
    from app.models.unified_capability import UnifiedCapability

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        UnifiedCapability.query.filter_by(organization_id=g.current_org_id)
        .order_by(UnifiedCapability.name, UnifiedCapability.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return success_response(
        {
            "capabilities": [
                {
                    "id": capability.id,
                    "name": capability.name,
                    "code": capability.code or "",
                    "description": capability.description or "",
                    "level": capability.level,
                    "status": capability.status,
                }
                for capability in queried.items
            ],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@strategy_v1_bp.route("/capabilities/<int:capability_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_capability(capability_id):
    """One capability by id, only when the calling organisation can see it."""
    from app.models.unified_capability import UnifiedCapability

    capability = UnifiedCapability.visible_to_organization(
        capability_id, g.current_org_id
    )
    if capability is None:
        return not_found_response("Capability")
    return success_response(
        {
            "id": capability.id,
            "name": capability.name,
            "code": capability.code or "",
            "description": capability.description or "",
            "level": capability.level,
            "status": capability.status,
            "scope": capability.scope or "",
        }
    )


@strategy_v1_bp.route("/value-streams", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_value_streams():
    """List the calling organisation's value streams, sorted by name then id."""
    from app.models.unified_capability import ValueStream

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        ValueStream.query.filter_by(organization_id=g.current_org_id)
        .order_by(ValueStream.name, ValueStream.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return success_response(
        {
            "value_streams": [
                {
                    "id": value_stream.id,
                    "name": value_stream.name,
                    "code": value_stream.code or "",
                    "description": value_stream.description or "",
                    "value_stream_type": value_stream.value_stream_type or "",
                    "industry_domain": value_stream.industry_domain or "",
                }
                for value_stream in queried.items
            ],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@strategy_v1_bp.route("/value-streams/<int:value_stream_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_value_stream(value_stream_id):
    """One value stream by id, only when it belongs to the calling organisation."""
    from app.models.unified_capability import ValueStream

    value_stream = ValueStream.query.filter_by(
        id=value_stream_id, organization_id=g.current_org_id
    ).first()
    if value_stream is None:
        return not_found_response("Value Stream")
    return success_response(
        {
            "id": value_stream.id,
            "name": value_stream.name,
            "code": value_stream.code or "",
            "description": value_stream.description or "",
            "value_stream_type": value_stream.value_stream_type or "",
            "industry_domain": value_stream.industry_domain or "",
            "strategic_importance": value_stream.strategic_importance or "",
            "business_owner": value_stream.business_owner or "",
        }
    )
