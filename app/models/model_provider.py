"""
ModelProvider — tenant-hybrid provider register.

Platform rows (organization_id=NULL) define which providers and model
versions are available globally. Organisations can override via their
own rows to restrict (is_allowed=False) or explicitly allow a provider
that is not in the platform set.

Resolution order:
1. Organisation-specific row for exact (provider, model_version, org) match.
2. Organisation-specific row for (provider, '*', org) — provider-wide.
3. Platform row for exact (provider, model_version, org NULL).
4. Platform row for (provider, '*', org NULL) — provider-wide.
5. If organisation has allow_list_only=True, any miss is refused.
6. No row at all -> True (unknown providers permitted by default).

Usage:
    # Platform-declared providers
    ModelProvider(provider="openai", model_version="gpt-4o", is_platform_default=True)

    # Organisation blocks a platform provider
    ModelProvider(organization_id=2, provider="openai", model_version="gpt-4o",
                  is_platform_default=False, is_allowed=False)

    # Organisation adds its own allowed provider
    ModelProvider(organization_id=2, provider="openai", model_version="gpt-4o-mini",
                  is_platform_default=False, is_allowed=True)
"""

from __future__ import annotations

from app import db
from app.models.mixins import TimestampMixin
from app.models.unified_capability import HybridCapabilityTenantMixin
from sqlalchemy import func


class ModelProvider(HybridCapabilityTenantMixin, TimestampMixin, db.Model):  # type: ignore[valid-type]
    """Provider register — platform defaults + per-org allow/restrict rows."""

    __tablename__ = "model_providers"

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(100), nullable=False, index=True,
                         comment="Provider name, e.g. openai, anthropic, huggingface")
    model_version = db.Column(db.String(255), nullable=False,
                              comment="Model identifier, e.g. gpt-4o, claude-opus-5, or * for provider-wide")

    # Platform-default row: NULL organisation_id.
    # Per-org row: scoped to that organisation.
    # organization_id from hybrid mixin (nullable)

    is_platform_default = db.Column(
        db.Boolean, default=False, nullable=False,
        comment="True for rows seeded as platform-level defaults",
    )
    is_allowed = db.Column(
        db.Boolean, default=True, nullable=False,
        comment="False when an organisation explicitly restricts a provider",
    )

    organization = db.relationship("Organization", lazy="select")

    __table_args__ = (
        # Partial unique index: platform rows (org NULL) are enforced server-side.
        # PostgreSQL treats NULLs as distinct in a plain UNIQUE constraint, so we
        # need a partial index for platform rows.
        db.Index(
            "uq_model_provider_platform",
            "provider", "model_version",
            unique=True,
            postgresql_where=db.text("organization_id IS NULL"),
        ),
        # Unique constraint for organisation-specific rows (org NOT NULL).
        db.UniqueConstraint("provider", "model_version", "organization_id",
                            name="uq_model_provider_org"),
    )

    def __repr__(self) -> str:
        scope = "[PLATFORM]" if self.organization_id is None else f"[org={self.organization_id}]"
        status = "ALLOW" if self.is_allowed else "BLOCK"
        return f"<ModelProvider {self.provider}/{self.model_version} {scope} {status}>"

    @staticmethod
    def _normalize_provider(provider: str | None) -> str | None:
        if provider is None:
            return None
        return provider.strip().lower()

    @staticmethod
    def _normalize_model(model_version: str | None) -> str | None:
        if model_version is None:
            return None
        return model_version.strip().lower()

    @classmethod
    def _normalized_query(cls, provider: str, model_version: str, organization_id: int | None):
        return cls.query.filter(
            func.lower(func.trim(cls.provider)) == provider,
            func.lower(func.trim(cls.model_version)) == model_version,
            cls.organization_id == organization_id,
        )

    @classmethod
    def is_allowed_for_org(cls, provider: str, model_version: str | None,
                           organization_id: int | None = None,
                           allow_list_only: bool = False) -> bool:
        """Check whether *provider/model_version* is allowed for *organization_id*.

        Each step falls through to the next if no match is found:

        1. Organisation-specific row for exact ``(provider, model_version, org)``.
        2. Organisation-specific wildcard row ``(provider, '*', org)``.
        3. Platform row for exact ``(provider, model_version, org NULL)``.
        4. Platform wildcard row ``(provider, '*', org NULL)``.
        5. If ``allow_list_only`` is True, the provider is refused.
        6. No row at all -> True (unknown providers permitted by default).

        When ``model_version`` is None only wildcard rows are consulted (steps 2, 4).
        """
        provider = cls._normalize_provider(provider)
        model_version = cls._normalize_model(model_version)

        # 1. Per-org exact match
        if organization_id is not None and model_version is not None:
            row = cls._normalized_query(provider, model_version, organization_id).first()
            if row is not None:
                return row.is_allowed

        # 2. Per-org provider-wide wildcard
        if organization_id is not None:
            row = cls._normalized_query(provider, "*", organization_id).first()
            if row is not None:
                return row.is_allowed

        # 3. Platform exact match
        if model_version is not None:
            row = cls._normalized_query(provider, model_version, None).filter(
                cls.is_platform_default.is_(True)
            ).first()
            if row is not None:
                return row.is_allowed

        # 4. Platform provider-wide wildcard
        row = cls._normalized_query(provider, "*", None).filter(
            cls.is_platform_default.is_(True)
        ).first()
        if row is not None:
            return row.is_allowed

        # 5. Allow-list-only mode: a miss is refused
        if allow_list_only:
            return False

        # 6. No row at all -> allowed by default
        return True

    @classmethod
    def platform_defaults(cls) -> list[ModelProvider]:
        """Return all platform-default rows."""
        return cls.query.filter_by(organization_id=None, is_platform_default=True).all()

    @classmethod
    def org_overrides(cls, organization_id: int) -> list[ModelProvider]:
        """Return all organisation-specific rows for *organization_id*."""
        return cls.query.filter_by(organization_id=organization_id).all()


def set_provider_restriction(org_id: int, provider: str,
                             model_version: str, allowed: bool) -> ModelProvider:
    """Add or update an organisation's restriction for *(provider, model_version)*.

    This is the write interface for the gateway module, intended to be called
    by the follow-up admin UI screen in PR 2. It creates a per-org row if none exists,
    or updates the ``is_allowed`` flag of an existing one.

    Returns the created/updated row.
    """
    normalized_provider = ModelProvider._normalize_provider(provider)
    normalized_model_version = ModelProvider._normalize_model(model_version)
    existing = ModelProvider._normalized_query(
        normalized_provider,
        normalized_model_version,
        org_id,
    ).first()
    if existing is not None:
        existing.provider = normalized_provider
        existing.model_version = normalized_model_version
        existing.is_allowed = allowed
        return existing
    row = ModelProvider(
        provider=normalized_provider,
        model_version=normalized_model_version,
        organization_id=org_id,
        is_platform_default=False,
        is_allowed=allowed,
    )
    db.session.add(row)
    return row
