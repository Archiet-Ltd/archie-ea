"""Shared HTTP boundary for the versioned Transformation Room API.

This module owns protocol concerns only.  Domain mutation, persistence and
authorisation remain in the operation-specific Task 4--9 services.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from typing import Any, Mapping, Sequence

from flask import current_app, g, jsonify, request
from flask_login import current_user
from sqlalchemy import inspect as sqlalchemy_inspect, or_, select
from sqlalchemy.orm import Session
from werkzeug.exceptions import BadRequest

from app import db
from app.models.transformation_programme import ProgrammeRoleAssignment
from app.modules.transformation_room.domain import (
    ActorContext,
    AuthenticationRequired,
    BlockedByEvidence,
    CommandConflict,
    CommandResult,
    KnownPreCommitTransient,
    NotAuthorised,
    NotFound,
    StaleClaim,
    TransformationError,
)
from app.services.rate_limiter import RateLimitExceeded, _rate_limiter
from app.utils.role_access import get_user_role


logger = logging.getLogger(__name__)

SERVER_OWNED_FIELDS = frozenset(
    {
        "actor",
        "actor_id",
        "approval",
        "approved",
        "authorisation",
        "authorization",
        "command_key",
        "created_at",
        "created_by",
        "created_by_id",
        "decision_by_id",
        "idempotency_key",
        "idempotent",
        "lifecycle",
        "lifecycle_stage",
        "operation_result_id",
        "organization_id",
        "readiness",
        "ready",
        "recorded_by_id",
        "request_id",
        "revision",
        "roles",
        "status",
        "submitted_by_id",
        "tenant_id",
        "updated_at",
        "updated_by_id",
        "expected_revision",
        "expected_head_revision",
    }
)


class RequestValidationError(ValueError):
    """A request-protocol error with an optional field pointer."""

    def __init__(self, message: str, *, field: str | None = None, **details: Any):
        self.field = field
        self.details = details
        super().__init__(message)


def request_id() -> str:
    """Return a bounded, log-safe caller correlation ID or a fresh UUID."""
    cached = getattr(g, "transformation_request_id", None)
    if cached:
        return cached
    supplied = (request.headers.get("X-Request-ID") or "").strip()
    if supplied and len(supplied) <= 128 and all(character.isprintable() for character in supplied):
        value = supplied
    else:
        value = str(uuid.uuid4())
    g.transformation_request_id = value
    return value


def resolve_enterprise_roles(
    user,
    organization_id: int,
    *,
    programme_id: int | None = None,
    workstream_id: int | None = None,
) -> frozenset[str]:
    """Resolve server-owned enterprise and effective assignment roles."""
    roles = {get_user_role(user)}
    if getattr(user, "is_org_admin", False):
        roles.add("organization_admin")
    if getattr(user, "is_platform_admin", False):
        roles.add("platform_admin")

    if programme_id is not None:
        today = date.today()
        statement = select(ProgrammeRoleAssignment.role).where(
            ProgrammeRoleAssignment.organization_id == organization_id,
            ProgrammeRoleAssignment.programme_id == programme_id,
            ProgrammeRoleAssignment.user_id == user.id,
            ProgrammeRoleAssignment.effective_from <= today,
            or_(
                ProgrammeRoleAssignment.effective_to.is_(None),
                ProgrammeRoleAssignment.effective_to >= today,
            ),
        )
        if workstream_id is not None:
            statement = statement.where(
                or_(
                    ProgrammeRoleAssignment.workstream_id.is_(None),
                    ProgrammeRoleAssignment.workstream_id == workstream_id,
                )
            )
        else:
            statement = statement.where(ProgrammeRoleAssignment.workstream_id.is_(None))
        with Session(db.engine) as session:
            roles.update(session.scalars(statement).all())
    return frozenset(role for role in roles if role)


def actor_context(
    *, programme_id: int | None = None, workstream_id: int | None = None
) -> ActorContext:
    """Build immutable actor context exclusively from authenticated state."""
    if not current_user.is_authenticated:
        raise AuthenticationRequired()
    organization_id = getattr(g, "current_org_id", None)
    if not isinstance(organization_id, int) or organization_id <= 0:
        raise AuthenticationRequired("active_organization_required")
    return ActorContext(
        user_id=current_user.id,
        organization_id=organization_id,
        roles=resolve_enterprise_roles(
            current_user,
            organization_id,
            programme_id=programme_id,
            workstream_id=workstream_id,
        ),
        request_id=request_id(),
    )


def _serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f") if value.is_finite() else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return _serialize(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_serialize(item) for item in value]
    try:
        mapper = sqlalchemy_inspect(type(value))
        return {
            column.key: _serialize(getattr(value, column.key))
            for column in mapper.columns
            if column.key != "organization_id"
        }
    except Exception as error:
        raise TypeError(f"unsupported response value: {type(value).__name__}") from error


def api_success(
    data: Any,
    *,
    status: int,
    request_id_value: str,
    meta: Mapping[str, Any] | None = None,
):
    response = jsonify(
        {
            "data": _serialize(data),
            "meta": _serialize(dict(meta or {})),
            "errors": [],
            "request_id": request_id_value,
        }
    )
    response.status_code = status
    return response


def api_error(
    code: str,
    message: str,
    *,
    status: int,
    request_id_value: str | None = None,
    field: str | None = None,
    details: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
):
    error: dict[str, Any] = {"code": code, "message": message}
    if field is not None:
        error["field"] = field
    if details:
        error["details"] = _serialize(details)
    response = jsonify(
        {
            "data": None,
            "meta": _serialize(dict(meta or {})),
            "errors": [error],
            "request_id": request_id_value or request_id(),
        }
    )
    response.status_code = status
    return response


def command_success(
    result: CommandResult,
    *,
    request_id_value: str,
    created_status: int = 200,
):
    status = 200 if result.idempotent else created_status
    return api_success(
        result.response,
        status=status,
        request_id_value=request_id_value,
        meta={
            "created": result.created,
            "idempotent": result.idempotent,
            "operation_result_id": result.operation_result_id,
        },
    )


def json_object(
    *, authority_paths: Sequence[Sequence[str]] = ((),)
) -> dict[str, Any]:
    if not request.is_json:
        raise RequestValidationError(
            "A JSON object is required.", field="Content-Type"
        )
    try:
        payload = request.get_json(silent=False)
    except BadRequest as error:
        raise RequestValidationError("The JSON body is malformed.") from error
    if not isinstance(payload, dict):
        raise RequestValidationError("A JSON object is required.")
    reject_server_owned_fields(payload, authority_paths=authority_paths)
    return payload


def reject_server_owned_fields(
    payload: Mapping[str, Any],
    *,
    authority_paths: Sequence[Sequence[str]] = ((),),
) -> None:
    """Reject authority/projection fields only in API-owned schema objects.

    Nested domain and provider documents are deliberately opaque to this
    boundary. Operation services validate the schemas they own; callers can
    name additional API-owned object paths explicitly when a route has more
    than one authority-bearing command object.
    """
    found: set[str] = set()
    for raw_path in authority_paths:
        value: Any = payload
        for segment in raw_path:
            if not isinstance(value, Mapping) or segment not in value:
                value = None
                break
            value = value[segment]
        if not isinstance(value, Mapping):
            continue
        for raw_key in value:
            key = str(raw_key).strip().lower()
            if key in SERVER_OWNED_FIELDS:
                found.add(key)
    if found:
        raise RequestValidationError(
            "Server-owned fields are not accepted.",
            fields=sorted(found),
        )


def idempotency_key() -> str:
    value = (request.headers.get("Idempotency-Key") or "").strip()
    if not value:
        raise RequestValidationError(
            "Idempotency-Key header is required.", field="Idempotency-Key"
        )
    if len(value) > 255:
        raise RequestValidationError(
            "Idempotency-Key must be at most 255 characters.",
            field="Idempotency-Key",
        )
    return value


def if_match(*, allow_zero: bool = False) -> int:
    supplied = (request.headers.get("If-Match") or "").strip()
    if not supplied:
        raise RequestValidationError("If-Match header is required.", field="If-Match")
    value = supplied
    if value.startswith("W/"):
        value = value[2:].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    try:
        revision = int(value)
    except (TypeError, ValueError) as error:
        raise RequestValidationError(
            "If-Match must contain an integer revision.", field="If-Match"
        ) from error
    minimum = 0 if allow_zero else 1
    if revision < minimum:
        raise RequestValidationError(
            f"If-Match revision must be at least {minimum}.", field="If-Match"
        )
    return revision


def iso_datetime(value: Any, field: str, *, required: bool = True) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError(f"{field} is required.", field=field)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise RequestValidationError(
            f"{field} must be an ISO-8601 timestamp.", field=field
        ) from error
    if parsed.tzinfo is None:
        raise RequestValidationError(
            f"{field} must include a timezone.", field=field
        )
    return parsed


def ensure_workstream_scope(
    actor: ActorContext, programme_id: int, workstream_id: int
) -> None:
    """Prove the nested URL relationship through the public read service."""
    from app.modules.transformation_room.programme_service import (
        TransformationProgrammeService,
    )

    programme = TransformationProgrammeService.get_programme(
        actor=actor, programme_id=programme_id
    )
    if workstream_id not in programme.workstream_ids:
        raise NotFound("workstream_not_found")


def enforce_foreign_probe_limit() -> None:
    """Charge one opaque denied identifier resolution to the shared bucket."""
    if not request.view_args or not current_app.config.get("RATE_LIMITING_ENABLED", True):
        return
    if not any(name.endswith("_id") for name in request.view_args):
        return
    organization_id = getattr(g, "current_org_id", "none")
    user_id = current_user.get_id() if current_user.is_authenticated else "anonymous"
    limit = int(current_app.config.get("TRANSFORMATION_FOREIGN_ID_PROBE_LIMIT", 60))
    allowed, retry_after = _rate_limiter.check_rate_limit(
        f"transformation-id-probe:{organization_id}:{user_id}", limit, 60
    )
    if not allowed:
        raise RateLimitExceeded(limit, "1m", retry_after)


def _rate_limit_response(error: RateLimitExceeded):
    _audit_security_denial("identifier_probe_rate_limited", alert=True)
    response = api_error(
        "retryable_failure",
        "Too many identifier-bearing requests; retry later.",
        status=429,
        meta={"retry_after": error.retry_after},
    )
    if error.retry_after:
        response.headers["Retry-After"] = str(error.retry_after)
    return response


def _denial_rate_limit_response():
    try:
        enforce_foreign_probe_limit()
    except RateLimitExceeded as error:
        return _rate_limit_response(error)
    return None


def _audit_security_denial(
    reason_code: str, *, alert: bool = False
) -> None:
    """Persist one opaque denial event without changing the HTTP outcome."""
    details = {
        "actor_id": getattr(current_user, "id", None),
        "endpoint": request.endpoint,
        "reason_code": str(reason_code)[:100],
        "request_id": request_id(),
        "tenant_id": getattr(g, "current_org_id", None),
    }
    logger.warning(
        "transformation_api_security_denial endpoint=%s organization=%s actor=%s reason=%s request_id=%s",
        details["endpoint"],
        details["tenant_id"],
        details["actor_id"],
        details["reason_code"],
        details["request_id"],
    )
    try:
        from app.security.audit import AuditEventSeverity, audit_logger

        audit_logger.log_security_event(
            "transformation_api_probe_rate_limited"
            if alert
            else "transformation_api_denial",
            AuditEventSeverity.HIGH if alert else AuditEventSeverity.MEDIUM,
            details,
        )
    except Exception:
        # Audit persistence is deliberately fail-open for the response; the
        # structured application log above remains available to operations.
        logger.exception("transformation API security audit persistence failed")


def api_endpoint(view):
    """Map a thin adapter's protocol/domain exceptions into one envelope."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            if not current_user.is_authenticated:
                raise AuthenticationRequired()
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                # Header validity precedes identifier resolution uniformly for
                # every mutation.  This both pins the public contract and
                # avoids route-by-route precedence drift.
                idempotency_key()
            return view(*args, **kwargs)
        except AuthenticationRequired as error:
            _audit_security_denial(error.reason)
            return api_error(
                "not_authenticated",
                "Authentication is required.",
                status=401,
            )
        except NotFound as error:
            limited = _denial_rate_limit_response()
            if limited is not None:
                return limited
            _audit_security_denial(error.reason)
            return api_error(
                "not_found", "The requested resource was not found.", status=404
            )
        except NotAuthorised as error:
            limited = _denial_rate_limit_response()
            if limited is not None:
                return limited
            _audit_security_denial(error.reason)
            return api_error(
                "not_authorised",
                "You are not authorised to perform this operation.",
                status=403,
            )
        except BlockedByEvidence as error:
            return api_error(
                "blocked_by_evidence",
                "The operation is blocked by governed evidence requirements.",
                status=422,
                details=error.details,
            )
        except (StaleClaim, CommandConflict):
            return api_error(
                "conflict",
                "The resource changed or the command conflicts with current state.",
                status=409,
            )
        except KnownPreCommitTransient:
            return api_error(
                "retryable_failure",
                "The operation could not be completed safely; retry later.",
                status=503,
            )
        except RequestValidationError as error:
            return api_error(
                "validation_failed",
                str(error),
                status=400,
                field=error.field,
                details=error.details,
            )
        except (TypeError, ValueError) as error:
            return api_error("validation_failed", str(error), status=400)
        except RateLimitExceeded as error:
            return _rate_limit_response(error)
        except TransformationError:
            logger.exception("unmapped transformation domain error")
            return api_error(
                "retryable_failure",
                "The operation could not be completed safely; retry later.",
                status=503,
            )
        except Exception:
            logger.exception("unexpected transformation API failure")
            return api_error(
                "retryable_failure",
                "The operation could not be completed safely; retry later.",
                status=503,
            )

    return wrapped


__all__ = [
    "RequestValidationError",
    "actor_context",
    "api_endpoint",
    "api_error",
    "api_success",
    "command_success",
    "enforce_foreign_probe_limit",
    "idempotency_key",
    "if_match",
    "iso_datetime",
    "json_object",
    "request_id",
    "resolve_enterprise_roles",
]
