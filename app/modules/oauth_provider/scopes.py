"""Scope namespaces and the per-route api-scope gate for /api/v1.

A token carries one namespace. ``mcp:*`` scopes open the connector; ``api:*``
scopes open the REST API. The bearer loader (``app.modules.oauth_provider.
identity``) enforces the namespace separation at the path level; this module
owns the constants and the one ``require_api_scope`` decorator REST routes use
to accept either a signed-in session or a token carrying the required scope.

``require_api_scope`` is the only scope decorator for REST routes: MCP tool
scopes stay on the tools themselves (``required_scope`` in the MCP
blueprint), and the consent flow stays exactly as it is.

The decorator also resolves the request to its contract operation id. A bearer
call is recorded once per (organisation, consumer, operation), so the
deprecation admin page can name the consumers of an operation before it
retires, and an operation a platform administrator has marked as retiring
answers with the retirement headers. Recording runs after the response has been
sent, in its own short transaction, and can never fail, slow or alter the
request.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from email.utils import format_datetime
from functools import wraps

from flask import after_this_request, g, request
from flask_login import current_user

from app.extensions import db
from app.utils.api_contract_index import (
    build_operation_paths,
    build_route_index,
    load_subset,
    normalize_path,
)
from app.utils.api_response import forbidden_response, unauthorized_response

logger = logging.getLogger(__name__)

API_SCOPE_PREFIX = "api:"
MCP_SCOPE_PREFIX = "mcp:"

API_READ_SCOPE = "api:read"
MCP_READ_SCOPE = "mcp:read"

# The only scopes a client-credentials grant may ever issue.
API_SCOPES = (API_READ_SCOPE,)

# (normalised path, method) -> operation id, and operation id -> contract path.
_operation_index: dict | None = None
_operation_paths: dict | None = None


def _load_contract() -> tuple[dict, dict]:
    """Build the lookups from the contract subset once; later calls are free.

    If the contract file is missing or unreadable the failure is logged once and
    every operation is treated as unknown: nothing is recorded, no headers are
    added, and the request is not affected.
    """
    global _operation_index, _operation_paths
    if _operation_index is not None and _operation_paths is not None:
        return _operation_index, _operation_paths
    try:
        subset = load_subset()
        index = {
            key: operation["operationId"]
            for key, operation in build_route_index(subset).items()
            if operation.get("operationId")
        }
        paths = build_operation_paths(subset)
    except Exception as exc:
        logger.warning("API contract could not be read; operations are treated as unknown: %s", exc)
        index, paths = {}, {}
    _operation_index, _operation_paths = index, paths
    return _operation_index, _operation_paths


def _load_operation_index() -> dict:
    return _load_contract()[0]


def _request_operation_id() -> str | None:
    """The contract operation id of the current request, or None.

    Matches on the route's own rule (``/api/v1/elements/<int:id>``), which has
    the same shape as the contract's ``/elements/{id}``, so an operation with a
    path parameter resolves as well as a collection read.
    """
    rule = request.url_rule.rule if request.url_rule is not None else request.path
    return _load_operation_index().get((normalize_path(rule), request.method.lower()))


def operation_path(operation_id: str | None) -> str | None:
    """The contract path of an operation id (``/api/v1/elements/{id}``), or None."""
    return _load_contract()[1].get(operation_id) if operation_id else None


def operation_ids() -> frozenset:
    """Every operation id the contract declares."""
    return frozenset(_load_contract()[1])


_UPSERT_CALL = db.text(
    "INSERT INTO api_operation_calls "
    "(organization_id, client_id, consumer_key, token_kind, operation_id, last_called_at, call_count) "
    "SELECT :org_id, c.client_id, :consumer_key, "
    "CASE WHEN c.client_id IS NULL THEN 'api_token' ELSE 'oauth_client' END, "
    ":operation_id, now(), 1 "
    "FROM (SELECT CAST(:consumer_key AS VARCHAR) AS k) s "
    "LEFT JOIN oauth_clients c ON c.client_id = s.k "
    "ON CONFLICT (organization_id, consumer_key, operation_id) "
    "DO UPDATE SET last_called_at = now(), "
    "call_count = api_operation_calls.call_count + 1"
)
_RELAXED_COMMIT = db.text("SET LOCAL synchronous_commit = off")
_SELECT_RETIREMENT = db.text(
    "SELECT retires_on, replacement_operation_id, marked_at "
    "FROM api_operation_retirements "
    "WHERE operation_id = :operation_id"
)


def _find_retirement(operation_id: str):
    """The retirement record for an operation, or None; never raises.

    One indexed read on the request's own connection, before the view has done
    anything, inside a savepoint: if it fails (a missing table, a dropped
    connection) only the savepoint is rolled back, so what the request's
    transaction already holds (the identity's last-used mark, for one) stays.
    """
    try:
        with db.session.begin_nested():
            return db.session.execute(_SELECT_RETIREMENT, {"operation_id": operation_id}).first()
    except Exception as exc:
        logger.warning("Retirement lookup skipped for %s: %s", operation_id, exc)
        return None


def _write_call(app, org_id: int, consumer_key: str, operation_id: str) -> None:
    """Upsert one call record in its own transaction; never raises.

    Runs once the response has been handed back (see ``_record_after_response``),
    so it adds nothing to the caller's wait, and in a transaction of its own, so
    the record is committed whatever the view did and a failure (a missing
    table, a lock, a dropped connection) is logged and touches nothing else. A
    personal API token has no ``oauth_clients`` row; it is recorded under its
    own identifier with ``client_id`` empty.
    """
    from sqlalchemy.orm import Session

    from app.middleware.tenant_isolation import set_database_tenant_context

    try:
        with app.app_context():
            with Session(bind=db.session.get_bind(), join_transaction_mode="create_savepoint") as own:
                set_database_tenant_context(own.connection(), org_id)
                # A usage counter does not need the commit to wait for the disk
                # flush; losing the last few increments in a crash is harmless.
                own.execute(_RELAXED_COMMIT)
                own.execute(
                    _UPSERT_CALL,
                    {"org_id": org_id, "consumer_key": consumer_key, "operation_id": operation_id},
                )
                own.commit()
    except Exception as exc:  # the record must never cost anyone a response
        logger.warning("API call record skipped for %s: %s", operation_id, exc)


def _record_after_response(org_id: int, consumer_key: str, operation_id: str) -> None:
    """Schedule the call record for when the response has been sent."""
    from flask import current_app

    app = current_app._get_current_object()

    @after_this_request
    def _schedule(response):
        response.call_on_close(lambda: _write_call(app, org_id, consumer_key, operation_id))
        return response


def retirement_headers(retirement) -> dict:
    """The headers an operation marked as retiring answers with.

    ``Deprecation`` carries the moment of marking (RFC 9745), ``Sunset`` the
    retirement date (RFC 8594), and ``Link`` the replacement operation's path
    from the contract (not its id).
    """
    marked_at = retirement.marked_at
    if marked_at.tzinfo is None:
        marked_at = marked_at.replace(tzinfo=timezone.utc)
    retires_at = datetime.combine(retirement.retires_on, time(0, 0), tzinfo=timezone.utc)
    headers = {
        "Deprecation": f"@{int(marked_at.timestamp())}",
        "Sunset": format_datetime(retires_at, usegmt=True),
    }
    path = operation_path(retirement.replacement_operation_id)
    if path:
        # A Link target must be a URI reference, and the contract's templated
        # form (``/api/v1/elements/{id}``) is not one. When the replacement has
        # path parameters the link names its collection path (the template cut
        # at the first parameter) instead; rel is ``successor-version`` because
        # ``deprecation`` (RFC 9745) is reserved for documentation of the
        # deprecation itself.
        target = path.split("{", 1)[0].rstrip("/") if "{" in path else path
        headers["Link"] = f'<{target}>; rel="successor-version"'
    return headers


def _announce_retirement(retirement) -> None:
    """Add the retirement headers to this request's final response, whatever its status."""
    headers = retirement_headers(retirement)

    @after_this_request
    def _add(response):
        for key, value in headers.items():
            response.headers[key] = value
        return response


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

    A passing bearer call is recorded once for its operation, and an operation
    marked as retiring adds the retirement headers to every response it gives.
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
                operation_id = _request_operation_id()
                if operation_id is not None:
                    retirement = _find_retirement(operation_id)
                    if retirement is not None:
                        _announce_retirement(retirement)
                    _record_after_response(token.organization_id, token.client_id, operation_id)
                return view(*args, **kwargs)
            if current_user.is_authenticated:
                operation_id = _request_operation_id()
                if operation_id is not None:
                    retirement = _find_retirement(operation_id)
                    if retirement is not None:
                        _announce_retirement(retirement)
                return view(*args, **kwargs)
            return unauthorized_response()

        wrapped._api_token_scope = required_scope
        return wrapped

    return decorator
