"""OAuth 2.0 / OAuth 2.1 well-known metadata endpoints.

GET /.well-known/oauth-protected-resource       — RFC 9728
GET /.well-known/oauth-authorization-server     — RFC 8414

Every URL here is built from the request's origin when it matches the
canonical PUBLIC_BASE_URL or a configured alias (PUBLIC_BASE_URL_ALIASES);
otherwise the canonical origin is used. This allows the metadata to answer
correctly on both entelim.tech and entelim.org without redirecting.
app/_bootstrap/blueprints.py refuses to boot with MCP_ENABLED true and
PUBLIC_BASE_URL empty, so these routes only ever run with a real base configured.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from app.services.origin import request_origin

oauth_metadata_bp = Blueprint("oauth_metadata", __name__)


@oauth_metadata_bp.route("/.well-known/oauth-protected-resource")
@oauth_metadata_bp.route("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata():
    """RFC 9728: Protected Resource Metadata.

    Published both at the root and under /mcp so a client that discovers
    either path finds the same document — the ``WWW-Authenticate`` challenge
    on 401 points at the ``/mcp`` path.
    """
    base = request_origin()
    return jsonify({
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp:read"],
    })


@oauth_metadata_bp.route("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    """RFC 8414: Authorization Server Metadata."""
    base = request_origin()
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
