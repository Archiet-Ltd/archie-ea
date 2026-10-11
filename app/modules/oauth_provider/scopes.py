"""Scope namespaces and the per-route api-scope gate for /api/v1.

A token carries one namespace. ``mcp:*`` scopes open the connector; ``api:*``
scopes open the REST API. The bearer loader (``app.modules.oauth_provider.
identity``) enforces the namespace separation at the path level; this module
owns the constants and the one ``require_api_scope`` decorator REST routes use
to accept either a signed-in session or a token carrying the required scope.

``require_api_scope`` is the only scope decorator for REST routes: MCP tool
scopes stay on the tools themselves (``required_scope`` in the MCP
blueprint), and the consent flow stays exactly as it is.
"""

from __future__ import annotations

from functools import wraps

from flask import g
from flask_login import current_user

from app.utils.api_response import forbidden_response, unauthorized_response

API_SCOPE_PREFIX = "api:"
MCP_SCOPE_PREFIX = "mcp:"

API_READ_SCOPE = "api:read"
MCP_READ_SCOPE = "mcp:read"

# The only scopes a client-credentials grant may ever issue.
API_SCOPES = (API_READ_SCOPE,)


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
                return view(*args, **kwargs)
            if current_user.is_authenticated:
                return view(*args, **kwargs)
            return unauthorized_response()

        wrapped._api_token_scope = required_scope
        return wrapped

    return decorator