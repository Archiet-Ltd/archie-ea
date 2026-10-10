"""
Composable decorator stack for new modular architecture.

Provides high-level decorators that bundle validation, auth, observability,
and error handling into a single decorator call.  These decorators apply
ONLY to new modules — legacy routes are never touched.

All decorators are feature-flag compatible and reversible:
- ``USE_GUARDRAILS=false`` env var disables all guardrail decorators globally
- Individual decorators accept ``enabled=True`` kwarg

Usage::

    from app.core.decorators import guarded_route, validated_route, timed_route

    @bp.route("/api/items", methods=["POST"])
    @guarded_route(auth="login", validate=CreateItemSchema)
    def create_item():
        ...

    @bp.route("/api/items/<int:item_id>")
    @timed_route
    def get_item(item_id):
        ...
"""

import functools
import logging
import os
import time
from typing import Any, Callable, Optional, Type

from flask import request

logger = logging.getLogger(__name__)

_GUARDRAILS_ENABLED = os.environ.get("USE_GUARDRAILS", "true").lower() != "false"


def _guardrails_active() -> bool:
    """Check if guardrails are globally enabled (feature-flag compatible)."""
    return _GUARDRAILS_ENABLED


def timed_route(f: Callable) -> Callable:
    """Record request duration and attach to response headers.

    Lightweight decorator — no auth, no validation.  Just timing + metrics.
    """

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not _guardrails_active():
            return f(*args, **kwargs)

        start = time.monotonic()
        response = f(*args, **kwargs)
        duration_ms = round((time.monotonic() - start) * 1000, 2)

        try:
            from app.core.observability.metrics import metrics_collector

            metrics_collector.record(request.endpoint, _extract_status(response), duration_ms)
        except Exception:
            pass

        if isinstance(response, tuple) and len(response) >= 1:
            resp_obj = response[0]
            if hasattr(resp_obj, "headers"):
                resp_obj.headers["X-Response-Time-Ms"] = str(duration_ms)
        elif hasattr(response, "headers"):
            response.headers["X-Response-Time-Ms"] = str(duration_ms)

        return response

    return wrapper


def validated_route(
    schema_cls: Type,
    *,
    source: str = "json",
    enabled: bool = True,
) -> Callable:
    """Validate request payload against a Schema before entering the route.

    Args:
        schema_cls: A ``app.core.validation.schemas.Schema`` subclass.
        source: ``"json"`` (default) or ``"form"`` — where to read data.
        enabled: Set False to disable without removing decorator.

    On validation failure, returns ``api_error`` with 400 status.
    On success, attaches ``g.validated_data`` for the route to consume.
    """

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if not enabled or not _guardrails_active():
                return f(*args, **kwargs)

            from flask import g

            from app.core.api import api_error

            if source == "json":
                raw = request.get_json(silent=True) or {}
            else:
                raw = request.form.to_dict()

            clean, errors = schema_cls.validate(raw)
            if errors:
                return api_error("Validation failed", 400, errors=errors)

            g.validated_data = clean
            return f(*args, **kwargs)

        return wrapper

    return decorator


def guarded_route(
    *,
    auth: Optional[str] = None,
    roles: Optional[list] = None,
    permission: Optional[str] = None,
    feature_flag: Optional[str] = None,
    validate: Optional[Type] = None,
    rate_limit: bool = False,
    enabled: bool = True,
) -> Callable:
    """All-in-one guardrail decorator for new module endpoints.

    Applies in order:
    1. Feature flag gate (if ``feature_flag`` set)
    2. Authentication (if ``auth`` set — "login" or "admin")
    3. Role check (if ``roles`` set)
    4. Permission check (if ``permission`` set)
    5. Payload validation (if ``validate`` set)
    6. Timing + metrics

    Args:
        auth: ``"login"`` or ``"admin"`` — authentication level.
        roles: List of required role names (user needs at least one).
        permission: Required permission string.
        feature_flag: Feature flag key that must be enabled.
        validate: Schema class for request body validation.
        rate_limit: Enable rate limiting (placeholder for wiring).
        enabled: Global kill switch for this decorator.

    Usage::

        @bp.route("/api/items", methods=["POST"])
        @guarded_route(auth="login", validate=CreateItemSchema)
        def create_item():
            data = g.validated_data
            ...
    """

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if not enabled or not _guardrails_active():
                return f(*args, **kwargs)

            start = time.monotonic()

            from flask import g
            from flask_login import current_user

            from app.core.api import api_error

            if feature_flag:

                try:
                    from app.models.feature_flags import FeatureFlag

                    flag = FeatureFlag.query.filter_by(key=feature_flag).first()
                    if flag and not flag.is_active:
                        return api_error("Feature not available", 404)
                except Exception:
                    pass

            if auth:
                if not current_user.is_authenticated:
                    return api_error("Authentication required", 401)
                if auth == "admin":
                    # R3-1 (PR 428 round 4): ``getattr(current_user,
                    # "is_admin", False)`` with no call returns the bound
                    # method, always truthy -- no caller passes
                    # ``auth="admin"`` today, but the check itself admitted
                    # everyone. ``is_superuser`` does not exist on ``User``
                    # at all. Judged against the active organisation, the
                    # same predicate every other admin check in this PR uses.
                    from app.middleware.tenant_decorators import is_active_org_admin

                    if not is_active_org_admin(current_user):
                        return api_error("Admin access required", 403)

            if roles:
                if not current_user.is_authenticated:
                    return api_error("Authentication required", 401)
                user_roles = set()
                if hasattr(current_user, "roles"):
                    for r in current_user.roles:
                        name = getattr(r, "name", r) if not isinstance(r, str) else r
                        user_roles.add(str(name).lower())
                required = {r.lower() for r in roles}
                if not user_roles & required:
                    return api_error("Insufficient role", 403)

            if permission:
                if not current_user.is_authenticated:
                    return api_error("Authentication required", 401)
                if hasattr(current_user, "has_permission"):
                    if not current_user.has_permission(permission):
                        return api_error(f"Permission '{permission}' required", 403)

            if validate:
                raw = request.get_json(silent=True) or {}
                clean, errors = validate.validate(raw)
                if errors:
                    return api_error("Validation failed", 400, errors=errors)
                g.validated_data = clean

            request._guardrail_enabled = True  # type: ignore[attr-defined]

            result = f(*args, **kwargs)

            duration_ms = round((time.monotonic() - start) * 1000, 2)
            try:
                from app.core.observability.metrics import metrics_collector

                metrics_collector.record(request.endpoint, _extract_status(result), duration_ms)
            except Exception:
                pass

            return result

        return wrapper

    return decorator


def _extract_status(response: Any) -> int:
    """Extract HTTP status code from various response shapes."""
    if isinstance(response, tuple) and len(response) >= 2:
        return int(response[1])
    if hasattr(response, "status_code"):
        return response.status_code
    return 200
