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

from werkzeug.security import check_password_hash, generate_password_hash

from app.models.user import PASSWORD_HASH_METHOD, password_hash_is_current

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

# Domains of public mailbox providers. No single organisation owns the
# addresses in them, so none may be claimed for an organisation's sign-in.
# The one list of these domains in the product.
PUBLIC_MAILBOX_DOMAINS = frozenset({
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "yahoo.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "gmx.com",
    "mail.com",
    "yandex.com",
    "zoho.com",
})

_MSG_MISSING_SUBJECT = (
    "Your identity provider did not return the details needed to sign you in."
)
_MSG_OUTSIDE_DOMAINS = (
    "This email address is not in a domain your organisation's SSO is configured for."
)
_MSG_LINKED_ELSEWHERE = (
    "This account is linked to a different sign-in. Contact your administrator."
)
_MSG_PASSWORD_ONLY = "This account signs in with a password."
_MSG_SERVICE_ACCOUNT = "This account is a service account and cannot sign in through single sign-on."
_MSG_OTHER_ORGANISATION = (
    "This email address belongs to a different organisation's "
    "account and cannot sign in through this organisation's SSO."
)
# How the connection notice names an organisation's sign-in when its
# configuration carries no display name of its own.
_DEFAULT_PROVIDER_LABEL = "your organisation's single sign-on"
# The sso_provider value per-organisation sign-in writes, for OIDC and SAML
# alike, so a returning member matches whichever protocol the org uses.
_ORG_SSO_PROVIDER = "oidc"
# Verified against when the address has no account or no password, so every
# sign-in-form decision for a claimed domain costs one hash check and the
# response time does not reveal which kind of account an address is.
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32), method=PASSWORD_HASH_METHOD)


class SSONotConfiguredError(Exception):
    """Raised when SSO is not configured or a required env var is missing."""


class SSODomainConflictError(SSONotConfiguredError):
    """Two enabled SSO configurations claim the same email domain. Sign-in for
    that domain fails closed: neither SSO nor password is offered."""


# The DNS label under which an organisation publishes its one-time proof for a domain.
_DNS_CHALLENGE_LABEL = "_entelim-challenge"

_MSG_DOMAIN_CONFLICT = (
    "Sign-in for this email domain is unavailable because it is claimed by more "
    "than one organisation. Contact your administrator."
)


def check_idp_url(url) -> str:
    """Return ``url`` if the server may call it, else raise ``BlockedOutboundURL``.

    One check for the saved metadata URL and for every URL read out of the
    fetched discovery document (token, JWKS, userinfo). It is the shared
    outbound guard: the host must resolve only to public addresses, and outside
    development and testing the scheme must be https. A public identity
    provider (Okta, Entra, Google) passes; loopback, private, link-local and
    metadata addresses and non-ASCII hosts do not.
    """
    from app.utils.ssrf_guard import (
        internal_targets_allowed,
        validate_outbound_url,
        validate_user_supplied_url,
    )

    try:
        internal_ok = internal_targets_allowed()
    except RuntimeError:  # no application context: behave as production
        internal_ok = False
    if internal_ok:
        return validate_user_supplied_url(url)
    return validate_outbound_url(url, allowed_host_suffixes=(), require_https=True)


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

        When more than one enabled configuration lists the domain the lookup
        fails closed: :class:`SSODomainConflictError` is raised, a warning
        names the configurations involved and an audit event is recorded.
        Callers refuse sign-in for the address rather than fall back to no
        enforcement.

        Args:
            email: User's email address (e.g. "alice@acme.com").

        Returns:
            :class:`app.models.sso_config.SSOConfig` or ``None``.
        """
        if not email or "@" not in email:
            return None
        from app.models.sso_config import normalise_email_domain

        domain = normalise_email_domain(email.rsplit("@", 1)[1])
        matches = []
        try:
            from app.models.sso_config import SSOConfig
            from app.models.sso_domain_claim import SSODomainClaim

            # tenant-scoping-ok: resolving SSO config by email domain happens
            # pre-authentication (no org context yet) -- the verified domain
            # claim itself is the scoping mechanism: a configuration answers
            # for a domain only when its organisation has proved it controls it.
            owners = [
                row.organization_id
                for row in SSODomainClaim.query.filter(
                    SSODomainClaim.domain == domain, SSODomainClaim.verified_at.isnot(None)
                ).all()
            ]
            configs = (
                SSOConfig.query.filter(SSOConfig.enabled.is_(True), SSOConfig.organization_id.in_(owners)).all()
                if owners
                else []
            )
            matches = [config for config in configs if domain in config.email_domains]
        except Exception as exc:
            logger.warning("SSO config lookup failed (non-fatal): %s", exc)
            try:
                from app import db
                db.session.rollback()
            except Exception as exc:
                logger.debug("suppressed error in SSOService.get_config_for_email (app/services/sso_service.py): %s", exc)
            return None
        if len(matches) > 1:
            ids = sorted(config.id for config in matches)
            logger.warning(
                "More than one enabled SSO configuration lists the same email "
                "domain (config ids %s); sign-in for it is refused.",
                ids,
            )
            self._record_failed_login(email)
            raise SSODomainConflictError(_MSG_DOMAIN_CONFLICT)
        return matches[0] if matches else None

    @staticmethod
    def _record_failed_login(email) -> None:
        """Record a failed sign-in attempt the way the password path does. Never raises."""
        try:
            from app.security.audit import audit_logger

            audit_logger.log_authentication(success=False)
        except Exception as exc:
            logger.warning("Audit log failed on login failure: %s", exc)
        try:
            from app.services import auth_audit

            auth_audit.record_login_failure(email)
        except Exception as exc:
            logger.warning("Login failure record failed: %s", exc)

    def config_for_login(self, email: str, password: str):
        """The enabled SSOConfig the sign-in form should send *email* to, or None.

        The one decision both account blueprints' ``login()`` make before the
        password check. A configuration is returned for every address in the
        matching domain, except that an account outside the configuration's
        organisation, or a platform administrator, is sent to the password
        check when, and only when, the submitted password is correct for that
        account. Exactly one password hash check runs for every address in a
        claimed domain. None means "carry on to the password check".

        Only a password hash in the platform's current format is checked; an
        account with any other format is treated as a wrong password here and
        recovers through a password reset.
        """
        config = self.get_config_for_email(email)
        if config is None or not config.enabled:
            return None
        try:
            from app.models.user import User

            user = User.find_by_email(email)
        except Exception as exc:
            logger.warning("SSO sign-in account lookup failed (non-fatal): %s", exc)
            try:
                from app import db

                db.session.rollback()
            except Exception as rollback_exc:
                logger.debug("suppressed error in SSOService.config_for_login: %s", rollback_exc)
            return None
        stored_hash = getattr(user, "password_hash", None) if user is not None else None
        # Only a hash in the platform's one current format is verified here; any
        # other stored value (an older cost, a foreign format) is checked against
        # the dummy instead and counts as a wrong password. So every address in a
        # claimed domain costs exactly one check at one fixed cost, and no stored
        # value can make this raise. Such an account recovers by password reset,
        # which rewrites its hash in the current format.
        usable = password_hash_is_current(stored_hash)
        password_ok = check_password_hash(stored_hash if usable else _DUMMY_PASSWORD_HASH, password or "")
        password_ok = password_ok and usable
        outside_or_admin = user is not None and (
            user.organization_id != config.organization_id
            or getattr(user, "is_platform_admin", False)
        )
        if outside_or_admin:
            if password_ok:
                return None
            # A wrong password for an account this configuration does not own
            # is a failed password sign-in like any other: record it before
            # the redirect so the attempt is counted and visible.
            if password:
                self._record_failed_login(email)
        return config

    # ------------------------------------------------------------------
    # Domain claims (admin configuration)
    # ------------------------------------------------------------------

    def domains_claimed_by_other_orgs(self, org_id, domains) -> dict:
        """``{domain: organization_id}`` for each of *domains* that another
        organisation's enabled SSO configuration lists. A disabled configuration
        holds no claim."""
        wanted = set(domains)
        if not wanted:
            return {}
        from app.models.sso_config import SSOConfig
        from app.models.sso_domain_claim import SSODomainClaim

        # tenant-scoping-ok: a domain claim is checked against every other
        # organisation's verified claims; only domains and org ids are read.
        query = SSODomainClaim.query.filter(
            SSODomainClaim.domain.in_(sorted(wanted)), SSODomainClaim.verified_at.isnot(None)
        )
        if org_id is not None:
            query = query.filter(SSODomainClaim.organization_id != org_id)
        claimed = {}
        for claim in query.all():
            holder = SSOConfig.query.filter_by(organization_id=claim.organization_id).first()
            # A claim held by a disabled configuration, or one that no longer lists the
            # domain, holds nothing back.
            if holder is not None and holder.enabled and claim.domain in holder.email_domains:
                claimed.setdefault(claim.domain, claim.organization_id)
        return claimed

    # ------------------------------------------------------------------
    # Domain ownership proof (DNS TXT)
    # ------------------------------------------------------------------

    @staticmethod
    def challenge_record_name(domain: str) -> str:
        return f"{_DNS_CHALLENGE_LABEL}.{domain}"

    @staticmethod
    def _lookup_txt(name: str) -> list:
        """The TXT strings published at *name*; empty on any failure, so an unanswered,
        refused or malformed lookup never counts as proof."""
        try:
            import dns.resolver

            resolver = dns.resolver.Resolver()
            resolver.lifetime = 5.0
            answers = resolver.resolve(name, "TXT")
            return [b"".join(rdata.strings).decode("utf-8", "replace") for rdata in answers]
        except Exception as exc:
            logger.info("DNS TXT lookup for %s found nothing: %s", name, exc)
            return []

    def sync_domain_claims(self, org_id: int, domains) -> None:
        """Keep one claim row per domain the organisation lists: a new domain gets a fresh
        token and no proof; a domain no longer listed loses its claim (and so its proof).
        Does not commit."""
        import secrets as _secrets

        from app import db
        from app.models.sso_domain_claim import SSODomainClaim

        wanted = set(domains)
        existing = {c.domain: c for c in SSODomainClaim.query.filter_by(organization_id=org_id).all()}
        for domain, claim in existing.items():
            if domain not in wanted:
                db.session.delete(claim)
        for domain in sorted(wanted - set(existing)):
            db.session.add(
                SSODomainClaim(organization_id=org_id, domain=domain, token=_secrets.token_hex(16))
            )
        db.session.flush()

    def domain_claims_for(self, org_id: int) -> list:
        from app.models.sso_domain_claim import SSODomainClaim

        return SSODomainClaim.query.filter_by(organization_id=org_id).order_by(SSODomainClaim.domain).all()

    def verified_domains_for(self, org_id) -> list:
        """The domains organisation *org_id* has proved it controls."""
        from app.models.sso_domain_claim import SSODomainClaim

        if org_id is None:
            return []
        # tenant-scoping-ok: read before sign-in, keyed by the organisation named in the call.
        return [
            c.domain
            for c in SSODomainClaim.query.filter(
                SSODomainClaim.organization_id == org_id, SSODomainClaim.verified_at.isnot(None)
            ).all()
        ]

    def verify_domain(self, org_id: int, raw_domain: str):
        """Check the DNS TXT proof for one of the organisation's listed domains.

        Returns ``(True, message)`` once the token is published and the claim is recorded.
        A domain another organisation holds under an enabled configuration that still lists
        it is refused; a stale claim (its configuration disabled, or the domain unlisted)
        is released first. Commits on success.
        """
        from datetime import datetime

        from app import db
        from app.models.sso_config import SSOConfig, normalise_email_domain
        from app.models.sso_domain_claim import SSODomainClaim

        domain = normalise_email_domain(raw_domain)
        claim = SSODomainClaim.query.filter_by(organization_id=org_id, domain=domain).first()
        config = SSOConfig.query.filter_by(organization_id=org_id).first()
        if claim is None or config is None or domain not in config.email_domains:
            return False, "Add the domain to your single sign-on settings and save before verifying it."
        if claim.verified_at is not None:
            return True, f"{domain} is already verified."
        name = self.challenge_record_name(domain)
        if claim.token not in {value.strip() for value in self._lookup_txt(name)}:
            return False, f"No TXT record with the expected value was found at {name}. DNS changes can take a while to appear."
        # Two organisations proving the same domain at once must not both pass the holder
        # check: ownership changes of one domain are serialised on a transaction-scoped lock,
        # and the holder is read again once the lock is ours.
        db.session.execute(
            db.text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"sso-domain:{domain}"}
        )
        holders = self.domains_claimed_by_other_orgs(org_id, [domain])
        if domain in holders:
            return False, f"{domain} is already used by another organisation's single sign-on."
        # Whatever other verified claim remains is stale (its configuration is disabled or no
        # longer lists the domain, or the holder check above would have refused): release it.
        for other in SSODomainClaim.query.filter(
            SSODomainClaim.domain == domain,
            SSODomainClaim.organization_id != org_id,
            SSODomainClaim.verified_at.isnot(None),
        ).all():
            other.verified_at = None
        db.session.flush()
        claim.verified_at = datetime.utcnow()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return False, f"{domain} could not be verified because another organisation holds it."
        return True, f"{domain} is verified."

    def domain_claim_problem(self, org_id, raw_email_domain: Optional[str]) -> Optional[str]:
        """Why organisation *org_id* may not save *raw_email_domain*, or None.

        Refuses a public mailbox domain and a domain another organisation's
        configuration already lists. Re-saving a domain this organisation
        already lists is allowed.
        """
        from app.models.sso_config import split_email_domains

        domains = split_email_domains(raw_email_domain)
        for domain in domains:
            if domain in PUBLIC_MAILBOX_DOMAINS:
                return (
                    f"{domain} is a public email provider's domain and cannot be "
                    "used for your organisation's single sign-on."
                )
        claimed = self.domains_claimed_by_other_orgs(org_id, domains)
        for domain in domains:
            if domain in claimed:
                return (
                    f"{domain} is already used by another organisation's single "
                    "sign-on and cannot be added here."
                )
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

            check_idp_url(metadata_url)
            resp = requests.get(metadata_url, timeout=10, allow_redirects=False)
            resp.raise_for_status()
        except Exception as exc:
            # Refused, unresolvable and unreachable look the same to the
            # caller; the reason is in the log only.
            logger.warning("OIDC discovery fetch failed: %s", exc)
            raise SSONotConfiguredError(
                "That address is not allowed or could not be reached"
            ) from exc
        try:
            doc = resp.json()
            if not isinstance(doc, dict):
                raise ValueError("not an object")
        except Exception as exc:
            logger.warning("OIDC discovery document invalid: %s", exc)
            raise SSONotConfiguredError(
                "The identity provider did not return a valid configuration document"
            ) from exc
        self._check_issuer_bound_to_address(metadata_url, doc)
        self._discovery_cache[metadata_url] = doc
        return doc

    @staticmethod
    def _check_issuer_bound_to_address(metadata_url: str, doc: dict) -> None:
        """The issuer a discovery document names must be the issuer the address it was
        fetched from implies (OpenID Connect Discovery 4.3): the address minus
        ``/.well-known/openid-configuration``. A document that names another issuer, or an
        address that is not a provider's well-known address, is refused, so a configuration
        can only ever trust tokens from the provider whose address the administrator entered
        (a shared multi-tenant endpoint whose issuer is a template never matches)."""
        suffix = "/.well-known/openid-configuration"
        issuer = str(doc.get("issuer") or "").strip()
        base = (metadata_url or "").split("?", 1)[0].split("#", 1)[0]
        if not base.endswith(suffix) or not issuer or issuer.rstrip("/") != base[: -len(suffix)].rstrip("/"):
            logger.warning("OIDC discovery issuer %r is not bound to address %r", issuer, metadata_url)
            raise SSONotConfiguredError(
                "The identity provider's configuration document names a different issuer than its address"
            )

    def _get_jwks(self, jwks_uri: str, *, force: bool = False) -> dict:
        """Return the IdP's key set, from the cache while it is fresh."""
        import time

        import requests

        cached = self._jwks_cache.get(jwks_uri)
        if cached is not None and not force and time.monotonic() - cached[0] < self._JWKS_TTL_SECONDS:
            return cached[1]
        try:
            check_idp_url(jwks_uri)
            jwks_resp = requests.get(jwks_uri, timeout=10, allow_redirects=False)
            jwks_resp.raise_for_status()
            jwks = jwks_resp.json()
        except Exception as exc:
            logger.warning("Failed to fetch JWKS: %s", exc)
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

        # The token endpoint comes from a fetched document. Check it before the
        # client secret is put in a request; a refused host never receives it.
        try:
            check_idp_url(token_endpoint)
        except Exception as exc:
            logger.warning("Token endpoint refused: %s", exc)
            raise SSONotConfiguredError("Token exchange failed") from exc

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
                allow_redirects=False,
            )
            resp.raise_for_status()
            token_data = resp.json()
        except Exception as exc:
            logger.warning("Token exchange failed: %s", exc)
            raise SSONotConfiguredError("Token exchange failed") from exc

        # The signed id_token is the only source of identity. It is required and verified
        # (signature, issuer, audience, expiry, nonce); an unsigned userinfo response is never
        # preferred over it. Userinfo may add claims the id_token lacks, but only for the same
        # subject and never in place of a claim the id_token carries.
        id_token = token_data.get("id_token", "")
        if not id_token:
            raise SSONotConfiguredError("id_token verification failed: the identity provider sent no id_token")
        try:
            claims = self._verify_id_token(
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

        access_token = token_data.get("access_token", "")
        if userinfo_endpoint and access_token:
            userinfo: dict = {}
            try:
                check_idp_url(userinfo_endpoint)
                uinfo_resp = requests.get(
                    userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                    allow_redirects=False,
                )
                if uinfo_resp.status_code == 200:
                    parsed = uinfo_resp.json()
                    userinfo = parsed if isinstance(parsed, dict) else {}
            except Exception as exc:
                logger.warning("Userinfo fetch failed (using the id_token alone): %s", exc)
            if userinfo:
                if str(userinfo.get("sub", "")) != str(claims.get("sub", "")):
                    logger.warning("Userinfo subject does not match the id_token subject")
                    raise SSONotConfiguredError("id_token verification failed: userinfo is for a different subject")
                for key, value in userinfo.items():
                    claims.setdefault(key, value)

        return claims

    # ------------------------------------------------------------------
    # User provisioning
    # ------------------------------------------------------------------

    def provision_user(self, org, userinfo: dict):
        """Resolve the account an organisation's IdP has signed in, creating
        it when the address is new. Shared by the OIDC and SAML callbacks.

        In order:

        - The claims must carry a subject (``sub``); the address is never
          used as the identity key.
        - A member of *org* already linked to this subject is returned
          (names refreshed) without looking the address up. A platform
          administrator is refused.
        - Otherwise the address must be in one of the domains *org*'s SSO
          configuration lists.
        - The address is looked up case-insensitively. An account in another
          organisation, a platform administrator, and an account already
          linked to a different sign-in are refused, unchanged.
        - An unlinked member of *org* is linked to this subject. If it
          already signed in with a password, the account's own address is
          told that a sign-in method was connected.
        - An unknown address is created in *org*.

        Any account this returns is confirmed: the organisation's identity
        provider has asserted its address in one of the configured domains,
        the same evidence a new account is created on.

        Args:
            org: :class:`app.models.organization.Organization` instance.
            userinfo: Dict of OIDC claims (``email``, ``sub``, ``name``, etc.).

        Returns:
            :class:`app.models.user.User` instance (persisted to DB).

        Raises:
            :class:`SSONotConfiguredError` when the sign-in is refused;
            nothing has been created or changed.
        """
        from app import db
        from app.jobs.tenant_safe_job import platform_scope
        from app.models.user import User

        email = (userinfo.get("email") or "").strip().lower()
        if not email:
            raise SSONotConfiguredError("IdP did not provide an email claim")

        raw_sub = userinfo.get("sub")
        if raw_sub is None or not str(raw_sub).strip():
            raise SSONotConfiguredError(_MSG_MISSING_SUBJECT)
        sub = str(raw_sub)

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

        def _refresh_names(user) -> None:
            if given_name:
                user.first_name = given_name
            if family_name:
                user.last_name = family_name

        config = getattr(org, "sso_config", None) if org is not None else None
        verified_domains = set(self.verified_domains_for(org.id)) if config is not None and org is not None else set()
        configured_domains = (
            [d for d in config.email_domains if d in verified_domains]
            if config is not None and org is not None
            else []
        )
        domain = email.rsplit("@", 1)[1] if "@" in email else ""
        send_connection_notice = False

        # Nobody is signed in yet, so there is no session organisation.
        # users, organizations, sso_configs and subscriptions carry no
        # row-level security policy, so the checks below are the only thing
        # that decides this sign-in. The platform scope changes nothing for
        # these tables today; it keeps the lookups working if one of them is
        # fenced later.
        with platform_scope("single sign-on: resolve the account for a person who is not signed in yet"):
            linked = None
            if org is not None:
                # tenant-scoping-ok: the organisation is part of the key
                linked = User.query.filter_by(
                    organization_id=org.id,
                    sso_provider=_ORG_SSO_PROVIDER,
                    external_id=sub,
                ).first()
            if linked is not None:
                if linked.is_platform_admin:
                    raise SSONotConfiguredError(_MSG_PASSWORD_ONLY)
                if getattr(linked, "is_service_user", False):
                    raise SSONotConfiguredError(_MSG_SERVICE_ACCOUNT)
                _refresh_names(linked)
                if not linked.confirmed:
                    linked.confirmed = True
                db.session.commit()
                return linked

            if org is None or not domain or domain not in configured_domains:
                raise SSONotConfiguredError(_MSG_OUTSIDE_DOMAINS)

            user = User.find_by_email(email)
            if user is not None and user.organization_id != org.id:
                # email_domain on SSOConfig is operator-entered with no
                # ownership verification, so an address can reach this
                # organisation's sign-in while its account belongs to another
                # organisation. Refuse rather than attach to that account or
                # create a second one under the same address.
                raise SSONotConfiguredError(_MSG_OTHER_ORGANISATION)

            if user is None:
                user = User(
                    email=email,
                    first_name=given_name,
                    last_name=family_name,
                    confirmed=True,
                    sso_provider=_ORG_SSO_PROVIDER,
                    external_id=sub,
                )
                user.organization_id = org.id
                db.session.add(user)
            else:
                if user.is_platform_admin:
                    raise SSONotConfiguredError(_MSG_PASSWORD_ONLY)
                if getattr(user, "is_service_user", False):
                    raise SSONotConfiguredError(_MSG_SERVICE_ACCOUNT)
                if user.external_id and (
                    user.external_id != sub
                    or (user.sso_provider or _ORG_SSO_PROVIDER) != _ORG_SSO_PROVIDER
                ):
                    raise SSONotConfiguredError(_MSG_LINKED_ELSEWHERE)
                send_connection_notice = (
                    not user.external_id and user.password_hash is not None
                )
                _refresh_names(user)
                user.sso_provider = _ORG_SSO_PROVIDER
                user.external_id = sub
                user.confirmed = True

            db.session.commit()

        if send_connection_notice:
            from app.modules.account.services.account_service import AccountService

            AccountService.notify_sign_in_method_connected(
                user, self._provider_label(config)
            )
        return user

    def reset_member_sign_in_link(self, org_id: int, user_id: int, *, actor_id: Optional[int], reason: str):
        """Clear a member's single sign-on link so the organisation's identity
        provider links the account again at the next sign-in.

        The one way a sign-in link is cleared, used by the Team page and by
        the operator runbook. Only a member of organisation *org_id* is
        reached, never a platform administrator. A member with no link is
        returned unchanged. A reset ends the account's existing sessions and
        connected assistants. Each reset writes an ``sso_link_reset`` audit
        event that carries neither the old subject nor the address; a failed
        audit write is logged and never undoes the reset.

        The account stays activated: a reset never returns it to the state
        of an invitation nobody has taken up.

        Raises:
            LookupError: no such user in organisation *org_id*.
            PermissionError: the user is a platform administrator.
        """
        from app import db
        from app.models.user import User

        user = db.session.get(User, user_id)
        if user is None or user.organization_id != org_id:
            raise LookupError("No such member in this organisation.")
        if user.is_platform_admin:
            raise PermissionError("A platform administrator's sign-in cannot be reset here.")
        if not user.external_id:
            return user

        # End every session the account holds first. A link that is cleared while the old
        # sign-in's sessions stay open leaves exactly the access the reset is meant to
        # remove; ending them first means a failure here leaves the link in place, so the
        # reset can simply be tried again.
        from app.services import session_registry

        session_registry.revoke_all_for_user(user.id, "sso_link_reset")

        previous_provider = user.sso_provider
        user.external_id = None
        user.sso_provider = None
        # A linked account has been signed in by an identity provider, so it
        # is in use. Clearing the link must never make it look like an
        # invitation nobody has taken up (invitation_service.is_unactivated),
        # whatever wrote the link: this organisation's SSO, a platform
        # provider, or a link made before accounts were confirmed on sign-in.
        user.confirmed = True
        db.session.commit()

        try:
            from app.security.audit import AuditEventSeverity, AuditEventType, audit_logger

            audit_logger.log_event(
                AuditEventType.SECURITY_EVENT,
                AuditEventSeverity.MEDIUM,
                "sso_link_reset",
                resource_type="user",
                resource_id=str(user.id),
                details={
                    "organization_id": org_id,
                    "actor_id": actor_id,
                    "reason": reason,
                    "previous_provider": previous_provider,
                },
            )
        except Exception as exc:
            logger.warning("Audit write for a sign-in link reset failed (non-fatal): %s", exc)
        return user

    @staticmethod
    def _provider_label(config) -> str:
        """How the connection notice names an organisation's sign-in."""
        label = getattr(config, "display_name", None) if config is not None else None
        if isinstance(label, str) and label.strip():
            return label.strip()
        return _DEFAULT_PROVIDER_LABEL

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
