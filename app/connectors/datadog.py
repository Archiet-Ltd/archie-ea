"""
Datadog APM Connector

Application performance monitoring integration with:
- Metric ingestion and SLO/SLA monitoring
- Real-time performance data synchronization
- Alert correlation with ArchiMate elements
- Performance threshold evaluation

API Reference: https://docs.datadoghq.com/api/latest/
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


from ..services.connector_framework import (
    BaseConnector,
    ConnectorConfig,
    ConnectorType,
    FieldMapping,
)

logger = logging.getLogger(__name__)


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


def create_datadog_connector(config: ConnectorConfig) -> DatadogAPMConnector:
    """Factory function to create Datadog connector."""
    return DatadogAPMConnector(config)
