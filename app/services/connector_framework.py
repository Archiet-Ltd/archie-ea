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

from app.services.change_source import records_changes_as
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
    _is_sensitive_key,
    _mask_sensitive,
)

logger = logging.getLogger(__name__)


class ConnectorURLRefused(ValueError):
    """A connector base URL the server must not call. The message is safe to show."""


def validate_connector_url(url) -> str:
    """Return ``url`` stripped of surrounding space and a trailing slash, or raise.

    The one check for every connector base URL (ServiceNow, Jira, Abacus):
    ``validate_user_supplied_url``, the same guard the webhooks use. Applied when
    a URL is saved and again before every call, because a saved name can start
    resolving to an internal address. The guard's detail (resolved addresses) is
    logged, not returned.
    """
    from app.utils.ssrf_guard import BlockedOutboundURL, validate_user_supplied_url

    if not isinstance(url, str) or not url.strip():
        raise ConnectorURLRefused("A connector URL is required")
    url = url.strip()
    try:
        validate_user_supplied_url(url)
    except BlockedOutboundURL as exc:
        logger.warning("Connector URL refused: %s", exc)
        raise ConnectorURLRefused("The connector URL is not an allowed address") from exc
    # A query or fragment on a base address would end up in the middle of the
    # request path once a path is appended; a base is scheme, host and path only.
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/")


def connector_url_field_allowed(field) -> bool:
    """Check a WTForms URL field with the outbound-URL guard before it is saved.

    On refusal the reason is added to the field and nothing should be stored; on
    success the field holds the cleaned URL.
    """
    try:
        field.data = validate_connector_url(field.data)
    except ConnectorURLRefused as exc:
        field.errors = list(field.errors) + [str(exc)]
        return False
    return True


def validate_connector_config_urls(config) -> None:
    """Check every URL-valued setting in a connector config dict (raises ConnectorURLRefused).

    A setting counts as a URL when its name ends in ``url`` or is ``endpoint``
    and its value is a non-empty string. Used where a free-form config object is
    saved, so a base URL cannot be slipped in under a connector type that has no
    form of its own.
    """
    if not isinstance(config, dict):
        return
    for key, value in config.items():
        name = str(key).lower()
        if (name.endswith("url") or name == "endpoint") and isinstance(value, str) and value.strip():
            validate_connector_url(value)


async def _refuse_redirect(session, trace_config_ctx, params):
    """aiohttp trace hook: a connector call is never redirected."""
    raise ConnectorURLRefused("The connector host answered with a redirect, which is not followed")


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

    def _lookup(self, source_data: Dict[str, Any]) -> Any:
        """The source value: the key as written, else a dotted path into nested data.

        Jira nests its fields (``fields.status.name``); a flat ``.get`` on that
        name never finds them, so every nested mapping silently came back empty.
        """
        if self.source_field in source_data:
            value = source_data[self.source_field]
            return self.default_value if value is None else value
        node: Any = source_data
        for part in self.source_field.split("."):
            if not isinstance(node, dict) or part not in node:
                return self.default_value
            node = node[part]
        return self.default_value if node is None else node

    def apply(self, source_data: Dict[str, Any]) -> Any:
        """Apply field mapping to source data."""
        value = self._lookup(source_data)

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

    def _checked_base_url(self) -> str:
        """The configured base_url, after the outbound-URL guard. Raises ConnectorURLRefused."""
        return validate_connector_url((self.config.config or {}).get("base_url"))

    def _outbound_session(self, **kwargs):
        """An aiohttp session for calls to this connector's base_url.

        Checks the base URL first (every call, not only at save time) and
        refuses redirects: a trace hook raises when a redirect response arrives,
        before aiohttp issues the next request, so a permitted host cannot
        bounce the request, with its credentials, to an internal address.
        """
        import aiohttp

        self._checked_base_url()
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(_refuse_redirect)
        return aiohttp.ClientSession(trace_configs=[trace], **kwargs)

    def _same_origin_link(self, link: str) -> str:
        """A server-supplied continuation link, only if it stays on the base URL's origin.

        The guard and the origin comparison (scheme and host) apply to the link
        as given; the link itself is returned unchanged, query string included,
        because a paging token lives in the query.
        """
        from urllib.parse import urlsplit

        validate_connector_url(link)  # raises ConnectorURLRefused if not allowed
        got = urlsplit(link.strip())
        want = urlsplit(self._checked_base_url())
        if (got.scheme.lower(), got.netloc.lower()) != (want.scheme.lower(), want.netloc.lower()):
            raise ConnectorURLRefused("The connector URL is not an allowed address")
        return link.strip()

    def _apply_field_mappings(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Map one source record onto target field names.

        A required field that is missing raises ``ValueError``; the caller counts
        that record as failed. Fields with no value are left out.
        """
        mapped: Dict[str, Any] = {}
        for mapping in self.get_field_mappings():
            value = mapping.apply(record)
            if value is not None:
                mapped[mapping.target_field] = value
        return mapped

    def persist_records(
        self,
        raw_records: List[Dict[str, Any]],
        *,
        source_system: str,
        to_element: Callable[[Dict[str, Any]], Dict[str, Any]],
        evidence: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Write fetched source records into the connector's organisation.

        The one persistence step every connector uses. ``to_element`` turns one
        raw record into ``{"external_id", "name", "element_type", "description"}``
        (raising ``ValueError`` when the record cannot be mapped). Each record is
        matched through the crosswalk and the matcher, then created through the
        one element writer or updated in place, and linked in the crosswalk, all
        inside a savepoint: a record that fails is counted and logged by id (never
        by content or credential) and leaves nothing behind, and the run goes on.

        With ``evidence`` (an object with ``matched(element, record) -> bool`` and
        ``unmatched(record, match) -> "proposed" | "skipped"``) the source is evidence
        for elements that already exist rather than a source of elements: a certain
        match is handed to ``matched`` (which adds facts and never renames), anything
        else to ``unmatched`` (which queues a proposal), and no element is created.
        Matching, the savepoint per record, the crosswalk link and the counts are the
        same; proposals are counted as ``records_proposed``.

        The organisation is the connector's own; a connector with none writes
        nothing. Returns the counts the sync log and ``run_connector_sync`` read.
        """
        org_id = getattr(self.config, "organization_id", None)
        if not org_id:
            raise ConnectorWriteRefused(
                "A connector sync cannot write to the model without an organisation."
            )

        counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "proposed": 0}
        touched: List[Dict[str, Any]] = []
        for raw in raw_records:
            try:
                with db.session.begin_nested():
                    outcome, element_id, external_id = _persist_element_record(
                        org_id, source_system, to_element(raw), evidence=evidence
                    )
            except Exception as exc:
                counts["failed"] += 1
                self.logger.warning(
                    "%s record %s was not written (%s)",
                    source_system,
                    _record_label(raw),
                    _failure_reason(exc),
                )
                continue
            counts[outcome] += 1
            if element_id is not None:
                touched.append({"external_id": external_id, "element_id": element_id})

        processed = len(raw_records)
        if counts["failed"] and counts["failed"] == processed:
            status = "error"
        elif counts["failed"]:
            status = "partial"
        else:
            status = "completed"
        result: Dict[str, Any] = {
            "status": status,
            "records_processed": processed,
            "records_created": counts["created"],
            "records_updated": counts["updated"],
            "records_skipped": counts["skipped"],
            "records_proposed": counts["proposed"],
            "records_failed": counts["failed"],
            "records_deleted": 0,
            "elements_touched": touched,
        }
        if counts["failed"]:
            result["error_message"] = (
                f"{counts['failed']} of {processed} records could not be written"
            )
        return result

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


def _record_label(raw: Any) -> str:
    """A source record's id for log lines: an identifier only, never its content."""
    if isinstance(raw, dict):
        for key in ("sys_id", "id", "key"):
            if isinstance(raw.get(key), (str, int)):
                return str(raw[key])
    return "(no id)"


def _failure_reason(exc: Exception) -> str:
    """Why a record failed, safe to log: our own validation messages, otherwise the class."""
    if isinstance(exc, (ValueError, LookupError)):
        return f"{type(exc).__name__}: {exc}"
    return type(exc).__name__


def _stored_element_name(name: str) -> str:
    """The name as the element writer stores it (it caps the length)."""
    from app.services.archimate_backbone import MAX_NAME

    return name if len(name) <= MAX_NAME else name[: MAX_NAME - 1] + "…"


def _review_already_queued(org_id: int, matched_element_id: int) -> bool:
    """True when the matcher already put this element in the organisation's review queue.

    The queue holds one matcher row per matched element (unique source), so a
    second proposal for the same element, from this run or the next, is not made.
    """
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    return (
        AIChatCRUDApproval.query.filter_by(
            organization_id=org_id,
            source_table="matcher_service",
            source_id=matched_element_id,
        ).first()
        is not None
    )


def _persist_evidence_record(org_id, source_system, external_id, match, evidence, record):
    """The evidence-mode ending of ``_persist_element_record``: a certain match gets
    facts added to its element (and is linked in the crosswalk); anything else is
    handed to the evidence object to queue as a proposal. Creates no element.
    Returns ``(outcome, element_id, external_id)``."""
    from app.models.archimate_core import ArchiMateElement
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService

    if match.matched_element_id is None or not match.certain:
        return evidence.unmatched(record, match), None, external_id
    element = db.session.get(ArchiMateElement, match.matched_element_id)
    if element is None or element.organization_id != org_id:
        raise LookupError("the linked element is not in this organisation")
    if getattr(element, "deleted_at", None) is not None:
        return "skipped", None, external_id
    changed = evidence.matched(element, record)
    CrosswalkService.write_link(source_system, external_id, element.id, org_id=org_id)
    return ("updated" if changed else "skipped"), element.id, external_id


def _persist_element_record(
    org_id: int, source_system: str, record: Dict[str, Any], *, evidence: Optional[Any] = None
):
    """Create, update or leave one source record's element. Returns
    ``(outcome, element_id, external_id)`` with outcome ``created``, ``updated``
    or ``skipped`` (``proposed`` too, in evidence mode).

    Identity is decided by the existing parts: the identifier crosswalk first,
    then the matcher's name match. A certain match is updated in place; an
    uncertain one is proposed into the organisation's approval queue and not
    written; no match creates the element through the one element writer
    (``create_backbone_element``). Every path ends in ``CrosswalkService.write_link``.
    Raises on any record that cannot be written; the caller's savepoint undoes it.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService
    from app.modules.intelligence.services.matcher_service import MatcherService
    from app.services.archimate_backbone import create_backbone_element
    from app.services.archimate_import_service import ArchiMateImportService

    external_id = str(record.get("external_id") or "").strip()
    name = record.get("name")
    name = name.strip() if isinstance(name, str) else ""
    element_type = record.get("element_type")
    description = record.get("description")
    description = description if isinstance(description, str) and description.strip() else None
    if not external_id:
        raise ValueError("the record has no identifier")
    if not name:
        raise ValueError("the record has no name")
    layer = ArchiMateImportService.TYPE_TO_LAYER.get(element_type)
    if layer is None:
        raise ValueError(f"unknown element type {element_type!r}")

    match = MatcherService.match(
        source_system=source_system,
        external_id=external_id,
        name=name,
        type_name=element_type,
        org_id=org_id,
    )

    # A name match onto an element this same source already links to under a
    # different identifier is a different record that happens to share a title.
    if match.matched_element_id is not None and match.match_method != "crosswalk":
        links = CrosswalkService.get_links_for_element(match.matched_element_id, org_id=org_id)
        if any(
            link.source_system == source_system and link.external_id != external_id
            for link in links
        ):
            match = MatcherService.match(
                source_system=source_system, external_id=external_id, org_id=org_id
            )

    if evidence is not None:
        return _persist_evidence_record(org_id, source_system, external_id, match, evidence, record)

    if match.matched_element_id is None:
        element = create_backbone_element(
            element_type=element_type,
            layer=layer,
            name=name,
            description=description,
            organization_id=org_id,
        )
        CrosswalkService.write_link(source_system, external_id, element.id, org_id=org_id)
        return "created", element.id, external_id

    if not match.certain:
        if not _review_already_queued(org_id, match.matched_element_id):
            MatcherService.propose_match(
                source_system=source_system,
                external_id=external_id,
                name=name,
                type_name=element_type,
                org_id=org_id,
            )
        return "skipped", None, external_id

    element = db.session.get(ArchiMateElement, match.matched_element_id)
    if element is None or element.organization_id != org_id:
        raise LookupError("the linked element is not in this organisation")
    if getattr(element, "deleted_at", None) is not None:
        return "skipped", None, external_id

    changed = match.match_method != "crosswalk"  # a first link counts as an update
    if match.match_method == "crosswalk":
        stored_name = _stored_element_name(name)
        if element.name != stored_name:
            element.name, changed = stored_name, True
        if element.type != element_type or element.layer != layer:
            element.type, element.layer, changed = element_type, layer, True
        if description is not None and element.description != description:
            element.description, changed = description, True
        if changed:
            db.session.flush()
    CrosswalkService.write_link(source_system, external_id, element.id, org_id=org_id)
    return ("updated" if changed else "skipped"), element.id, external_id


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

# The one secret each connector type's own save route collects, and the
# vault credential_type it belongs under once approved (M1). The generic
# connectors page stores a JSON blob under "credentials"
# (OrgCredentialVault.store_credentials); the three per-connector forms
# each store one named value instead.
CONNECTOR_CREDENTIAL_TYPES = {
    "github": "api_token",
    "jira": "api_token",
    "m365": "client_secret",
    "servicenow": "client_secret",
}

# F2 (cloud-review-pr448-a85f082b-v1.md): the legacy config keys each
# connector type's secret used to live under, before it moved to the
# vault. H2's carry-forward (below, in apply_connector_change) must drop
# these -- not preserve them -- whenever this approval is actually
# rotating the vault credential, or the superseded value sits on the row
# forever beside its replacement.
CONNECTOR_LEGACY_CONFIG_KEYS = {
    "github": {"api_token", "api_token_encrypted"},
    "jira": {"api_token", "api_token_encrypted"},
    "m365": {"client_secret"},
    "servicenow": {"client_secret"},
}

# Connector types surfaced by the connectors page. A type is only offered
# from a per-organisation configuration row already in the system; this is
# the display order, not an allowlist -- the permit gate is
# ``assert_connector_permitted``, so only types on that allowlist are
# offered in the add form.
CONNECTOR_TYPE_LABELS = {
    "servicenow": "ServiceNow",
    "jira": "Jira",
    "m365": "Microsoft 365",
    "ea_tool": "Enterprise Architecture tool",
    "github": "GitHub",
    "devops": "Code push (GitHub / Azure DevOps)",
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
    from app.connectors.github import GitHubConnector
    from app.connectors.jira import JiraALMConnector
    from app.connectors.servicenow import ServiceNowCMDBConnector

    _CONNECTOR_CLASSES.update(
        {
            "abacus": AbacusConnector,
            "ea_tool": AbacusConnector,
            "github": GitHubConnector,
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


def _connector_credential_mask(org_id: int, connector_type: str):
    """Masked proof that this organisation stores a credential for the
    connector type, across every credential type the change paths use."""
    if not connector_type:
        return None
    from app.models.connector_config import OrgConnectorCredential

    row = (
        OrgConnectorCredential.query.filter_by(
            organization_id=org_id, connector_type=connector_type
        ).first()
    )
    return "******" if row is not None else None


def _connector_view(cfg: ConnectorConfig, org_id: int) -> dict:
    """One connector as the connectors page and API render it.

    Carries configuration, credential reference (masked — never a secret),
    schedule, health (status) and last synchronisation, so the page has a
    single source per connector from the framework.
    """
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
        "credential_masked": _connector_credential_mask(org_id, cfg.connector_type),
        "credential_stored": bool(_connector_credential_mask(org_id, cfg.connector_type)),
        "last_sync": cfg.last_sync,
        "last_sync_status": latest_sync.status if latest_sync else None,
        "description": cfg.description,
        "is_proposal": False,
        "created_at": cfg.created_at,
        "updated_at": cfg.updated_at,
    }


def _proposal_connector_view(approval, org_id: int) -> dict:
    """A queued connector change as a pending row on the connectors page.

    Shown until its proposal is decided so the page is the one place that
    lists every connector of the organisation — configured ones with health
    and last synchronisation, and proposed ones with their (masked)
    credential state and the approval waiting in the inbox.
    """
    try:
        payload = json.loads(approval.operation_payload or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    connector_type = payload.get("connector_type") or ""
    return {
        "id": f"proposal-{approval.id}",
        "name": (payload.get("name") or connector_type or "Proposed connector"),
        "connector_type": connector_type,
        "connector_type_label": CONNECTOR_TYPE_LABELS.get(
            connector_type, connector_type or "connector"
        ),
        "status": "pending_review",
        "health": "pending_review",
        "sync_mode": None,
        "sync_schedule": None,
        # Masked for the same reason _connector_view uses public_config(): a
        # secret mistakenly typed into the configuration box (H1) must not
        # reach a pending-approval listing in plain text, even while the
        # proposal is still waiting for a decision.
        "config": _mask_sensitive(payload.get("config") or {}),
        "credential_ref": f"vault:{org_id}:{connector_type}" if connector_type else None,
        "credential_masked": _connector_credential_mask(org_id, connector_type),
        "credential_stored": bool(_connector_credential_mask(org_id, connector_type)),
        "last_sync": None,
        "last_sync_status": None,
        "description": approval.summary,
        "is_proposal": True,
        "proposal_id": approval.id,
        "operation_type": approval.operation_type,
        "created_at": approval.created_at,
        "updated_at": None,
    }


def list_org_connectors(org_id: int) -> list[dict]:
    """Every connector of *org_id* with health and last synchronisation,
    followed by the organisation's queued connector creations as pending rows.

    The one reader the connectors page uses. Only rows — and only approval
    proposals — the organisation owns are ever returned.
    """
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    rows = ConnectorConfig.query.filter_by(organization_id=org_id).all()
    connectors = [_connector_view(row, org_id) for row in rows]

    # A queued create has no row yet, but the page must still show it (with
    # its masked credential) so the organisation's one connectors page lists
    # every connector that has been proposed, not only those already applied.
    # Update and delete proposals act on an existing row, which is already
    # listed above with its current state.
    create_proposals = (
        AIChatCRUDApproval.query.filter_by(
            organization_id=org_id,
            entity_type=CONNECTOR_APPROVAL_ENTITY_TYPE,
            operation_type=CONNECTOR_ACTION_CREATE,
            status=ApprovalStatus.PENDING,
        )
        .order_by(AIChatCRUDApproval.created_at.asc())
        .all()
    )
    for proposal in create_proposals:
        connectors.append(_proposal_connector_view(proposal, org_id))
    return connectors


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

    def _promote_staged_credential_if_any() -> None:
        # M1: the save route staged this connector type's secret (if any)
        # under this approval rather than writing it live; now that the
        # write below has actually succeeded, promote it. A no-op when
        # nothing was staged (plain config edit, no new secret in this
        # change). N4 (cloud-review-pr448-505c3c2e-v1.md): called only
        # after the write succeeds -- promoting first would leave a secret
        # live for an approval whose write then fails (e.g. the connector
        # was deleted between proposal and approval).
        if approval is None or action not in (CONNECTOR_ACTION_CREATE, CONNECTOR_ACTION_UPDATE):
            return
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        vault = OrgCredentialVault()
        credential_type = CONNECTOR_CREDENTIAL_TYPES.get(connector_type)
        if credential_type:
            vault.promote_staged(resolved, connector_type, credential_type, approval.id)
        # The generic connectors page always stages under "credentials",
        # regardless of connector type.
        vault.promote_staged(resolved, connector_type, "credentials", approval.id)

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
        _promote_staged_credential_if_any()
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
            new_config = dict(payload["config"] or {})
            # A whole-config replace on update must not erase a sensitive key
            # the new payload simply did not resend (the Jira/M365 forms now
            # route secrets to the vault and never put them in this payload
            # at all). Carry forward anything sensitive already on the row
            # that the new config omits, so an update never silently wipes a
            # token the service still reads from here. H2.
            #
            # F2 (cloud-review-pr448-a85f082b-v1.md): H2's own carry-forward
            # kept a rotated secret on the row beside its vault replacement
            # forever, because it could not tell "nothing new to carry"
            # apart from "something new is replacing this".
            #
            # Earlier versions of this check inferred rotation from whether
            # *this approval* had a staged credential -- which only exists
            # on the queued path. A delegated organisation's save route
            # applies the config change through this same function before
            # it ever writes the new credential to the vault (no approval
            # exists to stage anything under), so that inference was always
            # False for a delegated rotation and the superseded row key
            # was carried forward forever -- the exact condition this check
            # exists to remove. The save route is the only place that knows
            # whether a credential is actually part of this change, on
            # either path, so it sets payload["credential_provided"]
            # itself; read that directly instead of re-deriving it from
            # vault/approval state.
            is_rotating = bool(payload.get("credential_provided"))
            legacy_keys = CONNECTOR_LEGACY_CONFIG_KEYS.get(connector_type, set()) if is_rotating else set()

            existing_config = cfg.config or {}
            for key, value in existing_config.items():
                if key in legacy_keys:
                    continue
                if key not in new_config and _is_sensitive_key(key):
                    new_config[key] = value
            cfg.config = new_config
        if "field_mappings" in payload:
            cfg.field_mappings = payload["field_mappings"]
        if "sync_schedule" in payload:
            cfg.sync_schedule = payload["sync_schedule"]
        if "status" in payload and payload["status"]:
            cfg.status = payload["status"]
        db.session.flush()
        _promote_staged_credential_if_any()
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
        from app.models.connector_config import OrgConnectorCredential

        # Remove every credential of the deleted connector (all credential
        # types — the ''credentials'' blob, legacy ''client_secret'' rows,
        # whatever else the connector used) from the per-organisation vault.
        OrgConnectorCredential.query.filter_by(
            organization_id=resolved, connector_type=connector_type
        ).delete()
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


def run_coroutine_sync(coro):
    """Run a coroutine to its result from synchronous code.

    With no event loop running in this thread this is asyncio.run. When a loop is
    already running here (a web worker, a job runner, a browser driver in the same
    process) asyncio.run refuses. This thread is inside a synchronous call, so the
    outer loop cannot run until the call returns: the outer loop is set aside, the
    coroutine runs to its end on a fresh loop in this same thread, and the outer loop
    is put back. Nothing else is started, so the application context, the database
    session and the organisation scope are the caller's own.
    """
    import asyncio
    from asyncio import events

    # events._set_running_loop is private asyncio API; checked on Python 3.12.3
    # (the build machine venv). Re-check here when the Python version is upgraded.
    outer = events._get_running_loop()
    if outer is None:
        return asyncio.run(coro)
    events._set_running_loop(None)
    try:
        return asyncio.run(coro)
    finally:
        events._set_running_loop(outer)


@records_changes_as("connector")
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
        result = run_coroutine_sync(call) if asyncio.iscoroutine(call) else call
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
    if result.get("error_message"):
        sync_log.error_message = result["error_message"]
    sync_log.completed_at = datetime.utcnow()

    # A partial run (some records written, some not) is still a working
    # connector; the failures are on the sync log.
    cfg.last_sync = datetime.utcnow()
    cfg.status = (
        ConnectorStatus.ACTIVE.value
        if sync_log.status in ("completed", "success", "partial")
        else ConnectorStatus.ERROR.value
    )

    touched = result.get("elements_touched")
    if touched:
        result["crosswalk_links_written"] = _write_touched_crosswalk_links(
            cfg.connector_type, touched, resolved
        )

    db.session.flush()
    logger.info("Connector sync completed for %s org %s: %s", cfg.id, resolved, result)
    result["success"] = sync_log.status != "error"
    if not result["success"]:
        result.setdefault("error", result.get("error_message") or "The sync failed")
    return result


def _credential_field_map(connector_type: str, org_id: int, config: dict) -> dict:
    """Map this connector type's own config/vault fields onto the field
    names its concrete class requires (``get_required_config_fields``).

    M4 (cloud-review-pr448-961a0c35-v1.md): the generic connectors page
    stores one vault entry under credential_type="credentials" as
    {"credential": "..."}, a key no connector class reads, and the
    per-connector forms keep their own vault keys ("api_token",
    "client_secret") that build_connector never looked at either -- no
    concrete connector could authenticate from anything either save path
    collected. Each branch here reads from the same place that connector
    type's own save route actually writes.
    """
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    vault = OrgCredentialVault()

    def _secret(credential_type: str) -> str:
        # N6 (cloud-review-pr448-a85f082b-v1.md): the per-connector forms
        # store their secret under this connector type's own named vault
        # key, but the generic /admin/connectors page always stores under
        # "credentials" as {"credential": "..."} regardless of connector
        # type -- a connector added through that page had no api_token/
        # client_secret key for build_connector to find at all. Fall back
        # to the generic blob so either save path reaches the connector.
        value = vault.retrieve(org_id, connector_type, credential_type)
        if value:
            return value
        generic = vault.retrieve_credentials(org_id, connector_type) or {}
        return generic.get("credential", "") or ""

    if connector_type == "jira":
        return {
            "base_url": config.get("instance_url", ""),
            "username": config.get("email", ""),
            "api_token": _secret("api_token"),
            "project_keys": [config["default_project_key"]] if config.get("default_project_key") else [],
        }

    if connector_type == "servicenow":
        # servicenow_config_save (and the service that uses this config)
        # authenticate with OAuth2 client credentials only -- client_id
        # (non-secret) and client_secret (vault). ServiceNowCMDBConnector's
        # own get_required_config_fields also lists "username"/"password",
        # but no save path anywhere collects either; inventing a value for
        # them would be fabricated data, so they are left absent rather
        # than guessed. A connector_type that actually needs a password
        # grant needs its own form field for it, not a value made up here.
        return {
            "base_url": config.get("instance_url", ""),
            "client_id": config.get("client_id", ""),
            "client_secret": _secret("client_secret"),
        }

    if connector_type == "github":
        # The GitHub organisation to read and, for GitHub Enterprise Server, its
        # API address come from the configuration; the bearer the reader sends
        # comes from the one token provider (app/connectors/github_app.py), which
        # mints a fresh installation token when the organisation installed the
        # GitHub App and falls back to the pasted personal token otherwise -- one
        # read path regardless of how the organisation connected.
        from app.connectors.github_app import resolve_api_token

        return {
            "organization": config.get("organization", ""),
            "base_url": config.get("base_url", ""),
            "api_token": resolve_api_token(org_id),
        }

    # No field map for this connector type: fall back to the generic
    # credentials blob the connectors page itself writes, unchanged.
    return vault.retrieve_credentials(org_id, connector_type) or {}


def build_connector(cfg: ConnectorConfig, org_id: int):
    """Build the concrete connector for a configuration, injecting the
    organisation's stored credentials for the run.

    Raises ``LookupError`` when the connector type has no concrete
    implementation registered in ``app/connectors/``.
    """
    _register_concrete_connectors()
    connector_class = _CONNECTOR_CLASSES.get(cfg.connector_type)
    if connector_class is None:
        raise LookupError(
            f"No sync implementation is registered for connector type "
            f"{cfg.connector_type!r}."
        )

    base_config = dict(cfg.config or {})
    merged_config = dict(base_config)
    merged_config.update(_credential_field_map(cfg.connector_type, org_id, base_config))

    runtime_config = ConnectorConfig(
        id=cfg.id,
        connector_type=cfg.connector_type,
        name=cfg.name,
        organization_id=cfg.organization_id,
        config=merged_config,
        sync_schedule=cfg.sync_schedule,
    )
    return connector_class(runtime_config)
