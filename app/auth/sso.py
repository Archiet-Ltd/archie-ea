"""SSO authentication service — Azure AD (MSAL) and Okta (OIDC).

Provides enterprise single sign-on via OpenID Connect for Azure AD and Okta.
The service is controlled by the ``SSO_ENABLED`` config flag (or by the
``sso_authentication`` FeatureFlag row when present).  When disabled, all SSO
routes return 404.

OIDC Token exchange uses the standard Authorization Code flow:
  1. Redirect user to IdP authorization endpoint.
  2. IdP redirects back with ``code``.
  3. Service exchanges code for tokens at the IdP token endpoint.
  4. ID-token claims are used to create or update the local ``User`` record.

Group-to-role mapping translates IdP group memberships into the platform's
``enterprise_role`` field (ENT-068).
"""

import hmac
import logging
import secrets
import time
from urllib.parse import urlencode

import requests
from flask import session, url_for

logger = logging.getLogger(__name__)

# ── Group-to-role mapping ────────────────────────────────────────────
# Keys are IdP group *display names*; values are platform enterprise_role
# values defined in app.models.user.VALID_ROLES.
DEFAULT_GROUP_ROLE_MAP = {
    "EA-Architects": "enterprise_architect",
    "Business-Architects": "business_architect",
    "Solution-Architects": "solution_architect",
    "ARB-Members": "arb_member",
    "Portfolio-Managers": "portfolio_manager",
    "Platform-Admins": "platform_admin",
    # These three shipped with sidebars, permissions and AI charters and no way
    # to be provisioned: an SSO-only customer had no group that maps to them.
    "CTO": "cto",
    "Procurement": "procurement",
    "Application-Managers": "application_manager",
    "Security-Architects": "security_architect",
    "Data-Architects": "data_architect",
}


class SSOError(Exception):
    """Raised when an SSO operation fails."""


class SSOService:
    """Manages OpenID Connect authentication for Azure AD and Okta."""

    def __init__(self):
        self.enabled = False
        self.providers = {}
        self._group_role_map = dict(DEFAULT_GROUP_ROLE_MAP)

    # ── Initialization ───────────────────────────────────────────────

    def init_app(self, app):
        """Read SSO configuration from *app*.config and store provider metadata.

        Called once at app startup.  If ``SSO_ENABLED`` is falsy **and** the
        ``sso_authentication`` FeatureFlag is absent/disabled, the service
        stays dormant and all public methods short-circuit.
        """
        self.enabled = app.config.get("SSO_ENABLED", False)

        # Allow the DB-driven FeatureFlag to override config when available.
        if not self.enabled:
            try:
                from app.models.feature_flags import FeatureFlag

                flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
                if flag and flag.is_active:
                    self.enabled = True
            except Exception as e:
                logger.debug("SSO feature flag check failed (DB not ready?): %s", e)

        if not self.enabled:
            logger.info("SSO disabled — skipping provider configuration.")
            return

        sso_cfg = app.config.get("SSO_PROVIDERS", {})

        # Azure AD
        azure = sso_cfg.get("azure", {})
        if azure.get("client_id"):
            self.providers["azure"] = {
                "client_id": azure["client_id"],
                "client_secret": azure["client_secret"],
                "metadata_url": azure.get("server_metadata_url", ""),
                "scope": azure.get("client_kwargs", {}).get("scope", "openid email profile"),
                "name": "Microsoft",
            }
            logger.info("SSO provider configured: Azure AD")

        # Okta
        okta = sso_cfg.get("okta", {})
        if okta.get("client_id"):
            self.providers["okta"] = {
                "client_id": okta["client_id"],
                "client_secret": okta["client_secret"],
                "metadata_url": okta.get("server_metadata_url", ""),
                "scope": okta.get("client_kwargs", {}).get("scope", "openid email profile"),
                "name": "Okta",
            }
            logger.info("SSO provider configured: Okta")

        # Custom group→role map from config (optional override)
        custom_map = app.config.get("SSO_GROUP_ROLE_MAP")
        if custom_map and isinstance(custom_map, dict):
            self._group_role_map = custom_map

    # ── Provider availability ────────────────────────────────────────

    def is_enabled(self):
        """Return True when SSO is active and at least one provider is configured."""
        return self.enabled and bool(self.providers)

    def available_providers(self):
        """Return list of configured provider keys (e.g. ``['azure', 'okta']``)."""
        return list(self.providers.keys())

    # ── OpenID Connect metadata ──────────────────────────────────────

    def _fetch_oidc_metadata(self, provider_key):
        """Fetch and cache the OIDC discovery document for *provider_key*.

        Returns a dict with at least ``authorization_endpoint``,
        ``token_endpoint``, and ``jwks_uri``.
        """
        provider = self.providers.get(provider_key)
        if not provider:
            raise SSOError(f"Unknown SSO provider: {provider_key}")

        cache_key = f"_oidc_meta_{provider_key}"
        cached = getattr(self, cache_key, None)
        if cached and (time.time() - cached.get("_ts", 0)) < 3600:
            return cached

        metadata_url = provider["metadata_url"]
        if not metadata_url:
            raise SSOError(f"No metadata URL configured for provider {provider_key}")

        try:
            resp = requests.get(metadata_url, timeout=10)
            resp.raise_for_status()
            meta = resp.json()
            meta["_ts"] = time.time()
            setattr(self, cache_key, meta)
            return meta
        except requests.RequestException as exc:
            logger.error("Failed to fetch OIDC metadata for %s: %s", provider_key, exc)
            raise SSOError(f"Cannot reach {provider_key} identity provider") from exc

    # ── Authorization URL builders ───────────────────────────────────

    def _build_auth_url(self, provider_key):
        """Build the IdP authorization redirect URL for *provider_key*.

        Generates a cryptographic ``state`` token stored in the session so the
        callback can verify the response originated from a legitimate request.
        """
        provider = self.providers.get(provider_key)
        if not provider:
            raise SSOError(f"Unknown SSO provider: {provider_key}")

        meta = self._fetch_oidc_metadata(provider_key)
        auth_endpoint = meta.get("authorization_endpoint")
        if not auth_endpoint:
            raise SSOError(f"No authorization_endpoint in {provider_key} metadata")

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(16)
        session["sso_state"] = state
        session["sso_nonce"] = nonce
        session["sso_provider"] = provider_key

        callback_url = url_for("account.sso_callback", provider=provider_key, _external=True)

        params = {
            "client_id": provider["client_id"],
            "response_type": "code",
            "redirect_uri": callback_url,
            "scope": provider["scope"],
            "state": state,
            "nonce": nonce,
        }
        return f"{auth_endpoint}?{urlencode(params)}"

    def get_azure_auth_url(self):
        """Return the Azure AD authorization redirect URL."""
        return self._build_auth_url("azure")

    def get_okta_auth_url(self):
        """Return the Okta authorization redirect URL."""
        return self._build_auth_url("okta")

    # ── Token exchange & user info ───────────────────────────────────

    def _exchange_code(self, provider_key, auth_code):
        """Exchange an authorization *auth_code* for tokens.

        Returns the parsed JSON token response containing ``id_token``,
        ``access_token``, etc.
        """
        provider = self.providers.get(provider_key)
        if not provider:
            raise SSOError(f"Unknown SSO provider: {provider_key}")

        meta = self._fetch_oidc_metadata(provider_key)
        token_endpoint = meta.get("token_endpoint")
        if not token_endpoint:
            raise SSOError(f"No token_endpoint in {provider_key} metadata")

        callback_url = url_for("account.sso_callback", provider=provider_key, _external=True)

        data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": callback_url,
            "client_id": provider["client_id"],
            "client_secret": provider["client_secret"],
        }

        try:
            resp = requests.post(token_endpoint, data=data, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("Token exchange failed for %s: %s", provider_key, exc)
            raise SSOError("Failed to exchange authorization code") from exc

    def _decode_id_token_claims(self, token_response):
        """Extract claims from the ID token without full JWT signature verification.

        In production you would verify the JWT signature against the JWKS.
        For this implementation we decode the payload segment (base64url) which
        is safe because the token was received directly from the IdP over TLS
        in the back-channel token exchange (not from the browser).
        """
        import base64
        import json

        id_token = token_response.get("id_token", "")
        if not id_token:
            raise SSOError("No id_token in token response")

        parts = id_token.split(".")
        if len(parts) != 3:
            raise SSOError("Malformed id_token")

        # base64url decode the payload (second segment)
        payload = parts[1]
        # Add padding
        payload += "=" * (4 - len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode(payload)
            return json.loads(decoded)
        except Exception as exc:
            raise SSOError("Failed to decode id_token claims") from exc

    def _fetch_userinfo(self, provider_key, access_token):
        """Call the IdP's userinfo endpoint to get extended user claims.

        Falls back gracefully if the endpoint is unavailable.
        """
        try:
            meta = self._fetch_oidc_metadata(provider_key)
            userinfo_url = meta.get("userinfo_endpoint")
            if not userinfo_url:
                return {}
            resp = requests.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("userinfo fetch failed for %s: %s", provider_key, exc)
            return {}

    # ── Callback handler ─────────────────────────────────────────────

    def handle_callback(self, provider_key, auth_code, state=None):
        """Process the SSO callback after the IdP redirects back.

        Steps:
          1. Verify ``state`` matches session to prevent CSRF.
          2. Exchange ``auth_code`` for tokens.
          3. Extract user claims from id_token + userinfo.
          4. Find-or-create local User record.
          5. Map IdP groups to ``enterprise_role``.

        Returns the local ``User`` instance (already persisted).
        Raises ``SSOError`` on any failure.
        """
        # 1. State verification
        expected_state = session.pop("sso_state", None)
        session.pop("sso_nonce", None)
        session.pop("sso_provider", None)

        if not state or not expected_state or not hmac.compare_digest(state, expected_state):
            raise SSOError("Invalid SSO state — possible CSRF attack")

        # 2. Token exchange
        token_response = self._exchange_code(provider_key, auth_code)

        # 3. Extract claims
        claims = self._decode_id_token_claims(token_response)
        access_token = token_response.get("access_token", "")

        # Augment with userinfo if available
        userinfo = self._fetch_userinfo(provider_key, access_token)
        claims.update({k: v for k, v in userinfo.items() if k not in claims})

        email = claims.get("email") or claims.get("preferred_username") or claims.get("upn")
        if not email:
            raise SSOError("SSO response missing user email")

        external_id = claims.get("sub") or claims.get("oid") or ""
        first_name = claims.get("given_name") or claims.get("name", "").split()[0] if claims.get("name") else ""
        last_name = claims.get("family_name") or ""
        groups = claims.get("groups", [])

        # 4. Find or create user
        user = self._find_or_create_user(
            email=email,
            external_id=external_id,
            provider=provider_key,
            first_name=first_name,
            last_name=last_name,
        )

        # 5. Map groups to role
        role = self.map_groups_to_role(groups)
        if role:
            user.enterprise_role = role

        # Store token expiry for session management
        expires_in = token_response.get("expires_in", 3600)
        session["sso_token_expiry"] = time.time() + int(expires_in)
        session["sso_access_token"] = access_token
        session["sso_refresh_token"] = token_response.get("refresh_token", "")

        from app.extensions import db

        db.session.commit()

        logger.info(
            "SSO login successful: user=%s provider=%s role=%s",
            email,
            provider_key,
            user.enterprise_role,
        )
        return user

    # ── User provisioning ────────────────────────────────────────────

    def _find_or_create_user(self, email, external_id, provider, first_name, last_name):
        """Find existing user by email/external_id or create a new one.

        SSO users get ``confirmed=True`` automatically (IdP is the authority).
        Password hash is left empty — they authenticate via SSO only.
        """
        from app.extensions import db
        from app.models.user import User

        # Try by external_id first (most reliable)
        user = None
        if external_id:
            user = User.query.filter_by(external_id=external_id, sso_provider=provider).first()

        # Fallback to email
        if not user:
            user = User.find_by_email(email)

        if user:
            # Update SSO fields on existing user
            user.external_id = external_id
            user.sso_provider = provider
            if first_name:
                user.first_name = first_name
            if last_name:
                user.last_name = last_name
        else:
            # Create new user — SSO users are auto-confirmed
            user = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                external_id=external_id,
                sso_provider=provider,
                confirmed=True,
            )
            db.session.add(user)
            logger.info("Created new SSO user: %s via %s", email, provider)

        return user

    # ── Group-to-role mapping ────────────────────────────────────────

    def _load_db_group_role_map(self):
        """Load active SSO group-to-role mappings from the database.

        Returns a dict of {group_name: role_name} for all active DB rows.
        Falls back to an empty dict if the table is not yet available.
        """
        try:
            from app.models.miscellaneous import SSOGroupRoleMapping

            rows = SSOGroupRoleMapping.query.filter_by(is_active=True).all()
            return {r.sso_group_name: r.role_name for r in rows}
        except Exception as exc:
            logger.debug("Could not load SSO mappings from DB (table ready?): %s", exc)
            return {}

    def map_groups_to_role(self, groups):
        """Map a list of IdP group names to a single platform enterprise_role.

        Checks database mappings first (PLT-033); falls back to the in-memory
        config map (``_group_role_map``) populated from DEFAULT_GROUP_ROLE_MAP /
        SSO_GROUP_ROLE_MAP config.  If multiple groups match, the
        highest-privilege role wins (platform_admin > enterprise_architect >
        arb_member > portfolio_manager > solution_architect).

        Returns the role string, or ``None`` if no groups match.
        """
        if not groups:
            return None

        # Priority order (highest first)
        priority = [
            "platform_admin",
            "enterprise_architect",
            "arb_member",
            "portfolio_manager",
            "solution_architect",
        ]

        # DB mappings take precedence; fall back to config map when DB is empty.
        db_map = self._load_db_group_role_map()
        effective_map = self._group_role_map.copy()
        if db_map:
            effective_map = db_map  # DB fully overrides config when rows exist

        matched_roles = set()
        for group in groups:
            role = effective_map.get(group)
            if role:
                matched_roles.add(role)

        if not matched_roles:
            return None

        # Return highest-priority matched role
        for role in priority:
            if role in matched_roles:
                return role

        return matched_roles.pop()

    # ── Token refresh ────────────────────────────────────────────────

    def refresh_token_if_needed(self, provider_key):
        """Check session token expiry and refresh if within 5 minutes of expiry.

        Returns True if the token was refreshed or still valid, False if refresh
        failed (caller should redirect to re-authenticate).
        """
        expiry = session.get("sso_token_expiry", 0)
        if time.time() < expiry - 300:
            return True  # Still valid, no refresh needed

        refresh_token = session.get("sso_refresh_token")
        if not refresh_token:
            return False

        provider = self.providers.get(provider_key)
        if not provider:
            return False

        try:
            meta = self._fetch_oidc_metadata(provider_key)
            token_endpoint = meta.get("token_endpoint")
            if not token_endpoint:
                return False

            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": provider["client_id"],
                "client_secret": provider["client_secret"],
            }
            resp = requests.post(token_endpoint, data=data, timeout=15)
            resp.raise_for_status()
            token_response = resp.json()

            session["sso_access_token"] = token_response.get("access_token", "")
            session["sso_token_expiry"] = time.time() + int(token_response.get("expires_in", 3600))
            if token_response.get("refresh_token"):
                session["sso_refresh_token"] = token_response["refresh_token"]

            return True
        except Exception as exc:
            logger.warning("Token refresh failed for %s: %s", provider_key, exc)
            return False


# Module-level singleton — initialized via init_app() at startup
sso_service = SSOService()
