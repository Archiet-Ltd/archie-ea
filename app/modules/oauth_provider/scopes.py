"""Scope namespaces and the per-route api-scope gate for /api/v1.

A token carries one namespace. ``mcp:*`` scopes open the connector; ``api:*``
scopes open the REST API. The bearer loader (``app.modules.oauth_provider.
identity``) enforces the namespace separation at the path level; this module
owns the constants and the one ``require_api_scope`` decorator REST routes use
to accept either a signed-in session or a token carrying the required scope.

``require_api_scope`` is the only scope decorator for REST routes: MCP tool
scopes stay on the tools themselves (``required_scope`` in the MCP
blueprint), and the consent flow stays exactly as it is.

Every bearer call that passes the gate is recorded once in
``api_operation_calls`` (organisation, client, operation, last called at,
call count) with a single upsert, so the deprecation admin page can name the
consumers of an interface version before it retires.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import g, request
from flask_login import current_user

from app.extensions import db
from app.utils.api_response import forbidden_response, unauthorized_response

API_SCOPE_PREFIX = "api:"
MCP_SCOPE_PREFIX = "mcp:"

API_READ_SCOPE = "api:read"
MCP_READ_SCOPE = "mcp:read"

# The only scopes a client-credentials grant may ever issue.
API_SCOPES = (API_READ_SCOPE,)

_CONTRACT_SUBSET = Path(__file__).resolve().parents[3] / "app" / "config" / "api_contract_subset.yaml"
_operation_index: dict | None = None


def _normalize_path(path: str) -> str:
    """Strip the /api/v1 prefix and replace path parameters with {}."""
    if path == "/api/v1" or path.startswith("/api/v1/"):
        path = path[len("/api/v1"):] or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return re.sub(r"\{[^}]+\}", "{}", path)


def _load_operation_index() -> dict:
    """Build (normalized_path, method) -> operationId from the contract subset.

    Cached after the first build so a request pays no YAML parse cost.
    """
    global _operation_index
    if _operation_index is not None:
        return _operation_index
    import yaml

    with open(_CONTRACT_SUBSET, encoding="utf-8") as f:
        subset = yaml.safe_load(f)
    index: dict = {}
    for path_key, path_item in (subset or {}).get("paths", {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is not None:
                norm = _normalize_path("/api/v1" + path_key)
                index[(norm, method.lower())] = operation.get("operationId")
    _operation_index = index
    return _operation_index


def _record_api_call(token, operation_id: str | None) -> None:
    """Upsert one row into ``api_operation_calls`` for this token's call.

    One statement, no SELECT: ``INSERT ... ON CONFLICT`` updates
    ``last_called_at`` and increments ``call_count``. The ORM tenant listener
    would stamp organization_id on a model insert, but raw SQL bypasses it, so
    the column is bound explicitly from the token's organisation.
    """
    if operation_id is None:
        return
    from app.modules.oauth_provider.models import ApiOperationCall

    now = datetime.now(timezone.utc)
    db.session.execute(
        db.text(
            f"INSERT INTO {ApiOperationCall.__tablename__} "
            "(organization_id, client_id, operation_id, last_called_at, call_count) "
            "VALUES (:org_id, :client_id, :operation_id, :last_called_at, 1) "
            "ON CONFLICT (organization_id, client_id, operation_id) "
            "DO UPDATE SET "
            "last_called_at = EXCLUDED.last_called_at, "
            "call_count = api_operation_calls.call_count + 1"
        ),
        {
            "org_id": token.organization_id,
            "client_id": token.client_id,
            "operation_id": operation_id,
            "last_called_at": now,
        },
    )


def carries_family_scope(scope: str | None, family_prefix: str) -> bool:
    """True when any of the token's scopes belongs to the given namespace.

    ``family_prefix`` is an API_SCOPE_PREFIX / MCP_SCOPE_PREFIX constant.
    An empty or None scope carries nothing.
    """
    return any(item.startswith(family_prefix) for item in (scope or "").split() if item)


def require_api_scope(required_scope: str):
    """Gate a REST route behind a session or a token holding *required_scope*.

    A signed-in session user passes; an anonymous request is refused with 401;
    a bearer request passes only when the token carries *required_scope* in
    the api namespace, has an organisation, and that organisation is the one
    the tenant context resolved (the organisation always comes from the
    token, never from the user or the request).

    A passing bearer call is recorded once in ``api_operation_calls`` with a
    single upsert before the view runs.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            token = getattr(g, "bearer_token", None)
            if getattr(g, "auth_mode", None) == "bearer" and token is not None:
                granted = set((token.scope or "").split())
                if required_scope not in granted:
                    return forbidden_response("This scope is not granted to this token")
                if token.organization_id is None:
                    return unauthorized_response("This token has no organisation")
                if getattr(g, "current_org_id", None) != token.organization_id:
                    return forbidden_response("This token belongs to another organisation")
                operation_id = _load_operation_index().get(
                    (_normalize_path(request.path), request.method.lower())
                )
                _record_api_call(token, operation_id)
                return view(*args, **kwargs)
            if current_user.is_authenticated:
                return view(*args, **kwargs)
            return unauthorized_response()

        wrapped._api_token_scope = required_scope
        return wrapped

    return decorator
