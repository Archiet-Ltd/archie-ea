"""OAuth 2.1 authorization, token and revocation endpoints.

POST /oauth/authorize  — authorization endpoint (PKCE S256 required)
POST /oauth/token       — token endpoint (authorization_code and refresh_token grants)
POST /oauth/revoke      — RFC 7009 token revocation
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import urllib.parse

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request
from flask_login import current_user, login_required

from app.extensions import csrf
from app.models.user import Permission
from app.modules.oauth_provider.models import OAuthAuthorizationCode, OAuthClient, OAuthToken

logger = logging.getLogger(__name__)

oauth_provider_bp = Blueprint("oauth_provider", __name__, url_prefix="/oauth")

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "[::1]"}

# Scopes a client may ever be granted. Anything else requested is silently
# dropped rather than granted — an allow-list, not a denylist.
ALLOWED_SCOPES = ("mcp:read", "mcp:propose")

# A scope that requires more than "a token exists" to grant: mcp:propose lets
# an assistant write, so it is only ever granted when the person ticks it on
# the consent screen AND their own account already carries general write
# permission. Dropping a requested scope here is not an error — the token
# response states the scope actually granted.
_SCOPE_PERMISSION_GATE = {
    "mcp:propose": Permission.GENERAL,
}


def _hash_code_verifier(verifier: str) -> str:
    """S256 code challenge hash."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    # Base64url-encode without padding, per RFC 7636 Appendix A
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _mcp_resource_url() -> str | None:
    """The one configured resource identifier, or None if unconfigured."""
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        return None
    return f"{base}/mcp"


def _filter_granted_scopes(requested_scope: str | None, user) -> list[str]:
    """Reduce a requested scope string to the scopes this user may actually
    be granted — allow-listed, and permission-gated where applicable."""
    requested = [s.strip() for s in (requested_scope or "").split() if s.strip()]
    granted = []
    for scope in requested:
        if scope not in ALLOWED_SCOPES:
            continue
        gate = _SCOPE_PERMISSION_GATE.get(scope)
        if gate is not None and not user.can(gate):
            continue
        granted.append(scope)
    if not granted:
        granted = ["mcp:read"]
    return granted


def _build_redirect(redirect_uri: str, params: dict) -> str:
    """Append *params* to *redirect_uri*'s query string, properly encoded,
    joining correctly whether or not the URI already carries a query."""
    parsed = urllib.parse.urlsplit(redirect_uri)
    existing = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    existing.extend(params.items())
    new_query = urllib.parse.urlencode(existing)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment)
    )


@oauth_provider_bp.route("/authorize", methods=["GET", "POST"])
@login_required
def authorize():
    """Authorization endpoint.

    GET: show the consent screen.
    POST: process the person's explicit decision (Allow/Deny) and, on Allow,
    issue an authorization code.
    """
    client_id = request.args.get("client_id") or request.form.get("client_id")
    redirect_uri = request.args.get("redirect_uri") or request.form.get("redirect_uri")
    code_challenge = request.args.get("code_challenge") or request.form.get("code_challenge")
    code_challenge_method = (
        request.args.get("code_challenge_method") or request.form.get("code_challenge_method") or "S256"
    )
    state = request.args.get("state") or request.form.get("state")
    requested_scope = request.args.get("scope") or request.form.get("scope") or "mcp:read"
    requested_resource = request.args.get("resource") or request.form.get("resource")

    if not client_id:
        return jsonify({"error": "invalid_request", "error_description": "client_id is required"}), 400

    client = OAuthClient.query.filter_by(client_id=client_id, is_active=True).first()  # tenant-scoping-ok: platform-level client registry, not tenant-owned data
    if client is None:
        return jsonify({"error": "invalid_client", "error_description": "client not found"}), 401

    if not redirect_uri:
        return jsonify({"error": "invalid_request", "error_description": "redirect_uri is required"}), 400

    # A client with no registered redirect URI accepts nothing — there is no
    # implicit "any URI is fine" fallback.
    if not client.redirect_uri_list or redirect_uri not in client.redirect_uri_list:
        return jsonify({"error": "invalid_request", "error_description": "redirect_uri mismatch"}), 400

    if not code_challenge:
        return jsonify({"error": "invalid_request", "error_description": "code_challenge (PKCE) is required"}), 400

    if code_challenge_method != "S256":
        return jsonify({"error": "invalid_request", "error_description": "only S256 code_challenge_method is supported"}), 400

    expected_resource = _mcp_resource_url()
    if not requested_resource or expected_resource is None or requested_resource.rstrip("/") != expected_resource:
        return jsonify({"error": "invalid_target", "error_description": "resource does not match this server"}), 400
    validated_resource = expected_resource

    if request.method == "GET":
        scopes = _filter_granted_scopes(requested_scope, current_user)
        parsed_redirect = urllib.parse.urlsplit(redirect_uri)
        # The consent form's Allow/Deny decision POSTs back to this same
        # origin, which then 302-redirects to the client's redirect_uri --
        # by design, an origin the CSP's blanket "form-action 'self'"
        # (app/_bootstrap/security.py, ARCH-070) does not otherwise permit.
        # Chromium enforces form-action against the whole redirect chain a
        # form submission produces, not just its initial target, so without
        # this the browser silently blocks the redirect after Allow/Deny is
        # clicked -- confirmed in a real browser, not assumed (the consent
        # POST itself succeeds; the external hop is what gets blocked, with
        # Chromium's own console message naming the *original* form target
        # rather than the redirect, which is what makes this easy to miss).
        # Safe to widen only to this one, already-registered redirect_uri:
        # the check above (redirect_uri in client.redirect_uri_list) has
        # already run, so this is never attacker-controlled free text.
        g.csp_form_action_extra = f"{parsed_redirect.scheme}://{parsed_redirect.netloc}"
        return render_template(
            "oauth/consent.html",
            client_name=client.client_name or client.client_id,
            redirect_host=parsed_redirect.netloc,
            organization_name=getattr(getattr(current_user, "organization", None), "name", None),
            scopes=scopes,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            state=state,
            scope=" ".join(scopes),
            resource=validated_resource,
        )

    # POST: the person's explicit decision from the consent form's two buttons.
    decision = request.form.get("decision")
    if decision not in ("allow", "deny"):
        return jsonify({"error": "invalid_request", "error_description": "decision is required"}), 400

    if decision == "deny":
        params = {"error": "access_denied"}
        if state:
            params["state"] = state
        return redirect(_build_redirect(redirect_uri, params))

    OAuthAuthorizationCode.clean_expired()

    granted_scopes = _filter_granted_scopes(requested_scope, current_user)
    raw_code, _auth_code = OAuthAuthorizationCode.issue(
        client_id=client_id,
        user_id=current_user.id,
        redirect_uri=redirect_uri,
        scope=" ".join(granted_scopes),
        resource=validated_resource,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        organization_id=current_user.organization_id,
    )

    params = {"code": raw_code}
    if state:
        params["state"] = state
    return redirect(_build_redirect(redirect_uri, params))


@oauth_provider_bp.route("/token", methods=["POST"])
@csrf.exempt
def token():
    """Token endpoint — authorization_code and refresh_token grants.

    Called by OAuth clients with no session cookie at all (a CLI, an
    assistant backend) — there is no CSRF token to carry, and nothing here
    can be driven by forging a browser session because none is read.
    """
    grant_type = request.form.get("grant_type", "")
    resource = request.form.get("resource", "")

    expected_resource = _mcp_resource_url()
    if not resource or expected_resource is None or resource.rstrip("/") != expected_resource:
        return jsonify({"error": "invalid_target", "error_description": "resource does not match this server"}), 400

    if grant_type == "authorization_code":
        return _token_authorization_code(expected_resource)
    if grant_type == "refresh_token":
        return _token_refresh(expected_resource)
    return jsonify({"error": "unsupported_grant_type"}), 400


def _token_authorization_code(expected_resource: str):
    code = request.form.get("code", "")
    redirect_uri = request.form.get("redirect_uri", "")
    client_id = request.form.get("client_id", "")
    code_verifier = request.form.get("code_verifier", "")

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

    expected_challenge = auth_code.code_challenge
    actual_challenge = _hash_code_verifier(code_verifier)
    if actual_challenge != expected_challenge:
        return jsonify({"error": "invalid_grant", "error_description": "code_verifier does not match"}), 400

    if auth_code.resource != expected_resource:
        return jsonify({"error": "invalid_target", "error_description": "resource does not match this server"}), 400

    raw_access, raw_refresh, token = OAuthToken.issue(
        client_id=client_id,
        user_id=auth_code.user_id,
        scope=auth_code.scope or "mcp:read",
        resource=auth_code.resource,
        grant_type="authorization_code",
        organization_id=auth_code.organization_id,
        refresh_expires_in=current_app.config.get("OAUTH_REFRESH_TOKEN_DAYS", 30) * 86400,
    )

    return jsonify({
        "access_token": raw_access,
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": raw_refresh,
        "scope": token.scope,
    })


def _token_refresh(expected_resource: str):
    raw_refresh = request.form.get("refresh_token", "")
    client_id = request.form.get("client_id", "")
    if not raw_refresh or not client_id:
        return jsonify({"error": "invalid_request"}), 400

    old_token = OAuthToken.find_by_refresh_token(raw_refresh)
    if old_token is None or not old_token.is_refresh_active:
        return jsonify({"error": "invalid_grant", "error_description": "refresh token not found, expired or revoked"}), 400

    if old_token.client_id != client_id:
        return jsonify({"error": "invalid_grant", "error_description": "client_id mismatch"}), 400

    if old_token.resource != expected_resource:
        return jsonify({"error": "invalid_target", "error_description": "resource does not match this server"}), 400

    # Rotation: the presented refresh token is revoked in the same request
    # that mints its replacement, so it can never be used a second time —
    # including by whoever it leaked to, once the legitimate client rotates.
    old_token.revoke()

    raw_access, raw_refresh_new, new_token = OAuthToken.issue(
        client_id=old_token.client_id,
        user_id=old_token.user_id,
        scope=old_token.scope,
        resource=old_token.resource,
        grant_type="refresh_token",
        organization_id=old_token.organization_id,
        refresh_expires_in=current_app.config.get("OAUTH_REFRESH_TOKEN_DAYS", 30) * 86400,
    )

    return jsonify({
        "access_token": raw_access,
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": raw_refresh_new,
        "scope": new_token.scope,
    })


@oauth_provider_bp.route("/revoke", methods=["POST"])
@csrf.exempt
def revoke():
    """RFC 7009 token revocation. Always returns 200 (per the RFC, even for
    an unknown token) so a client cannot use the response to probe which
    tokens exist."""
    raw_token = request.form.get("token", "")
    if raw_token:
        token = OAuthToken.find_by_access_token(raw_token) or OAuthToken.find_by_refresh_token(raw_token)
        if token is not None:
            token.revoke()
    return "", 200


def _is_valid_registration_redirect_uri(uri: str) -> bool:
    """https anywhere, or http on a loopback address only — no wildcards, no fragment."""
    if not uri or "*" in uri:
        return False
    try:
        parsed = urllib.parse.urlsplit(uri)
    except ValueError:
        return False
    if parsed.fragment:
        return False
    if not parsed.hostname:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http":
        return parsed.hostname in _LOOPBACK_HOSTS or parsed.hostname == "localhost"
    return False


def _registration_error(message: str):
    return jsonify({"error": "invalid_client_metadata", "error_description": message}), 400


def registration_rate_limit_string() -> str:
    """Read dynamically (not captured at import/decoration time) so a test
    overriding the config value after the app is built still takes effect."""
    return current_app.config.get("OAUTH_CLIENT_REGISTRATION_RATE_LIMIT", "10 per hour")


def register():
    """RFC 7591 dynamic client registration.

    Public clients only: no client secret is ever issued, because
    token_endpoint_auth_method is always "none" — the only value accepted
    here. There is no session to read (no credentials, no cookie), so this
    view is unauthenticated by design and rate-limited per remote address
    instead.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _registration_error("a JSON object body is required")

    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return _registration_error("redirect_uris must be a non-empty array")
    for uri in redirect_uris:
        if not isinstance(uri, str) or not _is_valid_registration_redirect_uri(uri):
            return _registration_error(
                f"redirect_uri {uri!r} must be https, or http on a loopback address, "
                "with no wildcard and no fragment"
            )

    token_endpoint_auth_method = body.get("token_endpoint_auth_method", "none")
    if token_endpoint_auth_method != "none":
        return _registration_error("only the public client method 'none' is supported")

    response_types = body.get("response_types", ["code"])
    if response_types != ["code"]:
        return _registration_error("response_types must be ['code']")

    grant_types = body.get("grant_types", ["authorization_code", "refresh_token"])
    if not set(grant_types) <= {"authorization_code", "refresh_token"} or not grant_types:
        return _registration_error("grant_types must be a subset of authorization_code, refresh_token")

    client_name = body.get("client_name")
    if client_name is not None:
        if not isinstance(client_name, str):
            return _registration_error("client_name must be a string")
        client_name = _CONTROL_CHARS.sub("", client_name)[:100]

    client = OAuthClient.register(client_name=client_name, redirect_uris=" ".join(redirect_uris))

    return jsonify({
        "client_id": client.client_id,
        "client_id_issued_at": int(client.created_at.timestamp()),
        "redirect_uris": redirect_uris,
        "grant_types": grant_types,
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "client_name": client.client_name,
    }), 201


register = oauth_provider_bp.route("/register", methods=["POST"])(csrf.exempt(register))
