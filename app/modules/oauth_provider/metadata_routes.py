"""OAuth 2.0 / OAuth 2.1 well-known metadata endpoints.

GET /.well-known/oauth-protected-resource       — RFC 9728
GET /.well-known/oauth-authorization-server     — RFC 8414
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

oauth_metadata_bp = Blueprint("oauth_metadata", __name__)


def _server_base_url() -> str:
    """The product's own canonical base URL from config."""
    raw = current_app.config.get("SERVER_NAME")
    if raw is None:
        return ""
    return str(raw).rstrip("/")


@oauth_metadata_bp.route("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    """RFC 9728: Protected Resource Metadata.

    Published at the root so any client can discover the authorization server
    before it ever reaches the MCP endpoint.
    """
    base = _server_base_url()
    return jsonify({
        "resource": f"{base}/mcp" if base else "/mcp",
        "authorization_servers": [base or "https://app.entelim.com"],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp:read", "mcp:propose"],
    })


@oauth_metadata_bp.route("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    """RFC 8414: Authorization Server Metadata."""
    base = _server_base_url()
    return jsonify({
        "issuer": base or "https://app.entelim.com",
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "scopes_supported": ["mcp:read", "mcp:propose"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "service_documentation": f"{base}/docs/reading-your-mcp-server.md" if base else "",
    })