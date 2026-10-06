"""
ConnectorConfig model — per-organisation connector credentials and settings.

Stores encrypted credentials for external connectors (ServiceNow, Jira, M365).
Unique per (organization_id, connector_type).
"""

import logging
import re
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declared_attr

from app.extensions import db
from app.models.mixins.core import TenantMixin
from app.modules.codegen.services.credential_encryption import (
    decrypt_credential,
    encrypt_credential,
)

logger = logging.getLogger(__name__)

# All Fernet tokens start with this byte (same check as app/models/models.py's
# EncryptedAPIKey type). The setters below used to store plaintext whenever
# no key was configured -- which, since nothing in this codebase ever set
# FERNET_KEY, was the only path any of them ever took -- so every existing
# stored value predates this fix and is plaintext, not a Fernet token.
_FERNET_PREFIX = b"gAAAAA"

# ConnectorConfig.config and .webhook_config are plain JSON columns that can
# hold real secrets alongside harmless settings (their own column comments:
# "API endpoints, credentials, etc." and "Webhook endpoints and secrets") --
# there is no separate encrypted-credentials column on this model. Returning
# either raw from an API response leaks whatever secret was stored in it,
# even to an admin of the connector's own organisation. public_config() /
# public_webhook_config() below mask any key that looks like it holds a
# secret before either column is ever serialised.
#
# Matched by case-insensitive substring, which is deliberately broad (an
# unrecognised key shaped like a secret is safer masked than leaked). Two
# documented exceptions, both scoped to "auth" specifically (the broadest,
# most collision-prone term here):
#
# 1. "auth" must start a word, not just appear inside one -- checked with
#    _AUTH_WORD_START_RE ((?:^|[^a-z0-9])auth) rather than plain substring
#    containment. Without this, "oauth" (an extremely common, entirely
#    harmless config section name) would match, because "auth" literally
#    occurs inside "oauth" -- which doesn't just over-mask a label, it masks
#    an entire nested dict of otherwise-fine settings. "authorization" and
#    "auth_token" still match (the word starts at position 0 or just after
#    an underscore); "oauth"/"oauth_token" don't trigger via "auth" at all
#    (though "oauth_token" is still masked anyway, via the separate "token"
#    term).
# 2. Even once "auth" starts a word, a key ending in one of
#    _AUTH_METADATA_SUFFIXES (or naming a URL) is metadata ABOUT an auth
#    setting, not a credential itself -- e.g. "auth_header_name" (the
#    header's NAME) or "basic_auth_enabled" (a boolean flag).
#
# Every other term here (password, secret, token, api_key/apikey,
# client_secret, private_key, credential) has no such exception -- e.g.
# "token_type" still gets masked even though it is usually just a label like
# "Bearer", because the cost of over-masking a label is cosmetic while the
# cost of under-masking a real secret is not.
#
# "webhook" gets no exception either, including the _url suffix one: a
# webhook URL (e.g. a Slack/Teams incoming-webhook URL) IS the credential --
# the path itself is the secret, not something a query string strips off --
# so any key naming one is fully masked, never just sanitized.
_SENSITIVE_KEY_TERMS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "client_secret",
    "private_key",
    "auth",
    "credential",
    "webhook",
)
_AUTH_WORD_START_RE = re.compile(r"(?:^|[^a-z0-9])auth")
_AUTH_METADATA_SUFFIXES = ("_name", "_type", "_method", "_id", "_enabled")
_MASKED_VALUE = "configured"

# Any OTHER key ending in "_url" (not already fully masked above -- in
# particular, not one naming a webhook) still isn't safe to return raw: a
# URL can carry userinfo (user:pass@host) or a secret in its query string
# (?token=...-/?key=.../?sig=...). These are sanitized rather than masked
# outright, since the scheme/host/path are normally useful, non-secret
# information (e.g. an instance URL an admin wants to see) -- see
# _sanitize_url. This is a key-*name* heuristic, same as the rest of this
# module; it does not inspect values that happen to look like URLs under an
# unrelated key name.
_URL_KEY_SUFFIX = "_url"


def _is_sensitive_key(key: str) -> bool:
    """True if *key* looks like it names a secret that must be fully masked
    (not merely sanitized) rather than a setting."""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    for term in _SENSITIVE_KEY_TERMS:
        if term == "auth":
            if not _AUTH_WORD_START_RE.search(lowered):
                continue
            if lowered.endswith(_AUTH_METADATA_SUFFIXES + (_URL_KEY_SUFFIX,)):
                continue
            return True
        if term not in lowered:
            continue
        return True
    return False


def _is_url_suffixed_key(key: str) -> bool:
    """True if *key* names a URL field by convention (ends in "_url")."""
    return isinstance(key, str) and key.lower().endswith(_URL_KEY_SUFFIX)


def _sanitize_url(value):
    """Strip userinfo, query string and fragment from a URL value.

    Scheme, host (with port, unchanged) and path are kept -- normally
    useful, non-secret information (an instance URL an admin wants to see).
    Userinfo (``user:pass@``) and the query string/fragment are dropped
    outright rather than inspected, since a secret there can be named
    anything (``?token=``, ``?sig=``, ``?key=``, a session id, ...) and
    guessing at which query parameters are safe is exactly the kind of
    regex-guess this needs to not be. Uses ``urllib.parse`` for real URL
    parsing rather than string-splitting on "@"/"?", so it isn't fooled by
    a path or fragment that happens to contain those characters.

    Non-string input, or a string that fails to parse as a URL at all, is
    masked outright -- safer than returning something unparsed and
    potentially still carrying a credential.
    """
    if not isinstance(value, str):
        return value
    from urllib.parse import urlsplit, urlunsplit

    try:
        parsed = urlsplit(value)
    except ValueError:
        return _MASKED_VALUE

    netloc = parsed.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _mask_sensitive(value, *, url_keys_fully_sensitive=False):
    """Recursively mask or sanitize sensitive-looking dict keys in a
    JSON-like value.

    Any dict key for which :func:`_is_sensitive_key` is true has its value
    replaced outright with the literal string "configured", regardless of
    that value's own shape -- a nested dict or list under a sensitive key is
    not partially revealed. Any other key ending in "_url" (or named exactly
    "url") has its value run through :func:`_sanitize_url` instead (stripping
    userinfo/query/fragment, keeping scheme/host/path) -- unless
    *url_keys_fully_sensitive* is set, in which case it is masked outright
    too. Every other key is walked recursively (into nested dicts, and into
    lists, so a secret nested one or more levels deep -- e.g. a list of
    per-environment credential blocks -- is still caught). The input is
    never mutated; a new structure is returned.

    *url_keys_fully_sensitive* exists for :meth:`ConnectorConfig.
    public_webhook_config`: every URL field inside ``webhook_config`` names a
    webhook endpoint (the column's own comment: "Webhook endpoints and
    secrets"), and a webhook URL's path is typically itself the credential
    (e.g. a Slack/Teams incoming-webhook URL) -- stripping just the userinfo
    and query string is not enough there, so the whole value is masked.
    """
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if not isinstance(k, str):
                result[k] = _mask_sensitive(v, url_keys_fully_sensitive=url_keys_fully_sensitive)
                continue
            is_url_key = k.lower() == "url" or _is_url_suffixed_key(k)
            if _is_sensitive_key(k) or (url_keys_fully_sensitive and is_url_key):
                result[k] = _MASKED_VALUE
            elif is_url_key:
                result[k] = _sanitize_url(v)
            else:
                result[k] = _mask_sensitive(v, url_keys_fully_sensitive=url_keys_fully_sensitive)
        return result
    if isinstance(value, list):
        return [
            _mask_sensitive(item, url_keys_fully_sensitive=url_keys_fully_sensitive)
            for item in value
        ]
    return value


def _decrypt_or_legacy_plaintext(encrypted: str) -> str | None:
    """Decrypt a stored credential, tolerating a pre-fix plaintext value.

    A value that doesn't look like a Fernet token is returned as-is (it
    predates this fix); one that does but fails to decrypt (corrupted, or
    encrypted under a since-rotated key) is also returned as-is rather than
    silently discarded, with a warning logged, matching the same tolerance
    app/models/models.py's EncryptedAPIKey already applies. Either way the
    value is re-encrypted correctly the next time its setter runs.
    """
    raw = encrypted.encode() if isinstance(encrypted, str) else encrypted
    if not raw.startswith(_FERNET_PREFIX):
        return encrypted
    decrypted = decrypt_credential(raw)
    if decrypted is None:
        logger.warning(
            "Failed to decrypt a stored connector credential — returning the "
            "raw value (may be legacy plaintext or encrypted under a "
            "different key)."
        )
        return encrypted
    return decrypted


class ConnectorType(str, Enum):
    """Supported connector types."""

    CMDB = "cmdb"
    ALM = "alm"
    APM = "apm"
    CLM = "clm"  # Kept for future use
    ERP = "erp"
    CRM = "crm"
    ITSM = "itsm"
    EA_TOOL = "ea_tool"  # Enterprise Architecture tools (Abacus, Ardoq, LeanIX, etc.)


class SyncMode(str, Enum):
    """Synchronization modes."""

    BATCH = "batch"
    EVENT = "event"
    HYBRID = "hybrid"


class ConnectorStatus(str, Enum):
    """Connector operational status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    MAINTENANCE = "maintenance"


class ConnectorConfig(TenantMixin, db.Model):
    """Connector configuration storage, scoped to the organisation that saved it.

    Inherits ``TenantMixin`` so the ORM tenant filter (do_orm_execute) and
    the before_flush auto-set both apply, but overrides its
    ``organization_id`` column to be nullable: existing rows had no
    organisation column at all, and a row whose origin cannot be determined
    (no audit trail recorded who saved it) is backfilled to NULL rather
    than guessed. NULL is not "shared" here -- the mixin's equality filter
    (``WHERE organization_id = g.current_org_id``) never matches NULL, so
    such a row is invisible to every organisation, not visible to all of
    them. A row assigned to the wrong organisation is worse than a row
    nobody can load.
    """

    __tablename__ = "connector_configs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    @declared_attr
    def organization_id(cls):
        from app.models.mixins.core import _default_org_id

        return Column(
            Integer,
            ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
            # Same belt-and-suspenders as TenantMixin's own column: an insert
            # that bypasses the before_flush listener (raw Table.insert(),
            # a seeder, a background thread with no request context) still
            # gets an org when one can be inferred. Nullable, unlike the
            # mixin's own column, because an existing row whose origin
            # cannot be determined is backfilled to NULL, not guessed.
            default=_default_org_id,
        )

    connector_type = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    config = Column(JSON, nullable=False)  # API endpoints, credentials, etc.
    field_mappings = Column(JSON)  # Field mapping DSL
    sync_schedule = Column(JSON)  # Cron expressions for batch sync
    webhook_config = Column(JSON)  # Webhook endpoints and secrets
    status = Column(String(20), default=ConnectorStatus.INACTIVE.value)
    last_sync = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ConnectorConfig {self.connector_type} org={self.organization_id}>"

    def derived_sync_mode(self) -> str:
        """Derive a human-readable sync mode from what's actually configured.

        There is no ``sync_mode`` column on this model -- only ``sync_schedule``
        (cron expressions for batch sync) and ``webhook_config`` (webhook
        endpoints/secrets). A connector can have either, both, or neither
        configured, so the mode is computed here rather than stored, and both
        the API routes and the dashboard card read it from this one place so
        they never disagree with each other.
        """
        has_schedule = bool(self.sync_schedule)
        has_webhook = bool(self.webhook_config)

        if has_schedule and has_webhook:
            return "scheduled, event"
        if has_schedule:
            return "scheduled"
        if has_webhook:
            return "event"
        return "manual"

    def public_config(self):
        """``config``, safe to serialise in an API response.

        ``config`` is a plain JSON column that can hold real credentials
        (its own comment: "API endpoints, credentials, etc."); returning it
        raw -- as api_get_connector() used to -- leaks whatever secret was
        stored in it, even to an admin of the connector's own organisation.
        This masks every key that looks like it names a secret (see
        ``_is_sensitive_key``) with the literal string "configured",
        recursively, so a nested credential is masked too. A key ending in
        "_url" that isn't otherwise sensitive (e.g. "instance_url") is
        sanitized rather than masked -- its userinfo/query/fragment are
        stripped (see ``_sanitize_url``), since a URL can carry a credential
        there even under a harmless-looking name. Everything else (flags,
        non-secret settings) passes through unchanged. Returns ``None``/
        ``{}`` unchanged if ``config`` is falsy.
        """
        return _mask_sensitive(self.config) if self.config else self.config

    def public_webhook_config(self):
        """``webhook_config``, safe to serialise in an API response.

        Same masking as :meth:`public_config`, with one difference:
        ``url_keys_fully_sensitive=True``. webhook_config's own comment is
        "Webhook endpoints and secrets" -- every URL field in it names a
        webhook endpoint, and a webhook URL's path is typically itself the
        credential (e.g. a Slack/Teams incoming-webhook URL), so sanitizing
        query/userinfo the way public_config() does for an ordinary URL is
        not enough here; any URL-shaped key is masked outright instead. No
        route currently serialises webhook_config directly (only
        derived_sync_mode()'s boolean presence check reads it), but this is
        provided so it is never a future leak if one starts to.
        """
        if not self.webhook_config:
            return self.webhook_config
        return _mask_sensitive(self.webhook_config, url_keys_fully_sensitive=True)


class SyncLog(db.Model):
    """Synchronization log entries for :class:`ConnectorConfig`."""

    __tablename__ = "sync_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connector_id = Column(String(36), ForeignKey("connector_configs.id"))
    sync_type = Column(String(20), nullable=False)  # batch, event, manual
    status = Column(String(20), nullable=False)  # success, error, partial
    records_processed = Column(Integer, default=0)
    records_created = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    records_deleted = Column(Integer, default=0)
    error_message = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)


class OrgConnectorConfig(db.Model):
    """Per-organisation connector configuration with encrypted credentials."""

    __tablename__ = "org_connector_configs"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "connector_type", name="uq_org_connector_type"),
    )

    id = db.Column(
        db.String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connector_type = db.Column(db.String(50), nullable=False)  # 'servicenow', 'jira', 'm365'
    instance_url = db.Column(db.String(512))
    client_id = db.Column(db.String(255))
    _client_secret_encrypted = db.Column("client_secret_encrypted", db.String(1024))
    field_mapping = db.Column(db.JSON, default=dict)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    last_sync_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # Relationships
    organization = db.relationship("Organization", backref="connector_configs")

    # ------------------------------------------------------------------
    # Encrypted credential property
    # ------------------------------------------------------------------

    @property
    def client_secret(self) -> str | None:
        """Return the decrypted client secret."""
        if not self._client_secret_encrypted:
            return None
        return _decrypt_or_legacy_plaintext(self._client_secret_encrypted)

    @client_secret.setter
    def client_secret(self, value: str | None) -> None:
        """Encrypt and store client secret. Raises if no encryption key is configured."""
        if value is None:
            self._client_secret_encrypted = None
            return
        self._client_secret_encrypted = encrypt_credential(value).decode()

    def __repr__(self) -> str:
        return f"<ConnectorConfig {self.connector_type} org={self.organization_id}>"


class DevOpsConnectorConfig(db.Model):  # migration-exempt — COM-018
    """Per-org GitHub / Azure DevOps connector configuration.

    One record per organisation. Access token is Fernet-encrypted using
    ``CREDENTIAL_ENCRYPTION_KEY``; the setter raises if no key is configured.
    """

    __tablename__ = "devops_connector_configs"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "connector_type",
            name="uq_devops_connector_org_type",
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connector_type = db.Column(db.String(50), nullable=False, default="devops")
    # 'github' or 'azure_devops'
    provider = db.Column(db.String(50), nullable=False, default="github")
    # GitHub Enterprise base URL or Azure DevOps org URL; leave blank for cloud
    instance_url = db.Column(db.String(512), nullable=True)
    client_id = db.Column(db.String(255), nullable=True)
    # Fernet-encrypted PAT / OAuth token — use the access_token property
    _access_token_encrypted = db.Column("access_token_encrypted", db.String(2000), nullable=True)
    # Full repo URL, e.g. https://github.com/acme/myrepo
    repo_url = db.Column(db.String(512), nullable=True)
    default_base_branch = db.Column(db.String(100), nullable=True, default="main")
    field_mapping = db.Column(db.JSON, default=dict)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    organization = db.relationship(
        "Organization", backref=db.backref("devops_connector_config", uselist=False)
    )

    # ------------------------------------------------------------------
    # Fernet-encrypted access_token property
    # ------------------------------------------------------------------

    @property
    def access_token(self) -> str | None:
        """Decrypt and return the stored access token."""
        if not self._access_token_encrypted:
            return None
        return _decrypt_or_legacy_plaintext(self._access_token_encrypted)

    @access_token.setter
    def access_token(self, value: str | None) -> None:
        """Encrypt and store the access token. Raises if no encryption key is configured."""
        if not value:
            self._access_token_encrypted = None
            return
        self._access_token_encrypted = encrypt_credential(value).decode()

    def __repr__(self) -> str:
        return f"<DevOpsConnectorConfig {self.provider} org={self.organization_id}>"


class LucidchartConnectorConfig(db.Model):  # migration-exempt — LUC-001
    """Per-org Lucidchart OAuth configuration with encrypted token storage."""

    __tablename__ = "lucidchart_connector_configs"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            name="uq_lucidchart_connector_org",
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connector_type = db.Column(db.String(50), nullable=False, default="lucidchart")
    client_id = db.Column(db.String(255), nullable=True)
    _client_secret_encrypted = db.Column(
        "client_secret_encrypted",
        db.String(2000),
        nullable=True,
    )
    _access_token_encrypted = db.Column(
        "access_token_encrypted",
        db.String(4000),
        nullable=True,
    )
    _refresh_token_encrypted = db.Column(
        "refresh_token_encrypted",
        db.String(4000),
        nullable=True,
    )
    token_expires_at = db.Column(db.DateTime, nullable=True)
    scope = db.Column(db.String(1000), nullable=True)
    lucid_account_id = db.Column(db.String(255), nullable=True)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    organization = db.relationship(
        "Organization",
        backref=db.backref("lucidchart_connector_config", uselist=False),
    )

    @property
    def client_secret(self) -> str | None:
        """Decrypt and return the stored OAuth client secret."""
        if not self._client_secret_encrypted:
            return None
        return _decrypt_or_legacy_plaintext(self._client_secret_encrypted)

    @client_secret.setter
    def client_secret(self, value: str | None) -> None:
        """Encrypt and store the OAuth client secret. Raises if no encryption key is configured."""
        if not value:
            self._client_secret_encrypted = None
            return
        self._client_secret_encrypted = encrypt_credential(value).decode()

    @property
    def access_token(self) -> str | None:
        """Decrypt and return the stored Lucidchart access token."""
        if not self._access_token_encrypted:
            return None
        return _decrypt_or_legacy_plaintext(self._access_token_encrypted)

    @access_token.setter
    def access_token(self, value: str | None) -> None:
        """Encrypt and store the Lucidchart access token. Raises if no encryption key is configured."""
        if not value:
            self._access_token_encrypted = None
            return
        self._access_token_encrypted = encrypt_credential(value).decode()

    @property
    def refresh_token(self) -> str | None:
        """Decrypt and return the stored Lucidchart refresh token."""
        if not self._refresh_token_encrypted:
            return None
        return _decrypt_or_legacy_plaintext(self._refresh_token_encrypted)

    @refresh_token.setter
    def refresh_token(self, value: str | None) -> None:
        """Encrypt and store the Lucidchart refresh token. Raises if no encryption key is configured."""
        if not value:
            self._refresh_token_encrypted = None
            return
        self._refresh_token_encrypted = encrypt_credential(value).decode()

    def token_is_expired(self, now: datetime | None = None) -> bool:
        """Return True when the stored access token is missing or expired."""
        if self.token_expires_at is None:
            return True
        now = now or datetime.utcnow()
        return self.token_expires_at <= now

    def __repr__(self) -> str:
        return f"<LucidchartConnectorConfig org={self.organization_id} enabled={self.enabled}>"
