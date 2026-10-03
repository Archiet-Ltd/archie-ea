"""OAuth 2.1 authorization server for the MCP mount point.

Built with authlib's flask_oauth2 provider side — the same dependency already
used as an OAuth *client* for Google/Microsoft sign-on in
``app/modules/account/routes/account_routes.py``.

Provides:
  POST /oauth/authorize  — authorization endpoint (PKCE S256 required)
  POST /oauth/token       — token endpoint (authorization_code grant)
  GET  /.well-known/oauth-protected-resource       — RFC 9728
  GET  /.well-known/oauth-authorization-server     — RFC 8414
"""

from app.modules.oauth_provider.routes import oauth_provider_bp
from app.modules.oauth_provider.metadata_routes import oauth_metadata_bp

__all__ = ["oauth_provider_bp", "oauth_metadata_bp"]