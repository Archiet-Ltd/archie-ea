"""HTTP adapter for objectives (goal elements) and their key results.

Thin by design: every view parses protocol values, builds the server-owned
actor and calls ``ObjectiveService``. Measurements are recorded through the
Transformation Room's existing outcome measurement service.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint

from app.modules.transformation_room.http import (
    RequestValidationError,
    actor_context,
    api_endpoint,
    api_success,
    command_success,
    idempotency_key,
    iso_datetime,
    json_object,
)
from app.modules.transformation_room.objective_service import ObjectiveService
from app.modules.transformation_room.api import _integer, _iso_date, _required

objectives_api_bp = Blueprint(
    "objectives_api", __name__, url_prefix="/api/v1/objectives"
)


def _ok(data, actor, *, status=200):
    return api_success(data, status=status, request_id_value=actor.request_id)


@objectives_api_bp.route("", methods=["GET"])
@api_endpoint
def list_objectives():
    actor = actor_context()
    return _ok(ObjectiveService.list_objectives(actor), actor)


@objectives_api_bp.route("", methods=["POST"])
@api_endpoint
def set_objective():
    actor = actor_context()
    payload = json_object()
    result = ObjectiveService.set_objective(
        actor=actor,
        goal_element_id=_integer(payload.get("goal_element_id"), "goal_element_id", optional=True),
        name=payload.get("name"),
        description=payload.get("description"),
    )
    return _ok(result, actor, status=201)


@objectives_api_bp.route("/<int:goal_element_id>", methods=["GET"])
@api_endpoint
def get_objective(goal_element_id):
    actor = actor_context()
    return _ok(ObjectiveService.get_objective(actor, goal_element_id), actor)


@objectives_api_bp.route("/<int:goal_element_id>/key-results", methods=["POST"])
@api_endpoint
def add_key_result(goal_element_id):
    actor = actor_context()
    payload = json_object()
    result = ObjectiveService.add_key_result(
        actor=actor,
        goal_element_id=goal_element_id,
        metric_name=_required(payload, "metric_name"),
        unit=_required(payload, "unit"),
        baseline_value=_required(payload, "baseline_value"),
        target_value=_required(payload, "target_value"),
        aggregation=payload.get("aggregation", "latest"),
        owner_id=_integer(payload.get("owner_id"), "owner_id", optional=True),
        target_date=_iso_date(payload.get("target_date"), "target_date", optional=True),
    )
    return _ok(result, actor, status=201)


@objectives_api_bp.route("/key-results/<int:key_result_id>", methods=["GET"])
@api_endpoint
def get_key_result(key_result_id):
    actor = actor_context()
    return _ok(ObjectiveService.get_key_result(actor, key_result_id), actor)


@objectives_api_bp.route(
    "/key-results/<int:key_result_id>/measurements", methods=["POST"]
)
@api_endpoint
def record_key_result_measurement(key_result_id):
    actor = actor_context()
    command_key = idempotency_key()
    payload = json_object()
    raw_value = payload.get("value")
    try:
        value = Decimal(str(raw_value)) if raw_value is not None else None
    except (InvalidOperation, ValueError) as error:
        raise RequestValidationError("value must be numeric.", field="value") from error
    result = ObjectiveService.record_measurement(
        actor=actor,
        key_result_id=key_result_id,
        value=value,
        unavailable_reason=payload.get("unavailable_reason"),
        observed_at=iso_datetime(_required(payload, "observed_at"), "observed_at"),
        source_identity=_required(payload, "source_identity"),
        source_version=_required(payload, "source_version"),
        command_key=command_key,
    )
    return command_success(result, request_id_value=actor.request_id, created_status=201)


__all__ = ["objectives_api_bp"]
