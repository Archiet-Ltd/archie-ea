"""Bearer-token identity resolution for the MCP endpoint.

This is a flask-login ``request_loader`` — the seam flask-login calls when a
request carries no session cookie. It resolves ``current_user`` from an
``Authorization: Bearer`` header for exactly one kind of request: a direct
call to the public ``/mcp`` endpoint.

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

Every request other than a direct call to ``/mcp`` is left alone — the
loader returns ``None`` and whatever session-cookie handling already exists
proceeds exactly as before. The loader never calls ``login_user()`` and
never touches the session: flask-login's request-loader contract is already
"resolve a user for *this* request only," which is exactly the
no-session-written behaviour a bearer-authenticated API caller needs.

Registered unconditionally at boot (see ``app/_bootstrap/blueprints.py``), but
inert — returns ``None`` immediately — while ``MCP_ENABLED`` is false, so the
mechanism exists in every build but only acts when the feature is turned on.
"""

from __future__ import annotations

from flask import current_app, g, request

from app.extensions import login_manager


def _is_mcp_scoped_request() -> bool:
    """True only for a direct call to the public /mcp endpoint.

    No header-based alternate path: see this module's docstring for why one
    existed before and why it was removed rather than hardened.
    """
    return request.path == "/mcp"


def load_user_from_bearer_token(req):
    """flask-login request_loader: resolve a user from an OAuth bearer token.

    Returns ``None`` for anything this loader should not touch — an unknown
    endpoint, no header, or any of the refusal conditions below — letting
    flask-login fall back to its usual anonymous-user handling.
    """
    if not current_app.config.get("MCP_ENABLED"):
        return None
    if not _is_mcp_scoped_request():
        return None

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

    public_base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    expected_resource = f"{public_base}/mcp" if public_base else None
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

    token.touch_last_used()

    g.auth_mode = "bearer"
    g.bearer_token = token
    return user


login_manager.request_loader(load_user_from_bearer_token)
