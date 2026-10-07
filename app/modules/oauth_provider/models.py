"""OAuth 2.1 models: client registration, authorization codes, and access tokens.

Three tables — no second auth system. The token this authorization server
issues resolves to the same ``User`` the session cookie already resolves to,
through the bearer identity loader in ``app.modules.oauth_provider.identity``.

Secrets are never stored in the clear. ``access_token``, ``refresh_token`` and
``code`` hold the SHA-256 hex digest of the value actually handed to the
client; the plaintext exists only for the instant it is generated and
returned. A lookup hashes the caller-supplied value and compares hashes, so a
read of this table (a backup, a stray log, a compromised replica) never
yields a credential someone could replay.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timezone

from app.extensions import db
from app.models.mixins.core import TenantMixin, _default_org_id


def _new_client_id(prefix: str = "cl") -> str:
    """A client identifier with enough entropy to resist enumeration."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_secret(value: str) -> str:
    """The SHA-256 hex digest this module stores in place of a plaintext secret."""
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class OAuthAuthorizationCode(TenantMixin, db.Model):
    """A single-use authorization code, stored in the database so every
    worker process in a multi-worker deployment can redeem codes issued by
    any other worker.

    ``code`` holds ``hash_secret(<plaintext code>)``, not the plaintext —
    the plaintext is returned to the caller once, by :meth:`issue`, and
    never persisted.
    """

    __tablename__ = "oauth_authorization_codes"

    code = db.Column(db.String(256), primary_key=True)
    client_id = db.Column(db.String(128), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    redirect_uri = db.Column(db.Text, nullable=False)
    scope = db.Column(db.String(256), nullable=True)
    resource = db.Column(db.String(512), nullable=True)
    code_challenge = db.Column(db.String(256), nullable=False)
    code_challenge_method = db.Column(db.String(16), nullable=False, default="S256")
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # TenantMixin's column is non-nullable by default; this table is an
    # ADD-only rollout onto an already-existing table (reconcile-schema can
    # only add nullable columns), so the organization is recorded from the
    # user at issue time but earlier/legacy rows are tolerated NULL.
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        default=_default_org_id,
    )

    @classmethod
    def issue(cls, *, client_id: str, user_id: int, redirect_uri: str,
              scope: str | None = None, resource: str | None = None,
              code_challenge: str, code_challenge_method: str = "S256",
              lifetime_seconds: int = 60,
              organization_id: int | None = None) -> tuple[str, "OAuthAuthorizationCode"]:
        """Create and persist a new authorization code.

        Returns ``(plaintext_code, row)``. The plaintext is the value to put
        in the redirect; the row's ``code`` column holds its hash.
        """
        raw_code = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        auth_code = cls(
            code=hash_secret(raw_code),
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            scope=scope,
            resource=resource,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            expires_at=datetime.fromtimestamp(time.time() + lifetime_seconds, tz=timezone.utc),
            created_at=now,
            organization_id=organization_id,
        )
        db.session.add(auth_code)
        # Commit, not flush: the authorization-code grant is always redeemed
        # from a SEPARATE HTTP request (POST /oauth/token) than the one that
        # issues it (POST /oauth/authorize). Flask's teardown_appcontext hook
        # (app/__init__.py's shutdown_session) does not commit a successful
        # request -- it only rolls back on exception, otherwise just removes
        # the session -- so a flush-only write here is silently discarded the
        # instant this request ends, before the token request ever has a
        # chance to see it. Confirmed by reproduction, not assumed: a
        # flush-only write here does not survive a real teardown_appcontext
        # cycle under any of this codebase's engine configs (pool_reset_on_
        # return made no difference), while an explicit commit does.
        db.session.commit()
        return raw_code, auth_code

    @classmethod
    def consume(cls, raw_code: str) -> OAuthAuthorizationCode | None:
        """Atomically look up and delete a non-expired authorization code.

        *raw_code* is the plaintext the client presents; it is hashed before
        lookup. Returns the code payload if found and not expired, or None.
        The code is deleted in the same transaction so it cannot be reused.
        Uses SELECT ... FOR UPDATE to prevent concurrent redemption of the
        same code.
        """
        now = datetime.now(timezone.utc)
        auth_code = (
            cls.query.filter_by(code=hash_secret(raw_code)).with_for_update().first()
        )
        if auth_code is None:
            return None
        if auth_code.expires_at.tzinfo is None:
            expires_at = auth_code.expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = auth_code.expires_at
        if now >= expires_at:
            db.session.delete(auth_code)
            db.session.flush()
            return None
        # Delete the row; the Python object remains readable for the caller
        db.session.delete(auth_code)
        db.session.flush()
        return auth_code

    @classmethod
    def clean_expired(cls) -> int:
        """Delete all expired authorization codes. Returns count removed."""
        now = datetime.now(timezone.utc)
        deleted = cls.query.filter(cls.expires_at < now).delete(synchronize_session=False)
        if deleted:
            db.session.flush()
        return deleted


class OAuthClient(db.Model):
    """A registered OAuth 2.1 client (dynamic registration, RFC 7591).

    Platform-level, not tenant-owned: one client (an AI assistant, an IDE
    plugin) is used by people across many organisations, so this table is
    read with ``tenant-scoping-ok`` rather than ``TenantMixin`` — which
    organisation issued a token through the client is recorded on the token,
    not the client.
    """

    __tablename__ = "oauth_clients"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(128), unique=True, nullable=False, index=True)
    client_secret_hash = db.Column(db.String(256), nullable=True)
    client_name = db.Column(db.String(256), nullable=True)
    redirect_uris = db.Column(db.Text, nullable=True)  # space-separated
    grant_types = db.Column(db.String(256), nullable=True)  # space-separated
    token_endpoint_auth_method = db.Column(db.String(32), nullable=True, default="none")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    @classmethod
    def register(cls, *, client_name: str | None = None,
                 redirect_uris: str | None = None) -> OAuthClient:
        """Register a new client dynamically (RFC 7591)."""
        client = cls(
            client_id=_new_client_id("cl"),
            client_name=client_name,
            redirect_uris=redirect_uris,
            grant_types="authorization_code refresh_token",
            token_endpoint_auth_method="none",
        )
        db.session.add(client)
        # Commit, not flush -- see the matching note on
        # OAuthAuthorizationCode.issue above: registration happens in its own
        # request, and every subsequent use of this client (the consent
        # screen, the token exchange) is a later, separate request that must
        # still be able to find it.
        db.session.commit()
        return client

    @property
    def redirect_uri_list(self) -> list[str]:
        if not self.redirect_uris:
            return []
        return self.redirect_uris.split()


class OAuthToken(db.Model):
    """An issued access token (and optional refresh token).

    ``access_token`` and ``refresh_token`` hold ``hash_secret(<plaintext>)``,
    not the plaintext. Resolves to a ``User`` through the bearer identity
    loader (``app.modules.oauth_provider.identity``), which never calls
    ``login_user`` and never writes the session.
    """

    __tablename__ = "oauth_tokens"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(128), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    access_token = db.Column(db.String(256), unique=True, nullable=False, index=True)
    refresh_token = db.Column(db.String(256), unique=True, nullable=True, index=True)
    scope = db.Column(db.String(256), nullable=True)
    resource = db.Column(db.String(512), nullable=True)
    grant_type = db.Column(db.String(32), nullable=True)
    issued_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)
    refresh_expires_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)

    # ADD-only rollout onto an already-existing table (see
    # OAuthAuthorizationCode above for the same reasoning) — nullable
    # override of TenantMixin's normally non-nullable column.
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        default=_default_org_id,
    )

    user = db.relationship("User", backref="oauth_tokens", lazy="select")

    @classmethod
    def issue(cls, *, client_id: str, user_id: int, scope: str | None = None,
              resource: str | None = None, expires_in: int = 3600,
              grant_type: str = "authorization_code",
              organization_id: int | None = None,
              refresh_expires_in: int | None = None) -> tuple[str, str, "OAuthToken"]:
        """Issue a new access token (and refresh token).

        Returns ``(plaintext_access_token, plaintext_refresh_token, row)``.
        """
        now = datetime.now(timezone.utc)
        raw_access = _new_client_id("at")
        raw_refresh = _new_client_id("rt")
        refresh_expires_at = None
        if refresh_expires_in is not None:
            refresh_expires_at = datetime.fromtimestamp(time.time() + refresh_expires_in, tz=timezone.utc)
        token = cls(
            client_id=client_id,
            user_id=user_id,
            access_token=hash_secret(raw_access),
            refresh_token=hash_secret(raw_refresh),
            scope=scope,
            resource=resource,
            grant_type=grant_type,
            organization_id=organization_id,
            issued_at=now,
            expires_at=datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc),
            refresh_expires_at=refresh_expires_at,
        )
        db.session.add(token)
        # Commit, not flush -- see the matching note on
        # OAuthAuthorizationCode.issue above: every MCP tool call that
        # presents this token arrives in its own later, separate request.
        # This commit also persists any earlier flush-only work still
        # pending in this same request's session (e.g. the authorization
        # code just consumed, or the refresh token just revoked), since a
        # commit always covers the whole transaction, not just this insert.
        db.session.commit()
        return raw_access, raw_refresh, token

    def revoke(self) -> None:
        self.revoked_at = datetime.now(timezone.utc)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        if self.expires_at.tzinfo is None:
            return datetime.now(timezone.utc) >= self.expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def is_active(self) -> bool:
        return not self.is_revoked and not self.is_expired

    @property
    def is_refresh_expired(self) -> bool:
        if self.refresh_expires_at is None:
            return False
        expires_at = self.refresh_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires_at

    @property
    def is_refresh_active(self) -> bool:
        return not self.is_revoked and not self.is_refresh_expired

    @classmethod
    def find_by_access_token(cls, raw_access_token: str) -> OAuthToken | None:
        return cls.query.filter_by(access_token=hash_secret(raw_access_token)).first()

    @classmethod
    def find_by_refresh_token(cls, raw_refresh_token: str) -> OAuthToken | None:
        return cls.query.filter_by(refresh_token=hash_secret(raw_refresh_token)).first()

    def touch_last_used(self, *, min_interval_seconds: int = 60) -> None:
        """Record that this token was just used, at most once per minute.

        Called on every bearer-authenticated request; without the interval
        guard it would be a write on every single MCP call.
        """
        now = datetime.now(timezone.utc)
        if self.last_used_at is not None:
            last = self.last_used_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < min_interval_seconds:
                return
        self.last_used_at = now
        db.session.flush()
