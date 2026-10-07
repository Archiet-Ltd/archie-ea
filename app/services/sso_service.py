"""
SSOService — per-organisation SSO federation (COM-005).

Handles OIDC (OpenID Connect) flow using authlib and provides a stub for
SAML 2.0. Each organisation has exactly one SSOConfig row; login is
intercepted when the user's email domain matches a configured domain.

Usage::

    svc = SSOService()
    config = svc.get_config_for_email("alice@acme.com")
    if config and config.enabled:
        result = svc.initiate_oidc_flow(config, redirect_uri)
        # redirect to result['redirect_url']
"""

import logging
import secrets
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# Signing algorithms this service will ever accept for an id_token. "none"
# and every HMAC ("HS*") algorithm are deliberately absent: HS* keys the
# signature with the IdP's public key material, which every client already
# holds, so accepting it here would let anyone forge a token.
_ID_TOKEN_ALGORITHMS = (
    "RS256", "RS384", "RS512",
    "PS256", "PS384", "PS512",
    "ES256", "ES384", "ES512",
)
# Clock-skew allowance applied to exp, iat and nbf.
_ID_TOKEN_LEEWAY_SECONDS = 120


class SSONotConfiguredError(Exception):
    """Raised when SSO is not configured or a required env var is missing."""


class SSOService:
    """Per-organisation SSO service (OIDC + SAML stub)."""

    # Simple in-process cache for OIDC discovery documents (URL → dict).
    _discovery_cache: dict = {}

    # In-process cache for JWKS documents (URI -> (fetched_at, jwks)). A signing
    # key that is not in the cached set is refetched once, so a key rotation at
    # the IdP does not lock users out for the length of the TTL.
    _jwks_cache: dict = {}
    _JWKS_TTL_SECONDS = 300

    # ------------------------------------------------------------------
    # Email-domain lookup
    # ------------------------------------------------------------------

    def get_config_for_email(self, email: str):
        """Return the enabled SSOConfig whose domain matches *email*, or None.

        Args:
            email: User's email address (e.g. "alice@acme.com").

        Returns:
            :class:`app.models.sso_config.SSOConfig` or ``None``.
        """
        if not email or "@" not in email:
            return None
        domain = email.split("@", 1)[1].lower()
        try:
            from app.models.sso_config import SSOConfig

            # tenant-scoping-ok: resolving SSO config by email domain happens
            # pre-authentication (no org context yet) -- the domain match
            # itself is the scoping mechanism.
            configs = SSOConfig.query.filter_by(enabled=True).all()
            for config in configs:
                if domain in config.email_domains:
                    return config
        except Exception as exc:
            logger.warning("SSO config lookup failed (non-fatal): %s", exc)
            try:
                from app import db
                db.session.rollback()
            except Exception as exc:
                logger.debug("suppressed error in SSOService.get_config_for_email (app/services/sso_service.py): %s", exc)
        return None

    # ------------------------------------------------------------------
    # OIDC flow
    # ------------------------------------------------------------------

    def _fetch_oidc_discovery(self, metadata_url: str) -> dict:
        """Fetch (and cache) the OIDC discovery document.

        Args:
            metadata_url: The ``/.well-known/openid-configuration`` URL.

        Returns:
            Parsed JSON dict.

        Raises:
            :class:`SSONotConfiguredError` on network / parsing failure.
        """
        if metadata_url in self._discovery_cache:
            return self._discovery_cache[metadata_url]
        try:
            import requests

            resp = requests.get(metadata_url, timeout=10)
            resp.raise_for_status()
            doc = resp.json()
            self._discovery_cache[metadata_url] = doc
            return doc
        except Exception as exc:
            raise SSONotConfiguredError(
                f"Failed to fetch OIDC discovery document from {metadata_url}: {exc}"
            ) from exc

    def _get_jwks(self, jwks_uri: str, *, force: bool = False) -> dict:
        """Return the IdP's key set, from the cache while it is fresh."""
        import time

        import requests

        cached = self._jwks_cache.get(jwks_uri)
        if cached is not None and not force and time.monotonic() - cached[0] < self._JWKS_TTL_SECONDS:
            return cached[1]
        try:
            jwks_resp = requests.get(jwks_uri, timeout=10)
            jwks_resp.raise_for_status()
            jwks = jwks_resp.json()
        except Exception as exc:
            logger.warning("Failed to fetch JWKS from %s: %s", jwks_uri, exc)
            raise SSONotConfiguredError(
                "id_token verification failed: the identity provider's signing keys are unavailable"
            ) from exc
        self._jwks_cache[jwks_uri] = (time.monotonic(), jwks)
        return jwks

    def _verify_id_token(
        self,
        id_token: str,
        config,
        discovery: dict,
        expected_nonce: str,
        access_token: Optional[str] = None,
    ) -> dict:
        """Verify id_token signature, issuer, audience, expiry and nonce.

        Uses the IdP's JWKS (from the OIDC discovery document) to verify the
        JWT signature, decoding with joserfc and validating the claims with
        authlib's own ``CodeIDToken`` claim class: ``iss``, ``sub``, ``aud``,
        ``exp`` and ``iat`` are required; ``aud`` must include the configured
        client_id; ``azp`` is checked when present and required when ``aud``
        holds more than one value; ``at_hash`` is checked when the token
        carries one and an access token was passed in; and the ``nonce``
        claim must equal the nonce generated for this login and kept in the
        caller's session, so a token captured from another login cannot be
        replayed here. Only RSA/EC/RSA-PSS signatures are accepted, pinned to
        whichever of them the discovery document advertises.

        Args:
            id_token: The ID token string from the token response.
            config: :class:`app.models.sso_config.SSOConfig` instance.
            discovery: OIDC discovery document dict (must contain ``jwks_uri``
                and ``issuer``).
            expected_nonce: The nonce sent in this login's authorization
                request. Required: a missing value refuses the token.
            access_token: The access token issued alongside this id_token, if
                any, so an ``at_hash`` claim can be checked against it.

        Returns:
            Dict of decoded JWT claims.

        Raises:
            :class:`SSONotConfiguredError` on any verification failure.
        """
        if not config.client_id:
            raise SSONotConfiguredError("SSO config has no client_id")
        if not expected_nonce:
            raise SSONotConfiguredError(
                "id_token verification failed: no nonce was issued for this login"
            )

        jwks_uri = discovery.get("jwks_uri")
        if not jwks_uri:
            raise SSONotConfiguredError(
                "OIDC discovery document missing 'jwks_uri'"
            )

        issuer = discovery.get("issuer", "")
        if not issuer:
            raise SSONotConfiguredError(
                "OIDC discovery document missing 'issuer'"
            )

        advertised_algorithms = (
            discovery.get("id_token_signing_alg_values_supported") or ["RS256"]
        )
        allowed_algorithms = [
            alg for alg in advertised_algorithms if alg in _ID_TOKEN_ALGORITHMS
        ]
        if not allowed_algorithms:
            raise SSONotConfiguredError(
                "id_token verification failed: the identity provider does not "
                "advertise an acceptable signing algorithm"
            )

        claims_options = {
            "iss": {"essential": True, "value": issuer},
            "aud": {"essential": True, "value": config.client_id},
        }
        claims_params = {
            "nonce": expected_nonce,
            "client_id": config.client_id,
            "access_token": access_token,
        }

        from authlib.oidc.core import CodeIDToken
        from joserfc import jwt
        from joserfc.errors import BadSignatureError, InvalidKeyIdError
        from joserfc.jwk import KeySet
        from joserfc.jws import JWSRegistry

        def _decode(jwks: dict) -> dict:
            key_set = KeySet.import_key_set(jwks)
            registry = JWSRegistry(algorithms=allowed_algorithms, strict_check_header=False)
            token = jwt.decode(id_token, key_set, registry=registry)
            claims = CodeIDToken(token.claims, token.header, claims_options, claims_params)
            claims.validate(leeway=_ID_TOKEN_LEEWAY_SECONDS)
            return dict(claims)

        jwks = self._get_jwks(jwks_uri)
        try:
            return _decode(jwks)
        except (InvalidKeyIdError, BadSignatureError) as first_exc:
            # The signing key may have rotated since the key set was cached:
            # refetch once and try again before refusing the token. Any other
            # failure (claims, algorithm, malformed token) refuses at once,
            # below, with no refetch.
            try:
                fresh = self._get_jwks(jwks_uri, force=True)
            except Exception as fetch_exc:
                logger.warning("id_token key refetch failed: %s", fetch_exc)
                raise SSONotConfiguredError("id_token verification failed") from first_exc
            if fresh == jwks:
                raise SSONotConfiguredError("id_token verification failed") from first_exc
            try:
                return _decode(fresh)
            except Exception as second_exc:
                logger.warning("id_token verification failed: %s", second_exc)
                raise SSONotConfiguredError("id_token verification failed") from second_exc
        except Exception as exc:
            logger.warning("id_token verification failed: %s", exc)
            raise SSONotConfiguredError("id_token verification failed") from exc

    def initiate_oidc_flow(self, config, redirect_uri: str) -> dict:
        """Build the OIDC authorization URL.

        Fetches the IdP's discovery document to obtain ``authorization_endpoint``,
        then constructs the redirect URL with PKCE-style ``state`` token.

        Args:
            config: :class:`app.models.sso_config.SSOConfig` instance.
            redirect_uri: The callback URL registered with the IdP.

        Returns:
            Dict with keys ``redirect_url``, ``state`` and ``nonce`` (str). The
            caller must keep ``state`` and ``nonce`` in the login session and
            hand the nonce back to :meth:`handle_oidc_callback`.

        Raises:
            :class:`SSONotConfiguredError` if config is None or setup is incomplete.
        """
        if config is None:
            raise SSONotConfiguredError("No SSO config provided")
        if not config.client_id:
            raise SSONotConfiguredError("SSO config has no client_id")
        if not config.idp_metadata_url:
            raise SSONotConfiguredError("SSO config has no idp_metadata_url")

        discovery = self._fetch_oidc_discovery(config.idp_metadata_url)
        auth_endpoint = discovery.get("authorization_endpoint")
        if not auth_endpoint:
            raise SSONotConfiguredError(
                "OIDC discovery document missing 'authorization_endpoint'"
            )

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
        }
        redirect_url = f"{auth_endpoint}?{urlencode(params)}"
        return {"redirect_url": redirect_url, "state": state, "nonce": nonce}

    def handle_oidc_callback(
        self,
        config,
        code: str,
        state: str,
        redirect_uri: str,
        expected_nonce: Optional[str] = None,
    ) -> dict:
        """Exchange the authorization code for tokens and return user info.

        Fetches the IdP's ``token_endpoint`` from the discovery document,
        exchanges the code, then calls ``userinfo_endpoint`` for claims.

        Args:
            config: :class:`app.models.sso_config.SSOConfig` instance.
            code: The authorization code from the IdP callback.
            state: The state parameter (caller should validate before calling).
            redirect_uri: Must match the value used in :meth:`initiate_oidc_flow`.
            expected_nonce: The nonce :meth:`initiate_oidc_flow` returned for
                this login. Needed whenever claims come from the id_token
                (the userinfo endpoint does not use it).

        Returns:
            Dict of user-info claims (``email``, ``sub``, ``name``, etc.).

        Raises:
            :class:`SSONotConfiguredError` if config is None or network errors occur.
        """
        if config is None:
            raise SSONotConfiguredError("No SSO config provided")
        if not config.client_id:
            raise SSONotConfiguredError("SSO config has no client_id")
        if not config.idp_metadata_url:
            raise SSONotConfiguredError("SSO config has no idp_metadata_url")

        import requests

        discovery = self._fetch_oidc_discovery(config.idp_metadata_url)
        token_endpoint = discovery.get("token_endpoint")
        userinfo_endpoint = discovery.get("userinfo_endpoint", "")
        if not token_endpoint:
            raise SSONotConfiguredError(
                "OIDC discovery document missing 'token_endpoint'"
            )

        client_secret = config.client_secret
        try:
            resp = requests.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": config.client_id,
                    "client_secret": client_secret or "",
                },
                timeout=15,
            )
            resp.raise_for_status()
            token_data = resp.json()
        except Exception as exc:
            raise SSONotConfiguredError(
                f"Token exchange failed: {exc}"
            ) from exc

        # Prefer userinfo endpoint for claims; fall back to id_token payload
        userinfo: dict = {}
        access_token = token_data.get("access_token", "")
        if userinfo_endpoint and access_token:
            try:
                uinfo_resp = requests.get(
                    userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if uinfo_resp.status_code == 200:
                    userinfo = uinfo_resp.json()
            except Exception as exc:
                logger.warning("Userinfo fetch failed (falling back to id_token): %s", exc)

        # Fall back: verify id_token signature and extract claims
        if not userinfo:
            id_token = token_data.get("id_token", "")
            if id_token:
                try:
                    userinfo = self._verify_id_token(
                        id_token,
                        config,
                        discovery,
                        expected_nonce or "",
                        access_token=token_data.get("access_token") or None,
                    )
                except SSONotConfiguredError:
                    raise
                except Exception as exc:
                    logger.warning("id_token verification failed: %s", exc)
                    raise SSONotConfiguredError("id_token verification failed") from exc

        return userinfo

    # ------------------------------------------------------------------
    # User provisioning
    # ------------------------------------------------------------------

    def provision_user(self, org, userinfo: dict):
        """Create or update a User record from IdP-supplied claims.

        Looks up by email first. Creates a confirmed SSO user if not found.
        Always syncs ``first_name``, ``last_name``, ``sso_provider``, and
        ``external_id`` from the IdP claims.

        Args:
            org: :class:`app.models.organization.Organization` instance.
            userinfo: Dict of OIDC claims (``email``, ``sub``, ``name``, etc.).

        Returns:
            :class:`app.models.user.User` instance (persisted to DB).

        Raises:
            :class:`SSONotConfiguredError` if email is missing from claims.
        """
        email = (userinfo.get("email") or "").strip().lower()
        if not email:
            raise SSONotConfiguredError("IdP did not provide an email claim")

        # Resolve name from claims, honouring attribute_mapping overrides
        attr_map: dict = {}
        if org and getattr(org, "sso_config", None):
            attr_map = org.sso_config.attribute_mapping or {}

        def _claim(key: str, fallback: str = "") -> str:
            mapped_key = attr_map.get(key, key)
            return (userinfo.get(mapped_key) or userinfo.get(fallback) or "").strip()

        given_name = _claim("given_name")
        family_name = _claim("family_name")
        full_name = userinfo.get("name", "").strip()
        if full_name and not given_name:
            parts = full_name.split(" ", 1)
            given_name = parts[0]
            family_name = parts[1] if len(parts) > 1 else ""

        sub = userinfo.get("sub", email)

        # The lookup, the cross-organisation refusal and the creation live in
        # the one provisioning service (R1-B26), shared with SCIM.
        from app.services import provisioning_service

        try:
            user, _created, _changed = provisioning_service.create_or_update_user(
                org.id if org else None,
                {
                    "email": email,
                    "first_name": given_name or None,
                    "last_name": family_name or None,
                    "external_id": sub or None,
                    "sso_provider": "oidc",
                },
                source=provisioning_service.SOURCE_SSO,
            )
        except provisioning_service.EmailInOtherOrganisation as exc:
            # email_domain on SSOConfig is operator-entered with no ownership
            # verification, so one organisation's admin can configure it to
            # claim another organisation's real domain; a login through that
            # config must be refused, never attached to the other
            # organisation's existing user.
            raise SSONotConfiguredError(
                "This email address belongs to a different organisation's "
                "account and cannot sign in through this organisation's SSO."
            ) from exc
        return user

    # ------------------------------------------------------------------
    # SAML stub
    # ------------------------------------------------------------------

    def initiate_saml_flow(self, config, base_url: str) -> dict:
        """Build a SAML 2.0 AuthnRequest redirect URL (R1-B12 PR 2, TB-0141).

        Delegates to :class:`app.services.saml_service.SAMLService`, which
        verifies responses with ``signxml`` against the operator-entered
        IdP certificate rather than the ``python3-saml``/``xmlsec1`` path
        this used to stub out.

        Args:
            config: :class:`app.models.sso_config.SSOConfig` instance.
            base_url: This platform's own base URL.

        Returns:
            Dict with keys ``redirect_url`` and ``request_id`` (str). The
            caller must keep ``request_id`` in the login session and hand
            it to :meth:`handle_saml_callback`.

        Raises:
            :class:`SSONotConfiguredError` if config is None or incomplete.
        """
        from app.services.saml_service import SAMLService

        redirect_url, request_id = SAMLService().build_authn_request(config, base_url)
        return {"redirect_url": redirect_url, "request_id": request_id}

    def handle_saml_callback(
        self,
        config,
        saml_response_b64: str,
        base_url: str,
        expected_request_id: Optional[str] = None,
    ) -> dict:
        """Verify a SAML Response and return its asserted claims.

        Delegates to :class:`app.services.saml_service.SAMLService`.

        Raises:
            :class:`SSONotConfiguredError` (specifically
            :class:`app.services.saml_service.SAMLVerificationError`) on any
            verification failure.
        """
        from app.services.saml_service import SAMLService

        return SAMLService().verify_and_parse_response(
            config, saml_response_b64, base_url, expected_request_id
        )
