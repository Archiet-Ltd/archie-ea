"""
Connector Framework

Scaffolding for mapping records from an external system into this
platform's shape:
- FieldMapping: a DSL for per-field API-response transformations
- BaseConnector: the interface each connector implements (test_connection,
  batch_sync, incremental_sync)
- ConnectorManager: registers connector instances and runs their sync
  methods

Not implemented: no connector in app/connectors/ is ever registered with
ConnectorManager, and none of them persist a fetched/mapped record anywhere.
There is no Kafka bus, no webhook-driven incremental sync, and no
reconciliation or fuzzy-matching layer -- this module provides only the
field-mapping scaffolding those would sit on.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app import db

# ConnectorConfig carries per-organisation credentials, so it lives under
# app/models/ where the tenancy checks (the isolation matrix and the static
# tenant-scoping gate) can see it. Re-exported here so existing imports of
# `app.services.connector_framework.ConnectorConfig` (and its enums and
# SyncLog) keep working unchanged.
from app.models.connector_config import (  # noqa: F401
    ConnectorConfig,
    ConnectorStatus,
    ConnectorType,
    SyncLog,
    SyncMode,
)

logger = logging.getLogger(__name__)


class FieldMapping:
    """Field mapping DSL for API transformations."""

    def __init__(
        self,
        source_field: str,
        target_field: str,
        transform: Optional[Callable] = None,
        default_value: Any = None,
        required: bool = False,
    ):
        self.source_field = source_field
        self.target_field = target_field
        self.transform = transform
        self.default_value = default_value
        self.required = required

    def apply(self, source_data: Dict[str, Any]) -> Any:
        """Apply field mapping to source data."""
        value = source_data.get(self.source_field, self.default_value)

        if self.required and value is None:
            raise ValueError(f"Required field '{self.source_field}' is missing")

        if self.transform and value is not None:
            value = self.transform(value)

        return value


class BaseConnector(ABC):
    """Abstract base class for all connectors."""

    def __init__(self, config: ConnectorConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @property
    @abstractmethod
    def connector_type(self) -> ConnectorType:
        """Return the connector type."""
        pass

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test connectivity to the external system."""
        pass

    @abstractmethod
    async def batch_sync(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """Perform batch synchronization of data."""
        pass

    @abstractmethod
    def get_field_mappings(self) -> List[FieldMapping]:
        """Return field mappings for this connector."""
        pass

    async def incremental_sync(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incremental updates from webhooks/events."""
        # Default implementation - can be overridden
        self.logger.info(f"Received incremental sync event: {event_data}")
        return {"status": "processed", "records": 1}

    def validate_config(self) -> List[str]:
        """Validate connector configuration."""
        errors = []

        if not self.config.config:
            errors.append("Configuration is required")

        required_fields = self.get_required_config_fields()
        for field in required_fields:
            if field not in self.config.config:
                errors.append(f"Required config field '{field}' is missing")

        return errors

    def get_required_config_fields(self) -> List[str]:
        """Return list of required configuration fields."""
        return ["base_url", "api_key"]  # Default - override as needed


class ConnectorManager:
    """Central manager for all connectors."""

    def __init__(self):
        self.connectors: Dict[str, BaseConnector] = {}
        self.event_handlers: Dict[str, List[Callable]] = {}

    def register_connector(self, connector: BaseConnector):
        """Register a connector instance."""
        self.connectors[connector.config.id] = connector
        self.logger.info(f"Registered connector: {connector.config.name}")

    def unregister_connector(self, connector_id: str):
        """Unregister a connector."""
        if connector_id in self.connectors:
            del self.connectors[connector_id]
            self.logger.info(f"Unregistered connector: {connector_id}")

    async def test_all_connections(self) -> Dict[str, bool]:
        """Test connectivity for all registered connectors."""
        results = {}
        for connector_id, connector in self.connectors.items():
            try:
                results[connector_id] = await connector.test_connection()
            except Exception as e:
                self.logger.error(f"Connection test failed for {connector_id}: {e}")
                results[connector_id] = False
        return results

    async def run_batch_sync(
        self, connector_id: str, since: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Run batch synchronization for a specific connector."""
        if connector_id not in self.connectors:
            raise ValueError(f"Connector {connector_id} not found")

        connector = self.connectors[connector_id]

        # Log sync start
        sync_log = SyncLog(connector_id=connector_id, sync_type="batch", status="running")
        db.session.add(sync_log)
        db.session.commit()

        try:
            result = await connector.batch_sync(since)

            # Update sync log
            sync_log.status = result.get("status", "completed")
            sync_log.records_processed = result.get("records_processed", 0)
            sync_log.records_created = result.get("records_created", 0)
            sync_log.records_updated = result.get("records_updated", 0)
            sync_log.records_deleted = result.get("records_deleted", 0)
            sync_log.completed_at = datetime.utcnow()

            # Update connector last sync
            connector.config.last_sync = datetime.utcnow()
            db.session.commit()

            return result

        except Exception as e:
            self.logger.error(f"Batch sync failed for {connector_id}: {e}")
            sync_log.status = "error"
            sync_log.error_message = str(e)
            sync_log.completed_at = datetime.utcnow()
            db.session.commit()
            raise

    def register_event_handler(self, event_type: str, handler: Callable):
        """Register an event handler for webhook events."""
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)

    async def handle_webhook_event(
        self, connector_id: str, event_type: str, event_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle incoming webhook event."""
        if connector_id not in self.connectors:
            raise ValueError(f"Connector {connector_id} not found")

        connector = self.connectors[connector_id]

        # Log the event
        sync_log = SyncLog(connector_id=connector_id, sync_type="event", status="processing")
        db.session.add(sync_log)
        db.session.commit()

        try:
            result = await connector.incremental_sync(event_data)

            # Update sync log
            sync_log.status = "completed"
            sync_log.records_processed = 1
            sync_log.completed_at = datetime.utcnow()
            db.session.commit()

            # Trigger event handlers
            if event_type in self.event_handlers:
                for handler in self.event_handlers[event_type]:
                    try:
                        await handler(event_data, result)
                    except Exception as e:
                        self.logger.error(f"Event handler failed: {e}")

            return result

        except Exception as e:
            self.logger.error(f"Event processing failed for {connector_id}: {e}")
            sync_log.status = "error"
            sync_log.error_message = str(e)
            sync_log.completed_at = datetime.utcnow()
            db.session.commit()
            raise


# Global connector manager instance
connector_manager = ConnectorManager()


def init_connector_tables():
    """Initialize connector database tables."""
    db.create_all()


def get_connector_manager() -> ConnectorManager:
    """Get the global connector manager instance."""
    return connector_manager
