"""
Microsoft 365 licence usage connector

Reads assigned seats per subscribed SKU from Microsoft Graph
(``GET /subscribedSkus``, ``consumedUnits``) and hands them to the connector
framework's sync path, which records them as measured licence usage for the
organisation that owns the connector configuration. Seat counts are a point in
time snapshot, so ``since`` does not narrow the fetch.

Required settings: ``tenant_id``, ``client_id`` and ``client_secret`` (the
secret is injected from the organisation's credential vault for the run).
Optional ``licence_sku_map`` maps a SKU part number to a licence entitlement id
when the entitlement's product name is not the SKU part number.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.connector_framework import (
    BaseConnector,
    ConnectorType,
    FieldMapping,
)

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_TENANT_RE = re.compile(r"^[A-Za-z0-9.\-]{1,255}$")


class M365LicenceUsageConnector(BaseConnector):
    """Microsoft 365 seat usage, fetched through the connector framework."""

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.PRODUCTIVITY

    def get_required_config_fields(self) -> List[str]:
        return ["base_url", "tenant_id", "client_id", "client_secret"]

    def get_field_mappings(self) -> List[FieldMapping]:
        return [
            FieldMapping("skuPartNumber", "source_ref", required=True),
            FieldMapping("consumedUnits", "seats_used", required=True),
        ]

    async def _get_token(self) -> str:
        cfg = self.config.config
        tenant_id = str(cfg.get("tenant_id", ""))
        if not _TENANT_RE.match(tenant_id):
            raise ValueError("The Microsoft 365 tenant identifier is not valid")
        payload = {
            "grant_type": "client_credentials",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "scope": "https://graph.microsoft.com/.default",
        }
        import aiohttp

        # Fixed Microsoft sign-in host; no redirects followed.
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _TOKEN_URL.format(tenant_id=tenant_id),
                data=payload,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"Microsoft 365 sign-in failed: {response.status}")
                token = (await response.json()).get("access_token")
        if not token:
            raise RuntimeError("Microsoft 365 sign-in returned no token")
        return token

    async def test_connection(self) -> bool:
        try:
            await self._fetch_skus()
            return True
        except Exception as exc:
            logger.error("Microsoft 365 connection test failed: %s", exc)
            return False

    async def _fetch_skus(self) -> List[Dict[str, Any]]:
        token = await self._get_token()
        base = self._checked_base_url()
        url = f"{base}/subscribedSkus"
        headers = {"Authorization": f"Bearer {token}"}
        skus: List[Dict[str, Any]] = []
        async with self._outbound_session() as session:
            while url:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Microsoft Graph request failed: {response.status}")
                    data = await response.json()
                skus.extend(data.get("value", []))
                next_link = data.get("@odata.nextLink")
                url = self._same_origin_link(next_link) if next_link else None
        return skus

    async def batch_sync(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        fetched_at = datetime.utcnow()
        skus = await self._fetch_skus()
        readings = []
        for sku in skus:
            if sku.get("skuPartNumber") is None or sku.get("consumedUnits") is None:
                continue
            readings.append(
                {
                    "source_ref": str(sku["skuPartNumber"]),
                    "seats_used": int(sku["consumedUnits"]),
                }
            )
        return {
            "status": "completed",
            "records_processed": len(readings),
            "records_created": 0,
            "records_updated": 0,
            "records_deleted": 0,
            "licence_usage": {
                "source": "m365",
                "fetched_at": fetched_at,
                "readings": readings,
                "sku_map": dict(self.config.config.get("licence_sku_map") or {}),
            },
        }
