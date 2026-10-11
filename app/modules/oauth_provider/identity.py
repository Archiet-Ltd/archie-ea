"""Bearer-token identity resolution for the MCP endpoint and the /api/v1
read route family.

This is a flask-login ``request_loader`` — the seam flask-login calls when a
request carries no session cookie. It resolves ``current_user`` from an
``Authorization: Bearer`` header for two kinds of request: a direct call to
the public ``/mcp`` endpoint, and a call to an /api/v1 route that opts into
bearer tokens with the ``require_api_scope`` decorator.

A prior version of this module also authenticated an *internal* bridge call
(``app.utils.internal_api.call_internal_api`` in bearer mode) made on that
same caller's behalf, identified by a hardcoded, non-secret marker header
(``X-Entelim-MCP-Bridge: internal-bearer-bridge-v1``). That header and its
value were a literal constant committed in source, so anyone holding ANY
valid MCP bearer token could add the header to a request against ANY other
route in the app and be authenticated as that token's user there too —
privilege escalation past the intended MCP-only scope. It was also
unnecessary: ``call_internal_api``'s nested internal call already inherits
the outer request's resolved ``current_user``/``g`` state directly, via
Flask's own app-context stack, because the nested call happens in-process
while the outer request's context is still open — none of the existing
read-only tools ever needed the header, and ``call_internal_api`` never set
it. The marker mechanism has been removed outright rather than hardened.

Every request other than a direct call to ``/mcp`` or a token-accepting
/api/v1 route is left alone — the loader returns ``None`` and whatever
session-cookie handling already exists proceeds exactly as before. The
loader never calls ``login_user()`` and never touches the session:
flask-login's request-loader contract is already "resolve a user for *this*
request only," which is exactly the no-session-written behaviour a
bearer-authenticated API caller needs.

Registered unconditionally at boot (see ``app/_bootstrap/blueprints.py``), but
inert — returns ``None`` immediately — while ``MCP_ENABLED`` is false, so the
mechanism exists in every build but only acts when the feature is turned on.
"""

from __future__ import annotations

from flask import current_app, g, request

from app.extensions import login_manager
from app.modules.oauth_provider.scopes import (
    API_SCOPE_PREFIX,
    MCP_SCOPE_PREFIX,
    carries_family_scope,
)


def _is_mcp_scoped_request() -> bool:
    """True only for a direct call to the public /mcp endpoint.

    No header-based alternate path: see this module's docstring for why one
    existed before and why it was removed rather than hardened.
    """
    return request.path == "/mcp"


def _api_token_route_scope() -> str | None:
    """The api scope the current /api/v1 route accepts, or None.

    Returns None for any path that is not an /api/v1 route opting into bearer
    tokens (``require_api_scope``), so a session-only route — including every
    pre-existing @login_required route — never sees a bearer identity.
    Matching against the registered URL map means a route added later is
    covered automatically.
    """
    from werkzeug.exceptions import MethodNotAllowed, NotFound

    if not request.path.startswith("/api/v1"):
        return None
    try:
        matcher = current_app.url_map.bind_to_environ(request.environ)
        endpoint, _args = matcher.match()
    except (NotFound, MethodNotAllowed):
        return None
    view = current_app.view_functions.get(endpoint)
    if view is None:
        return None
    return getattr(view, "_api_token_scope", None)


def _token_is_refused_for_namespace(scope: str | None, required_family: str) -> bool:
    """Namespace separation: a token carries one namespace.

    When the path family needs ``api`` scopes, a token holding any ``mcp:*``
    scope is refused; when it needs ``mcp`` scopes, a token holding any
    ``api:*`` scope is refused. A token with scopes from both namespaces is
    therefore refused on both. Empty or connector-scope tokens on /mcp still
    resolve — the per-tool ``required_scope`` check in the MCP blueprint
    refuses them where the tool is called, exactly as before.
    """
    if required_family == API_SCOPE_PREFIX:
        return carries_family_scope(scope, MCP_SCOPE_PREFIX)
    return carries_family_scope(scope, API_SCOPE_PREFIX)


def load_user_from_bearer_token(req):
    """flask-login request_loader: resolve a user from an OAuth bearer token.

    Returns ``None`` for anything this loader should not touch — a route
    that takes no bearer token, no header, or any of the refusal conditions
    below — letting flask-login fall back to its usual anonymous-user
    handling.
    """
    if not current_app.config.get("MCP_ENABLED"):
        return None

    if _is_mcp_scoped_request():
        required_family = MCP_SCOPE_PREFIX
        resource_suffix = "/mcp"
    else:
        required_scope = _api_token_route_scope()
        if required_scope is None:
            return None
        required_family = API_SCOPE_PREFIX
        resource_suffix = "/api/v1"

    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    raw_token = auth_header[len("Bearer "):].strip()
    if not raw_token:
        return None

    from app.modules.oauth_provider.models import OAuthToken

    token = OAuthToken.find_by_access_token(raw_token)
    if token is None:
        return None
    if not token.is_active:
        return None

    if _token_is_refused_for_namespace(token.scope, required_family):
        return None

    public_base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    expected_resource = f"{public_base}{resource_suffix}" if public_base else None
    if expected_resource is None or token.resource != expected_resource:
        return None

    from app.extensions import db
    from app.models.user import User

    user = db.session.get(User, token.user_id)
    if user is None:
        return None
    if not getattr(user, "is_active", True):
        return None
    if getattr(user, "organization_id", None) != token.organization_id:
        return None

    # The account must still be what it was when the person consented: a reset
    # password, changed multi-factor or single-sign-on state, a move to another
    # organisation or an unconfirmed account ends the token (D1).
    from app.modules.oauth_provider.models import auth_state_for

    if not token.auth_state or token.auth_state != auth_state_for(user):
        return None

    token.touch_last_used()

    g.auth_mode = "bearer"
    g.bearer_token = token
    return user


login_manager.request_loader(load_user_from_bearer_token)
