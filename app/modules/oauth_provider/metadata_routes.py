"""OAuth 2.0 / OAuth 2.1 well-known metadata endpoints.

GET /.well-known/oauth-protected-resource       — RFC 9728
GET /.well-known/oauth-authorization-server     — RFC 8414

Every URL here is built from PUBLIC_BASE_URL, the one configured origin —
never from the incoming request's Host header, and never from a literal
fallback host. app/_bootstrap/blueprints.py refuses to boot with
MCP_ENABLED true and PUBLIC_BASE_URL empty, so these routes only ever run
with a real base configured.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

oauth_metadata_bp = Blueprint("oauth_metadata", __name__)


def _public_base_url() -> str:
    return (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")


@oauth_metadata_bp.route("/.well-known/oauth-protected-resource")
@oauth_metadata_bp.route("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata():
    """RFC 9728: Protected Resource Metadata.

    Published both at the root and under /mcp so a client that discovers
    either path finds the same document — the ``WWW-Authenticate`` challenge
    on 401 points at the ``/mcp`` path.
    """
    base = _public_base_url()
    return jsonify({
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp:read"],
    })


@oauth_metadata_bp.route("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    """RFC 8414: Authorization Server Metadata."""
    base = _public_base_url()
    return jsonify({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "scopes_supported": ["mcp:read"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
    })
