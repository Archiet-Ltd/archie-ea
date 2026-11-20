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


def _owners_for_element(element, organization_id):
    """Owner display rows for *element*, resolved through the one ownership
    record (``ApplicationOwner``) via whichever of its three keyed
    categories this element belongs to (ADR 0008: one system of record per
    concept — this does not add a second owner lookup, it calls the
    existing one for each category). Returns ``[]`` rather than guessing
    when none apply.
    """
    from app.models.application_owner import ApplicationOwner

    application_id = getattr(element, "application_component_id", None)
    if application_id is not None:
        return ApplicationOwner.get_display_rows_for_application(application_id, organization_id)

    from app.modules.enterprise_genome.services.ai_system_profile import is_ai_system

    if is_ai_system(element):
        return ApplicationOwner.get_display_rows_for_element("ai_system", element.id, organization_id)

    from app.models.unified_capability import UnifiedCapability

    capability = UnifiedCapability.query.filter_by(
        archimate_element_id=element.id, organization_id=organization_id
    ).first()
    if capability is not None:
        return ApplicationOwner.get_display_rows_for_element("capability", capability.id, organization_id)

    return []


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
    # No per-property "shareable" flag exists on custom_properties today
    # (it is a plain key/value map — see CMP-043 in archimate_routes.py), so
    # there is nothing to pass through yet; add it here once the model
    # records one rather than inventing a value.
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
            "properties": element.custom_properties or {},
            "source_kind": getattr(element, "source_kind", None),
            "source_ref": getattr(element, "source_ref", None),
            "status": getattr(element, "status", None),
            "owners": _owners_for_element(element, g.current_org_id),
        }
    )