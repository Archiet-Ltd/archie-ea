"""Scenario branches: named, what-if changes laid over the model.

  GET    /api/v1/intelligence/scenarios
  POST   /api/v1/intelligence/scenarios
  GET    /api/v1/intelligence/scenarios/<id>
  PATCH  /api/v1/intelligence/scenarios/<id>
  DELETE /api/v1/intelligence/scenarios/<id>
  POST   /api/v1/intelligence/scenarios/<id>/changes
  DELETE /api/v1/intelligence/scenarios/<id>/changes/<change_id>

Reads need a signed-in user; every write needs the general permission, so a
Viewer is refused. A branch never alters the model: the impact answer lays the
branch over it (``?scenario_id=`` on the impact route).
"""

from __future__ import annotations

from flask import request
from flask_login import current_user, login_required

from app._decorators_base import permission_required
from app.extensions import db
from app.models import Permission
from app.modules.intelligence.routes.api import intelligence_api
from app.modules.intelligence.services import scenario_service as svc
from app.utils.api_response import error_response, success_response
from app.utils.tenant import current_organization_id

_NO_ORG = "no tenant context for this request"


def _org_or_error():
    org_id = current_organization_id()
    if org_id is None:
        return None, error_response(_NO_ORG, code="NO_TENANT_CONTEXT", status_code=400)
    return org_id, None


def _refusal(exc: svc.ScenarioError):
    db.session.rollback()
    code = "NOT_FOUND" if exc.status_code == 404 else "INVALID_PARAMETER"
    return error_response(exc.message, code=code, status_code=exc.status_code)


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@intelligence_api.route("/scenarios", methods=["GET"])
@login_required
def scenarios_list():
    org_id, err = _org_or_error()
    if err is not None:
        return err
    return success_response({"scenarios": [b.to_dict() for b in svc.list_branches(org_id)]})


@intelligence_api.route("/scenarios", methods=["POST"])
@login_required
@permission_required(Permission.GENERAL)
def scenarios_create():
    org_id, err = _org_or_error()
    if err is not None:
        return err
    body = _body()
    try:
        branch = svc.create_branch(org_id, body.get("name"), body.get("description"), current_user.id)
        db.session.commit()
    except svc.ScenarioError as exc:
        return _refusal(exc)
    return success_response(branch.to_dict(with_changes=True), status_code=201)


@intelligence_api.route("/scenarios/<int:branch_id>", methods=["GET"])
@login_required
def scenarios_get(branch_id: int):
    org_id, err = _org_or_error()
    if err is not None:
        return err
    try:
        branch = svc.get_branch(org_id, branch_id)
    except svc.ScenarioError as exc:
        return _refusal(exc)
    return success_response(branch.to_dict(with_changes=True))


@intelligence_api.route("/scenarios/<int:branch_id>", methods=["PATCH"])
@login_required
@permission_required(Permission.GENERAL)
def scenarios_rename(branch_id: int):
    org_id, err = _org_or_error()
    if err is not None:
        return err
    body = _body()
    try:
        branch = svc.rename_branch(org_id, branch_id, body.get("name"), body.get("description"))
        db.session.commit()
    except svc.ScenarioError as exc:
        return _refusal(exc)
    return success_response(branch.to_dict(with_changes=True))


@intelligence_api.route("/scenarios/<int:branch_id>", methods=["DELETE"])
@login_required
@permission_required(Permission.GENERAL)
def scenarios_delete(branch_id: int):
    org_id, err = _org_or_error()
    if err is not None:
        return err
    try:
        svc.delete_branch(org_id, branch_id)
        db.session.commit()
    except svc.ScenarioError as exc:
        return _refusal(exc)
    return success_response({"deleted": True})


@intelligence_api.route("/scenarios/<int:branch_id>/changes", methods=["POST"])
@login_required
@permission_required(Permission.GENERAL)
def scenarios_add_change(branch_id: int):
    org_id, err = _org_or_error()
    if err is not None:
        return err
    body = _body()
    try:
        change = svc.add_change(
            org_id,
            branch_id,
            body.get("change_kind"),
            body.get("target_kind"),
            body.get("target_id"),
            body.get("payload"),
            current_user.id,
        )
        db.session.commit()
    except svc.ScenarioError as exc:
        return _refusal(exc)
    return success_response(change.to_dict(), status_code=201)


@intelligence_api.route("/scenarios/<int:branch_id>/changes/<int:change_id>", methods=["DELETE"])
@login_required
@permission_required(Permission.GENERAL)
def scenarios_remove_change(branch_id: int, change_id: int):
    org_id, err = _org_or_error()
    if err is not None:
        return err
    try:
        svc.remove_change(org_id, branch_id, change_id)
        db.session.commit()
    except svc.ScenarioError as exc:
        return _refusal(exc)
    return success_response({"deleted": True})
