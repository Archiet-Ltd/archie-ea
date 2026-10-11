"""MCP Streamable HTTP blueprint.

A single POST endpoint at /mcp that accepts JSON-RPC messages and dispatches
to the registered tool handlers. Every tool call executes inside a real Flask
request carrying an authenticated ``current_user`` resolved from the OAuth
bearer token by the flask-login request_loader in
``app.modules.oauth_provider.identity`` — this module never calls
``login_user()`` and never touches the session itself. Because that loader
runs lazily the first time anything asks for ``current_user`` — which happens
inside the app's existing tenant-context ``before_request`` hook, before this
view ever runs — ``g.current_org_id`` is already correctly set by the time
any of the code below executes; nothing here needs to set it by hand.

CSRF
----
``mcp_endpoint`` is marked ``@csrf.exempt`` from flask-wtf's own blanket
``before_request`` check (see ``app/_bootstrap/csrf_coverage.py``'s
``VIEW_OPT_OUT`` for the recorded justification) because that global check
runs too early to judge this route correctly: it fires before the app's
tenant-context hook has ever touched ``current_user``, so ``g.auth_mode`` is
never set yet at that point, and a blanket CSRF check would reject every
genuine bearer-only call outright. ``_csrf_guard`` below is this blueprint's
own ``before_request``, which Flask runs *after* every app-level hook
(tenant-context included) for any request this blueprint handles — so
``g.auth_mode`` is already correctly set by the time it runs. It re-enables
the real check for everything except the one case CSRF has nothing to
protect against: a request that resolved via the bearer loader
(``g.auth_mode == "bearer"``), to this exact path, carrying no session
cookie at all. CSRF exists to stop a browser silently replaying a victim's
*session cookie* cross-site; an explicit ``Authorization: Bearer`` header is
not an ambient credential a cross-site page can read or forge, so there is
nothing here for CSRF to protect once all three of those hold. Any request
shape that fails even one of them — including a request that happens to
carry a valid session cookie — falls through to the ordinary check,
unchanged.
"""

from __future__ import annotations

import json
import logging
import time

from flask import Blueprint, current_app, g, jsonify, request
from flask_login import current_user

from app.extensions import csrf
from app.modules.mcp.tools import TOOL_REGISTRY

logger = logging.getLogger(__name__)

mcp_bp = Blueprint("mcp", __name__, url_prefix="/mcp")

# JSON-RPC 2.0 error codes
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


def _jsonrpc_error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _jsonrpc_result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _request_carries_session_cookie() -> bool:
    """True when this request's own wire data includes the app's session
    cookie -- the ambient credential CSRF exists to stop a forged cross-site
    request from silently replaying. Checked directly against
    ``request.cookies`` (not against whether the cookie actually
    authenticated anything), so a session cookie that is present but stale,
    expired or otherwise failed to authenticate still counts: the point is
    whether a browser sent one on the wire, not whether it worked, since a
    cross-site forgery rides whatever the browser attaches regardless of
    whether it was still valid.
    """
    cookie_name = current_app.config.get("SESSION_COOKIE_NAME", "session")
    return cookie_name in request.cookies


def is_bearer_only_mcp_request() -> bool:
    """True only when ALL THREE hold: this request is addressed at the
    public /mcp endpoint itself, it resolved to a real, already-validated
    bearer token (``g.auth_mode == "bearer"``, set only by
    ``app.modules.oauth_provider.identity`` after checking the token's
    validity, resource and organization -- never set here), and it carries
    no session cookie at all. The session-cookie check is deliberate
    defense in depth rather than redundant with the ``auth_mode`` check:
    flask-login resolves a session-based user before it ever falls back to
    the bearer request_loader, so in the ordinary case a request that
    carries a *valid* session cookie never reaches ``auth_mode == "bearer"``
    in the first place -- but this function must not quietly assume that
    stays true (an expired or otherwise-rejected session cookie can still be
    present on the wire alongside a valid bearer header), so it checks the
    cookie's presence explicitly rather than inferring it from
    ``auth_mode`` alone.
    """
    return (
        request.path == "/mcp"
        and getattr(g, "auth_mode", None) == "bearer"
        and not _request_carries_session_cookie()
    )


@mcp_bp.before_request
def _csrf_guard():
    """Run the real CSRF check for every request to this blueprint except a
    genuine bearer-only call to /mcp -- see the module docstring's "CSRF"
    section for why this has to be a blueprint-level hook rather than relying
    on flask-wtf's own blanket check.
    """
    if is_bearer_only_mcp_request():
        return None
    # A standard MCP client (including Claude Code's own) is expected to
    # trigger OAuth (re-)authentication specifically on an HTTP 401 from this
    # endpoint -- both on first connection (no token yet) and when a token
    # has expired. Without this, a POST /mcp carrying no valid bearer token
    # and no session cookie never sets g.auth_mode == "bearer" (so the
    # is_bearer_only_mcp_request() check above returns False) and falls
    # through to the ordinary CSRF check below, which fails with a generic
    # 400 before the view body -- which would have returned a proper 401
    # JSON-RPC error -- ever runs. Restricted to POST so the public,
    # unauthenticated GET /mcp metadata-discovery endpoint (mcp_get) is
    # unaffected. Restricted to "no session cookie at all" so a browser call
    # authenticated via a valid session cookie (no bearer token at all) is
    # completely unaffected and still goes through the ordinary CSRF path
    # exactly as today. This also correctly applies to the "initialize"
    # JSON-RPC method, which skips the current_user.is_authenticated check
    # entirely in the view body -- a client's very first POST /mcp call
    # before it has ever obtained a token should get 401 with
    # WWW-Authenticate, which is what tells a spec-compliant MCP client to go
    # start the OAuth flow.
    if (request.path == "/mcp" and request.method == "POST"
            and not _request_carries_session_cookie()
            and getattr(g, "auth_mode", None) != "bearer"):
        response = jsonify({"error": "invalid_token"})
        response.status_code = 401
        response.headers["WWW-Authenticate"] = 'Bearer realm="entelim", error="invalid_token"'
        return response
    if not current_app.config.get("WTF_CSRF_ENABLED", True):
        return None
    if not current_app.config.get("WTF_CSRF_CHECK_DEFAULT", True):
        return None
    # apply_exemptions=False: this view is marked @csrf.exempt for flask-wtf's
    # own automatic pass (so that earlier, auth-blind pass never rejects a
    # genuine bearer-only call), but here -- having just determined this is
    # NOT that case -- the real check must run unconditionally rather than
    # finding the same static exemption and skipping again.
    csrf.protect(apply_exemptions=False)
    return None


def _audit_tool_call(tool_name, org_id, user_id, status) -> None:
    """Record the call in the one audit log. Never raises."""
    try:
        from app.models.audit_log import AuditLog

        AuditLog.log(
            action="mcp_tool_call",
            entity_type="mcp_tool",
            entity_name=tool_name,
            organization_id=org_id,
            user_id=user_id,
            ip_address=request.remote_addr,
            description=f"Assistant connector called {tool_name}",
            status=status,
        )
    except Exception:
        logger.warning("mcp audit write failed", exc_info=True)


@mcp_bp.route("", methods=["POST"])
@csrf.exempt
def mcp_endpoint():
    """Streamable HTTP MCP endpoint — accepts JSON-RPC messages."""
    # Validate Origin header for DNS rebinding protection
    origin = request.headers.get("Origin", "")
    if origin:
        allowed_origin = current_app.config.get("MCP_ALLOWED_ORIGIN", "")
        if allowed_origin and origin != allowed_origin:
            return jsonify({"error": "forbidden"}), 403

    try:
        body = request.get_json(silent=True)
        if body is None:
            return jsonify(_jsonrpc_error(None, JSONRPC_PARSE_ERROR, "Parse error")), 400
    except Exception:
        return jsonify(_jsonrpc_error(None, JSONRPC_PARSE_ERROR, "Parse error")), 400

    req_id = body.get("id")
    method = body.get("method", "")

    # Handle MCP lifecycle methods
    if method == "initialize":
        return jsonify(_jsonrpc_result(req_id, {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "entelim", "version": "1.0.0"},
        }))

    if method == "notifications/initialized":
        return jsonify(_jsonrpc_result(req_id, {}))

    if method == "tools/list":
        if not current_user.is_authenticated:
            return jsonify(_jsonrpc_error(req_id, JSONRPC_INTERNAL_ERROR,
                                          "Authentication required")), 401
        tools = []
        for name, handler in sorted(TOOL_REGISTRY.items()):
            tools.append({
                "name": name,
                "description": handler.description,
                "inputSchema": handler.input_schema,
                "annotations": handler.annotations,
            })
        return jsonify(_jsonrpc_result(req_id, {"tools": tools}))

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in TOOL_REGISTRY:
            return jsonify(_jsonrpc_error(req_id, JSONRPC_METHOD_NOT_FOUND,
                                          f"Tool not found: {tool_name}")), 404

        # Authenticated via Bearer token by the request_loader in
        # app.modules.oauth_provider.identity, resolved the first time
        # current_user was touched (the tenant-context before_request hook).
        if not current_user.is_authenticated:
            return jsonify(_jsonrpc_error(req_id, JSONRPC_INTERNAL_ERROR,
                                          "Authentication required")), 401

        handler = TOOL_REGISTRY[tool_name]

        # Scope check -- only meaningful for a bearer-authenticated call: the
        # token's granted scope (set at issue time by
        # oauth_provider.routes._filter_granted_scopes) is the only place a
        # scope exists at all. A session-cookie-authenticated browser call
        # carries no OAuth token and no scope to check -- the existing
        # permission system already governs that path, unchanged.
        if getattr(g, "auth_mode", None) == "bearer":
            bearer_token = getattr(g, "bearer_token", None)
            granted_scopes = set((bearer_token.scope or "").split()) if bearer_token else set()
            if handler.required_scope not in granted_scopes:
                return jsonify(_jsonrpc_error(
                    req_id, JSONRPC_INTERNAL_ERROR,
                    f"insufficient_scope: this token does not have {handler.required_scope}",
                )), 403

        # Meter the call
        try:
            from app.services.usage_metering_service import UsageMeteringService
            UsageMeteringService.record(
                org_id=g.current_org_id,
                user_id=current_user.id,
                event_type="mcp_tool_call",
                resource_type=tool_name,
            )
        except Exception:
            pass  # Metering never raises

        org_id = g.current_org_id
        user_id = current_user.id
        start = time.monotonic()
        try:
            # The tenant middleware has already put this request in the
            # token's organisation scope (it sets g.current_org_id and the
            # database setting the row-level policies read). Refuse to run any
            # tool if that scope is missing or is not the caller's own
            # organisation, so no query ever runs unscoped.
            if org_id is None or org_id != getattr(current_user, "organization_id", None):
                return jsonify(_jsonrpc_error(
                    req_id, JSONRPC_INTERNAL_ERROR, "Organisation scope unavailable.",
                )), 403
            result = handler.execute(arguments)
            elapsed_ms = int((time.monotonic() - start) * 1000)

            logger.info(
                "mcp_tool_call tool=%s org=%s user=%s elapsed_ms=%d",
                tool_name, g.current_org_id, current_user.id, elapsed_ms,
            )

            _audit_tool_call(tool_name, org_id, user_id, "success")
            return jsonify(_jsonrpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result)}],
            }))
        except Exception:
            logger.exception("mcp_tool_call tool=%s failed", tool_name)
            _audit_tool_call(tool_name, org_id, user_id, "failure")
            # The exception text is never returned: it can carry SQL, paths
            # or another tenant's identifiers.
            return jsonify(_jsonrpc_error(req_id, JSONRPC_INTERNAL_ERROR,
                                          "The tool failed.")), 500

    # Unknown method
    return jsonify(_jsonrpc_error(req_id, JSONRPC_METHOD_NOT_FOUND,
                                  f"Method not found: {method}")), 404


@mcp_bp.route("", methods=["GET"])
def mcp_get():
    """GET on the MCP endpoint — returns server metadata for SSE discovery."""
    return jsonify({
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "entelim", "version": "1.0.0"},
    })