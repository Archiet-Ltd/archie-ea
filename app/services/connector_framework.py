"""
Connector Framework

The single framework every connector goes through:

- FieldMapping: a DSL for per-field API-response transformations
- BaseConnector: the interface each connector implements (test_connection,
  batch_sync, incremental_sync)
- ConnectorManager: registers connector instances and runs their sync
  methods
- The connector change path: every connector change (create/update/delete
  of a ConnectorConfig row, and a sync run) is either proposed into the
  acting organisation's own approval queue (``propose_or_apply_connector_change``)
  or, when that organisation has delegated the action type (see
  ``org_has_delegated_action``), applied directly. A write that is neither
  an approved proposal nor delegated is refused with ``ConnectorWriteRefused``
  -- never queued silently and never applied behind an approval's back.
  ``apply_connector_change`` is the one writer of connector configuration,
  ``list_org_connectors`` the one reader the connectors page uses, and
  ``run_connector_sync`` the one sync path that records health, last
  synchronisation and the identifier crosswalk links for every element a
  sync touches.

Credentials live only in the per-organisation store
(``OrgCredentialVault`` over ``OrgConnectorCredential``); connector
configuration rows hold settings, never secrets.
"""

import json
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
        self.logger = logger

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


# ===========================================================================
# Connector change path — every connector change goes through the queue
# ===========================================================================

# The approval entity type used for every connector change proposal. The
# approval inbox renders these like any other proposal; the connector
# framework executes them on approval.
CONNECTOR_APPROVAL_ENTITY_TYPE = "connectors"

# Action types a connector change can carry. An organisation delegates any
# of these in ``Organization.settings["connector_action_delegation"]`` and
# that action then applies directly instead of queueing a proposal.
CONNECTOR_ACTION_CREATE = "create"
CONNECTOR_ACTION_UPDATE = "update"
CONNECTOR_ACTION_DELETE = "delete"
CONNECTOR_ACTION_SYNC = "sync"

# Connector types surfaced by the connectors page. A type is only offered
# from a per-organisation configuration row already in the system; this is
# the display order, not an allowlist -- the permit gate is
# ``assert_connector_permitted``.
CONNECTOR_TYPE_LABELS = {
    "servicenow": "ServiceNow",
    "jira": "Jira",
    "m365": "Microsoft 365",
    "abacus": "Abacus",
    "ea_tool": "Enterprise Architecture tool",
    "devops": "GitHub / Azure DevOps",
    "lucidchart": "Lucidchart",
}

# Concrete connector implementations available for sync (outside credential
# storage, which lives in the per-organisation vault).
_CONNECTOR_CLASSES: dict = {}


def _register_concrete_connectors():
    """Import the concrete connector classes once, on first use."""
    if _CONNECTOR_CLASSES:
        return
    from app.connectors.abacus import AbacusConnector
    from app.connectors.jira import JiraALMConnector
    from app.connectors.servicenow import ServiceNowCMDBConnector

    _CONNECTOR_CLASSES.update(
        {
            "abacus": AbacusConnector,
            "ea_tool": AbacusConnector,
            "jira": JiraALMConnector,
            "servicenow": ServiceNowCMDBConnector,
        }
    )


class ConnectorWriteRefused(RuntimeError):
    """A connector write that is neither an approved approval proposal nor
    explicitly delegated by the organisation is refused, not queued
    silently.

    Raised by ``apply_connector_change`` and ``run_connector_sync`` when a
    caller attempts a write without either an ``approval`` or
    ``delegated=True``. A connector change reaches the model only as an
    approval proposal in the acting organisation's queue (executed by the
    approval service on approval) or directly when that organisation has
    delegated the action type.
    """


def org_has_delegated_action(org_id: int, action: str) -> bool:
    """True when *org_id* has delegated *action*, letting a connector write
    apply directly instead of queueing an approval proposal.

    Delegation is an organisation-level setting: ``Organization.settings``
    key ``connector_action_delegation`` maps an action name (create,
    update, delete, sync) to a boolean. Absent or unset means the action is
    not delegated and therefore goes through the approval queue.
    """
    if org_id is None:
        return False
    from app.models.organization import Organization

    org = Organization.query.filter_by(id=org_id).first()
    if org is None:
        return False
    delegation = (org.settings or {}).get("connector_action_delegation") or {}
    return bool(delegation.get(action))


def scope_connector_change_to_org(*, org_id: int | None) -> int:
    """Resolve the organisation a connector change acts as.

    Uses the passed org id, or ``g.current_org_id`` when inside a request.
    A connector write with no organisation context is refused outright --
    the change path is organisation-scoped by construction, and a write
    that cannot be attributed to an acting organisation must not be queued
    into a void.
    """
    if org_id is not None:
        return org_id
    from flask import g, has_app_context

    if has_app_context():
        current = getattr(g, "current_org_id", None)
        if current is not None:
            return current
    raise ConnectorWriteRefused(
        "A connector change requires an acting organisation; none is active "
        "for this request."
    )


def _load_org_connector(org_id: int, connector_id=None, connector_type=None):
    """One org-scoped ConnectorConfig row, or None if it does not exist.

    The explicit ``organization_id`` filter is belt-and-suspenders on the
    tenant middleware: the connectors page and the change path must never
    resolve a row that another organisation owns.
    """
    query = ConnectorConfig.query.filter_by(organization_id=org_id)
    if connector_id:
        query = query.filter_by(id=connector_id)
    if connector_type:
        query = query.filter_by(connector_type=connector_type)
    return query.first()


def _connector_view(cfg: ConnectorConfig, org_id: int) -> dict:
    """One connector as the connectors page and API render it.

    Carries configuration, credential reference (masked — never a secret),
    schedule, health (status) and last synchronisation, so the page has a
    single source per connector from the framework.
    """
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    latest_sync = (
        SyncLog.query.filter_by(connector_id=cfg.id)
        .order_by(SyncLog.started_at.desc())
        .first()
    )
    return {
        "id": cfg.id,
        "name": cfg.name,
        "connector_type": cfg.connector_type,
        "connector_type_label": CONNECTOR_TYPE_LABELS.get(cfg.connector_type, cfg.connector_type),
        "status": cfg.status,
        "health": cfg.status,
        "sync_mode": cfg.derived_sync_mode(),
        "sync_schedule": cfg.sync_schedule,
        "config": cfg.public_config(),
        "credential_ref": f"vault:{org_id}:{cfg.connector_type}",
        "credential_masked": OrgCredentialVault().get_masked(
            org_id, cfg.connector_type, "credentials"
        ),
        "last_sync": cfg.last_sync,
        "last_sync_status": latest_sync.status if latest_sync else None,
        "description": cfg.description,
        "created_at": cfg.created_at,
        "updated_at": cfg.updated_at,
    }


def list_org_connectors(org_id: int) -> list[dict]:
    """Every connector of *org_id* with health and last synchronisation.

    The one reader the connectors page uses. Only rows the organisation
    owns are ever returned.
    """
    rows = ConnectorConfig.query.filter_by(organization_id=org_id).all()
    return [_connector_view(row, org_id) for row in rows]


def propose_connector_change(
    *,
    org_id: int,
    actor_user_id: int | None = None,
    action: str,
    connector_type: str,
    summary: str,
    payload: dict,
) -> Any:
    """Queue one connector change as an approval proposal in the acting
    organisation's own queue.

    Uses ``create_approval_record`` -- the one creator the approval queue
    established -- with the connector entity type, so the same inbox that
    shows every other proposed change shows connector changes too and the
    same approval service executes them.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        create_approval_record,
    )

    resolved = scope_connector_change_to_org(org_id=org_id)
    approval = create_approval_record(
        organization_id=resolved,
        operation_type=action,
        entity_type=CONNECTOR_APPROVAL_ENTITY_TYPE,
        summary=summary,
        operation_payload=payload,
        user_id=actor_user_id,
        source_table="connector_config",
    )
    return approval


def apply_connector_change(
    *,
    org_id: int,
    action: str,
    payload: dict,
    approval=None,
    delegated: bool = False,
):
    """The single writer of connector configuration.

    Refuses (``ConnectorWriteRefused``) a write that is neither an approved
    approval proposal for the same organisation nor an explicit delegation
    by that organisation, then applies create/update/delete on
    ``ConnectorConfig``. Secrets never pass through here: credentials were
    stored in the per-organisation vault at proposal time -- the payload
    only carries the connector type that references them.
    """
    from app.modules.intelligence.services.connector_allowlist import (
        assert_connector_permitted,
    )

    resolved = scope_connector_change_to_org(org_id=org_id)
    if approval is None and not delegated:
        raise ConnectorWriteRefused(
            "A connector write is refused unless it is an approved approval "
            "proposal or the organisation has delegated this action type."
        )
    if approval is not None and approval.organization_id != resolved:
        raise ConnectorWriteRefused(
            "The approval executing this connector change belongs to another "
            "organisation."
        )

    connector_type = payload.get("connector_type")
    assert_connector_permitted(connector_type)

    if action == CONNECTOR_ACTION_CREATE:
        cfg = ConnectorConfig(
            organization_id=resolved,
            connector_type=connector_type,
            name=payload.get("name") or connector_type,
            description=payload.get("description"),
            config=payload.get("config") or {},
            field_mappings=payload.get("field_mappings"),
            sync_schedule=payload.get("sync_schedule"),
            status=(
                payload.get("status")
                if payload.get("status")
                else ConnectorStatus.ACTIVE.value
            ),
        )
        db.session.add(cfg)
        db.session.flush()
        return cfg

    if action == CONNECTOR_ACTION_UPDATE:
        cfg = _load_org_connector(
            resolved,
            connector_id=payload.get("connector_id"),
            connector_type=connector_type,
        )
        if cfg is None:
            raise LookupError(
                f"Connector {payload.get('connector_id') or connector_type} "
                f"is not configured in this organisation."
            )
        if "name" in payload:
            cfg.name = payload["name"] or cfg.name
        if "description" in payload:
            cfg.description = payload["description"]
        if "config" in payload:
            cfg.config = payload["config"]
        if "field_mappings" in payload:
            cfg.field_mappings = payload["field_mappings"]
        if "sync_schedule" in payload:
            cfg.sync_schedule = payload["sync_schedule"]
        if "status" in payload and payload["status"]:
            cfg.status = payload["status"]
        db.session.flush()
        return cfg

    if action == CONNECTOR_ACTION_DELETE:
        cfg = _load_org_connector(
            resolved,
            connector_id=payload.get("connector_id"),
            connector_type=connector_type,
        )
        if cfg is None:
            raise LookupError(
                f"Connector {payload.get('connector_id') or connector_type} "
                f"is not configured in this organisation."
            )
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        OrgCredentialVault().delete(resolved, connector_type)
        db.session.delete(cfg)
        db.session.flush()
        return cfg

    raise ConnectorWriteRefused(f"Unsupported connector action: {action!r}")


def execute_connector_proposal(approval) -> dict:
    """Apply one approved connector proposal.

    Called by the approval service's execution dispatch. The approval row
    itself is the authorisation: only a proposal created for the same
    organisation, executed through the approval path, can write.
    """
    try:
        payload = json.loads(approval.operation_payload or "{}")
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "error": "Invalid connector proposal payload"}

    try:
        if approval.operation_type == CONNECTOR_ACTION_SYNC:
            return run_connector_sync(
                org_id=approval.organization_id,
                connector_id=payload.get("connector_id"),
                approval=approval,
            )
        result = apply_connector_change(
            org_id=approval.organization_id,
            action=approval.operation_type,
            payload=payload,
            approval=approval,
        )
    except ConnectorWriteRefused as exc:
        return {"success": False, "error": str(exc)}
    except (LookupError, ValueError, KeyError) as exc:
        return {"success": False, "error": str(exc)}

    return {"success": True, "connector_id": getattr(result, "id", None)}


def propose_or_apply_connector_change(
    *,
    org_id: int,
    actor_user_id: int | None = None,
    action: str,
    connector_type: str,
    summary: str,
    payload: dict,
) -> dict:
    """Route one connector change.

    An organisation that has delegated *action* gets it applied directly;
    every other organisation gets it queued as an approval proposal in its
    own queue. A change carries no organisation at all and is refused.
    """
    resolved = scope_connector_change_to_org(org_id=org_id)
    if not org_has_delegated_action(resolved, action):
        approval = propose_connector_change(
            org_id=resolved,
            actor_user_id=actor_user_id,
            action=action,
            connector_type=connector_type,
            summary=summary,
            payload=payload,
        )
        return {"queued": True, "approval_id": approval.id}

    cfg = apply_connector_change(
        org_id=resolved,
        action=action,
        payload=payload,
        delegated=True,
    )
    return {"applied": True, "connector_id": getattr(cfg, "id", None)}


def _orphan_sync_log(connector_id: str):
    """A SyncLog row for a connector run, created before the run so a
    failure still leaves a record the page can report."""
    sync_log = SyncLog(
        connector_id=connector_id,
        sync_type="manual",
        status="running",
    )
    db.session.add(sync_log)
    db.session.flush()
    return sync_log


def _write_touched_crosswalk_links(connector_type: str, touched, org_id: int) -> int:
    """Write one identifier crosswalk link per element a sync touched.

    Goes through ``CrosswalkService.write_link`` -- the single gated
    crosswalk writer, which applies ``assert_connector_permitted`` before
    the write -- so a sync never reaches the model through a second
    identity path.
    """
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService

    written = 0
    for item in touched or []:
        external_id = item.get("external_id")
        element_id = item.get("element_id")
        if not external_id or not element_id:
            continue
        CrosswalkService.write_link(
            source_system=connector_type,
            external_id=str(external_id),
            element_id=int(element_id),
            org_id=org_id,
        )
        written += 1
    return written


def run_connector_sync(
    *,
    org_id: int,
    connector_id: str,
    approval=None,
    delegated: bool = False,
) -> dict:
    """Run one connector sync and record health, last synchronisation and
    the identifier crosswalk links for every element touched.

    The one sync path behind the connectors page. Refuses a sync that is
    neither an approved proposal nor delegated, exactly like configuration
    writes: a sync reaches the external system and the model only through
    the organisation's own queue (or its explicit delegation).
    """
    import asyncio

    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.modules.intelligence.services.connector_allowlist import (
        assert_connector_permitted,
    )

    resolved = scope_connector_change_to_org(org_id=org_id)
    if approval is None and not delegated:
        raise ConnectorWriteRefused(
            "A connector sync is refused unless it is an approved approval "
            "proposal or the organisation has delegated sync."
        )
    if approval is not None and approval.organization_id != resolved:
        raise ConnectorWriteRefused(
            "The approval executing this connector sync belongs to another "
            "organisation."
        )

    cfg = _load_org_connector(resolved, connector_id=connector_id)
    if cfg is None:
        return {"success": False, "error": "Connector not found"}

    assert_connector_permitted(cfg.connector_type)

    sync_log = _orphan_sync_log(cfg.id)

    try:
        connector = build_connector(cfg, resolved)
    except LookupError as exc:
        sync_log.status = "error"
        sync_log.error_message = str(exc)
        sync_log.completed_at = datetime.utcnow()
        db.session.flush()
        return {"success": False, "error": str(exc)}

    try:
        call = connector.batch_sync()
        result = asyncio.run(call) if asyncio.iscoroutine(call) else call
    except Exception as exc:
        logger.error("Connector sync failed for %s org %s: %s", cfg.id, resolved, exc)
        sync_log.status = "error"
        sync_log.error_message = str(exc)
        sync_log.completed_at = datetime.utcnow()
        cfg.status = ConnectorStatus.ERROR.value
        db.session.flush()
        return {"success": False, "error": str(exc)}

    result = result or {}
    sync_log.status = result.get("status", "completed")
    sync_log.records_processed = result.get("records_processed", 0)
    sync_log.records_created = result.get("records_created", 0)
    sync_log.records_updated = result.get("records_updated", 0)
    sync_log.records_deleted = result.get("records_deleted", 0)
    sync_log.completed_at = datetime.utcnow()

    cfg.last_sync = datetime.utcnow()
    cfg.status = (
        ConnectorStatus.ACTIVE.value
        if sync_log.status in ("completed", "success")
        else ConnectorStatus.ERROR.value
    )

    touched = result.get("elements_touched")
    if touched:
        result["crosswalk_links_written"] = _write_touched_crosswalk_links(
            cfg.connector_type, touched, resolved
        )

    db.session.flush()
    logger.info("Connector sync completed for %s org %s: %s", cfg.id, resolved, result)
    result["success"] = True
    return result


def build_connector(cfg: ConnectorConfig, org_id: int):
    """Build the concrete connector for a configuration, injecting the
    organisation's stored credentials for the run.

    Raises ``LookupError`` when the connector type has no concrete
    implementation registered in ``app/connectors/``.
    """
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    _register_concrete_connectors()
    connector_class = _CONNECTOR_CLASSES.get(cfg.connector_type)
    if connector_class is None:
        raise LookupError(
            f"No sync implementation is registered for connector type "
            f"{cfg.connector_type!r}."
        )

    credentials = OrgCredentialVault().retrieve_credentials(
        org_id, cfg.connector_type
    )
    merged_config = dict(cfg.config or {})
    if credentials:
        merged_config.update(credentials)

    runtime_config = ConnectorConfig(
        id=cfg.id,
        connector_type=cfg.connector_type,
        name=cfg.name,
        organization_id=cfg.organization_id,
        config=merged_config,
        sync_schedule=cfg.sync_schedule,
    )
    return connector_class(runtime_config)
