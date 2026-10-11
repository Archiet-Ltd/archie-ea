"""Element list and detail: the first /api/v1 read family that accepts a
scoped ``api:read`` organisation token (or a signed-in session).

Every other /api/v1 route stays session-only: the bearer loader resolves a
token only for routes decorated with ``require_api_scope``, so a token never
grants anything on a route that does not opt in. The organisation always
comes from the token (the bearer loader enforces
``user.organization_id == token.organization_id`` and the tenant context
resolves to that organisation); both routes also filter by
``g.current_org_id`` explicitly rather than relying on row-level security
alone, so both are tenant-scoped reads.
"""

from __future__ import annotations

from flask import Blueprint, g

from app.modules.oauth_provider.scopes import API_READ_SCOPE, require_api_scope
from app.utils.api_response import not_found_response, success_response
from app.utils.pagination import get_pagination_params

elements_v1_bp = Blueprint("elements_v1", __name__)


@elements_v1_bp.route("", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_elements():
    """List the calling organisation's ArchiMate elements, sorted by name."""
    from app.models.archimate_core import ArchiMateElement

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        ArchiMateElement.query.filter_by(organization_id=g.current_org_id)
        .order_by(ArchiMateElement.name)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    elements = [
        {
            "id": element.id,
            "name": element.name,
            "type": element.type,
            "layer": element.layer,
            "description": element.description or "",
        }
        for element in queried.items
    ]
    return success_response(
        {
            "elements": elements,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@elements_v1_bp.route("/<int:element_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_element(element_id):
    """One element by id — only when it belongs to the calling organisation."""
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement.query.filter_by(
        id=element_id, organization_id=g.current_org_id
    ).first()
    if element is None:
        return not_found_response("Element")
    return success_response(
        {
            "id": element.id,
            "name": element.name,
            "type": element.type,
            "layer": element.layer,
            "description": element.description or "",
            "scope": element.scope or "",
            "building_block_type": element.building_block_type or "",
            "plateau": element.togaf_plateau or "",
        }
    )