"""Application reads over the public interface (application components).

Two GET routes under /api/v1 accept a signed-in session or a bearer token
carrying ``api:read`` (``require_api_scope``); a token with ``api:write`` or
any ``mcp:*`` scope is refused, and a token without an organisation is
refused. The organisation always comes from the token: the bearer loader
binds ``g.current_org_id`` to the token's organisation, and both reads filter
by it explicitly, so one organisation can never read another's applications.

The older session-only ``/api/v1/applications`` blueprint is left as it is:
it carries the writes and the screens' own shapes. These routes sit beside it
on a different path and read the same ``ApplicationComponent`` store, so there
is no second application store and no route-rule collision.
"""

from __future__ import annotations

from flask import Blueprint, g

from app.modules.oauth_provider.scopes import API_READ_SCOPE, require_api_scope
from app.utils.api_response import not_found_response, success_response
from app.utils.pagination import get_pagination_params

application_components_v1_bp = Blueprint("application_components_v1", __name__)


def _summary(application):
    return {
        "id": application.id,
        "name": application.name,
        "code": application.application_code or "",
        "description": application.description or "",
        "lifecycle_status": application.lifecycle_status or "",
        "criticality": application.criticality or "",
        "application_type": application.application_type or "",
        "deployment_model": application.deployment_model or "",
    }


@application_components_v1_bp.route("", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_application_components():
    """List the calling organisation's applications, sorted by name then id."""
    from app.models.application_portfolio import ApplicationComponent

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        ApplicationComponent.query.filter_by(organization_id=g.current_org_id)
        .order_by(ApplicationComponent.name, ApplicationComponent.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return success_response(
        {
            "application_components": [_summary(item) for item in queried.items],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@application_components_v1_bp.route("/<int:application_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_application_component(application_id):
    """One application by id, only when it belongs to the calling organisation."""
    from app.models.application_portfolio import ApplicationComponent

    application = ApplicationComponent.query.filter_by(
        id=application_id, organization_id=g.current_org_id
    ).first()
    if application is None:
        return not_found_response("Application Component")
    detail = _summary(application)
    detail.update(
        {
            "application_category": application.application_category or "",
            "business_domain": application.business_domain or "",
            "business_purpose": application.business_purpose or "",
            "vendor_name": application.vendor_name or "",
        }
    )
    return success_response(detail)
