"""Crosswalk reads and the single gated writer for external identifiers."""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import text

from app import db
from app.middleware.tenant_context import current_org_id
from app.models.archimate_core import ArchiMateElement
from app.models.external_identity_crosswalk import ExternalIdentityCrosswalk
from app.modules.intelligence.services.connector_allowlist import (
    assert_connector_permitted,
)


class CrosswalkService:
    """Read and write the tenant-scoped external identifier crosswalk."""

    @staticmethod
    def _require_org_id(org_id: int | None = None) -> int:
        resolved = org_id if org_id is not None else current_org_id()
        if resolved is None:
            raise RuntimeError("crosswalk operations require an organisation context")
        return resolved

    @staticmethod
    def _normalize_source_system(source_system: str) -> str:
        value = (source_system or "").strip().lower()
        if not value:
            raise ValueError("source_system is required")
        return value

    @staticmethod
    def _normalize_external_id(external_id: str) -> str:
        value = (external_id or "").strip()
        if not value:
            raise ValueError("external_id is required")
        return value

    @classmethod
    def _assert_element_belongs_to_org(cls, element_id: int, org_id: int) -> None:
        exists = (
            ArchiMateElement.query.filter_by(id=element_id, organization_id=org_id)
            .with_entities(ArchiMateElement.id)
            .first()
        )
        if exists is None:
            raise LookupError(
                f"element {element_id} is not visible in organisation {org_id}"
            )

    @classmethod
    def write_link(
        cls,
        source_system: str,
        external_id: str,
        element_id: int,
        *,
        confidence: float = 1.0,
        first_seen: datetime | None = None,
        last_seen: datetime | None = None,
        org_id: int | None = None,
    ) -> ExternalIdentityCrosswalk:
        """Create or update one tenant-scoped external-id link.

        The same ``(organisation, source_system, external_id)`` triple always
        resolves to one row. Re-importing a renamed element therefore updates the
        linked ``element_id`` instead of creating a duplicate crosswalk record.
        """
        org_id = cls._require_org_id(org_id)
        source_system = cls._normalize_source_system(source_system)
        external_id = cls._normalize_external_id(external_id)
        assert_connector_permitted(source_system)
        cls._assert_element_belongs_to_org(element_id, org_id)

        observed_at = last_seen or datetime.now(UTC)
        first_seen_value = first_seen or observed_at

        # Strip timezone for the naive DateTime column.
        observed_naive = observed_at.replace(tzinfo=None)
        first_seen_naive = first_seen_value.replace(tzinfo=None)

        db.session.execute(
            text(
                "INSERT INTO external_identity_crosswalk "
                "(organization_id, source_system, external_id, element_id, "
                "confidence, first_seen, last_seen) "
                "VALUES (:org_id, :source, :ext_id, :elem_id, :conf, :first, :last) "
                "ON CONFLICT (organization_id, source_system, external_id) "
                "DO UPDATE SET element_id = EXCLUDED.element_id, "
                "confidence = EXCLUDED.confidence, "
                "last_seen = EXCLUDED.last_seen, "
                "first_seen = LEAST(external_identity_crosswalk.first_seen, EXCLUDED.first_seen)"
            ),
            {
                "org_id": org_id,
                "source": source_system,
                "ext_id": external_id,
                "elem_id": element_id,
                "conf": confidence,
                "first": first_seen_naive,
                "last": observed_naive,
            },
        )
        db.session.flush()

        row = ExternalIdentityCrosswalk.query.filter_by(
            organization_id=org_id,
            source_system=source_system,
            external_id=external_id,
        ).populate_existing().first()
        assert row is not None, "crosswalk row must exist after upsert"
        return row

    @classmethod
    def get_link_by_external_id(
        cls,
        source_system: str,
        external_id: str,
        *,
        org_id: int | None = None,
    ) -> ExternalIdentityCrosswalk | None:
        """Return one link for the current organisation and external identifier."""
        org_id = cls._require_org_id(org_id)
        source_system = cls._normalize_source_system(source_system)
        external_id = cls._normalize_external_id(external_id)
        return ExternalIdentityCrosswalk.query.filter_by(
            organization_id=org_id,
            source_system=source_system,
            external_id=external_id,
        ).first()

    @classmethod
    def get_links_for_element(
        cls,
        element_id: int,
        *,
        org_id: int | None = None,
    ) -> list[ExternalIdentityCrosswalk]:
        """Return every external identifier linked to one element in one org."""
        org_id = cls._require_org_id(org_id)
        return list(
            ExternalIdentityCrosswalk.query.filter_by(
                organization_id=org_id,
                element_id=element_id,
            )
            .order_by(
                ExternalIdentityCrosswalk.source_system.asc(),
                ExternalIdentityCrosswalk.external_id.asc(),
            )
            .all()
        )
