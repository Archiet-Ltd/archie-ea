"""Risk reads over the public interface.

The two routes accept a signed-in session or a bearer token carrying
``api:read`` (``require_api_scope``); a token with ``api:write`` or any
``mcp:*`` scope is refused, and a token without an organisation is refused.
The organisation always comes from the token: the bearer loader binds
``g.current_org_id`` to the token's organisation, and every read filters by
it explicitly, so one organisation can never read another organisation's
rows.

Risk is a tenant-owned row (TenantMixin), looked up by id and the caller's
organisation. The response exposes only plain facts about the record: id,
name, description, likelihood, impact, the stored inherent and residual
pairs with the scores they give, and the lifecycle state. The owner text and
the mitigation plan are free text and are not exposed.

The control reads (listControls, getControl) are not served yet: the only
tenant-owned control table is an application-to-control mapping, which is a
different concept from the contract's governance control.
"""

from __future__ import annotations

from flask import Blueprint, g

from app.modules.oauth_provider.scopes import API_READ_SCOPE, require_api_scope
from app.utils.api_response import not_found_response, success_response
from app.utils.pagination import get_pagination_params

risks_v1_bp = Blueprint("risks_v1", __name__)


def _score(likelihood, impact):
    """A score only when both stored parts exist; never invented."""
    if likelihood is None or impact is None:
        return None
    return likelihood * impact


def _risk_payload(risk):
    return {
        "id": risk.id,
        "name": risk.title,
        "description": risk.description or "",
        "likelihood": risk.likelihood,
        "impact": risk.impact,
        "inherent_score": _score(risk.inherent_likelihood, risk.inherent_impact),
        "residual_score": _score(risk.residual_likelihood, risk.residual_impact),
        "lifecycle_state": risk.status.value if risk.status is not None else "",
    }


@risks_v1_bp.route("/risks", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_risks():
    """List the calling organisation's risks, sorted by name then id."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.risk import Risk

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        Risk.query.filter_by(organization_id=g.current_org_id)
        .order_by(Risk.title, Risk.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return success_response(
        {
            "risks": [_risk_payload(risk) for risk in queried.items],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@risks_v1_bp.route("/risks/<int:risk_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_risk(risk_id):
    """One risk by id, only when it belongs to the calling organisation."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.risk import Risk

    risk = Risk.query.filter_by(id=risk_id, organization_id=g.current_org_id).first()
    if risk is None:
        return not_found_response("Risk")
    return success_response(_risk_payload(risk))
