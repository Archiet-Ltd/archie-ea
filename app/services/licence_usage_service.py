"""Record measured licence seat usage fetched through a connector.

The one writer of ``LicenseUsageMeasurement``. Matching and writing are scoped
to the organisation that owns the connector configuration, so a measurement
fetched for one organisation's tenant can only land on that organisation's
entitlements.
"""

import logging
from datetime import datetime

from app import db
from app.models.license_entitlement import LicenseEntitlement
from app.models.license_usage_measurement import LicenseUsageMeasurement

logger = logging.getLogger(__name__)


def record_licence_usage(*, org_id: int, connector_id, usage: dict) -> dict:
    """Write one measurement per matched entitlement and stamp ``last_usage_sync``.

    ``usage`` is the connector's ``licence_usage`` result: ``source``,
    ``fetched_at``, ``readings`` (``source_ref`` + ``seats_used``) and an
    optional ``sku_map`` (source_ref -> entitlement id). A reading matches an
    entitlement through the map, else by product name equal to the source_ref
    (case-insensitive). Readings that match nothing are returned, not guessed.
    """
    fetched_at = usage.get("fetched_at") or datetime.utcnow()
    source = usage["source"]
    sku_map = {str(k).lower(): v for k, v in (usage.get("sku_map") or {}).items()}

    entitlements = (
        db.session.execute(
            db.select(LicenseEntitlement).where(LicenseEntitlement.organization_id == org_id)
        )
        .scalars()
        .all()
    )
    by_id = {e.id: e for e in entitlements}
    by_name = {}
    for e in entitlements:
        if e.product_name:
            by_name.setdefault(e.product_name.strip().lower(), []).append(e)

    recorded, unmatched = 0, []
    for reading in usage.get("readings", []):
        ref = reading["source_ref"]
        targets = []
        mapped = sku_map.get(ref.lower())
        if mapped is not None:
            if mapped in by_id:
                targets = [by_id[mapped]]
        else:
            targets = by_name.get(ref.strip().lower(), [])
        if not targets:
            unmatched.append(ref)
            continue
        for ent in targets:
            db.session.add(
                LicenseUsageMeasurement(
                    organization_id=org_id,
                    entitlement_id=ent.id,
                    seats_used=int(reading["seats_used"]),
                    source=source,
                    source_ref=ref,
                    connector_id=str(connector_id) if connector_id is not None else None,
                    fetched_at=fetched_at,
                )
            )
            ent.last_usage_sync = fetched_at
            recorded += 1
    db.session.flush()
    return {"measurements_recorded": recorded, "unmatched": unmatched}
