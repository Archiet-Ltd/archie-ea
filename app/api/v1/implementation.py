"""Work package and deliverable reads over the public interface.

The four routes accept a signed-in session or a bearer token carrying
``api:read`` (``require_api_scope``); a token with ``api:write`` or any
``mcp:*`` scope is refused, and a token without an organisation is refused.
The organisation always comes from the token: the bearer loader binds
``g.current_org_id`` to the token's organisation, and every read filters by
it explicitly, so one organisation can never read another organisation's
rows.

WorkPackage is a tenant-owned row (TenantMixin). Deliverable carries no
organisation column of its own: it belongs to an organisation through its
work package, either the work_packages row (work_package_id) or the unified
work package (unified_work_package_id). A deliverable is visible to the caller
only when every parent it names belongs to the caller's organisation and it
names at least one; a deliverable with no parent is not served to anyone.

The response exposes only plain facts about the record: id, name,
description, dates, progress and state. Owners, costs, effort and notes are
not exposed.
"""

from __future__ import annotations

from flask import Blueprint, g

from app.modules.oauth_provider.scopes import API_READ_SCOPE, require_api_scope
from app.utils.api_response import not_found_response, success_response
from app.utils.pagination import get_pagination_params

implementation_v1_bp = Blueprint("implementation_v1", __name__)


def _iso(value):
    return value.isoformat() if value else None


def _work_package_payload(package):
    return {
        "id": package.id,
        "name": package.name,
        "description": package.description or "",
        "start_date": _iso(package.start_date),
        "end_date": _iso(package.target_date),
        "percent_complete": package.percent_complete,
        "parent_work_package_id": package.parent_id,
        "lifecycle_state": package.status or "",
    }


def _deliverable_payload(deliverable):
    return {
        "id": deliverable.id,
        "name": deliverable.name,
        "description": deliverable.description or "",
        "due_date": _iso(deliverable.target_date),
        "accepted": deliverable.approval_status == "approved",
        "lifecycle_state": deliverable.delivery_status or "",
    }


def _organisation_deliverables(org_id):
    """Deliverables whose every named parent belongs to *org_id*."""
    from sqlalchemy import or_

    from app import db
    from app.models.implementation_migration import Deliverable, WorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage

    own_packages = db.session.query(WorkPackage.id).filter(
        WorkPackage.organization_id == org_id
    )
    own_unified = db.session.query(UnifiedWorkPackage.id).filter(
        UnifiedWorkPackage.organization_id == org_id
    )
    return Deliverable.query.filter(
        or_(
            Deliverable.work_package_id.isnot(None),
            Deliverable.unified_work_package_id.isnot(None),
        ),
        or_(
            Deliverable.work_package_id.is_(None),
            Deliverable.work_package_id.in_(own_packages),
        ),
        or_(
            Deliverable.unified_work_package_id.is_(None),
            Deliverable.unified_work_package_id.in_(own_unified),
        ),
    ), Deliverable


def _page(queried, page, per_page, key, payload):
    return success_response(
        {
            key: [payload(row) for row in queried.items],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": queried.total,
                "pages": queried.pages,
            },
        }
    )


@implementation_v1_bp.route("/work-packages", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_work_packages():
    """List the calling organisation's work packages, sorted by name then id."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.implementation_migration import WorkPackage

    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    queried = (
        WorkPackage.query.filter_by(organization_id=g.current_org_id)
        .order_by(WorkPackage.name, WorkPackage.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return _page(queried, page, per_page, "work_packages", _work_package_payload)


@implementation_v1_bp.route("/work-packages/<int:package_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_work_package(package_id):
    """One work package by id, only when it belongs to the calling organisation."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    from app.models.implementation_migration import WorkPackage

    package = WorkPackage.query.filter_by(
        id=package_id, organization_id=g.current_org_id
    ).first()
    if package is None:
        return not_found_response("Work Package")
    return success_response(_work_package_payload(package))


@implementation_v1_bp.route("/deliverables", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def list_deliverables():
    """List the calling organisation's deliverables, sorted by name then id."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    page, per_page = get_pagination_params(default_per_page=50, max_per_page=200)
    query, Deliverable = _organisation_deliverables(g.current_org_id)
    queried = query.order_by(Deliverable.name, Deliverable.id).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return _page(queried, page, per_page, "deliverables", _deliverable_payload)


@implementation_v1_bp.route("/deliverables/<int:deliverable_id>", methods=["GET"])
@require_api_scope(API_READ_SCOPE)
def get_deliverable(deliverable_id):
    """One deliverable by id, only when it belongs to the calling organisation."""
    if g.current_org_id is None:
        return not_found_response("Organisation")
    query, Deliverable = _organisation_deliverables(g.current_org_id)
    deliverable = query.filter(Deliverable.id == deliverable_id).first()
    if deliverable is None:
        return not_found_response("Deliverable")
    return success_response(_deliverable_payload(deliverable))
