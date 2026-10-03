"""OAuth 2.1 authorization and token endpoints.

POST /oauth/authorize  — authorization endpoint (PKCE S256 required)
POST /oauth/token       — token endpoint (authorization_code grant)
"""

from __future__ import annotations

import hashlib
import logging

from flask import Blueprint, current_app, jsonify, redirect, render_template, request
from flask_login import current_user, login_required

from app.modules.oauth_provider.models import OAuthAuthorizationCode, OAuthClient, OAuthToken

logger = logging.getLogger(__name__)

oauth_provider_bp = Blueprint("oauth_provider", __name__, url_prefix="/oauth")


def _hash_code_verifier(verifier: str) -> str:
    """S256 code challenge hash."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    # Base64url-encode without padding, per RFC 7636 Appendix A
    import base64
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _validate_resource(resource: str | None) -> str | None:
    """Validate the ``resource`` parameter (RFC 8707) against the MCP endpoint URL.

    Returns the canonical resource URL if valid, or None.
    """
    if not resource:
        return None
    mcp_url = current_app.config.get("MCP_ENDPOINT_URL", "")
    if not mcp_url:
        return resource  # Not configured — accept any resource
    # Strip trailing slash for comparison
    resource_clean = resource.rstrip("/")
    mcp_clean = mcp_url.rstrip("/")
    if resource_clean == mcp_clean:
        return resource
    return None


@oauth_provider_bp.route("/authorize", methods=["GET", "POST"])
@login_required
def authorize():
    """Authorization endpoint.

    GET: show consent screen.
    POST: process consent and issue authorization code.
    """
    client_id = request.args.get("client_id") or (request.form.get("client_id") if request.method == "POST" else None)
    redirect_uri = request.args.get("redirect_uri") or (request.form.get("redirect_uri") if request.method == "POST" else None)
    code_challenge = request.args.get("code_challenge") or (request.form.get("code_challenge") if request.method == "POST" else None)
    code_challenge_method = request.args.get("code_challenge_method") or (request.form.get("code_challenge_method") if request.method == "POST" else "S256")
    state = request.args.get("state") or (request.form.get("state") if request.method == "POST" else None)
    requested_scope = request.args.get("scope") or (request.form.get("scope") if request.method == "POST" else "mcp:read")
    requested_resource = request.args.get("resource") or (request.form.get("resource") if request.method == "POST" else None)

    if not client_id:
        return jsonify({"error": "invalid_request", "error_description": "client_id is required"}), 400

    client = OAuthClient.query.filter_by(client_id=client_id, is_active=True).first()
    if client is None:
        return jsonify({"error": "invalid_client", "error_description": "client not found"}), 401

    if not redirect_uri:
        return jsonify({"error": "invalid_request", "error_description": "redirect_uri is required"}), 400

    if client.redirect_uri_list and redirect_uri not in client.redirect_uri_list:
        return jsonify({"error": "invalid_request", "error_description": "redirect_uri mismatch"}), 400

    if not code_challenge:
        return jsonify({"error": "invalid_request", "error_description": "code_challenge (PKCE) is required"}), 400

    if code_challenge_method != "S256":
        return jsonify({"error": "invalid_request", "error_description": "only S256 code_challenge_method is supported"}), 400

    # Validate resource parameter
    if requested_resource:
        validated_resource = _validate_resource(requested_resource)
        if validated_resource is None:
            return jsonify({"error": "invalid_resource", "error_description": "resource does not match this server"}), 400
    else:
        validated_resource = None

    if request.method == "GET":
        # Show consent screen
        scopes = [s.strip() for s in requested_scope.split() if s.strip()]
        return render_template(
            "oauth/consent.html",
            client_name=client.client_name or client.client_id,
            scopes=scopes,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            state=state,
            scope=requested_scope,
            resource=validated_resource,
        )

    # POST: process consent
    OAuthAuthorizationCode.clean_expired()

    auth_code = OAuthAuthorizationCode.issue(
        client_id=client_id,
        user_id=current_user.id,
        redirect_uri=redirect_uri,
        scope=requested_scope,
        resource=validated_resource,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )

    params = {"code": auth_code.code}
    if state:
        params["state"] = state

    separator = "&" if "?" in redirect_uri else "?"
    location = redirect_uri + separator + "&".join(f"{k}={v}" for k, v in params.items())
    return redirect(location)


@oauth_provider_bp.route("/token", methods=["POST"])
def token():
    """Token endpoint — authorization_code grant with PKCE."""
    grant_type = request.form.get("grant_type", "")
    code = request.form.get("code", "")
    redirect_uri = request.form.get("redirect_uri", "")
    client_id = request.form.get("client_id", "")
    code_verifier = request.form.get("code_verifier", "")
    resource = request.form.get("resource", "")

    if grant_type != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400

    if not code or not client_id or not code_verifier:
        return jsonify({"error": "invalid_request"}), 400

    OAuthAuthorizationCode.clean_expired()

    auth_code = OAuthAuthorizationCode.consume(code)
    if auth_code is None:
        return jsonify({"error": "invalid_grant", "error_description": "authorization code not found or expired"}), 400

    if auth_code.client_id != client_id:
        return jsonify({"error": "invalid_grant", "error_description": "client_id mismatch"}), 400

    if auth_code.redirect_uri != redirect_uri:
        return jsonify({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}), 400

    # Verify PKCE
    expected_challenge = auth_code.code_challenge
    actual_challenge = _hash_code_verifier(code_verifier)
    if actual_challenge != expected_challenge:
        return jsonify({"error": "invalid_grant", "error_description": "code_verifier does not match"}), 400

    # Validate resource on token request too
    if resource:
        validated_resource = _validate_resource(resource)
        if validated_resource is None:
            return jsonify({"error": "invalid_resource"}), 400
    else:
        validated_resource = auth_code.resource

    token = OAuthToken.issue(
        client_id=client_id,
        user_id=auth_code.user_id,
        scope=auth_code.scope or "mcp:read",
        resource=validated_resource,
    )

    return jsonify({
        "access_token": token.access_token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": token.refresh_token,
        "scope": token.scope,
    })