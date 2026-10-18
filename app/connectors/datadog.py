"""
Datadog APM Connector

Application performance monitoring integration with:
- Metric ingestion and SLO/SLA monitoring
- Real-time performance data synchronization
- Alert correlation with ArchiMate elements
- Performance threshold evaluation

API Reference: https://docs.datadoghq.com/api/latest/
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional


from app.modules.intelligence.services.operational_source import (
    OperationalRecordDraft,
    OperationalSourceAdapter,
)

from ..services.connector_framework import (
    BaseConnector,
    ConnectorConfig,
    ConnectorType,
    FieldMapping,
)

logger = logging.getLogger(__name__)

_MONITOR_PAGE_SIZE = 1000
_MAX_MONITOR_PAGES = 50


class DatadogAPMConnector(BaseConnector):
    """Datadog APM connector implementation."""

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.APM

    def get_required_config_fields(self) -> List[str]:
        return ["api_key", "app_key", "site"]

    def get_field_mappings(self) -> List[FieldMapping]:
        """Return field mappings for Datadog metrics to telemetry points."""
        return [
            # Basic metric fields
            FieldMapping("metric", "metric_name", required=True),
            FieldMapping("tags", "tags", transform=self._parse_tags),
            FieldMapping("points", "data_points", transform=self._parse_data_points),
            # Service/application mapping
            FieldMapping("tags.service", "service_name"),
            FieldMapping("tags.env", "environment"),
            FieldMapping("tags.version", "version"),
            # Performance metrics
            FieldMapping("tags.endpoint", "endpoint"),
            FieldMapping("tags.method", "http_method"),
            FieldMapping("tags.status_code", "status_code", transform=self._parse_status_code),
        ]

    def _parse_tags(self, tags: List[str]) -> Dict[str, str]:
        """Parse Datadog tags into key-value pairs."""
        parsed = {}
        for tag in tags:
            if ":" in tag:
                key, value = tag.split(":", 1)
                parsed[key] = value
        return parsed

    def _parse_data_points(self, points: List[List]) -> List[Dict[str, Any]]:
        """Parse Datadog data points into structured format."""
        parsed = []
        for point in points:
            if len(point) >= 2:
                timestamp, value = point[0], point[1]
                parsed.append({"timestamp": datetime.fromtimestamp(timestamp), "value": value})
        return parsed

    def _parse_status_code(self, status_code: str) -> Optional[int]:
        """Parse status code string to integer."""
        try:
            return int(status_code) if status_code else None
        except (ValueError, TypeError):
            return None

    def _checked_base_url(self) -> str:
        """https://api.<site>, after the outbound-URL guard.

        ``site`` is tenant-configured, so it is held to a plain domain name
        (no scheme, port, path or credentials) before it becomes a host.
        """
        import re

        from app.services.connector_framework import ConnectorURLRefused, validate_connector_url

        site = str((self.config.config or {}).get("site") or "").strip().lower()
        if not re.fullmatch(r"([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?", site):
            raise ConnectorURLRefused("The connector URL is not an allowed address")
        return validate_connector_url(f"https://api.{site}")

    async def test_connection(self) -> bool:
        """Test connectivity to Datadog API."""
        try:
            headers = self._get_auth_headers()
            url = f"{self._checked_base_url()}/api/v1/validate"

            async with self._outbound_session() as session:
                async with session.get(url, headers=headers) as response:
                    return response.status == 200

        except Exception as e:
            logger.error(f"Datadog connection test failed: {e}")
            return False

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for Datadog API."""
        return {
            "DD-API-KEY": self.config.config["api_key"],
            "DD-APPLICATION-KEY": self.config.config["app_key"],
            "Content-Type": "application/json",
        }

    async def fetch_monitors(self) -> List[Dict[str, Any]]:
        """Every monitor the keys can see, paged, with its current state and tags.

        Uses the connector's own auth headers, outbound guard and no-redirect
        session. A non-200 page raises rather than returning a short list: a
        partial list would read as "those alerts do not exist".
        """
        headers = self._get_auth_headers()
        url = f"{self._checked_base_url()}/api/v1/monitor"
        monitors: List[Dict[str, Any]] = []
        async with self._outbound_session() as session:
            for page in range(_MAX_MONITOR_PAGES):
                params = {"page": page, "page_size": _MONITOR_PAGE_SIZE}
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Datadog monitor list returned HTTP {response.status}")
                    batch = await response.json()
                monitors.extend(batch)
                if len(batch) < _MONITOR_PAGE_SIZE:
                    return monitors
        raise RuntimeError(
            f"Datadog returned more than {_MAX_MONITOR_PAGES * _MONITOR_PAGE_SIZE} monitors"
        )

    async def batch_sync(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """Perform batch synchronization of APM data."""
        try:
            # Default to last 24 hours if no since time specified
            if not since:
                since = datetime.utcnow() - timedelta(hours=24)

            # Fetch key metrics
            metrics_data = await self._fetch_metrics(since)

            # Process metrics
            processed = await self._process_metrics(metrics_data)

            return {
                "status": "completed",
                "records_processed": len(metrics_data),
                "records_created": processed["created"],
                "records_updated": processed["updated"],
                "records_deleted": processed["deleted"],
            }

        except Exception as e:
            logger.error(f"Batch sync failed: {e}")
            raise

    async def _fetch_metrics(self, since: datetime) -> List[Dict[str, Any]]:
        """Fetch metrics from Datadog."""
        headers = self._get_auth_headers()
        base_url = f"{self._checked_base_url()}/api/v2/metrics"

        # Define key APM metrics to fetch
        metrics_queries = [
            "system.cpu.idle{*}",
            "system.mem.used{*}",
            "trace.flask.request.duration{*}",
            "trace.flask.request.errors{*}",
            "dbm-mysql.connection_pool.connections_used{*}",
            "dbm-mysql.connection_pool.connections_idle{*}",
        ]

        all_metrics = []

        async with self._outbound_session() as session:
            for query in metrics_queries:
                try:
                    params = {
                        "query": query,
                        "from": int(since.timestamp()),
                        "to": int(datetime.utcnow().timestamp()),
                        "interval": 3600,  # 1 hour intervals
                    }

                    async with session.get(base_url, headers=headers, params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            series = data.get("data", {}).get("series", [])
                            all_metrics.extend(series)
                        else:
                            logger.warning(f"Failed to fetch metric {query}: {response.status}")

                except Exception as e:
                    logger.error(f"Error fetching metric {query}: {e}")
                    continue

        return all_metrics

    async def _process_metrics(self, metrics: List[Dict[str, Any]]) -> Dict[str, int]:
        """Process Datadog metrics and update telemetry."""
        created = 0
        updated = 0
        deleted = 0

        for metric in metrics:
            try:
                # Apply field mappings
                mapped_data = {}
                for mapping in self.get_field_mappings():
                    try:
                        value = mapping.apply(metric)
                        if value is not None:
                            mapped_data[mapping.target_field] = value
                    except ValueError as e:
                        logger.warning(f"Field mapping failed for {metric.get('metric')}: {e}")
                        continue

                logger.warning(
                    "Metric %s mapped but not persisted — KG telemetry integration not available",
                    metric.get("metric"),
                )

            except Exception as e:
                logger.error(f"Failed to process Datadog metric {metric.get('metric')}: {e}")
                continue

        return {"created": created, "updated": updated, "deleted": deleted}

    async def incremental_sync(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle real-time APM updates from webhooks."""
        try:
            # Datadog webhooks typically contain alert/metric data
            metric_data = event_data.get("metric", {})
            if not metric_data:
                raise ValueError("No metric data in webhook event")

            # Process single metric
            processed = await self._process_metrics([metric_data])

            return {
                "status": "completed",
                "records_processed": 1,
                "records_created": processed["created"],
                "records_updated": processed["updated"],
                "records_deleted": processed["deleted"],
            }

        except Exception as e:
            logger.error(f"Incremental sync failed: {e}")
            raise


# ----------------------------------------------------------------------
# Operational source adapter
# ----------------------------------------------------------------------

_OPEN_STATES = {"Alert", "Warn", "No Data"}


def _dd_time(value) -> Optional[datetime]:
    """Datadog ISO-8601 timestamp as naive UTC; empty means not set."""
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _service_tag(tags: Iterable[str]) -> Optional[str]:
    """The one ``service:`` tag on a monitor; more than one is ambiguous."""
    services = {t.split(":", 1)[1].strip() for t in tags or [] if t.startswith("service:")}
    services.discard("")
    if len(services) > 1:
        raise ValueError("monitor carries more than one service tag")
    return next(iter(services), None)


class DatadogOperationalAdapter(OperationalSourceAdapter):
    """Datadog monitors that have changed state, as operational incidents.

    A monitor is one incident record: its external id is the monitor id, its
    configuration item is the monitor's ``service:`` tag (resolved only through
    the crosswalk, namespace ``datadog``). A monitor that has never left its
    initial state is not an alert and is not fetched.
    """

    source_system = "datadog"

    def __init__(self, connector: DatadogAPMConnector) -> None:
        self._connector = connector

    def fetch_since(self, org_id: int, since: Optional[datetime]):
        monitors = asyncio.run(self._connector.fetch_monitors())
        for monitor in monitors:
            try:
                changed = _dd_time(monitor.get("overall_state_modified"))
                if changed is None or changed <= (_dd_time(monitor.get("created")) or changed):
                    continue
                touched = max(changed, _dd_time(monitor.get("modified")) or changed)
                if since is not None and touched < since:
                    continue
            except (ValueError, TypeError):
                pass  # unreadable timestamps: hand it on so the ingest reports it as unmappable
            yield monitor

    def map_record(self, raw: Dict[str, Any]) -> OperationalRecordDraft:
        state = raw.get("overall_state")
        changed = _dd_time(raw.get("overall_state_modified"))
        priority = raw.get("priority")
        return OperationalRecordDraft(
            kind="incident",
            external_id=str(raw["id"]),
            item_external_id=_service_tag(raw.get("tags")),
            title=raw.get("name") or None,
            state=state or None,
            severity=str(priority) if priority is not None else None,
            opened_at=changed if state in _OPEN_STATES else None,
            resolved_at=changed if state == "OK" else None,
            source_updated_at=max(changed, _dd_time(raw.get("modified")) or changed)
            if changed
            else _dd_time(raw.get("modified")),
        )


def pull_operational_records(org_id: int, since: Optional[datetime] = None) -> Dict[str, Any]:
    """Pull the organisation's Datadog monitor alerts into its operational records.

    Runs the Datadog adapter through the one ingest path and returns its report.
    Returns {} when the organisation has no enabled Datadog connector.
    """
    from app.extensions import db
    from app.models.connector_config import ConnectorConfig as StoredConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.modules.intelligence.services.operational_source import (
        ingest_operational_records,
    )
    from app.services.connector_framework import ConnectorConfig, ConnectorType

    stored = StoredConfig.query.filter_by(organization_id=org_id, connector_type="datadog").first()
    if stored is None or not (stored.config or {}).get("enabled"):
        return {}
    vault = OrgCredentialVault()
    blob = vault.retrieve_credentials(org_id, "datadog") or {}
    config = dict(stored.config)
    for key in ("api_key", "app_key"):
        config[key] = vault.retrieve(org_id, "datadog", key) or blob.get(key, "")
    connector = DatadogAPMConnector(
        ConnectorConfig(
            id=stored.id,
            connector_type="datadog",
            name=stored.name,
            organization_id=org_id,
            config=config,
        )
    )
    report = ingest_operational_records(DatadogOperationalAdapter(connector), org_id, since)
    db.session.commit()
    logger.info(
        "Datadog operational pull for org %s: fetched=%s created=%s updated=%s unmatched=%s",
        org_id, report.fetched, report.created, report.updated, len(report.unmatched),
    )
    return report.to_dict()


def create_datadog_connector(config: ConnectorConfig) -> DatadogAPMConnector:
    """Factory function to create Datadog connector."""
    return DatadogAPMConnector(config)
