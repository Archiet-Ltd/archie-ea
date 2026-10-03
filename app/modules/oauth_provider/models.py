"""OAuth 2.1 models: client registration, authorization codes, and access tokens.

Three tables — no second auth system. The token this authorization server
issues resolves to the same ``User`` the session cookie already resolves to,
through the same ``flask-login`` loader seam.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from app.extensions import db


def _new_token_id(prefix: str = "at") -> str:
    """A token identifier with enough entropy to resist enumeration."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


class OAuthAuthorizationCode(db.Model):
    """A single-use authorization code, stored in the database so every
    worker process in a multi-worker deployment can redeem codes issued by
    any other worker.
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

    @classmethod
    def issue(cls, *, client_id: str, user_id: int, redirect_uri: str,
              scope: str | None = None, resource: str | None = None,
              code_challenge: str, code_challenge_method: str = "S256",
              lifetime_seconds: int = 60) -> OAuthAuthorizationCode:
        """Create and persist a new authorization code."""
        code = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        auth_code = cls(
            code=code,
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            scope=scope,
            resource=resource,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            expires_at=datetime.fromtimestamp(time.time() + lifetime_seconds, tz=timezone.utc),
            created_at=now,
        )
        db.session.add(auth_code)
        db.session.flush()
        return auth_code

    @classmethod
    def consume(cls, code: str) -> OAuthAuthorizationCode | None:
        """Atomically look up and delete a non-expired authorization code.

        Returns the code payload if found and not expired, or None.
        The code is deleted in the same transaction so it cannot be reused.
        Uses SELECT ... FOR UPDATE to prevent concurrent redemption of the
        same code.
        """
        now = datetime.now(timezone.utc)
        auth_code = cls.query.filter_by(code=code).with_for_update().first()
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
    """A registered OAuth 2.1 client (dynamic registration, RFC 7591)."""

    __tablename__ = "oauth_clients"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(128), unique=True, nullable=False, index=True)
    client_secret_hash = db.Column(db.String(256), nullable=True)
    client_name = db.Column(db.String(256), nullable=True)
    redirect_uris = db.Column(db.Text, nullable=True)  # space-separated
    grant_types = db.Column(db.String(256), nullable=True)  # space-separated
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    @classmethod
    def register(cls, *, client_name: str | None = None,
                 redirect_uris: str | None = None) -> OAuthClient:
        """Register a new client dynamically (RFC 7591)."""
        client = cls(
            client_id=_new_token_id("cl"),
            client_name=client_name,
            redirect_uris=redirect_uris,
            grant_types="authorization_code refresh_token",
        )
        db.session.add(client)
        db.session.flush()
        return client

    @property
    def redirect_uri_list(self) -> list[str]:
        if not self.redirect_uris:
            return []
        return self.redirect_uris.split()


class OAuthToken(db.Model):
    """An issued access token (and optional refresh token).

    Resolves to a ``User`` through the MCP blueprint's ``_authenticate_request``,
    which calls ``login_user()`` to set ``flask-login``'s ``current_user``.
    Scoped to the MCP mount; not a general API key.
    """

    __tablename__ = "oauth_tokens"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(128), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    access_token = db.Column(db.String(256), unique=True, nullable=False, index=True)
    refresh_token = db.Column(db.String(256), unique=True, nullable=True, index=True)
    scope = db.Column(db.String(256), nullable=True)
    resource = db.Column(db.String(512), nullable=True)
    issued_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked = db.Column(db.Boolean, nullable=False, default=False)
    last_used_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", backref="oauth_tokens", lazy="select")

    @classmethod
    def issue(cls, *, client_id: str, user_id: int, scope: str | None = None,
              resource: str | None = None, expires_in: int = 3600) -> OAuthToken:
        """Issue a new access token."""
        now = datetime.now(timezone.utc)
        token = cls(
            client_id=client_id,
            user_id=user_id,
            access_token=_new_token_id("at"),
            refresh_token=_new_token_id("rt"),
            scope=scope,
            resource=resource,
            issued_at=now,
            expires_at=datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc),
        )
        db.session.add(token)
        db.session.flush()
        return token

    @property
    def is_expired(self) -> bool:
        if self.expires_at.tzinfo is None:
            return datetime.now(timezone.utc) >= self.expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def is_active(self) -> bool:
        return not self.revoked and not self.is_expired

    @classmethod
    def find_by_access_token(cls, access_token: str) -> OAuthToken | None:
        return cls.query.filter_by(access_token=access_token).first()

    @classmethod
    def find_by_refresh_token(cls, refresh_token: str) -> OAuthToken | None:
        return cls.query.filter_by(refresh_token=refresh_token).first()