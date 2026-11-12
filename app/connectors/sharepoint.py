"""SharePoint (read): an organisation's own SharePoint list becomes its application inventory.

This is the first part of the connector. An administrator chooses one list on the
site saved on the Microsoft 365 page, maps the list's columns to the fields an
application has, and saves the connection. Reading the items comes with the next
part; until then ``batch_sync`` says plainly that nothing has been read.

The connection reads with the organisation's own Microsoft 365 registration (tenant,
client and secret from the Microsoft 365 page; the token comes from
``M365Service.graph_token``). It holds no secret of its own.

Isolation. Every address this connector asks Microsoft for is built by
``selection_path`` from a ``Selection`` (tenant, site, list) taken from the
organisation's own saved settings. A list id from anywhere else is refused with
``SelectionRefused`` before a request is made, and a list is only accepted when it is
one of the lists currently on the saved site.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.api_clients.graph_client import GraphClient, GraphError
from app.services.connector_framework import (
    BaseConnector,
    ConnectorConfig,
    ConnectorType,
    FieldMapping,
)

logger = logging.getLogger(__name__)

CONNECTOR_TYPE = "sharepoint_read"

# The fields of an application the list can fill, in the order the page shows them, with the
# words a person reads. The mapping is a dict from field to the column's internal name.
MODEL_FIELDS = (
    ("name", "Name"),
    ("description", "Description"),
    ("owner", "Owner"),
    ("lifecycle", "Lifecycle"),
    ("annual_cost", "Annual cost"),
)

# The column titles that are proposed for each field, most likely first.
_PROPOSALS = {
    "name": ("title", "name", "application"),
    "description": ("description",),
    "owner": ("owner", "businessowner"),
    "annual_cost": ("annualcost", "cost"),
    "lifecycle": ("lifecycle", "status"),
}

NO_COLUMN = "No column chosen"
NOT_READ_YET = "This list is saved. Its items have not been read yet."
LIST_NOT_ALLOWED = "That list is not on a site this organisation can read"


class SharePointError(RuntimeError):
    """A SharePoint problem whose message is plain words a person can read."""


class SelectionRefused(SharePointError):
    """A site or list that is not the one this organisation saved was asked for."""


@dataclass(frozen=True)
class Selection:
    """The only source of the site and list a request may name."""

    tenant_id: str
    site_id: str
    list_id: Optional[str] = None


def selection_path(selection: Selection, suffix: str) -> str:
    """The Graph path for ``suffix`` ("lists" or "columns"), built only from the selection."""
    site = GraphClient.segment(selection.site_id)
    if suffix == "lists":
        return f"sites/{site}/lists"
    if suffix == "columns":
        if not selection.list_id:
            raise SelectionRefused("No list has been chosen")
        return f"sites/{site}/lists/{GraphClient.segment(selection.list_id)}/columns"
    raise SelectionRefused("That part of the list is not read")


def _fold(text: Any) -> str:
    """A column title for comparison: lower case, no spaces or underscores."""
    return re.sub(r"[\s_]+", "", str(text or "")).lower()


def propose_mapping(columns: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """The column proposed for each field, by title; ``None`` where no column fits.

    Hidden and read-only columns are never proposed. Each column is proposed for at most
    one field. A person can change every proposal on the page.
    """
    offered = [c for c in columns if not c.get("hidden") and not c.get("read_only")]
    taken: set = set()
    proposal: Dict[str, Optional[str]] = {}
    for field, _label in MODEL_FIELDS:
        proposal[field] = None
        for column in offered:
            if column["name"] in taken:
                continue
            if _fold(column.get("display_name")) in _PROPOSALS[field]:
                proposal[field] = column["name"]
                taken.add(column["name"])
                break
    return proposal


def clean_mapping(posted: Dict[str, Any], columns: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """The mapping a person posted, kept only where it names an offered column of this list."""
    offered = {c["name"] for c in columns if not c.get("hidden") and not c.get("read_only")}
    mapping: Dict[str, Optional[str]] = {}
    for field, _label in MODEL_FIELDS:
        value = (posted.get(field) or "").strip() if isinstance(posted.get(field), str) else ""
        mapping[field] = value if value in offered else None
    return mapping


class SharePointConnector(BaseConnector):
    """Reads one SharePoint list of the organisation's saved site."""

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.APM

    # ----- building ------------------------------------------------------

    @classmethod
    def for_organisation(
        cls,
        org_id: int,
        *,
        saved: Optional[ConnectorConfig] = None,
        list_id: Optional[str] = None,
        list_name: Optional[str] = None,
        mapping: Optional[Dict[str, Any]] = None,
    ) -> "SharePointConnector":
        """The connector for an organisation, from its own Microsoft 365 row and, once saved, its own list.

        Raises ``SharePointError`` (plain words) when the Microsoft 365 page lacks tenant, client or site.
        """
        from app.services.connector_framework import _load_org_connector

        m365 = _load_org_connector(org_id, connector_type="m365")
        settings = (m365.config or {}) if m365 else {}
        missing = [
            label
            for key, label in (("tenant_id", "tenant"), ("client_id", "client"), ("site_id", "SharePoint site"))
            if not str(settings.get(key) or "").strip()
        ]
        if m365 is None or missing:
            raise SharePointError(
                "Save the tenant, client and SharePoint site on the Microsoft 365 page first"
                + (f" (missing: {', '.join(missing)})" if m365 is not None else "")
            )
        saved_settings = (saved.config or {}) if saved is not None else {}
        config = {
            "tenant_id": str(settings["tenant_id"]).strip(),
            "site_id": str(settings["site_id"]).strip(),
            "list_id": list_id if list_id is not None else saved_settings.get("list_id"),
            "list_name": list_name if list_name is not None else saved_settings.get("list_name"),
            "mapping": mapping if mapping is not None else saved_settings.get("mapping") or {},
            "provider": "own_registration",
        }
        runtime = ConnectorConfig(
            id=saved.id if saved is not None else None,
            connector_type=CONNECTOR_TYPE,
            name="SharePoint (read)",
            organization_id=org_id,
            config=config,
        )
        connector = cls(runtime)
        connector._m365 = m365
        return connector

    _m365: Any = None

    # ----- the selection and the client ---------------------------------

    @property
    def selection(self) -> Selection:
        settings = self.config.config or {}
        return Selection(
            tenant_id=str(settings.get("tenant_id") or ""),
            site_id=str(settings.get("site_id") or ""),
            list_id=settings.get("list_id") or None,
        )

    def _client(self) -> GraphClient:
        if self._m365 is None:
            raise SharePointError("The Microsoft 365 connection is not available")
        from app.modules.codegen.services.credential_encryption import CredentialUnreadable
        from app.services.m365_service import M365Service

        try:
            token = M365Service().graph_token(self._m365)
        except CredentialUnreadable as exc:
            raise SharePointError(str(exc)) from exc
        if not token:
            raise SharePointError(
                "Microsoft did not give Entelim a sign-in for this connection. "
                "Check the client secret on the Microsoft 365 page."
            )
        return GraphClient(token)

    def _fail(self, exc: GraphError) -> SharePointError:
        logger.warning("SharePoint request failed: %s", exc)
        return SharePointError(f"Microsoft could not be read: {exc}")

    # ----- lists and columns ---------------------------------------------

    def available_lists(self) -> List[Dict[str, Any]]:
        """The lists on the saved site (system lists left out)."""
        try:
            return self._client().lists_at(selection_path(self.selection, "lists"))
        except GraphError as exc:
            raise self._fail(exc) from exc

    def choose_list(self, list_id: str) -> "SharePointConnector":
        """This connector with ``list_id`` chosen, only when it is a list on the saved site."""
        chosen = next((item for item in self.available_lists() if item["id"] == list_id), None)
        if chosen is None:
            raise SelectionRefused(LIST_NOT_ALLOWED)
        config = dict(self.config.config or {})
        config.update({"list_id": chosen["id"], "list_name": chosen["display_name"]})
        runtime = ConnectorConfig(
            id=self.config.id,
            connector_type=CONNECTOR_TYPE,
            name=self.config.name,
            organization_id=self.config.organization_id,
            config=config,
        )
        other = type(self)(runtime)
        other._m365 = self._m365
        return other

    def columns(self, list_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """The columns of the chosen list. A different list id is refused before any request."""
        if list_id is not None and list_id != self.selection.list_id:
            raise SelectionRefused(LIST_NOT_ALLOWED)
        path = selection_path(self.selection, "columns")
        try:
            return self._client().columns_at(path)
        except GraphError as exc:
            raise self._fail(exc) from exc

    # ----- the BaseConnector interface -----------------------------------

    def get_required_config_fields(self) -> List[str]:
        return ["tenant_id", "site_id", "list_id"]

    def validate_config(self) -> List[str]:
        errors = super().validate_config()
        mapping = (self.config.config or {}).get("mapping") or {}
        if not (isinstance(mapping, dict) and mapping.get("name")):
            errors.append("Choose the column that holds the application name")
        return errors

    def get_field_mappings(self) -> List[FieldMapping]:
        mapping = (self.config.config or {}).get("mapping") or {}
        return [
            FieldMapping(column, field, required=(field == "name"))
            for field, column in mapping.items()
            if column
        ]

    def check_connection(self) -> tuple:
        try:
            lists = self.available_lists()
        except SharePointError as exc:
            return False, str(exc)
        return True, f"Connected: {len(lists)} lists on the site"

    async def test_connection(self) -> bool:
        return self.check_connection()[0]

    async def batch_sync(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """Nothing is read yet: the items arrive with the next part of this connector."""
        return {
            "status": "success",
            "records_processed": 0,
            "records_created": 0,
            "records_updated": 0,
            "records_skipped": 0,
            "records_proposed": 0,
            "records_failed": 0,
            "elements_touched": [],
            "message": NOT_READ_YET,
        }
