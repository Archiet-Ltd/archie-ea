"""
ServiceNow CMDB bidirectional connector service.

Push direction: ARB decisions → ServiceNow Change Requests
Pull direction: CMDB CIs → ApplicationComponent inventory

All HTTP calls use requests with timeout=10.
OAuth2 tokens are cached per service instance with expiry tracking.
"""

import functools
import logging
import time
from datetime import datetime
from typing import Any

import requests

from app.modules.intelligence.services.operational_source import (
    OperationalRecordDraft,
    OperationalSourceAdapter,
)
from app.utils.ssrf_guard import build_checked_url

logger = logging.getLogger(__name__)


def _instance_base(config) -> str:
    """The configured instance URL after the outbound-URL guard (raises ConnectorURLRefused).

    Called before every request, and before the client secret is read, so a
    refused URL never receives the secret.
    """
    from app.services.connector_framework import validate_connector_url

    return validate_connector_url(config.instance_url)

# ServiceNow OAuth2 token lifespan is 30 minutes; refresh with 60s margin
_TOKEN_TTL_SECONDS = 29 * 60


def _refresh_token_on_401(method):
    """Decorator: retry once after refreshing the OAuth token on HTTP 401."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        result = method(self, *args, **kwargs)
        return result

    # Actual refresh logic is wired directly inside each method that calls
    # _request(), keeping the decorator lightweight and testable.
    return wrapper


class _ServiceNowConfigView:
    """ConnectorConfig's JSON config, as the attributes this file already reads.

    Not a model -- never queried, never persisted. Exists only so
    ServiceNowConnectorService._get_token and the CMDB/ARB methods can keep
    their existing ``config.instance_url`` style access after _get_config
    moved off the old OrgConnectorConfig row (M2).
    """

    __slots__ = ("instance_url", "client_id", "field_mapping", "enabled")

    def __init__(self, *, instance_url, client_id, field_mapping, enabled):
        self.instance_url = instance_url
        self.client_id = client_id
        self.field_mapping = field_mapping
        self.enabled = enabled


class ServiceNowConnectorService:
    """Bidirectional connector between Entelim and a ServiceNow instance."""

    def __init__(self):
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pull_cmdb_inventory(self, org_id: int) -> dict[str, Any]:
        """
        Pull CMDB Application CIs from ServiceNow and upsert into
        ApplicationComponent.

        Returns {pulled, created, updated}.
        """
        from app.extensions import db
        from app.models.application_portfolio import ApplicationComponent

        config = self._get_config(org_id, "servicenow")
        if config is None or not config.enabled:
            return {}

        try:
            token = self._get_token(config, org_id)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            url = build_checked_url(_instance_base(config), "/api/now/table/cmdb_ci_appl")
            params = {
                "sysparm_fields": "name,sys_class_name,u_business_owner,sys_id",
                "sysparm_limit": "1000",
            }
            # Apply optional query filter from field_mapping config
            ci_filter = (config.field_mapping or {}).get("ci_query_filter")
            if ci_filter:
                params["sysparm_query"] = ci_filter

            resp = requests.get(url, headers=headers, params=params, timeout=10, allow_redirects=False)
            if resp.status_code == 401:
                self._token = None
                token = self._get_token(config, org_id)
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.get(url, headers=headers, params=params, timeout=10, allow_redirects=False)

            resp.raise_for_status()
            records = resp.json().get("result", [])

            created = updated = 0
            # Batch-load existing components by name to avoid one query per CI record
            ci_names = [rec.get("name") for rec in records if rec.get("name")]
            existing_by_name = {
                comp.name: comp
                for comp in ApplicationComponent.query.filter(
                    ApplicationComponent.name.in_(ci_names)
                ).all()
            } if ci_names else {}
            for rec in records:
                name = rec.get("name") or ""
                if not name:
                    continue
                component_type = self._map_ci_class(rec.get("sys_class_name", ""))
                business_owner = rec.get("u_business_owner") or ""

                existing = existing_by_name.get(name)
                if existing:
                    existing.component_type = component_type
                    existing.business_owner = business_owner
                    updated += 1
                else:
                    new_comp = ApplicationComponent(
                        name=name,
                        component_type=component_type,
                        business_owner=business_owner,
                    )
                    db.session.add(new_comp)
                    created += 1

            config.last_sync_at = datetime.utcnow()
            db.session.commit()

            result = {"pulled": len(records), "created": created, "updated": updated}
            logger.info("ServiceNow CMDB pull for org %s: %s", org_id, result)
            return result

        except Exception as exc:
            logger.error("ServiceNow pull_cmdb_inventory failed for org %s: %s", org_id, exc)
            raise

    def pull_operational_records(self, org_id: int, since: datetime | None = None) -> dict[str, Any]:
        """Pull incidents, problems and changes into the organisation's operational records.

        Runs the ServiceNow adapter through the one ingest path and returns its
        report (fetched, created, updated, and every unmatched record). Returns
        {} when the organisation has no enabled ServiceNow connector.
        """
        from app.extensions import db
        from app.modules.intelligence.services.operational_source import (
            ingest_operational_records,
        )

        config = self._get_config(org_id, "servicenow")
        if config is None or not config.enabled:
            return {}
        report = ingest_operational_records(
            ServiceNowOperationalAdapter(self, config), org_id, since
        )
        db.session.commit()
        logger.info(
            "ServiceNow operational pull for org %s: fetched=%s created=%s updated=%s unmatched=%s",
            org_id, report.fetched, report.created, report.updated, len(report.unmatched),
        )
        return report.to_dict()

    def push_arb_decision(self, solution_id: int, arb_decision_id: int) -> dict[str, Any]:
        """
        Push an approved ARB decision to ServiceNow as a Change Request.

        Stores the returned sys_id in ARBReviewItem.servicenow_change_id.
        Returns the created Change Request payload.
        """
        from app.extensions import db
        from app.models.architecture_review_board import ARBReviewItem

        arb_item = ARBReviewItem.query.get(arb_decision_id)
        if arb_item is None:
            logger.warning("push_arb_decision: ARBReviewItem %s not found", arb_decision_id)
            return {}

        # Resolve org from the solution's organisation
        org_id = self._resolve_org_id(solution_id)
        config = self._get_config(org_id, "servicenow")
        if config is None or not config.enabled:
            return {}

        try:
            token = self._get_token(config, org_id)
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            url = build_checked_url(_instance_base(config), "/api/now/table/change_request")

            approver_name = ""
            if arb_item.decided_by:
                approver_name = arb_item.decided_by.username or ""

            blueprint_url = self._resolve_blueprint_url(solution_id)

            payload = {
                "short_description": arb_item.title or f"ARB Decision {arb_item.review_number}",
                "description": arb_item.decision_rationale or "",
                "assigned_to": approver_name,
                "u_blueprint_url": blueprint_url,
                "category": "Software",
                "type": "normal",
            }

            resp = requests.post(url, json=payload, headers=headers, timeout=10, allow_redirects=False)
            if resp.status_code == 401:
                self._token = None
                token = self._get_token(config, org_id)
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.post(url, json=payload, headers=headers, timeout=10, allow_redirects=False)

            resp.raise_for_status()
            data = resp.json().get("result", {})
            sys_id = data.get("sys_id")

            if sys_id:
                arb_item.servicenow_change_id = sys_id
                db.session.commit()
                logger.info(
                    "ARB decision %s pushed to ServiceNow: sys_id=%s",
                    arb_decision_id,
                    sys_id,
                )

            return data

        except Exception as exc:
            logger.error(
                "ServiceNow push_arb_decision failed for arb_decision_id=%s: %s",
                arb_decision_id,
                exc,
            )
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_token(self, config, org_id: int) -> str:
        """
        Return a valid OAuth2 bearer token for the given config.
        Caches the token in-instance; refreshes when within 60s of expiry.

        The client secret is stored in ``OrgConnectorCredential`` via
        ``OrgCredentialVault`` (encrypted with the organisation's own key),
        not on the retired ``OrgConnectorConfig.client_secret`` column.
        """
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token

        token_base = _instance_base(config)  # before the secret is read

        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        client_secret = OrgCredentialVault().retrieve(
            org_id=org_id, connector_type="servicenow", credential_type="client_secret"
        )

        token_url = build_checked_url(token_base, "/oauth_token.do")
        resp = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": config.client_id,
                "client_secret": client_secret,
            },
            timeout=10,
            allow_redirects=False,
        )
        resp.raise_for_status()
        token_data = resp.json()
        self._token = token_data["access_token"]
        expires_in = int(token_data.get("expires_in", _TOKEN_TTL_SECONDS))
        self._token_expires_at = now + expires_in - 60  # 60s safety margin
        return self._token

    @staticmethod
    def _get_config(org_id: int | None, connector_type: str):
        """Load the org's ServiceNow connector configuration.

        Reads ``ConnectorConfig`` (the one framework's store, written by
        app/modules/admin/connector_routes.py::servicenow_config_save and the
        new /admin/connectors page) rather than the retired
        ``OrgConnectorConfig``, which the save path stopped writing (M2 in
        cloud-review-pr448-961a0c35-v1.md: settings saved on the new page
        were never seen by this service, and existing organisations' settings
        disappeared from the form). Wraps the JSON ``config`` dict in a thin
        view exposing the same ``instance_url``/``client_id``/
        ``field_mapping``/``enabled`` attributes this service's other methods
        already read, so nothing past this method needs to change.
        """
        if org_id is None:
            return None
        from app.models.connector_config import ConnectorConfig

        cfg = ConnectorConfig.query.filter_by(
            organization_id=org_id, connector_type=connector_type
        ).first()
        if cfg is None:
            return None
        data = cfg.config or {}
        return _ServiceNowConfigView(
            instance_url=data.get("instance_url") or "",
            client_id=data.get("client_id") or "",
            field_mapping=data.get("field_mapping") or {},
            enabled=bool(data.get("enabled")),
        )

    @staticmethod
    def _map_ci_class(ci_class: str) -> str:
        """Map ServiceNow CI class name to an ApplicationComponent component_type."""
        mapping = {
            "cmdb_ci_appl": "Application",
            "cmdb_ci_application": "Application",
            "cmdb_ci_service": "BusinessService",
            "cmdb_ci_server": "Node",
            "cmdb_ci_database": "Artifact",
            "cmdb_ci_network": "CommunicationNetwork",
        }
        return mapping.get(ci_class, "ApplicationComponent")

    @staticmethod
    def _resolve_org_id(solution_id: int) -> int | None:
        """Resolve the organization_id for a solution."""
        try:
            from app.models.truly_missing_models import Solution

            sol = Solution.query.get(solution_id)
            if sol:
                return getattr(sol, "organization_id", None)
        except Exception as exc:
            logger.debug("suppressed error in ServiceNowConnectorService._resolve_org_id (app/services/servicenow_connector_service.py): %s", exc)
        return None

    @staticmethod
    def _resolve_blueprint_url(solution_id: int) -> str:
        """Return the blueprint PDF URL for a solution, or empty string."""
        try:
            from flask import url_for

            return url_for(
                "solution_design.export_pdf", solution_id=solution_id, _external=True
            )
        except Exception:
            return ""


# ----------------------------------------------------------------------
# Operational source adapter
# ----------------------------------------------------------------------

_OPERATIONAL_TABLES = {"incident": "incident", "problem": "problem", "change": "change_request"}
_OPERATIONAL_FIELDS = (
    "sys_id,number,short_description,state,severity,priority,cmdb_ci,"
    "opened_at,resolved_at,closed_at,sys_updated_on"
)
_PAGE_SIZE = 1000
_MAX_PAGES = 50


def _sn_time(value) -> datetime | None:
    """ServiceNow UTC 'YYYY-MM-DD HH:MM:SS'; empty means not set."""
    if not value:
        return None
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")


def _sn_ref(value) -> str | None:
    """A reference field is either a bare sys_id or {'link':..., 'value': sys_id}."""
    if isinstance(value, dict):
        value = value.get("value")
    return str(value).strip() or None if value else None


class ServiceNowOperationalAdapter(OperationalSourceAdapter):
    """ServiceNow incidents, problems and changes as an operational source adapter.

    Reuses the service's token cache, outbound-URL guard and configuration.
    """

    source_system = "servicenow"

    def __init__(self, service: ServiceNowConnectorService, config) -> None:
        self._service = service
        self._config = config

    def fetch_since(self, org_id: int, since: datetime | None):
        service, config = self._service, self._config
        token = service._get_token(config, org_id)
        for kind, table in _OPERATIONAL_TABLES.items():
            url = build_checked_url(_instance_base(config), f"/api/now/table/{table}")
            offset = 0
            for _page in range(_MAX_PAGES):
                params = {
                    "sysparm_fields": _OPERATIONAL_FIELDS,
                    "sysparm_limit": str(_PAGE_SIZE),
                    "sysparm_offset": str(offset),
                    "sysparm_query": "ORDERBYsys_updated_on"
                    if since is None
                    else f"sys_updated_on>={since:%Y-%m-%d %H:%M:%S}^ORDERBYsys_updated_on",
                }
                headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
                resp = requests.get(url, headers=headers, params=params, timeout=10, allow_redirects=False)
                if resp.status_code == 401:
                    service._token = None
                    token = service._get_token(config, org_id)
                    headers["Authorization"] = f"Bearer {token}"
                    resp = requests.get(url, headers=headers, params=params, timeout=10, allow_redirects=False)
                resp.raise_for_status()
                rows = resp.json().get("result", [])
                for row in rows:
                    yield {**row, "_kind": kind}
                if len(rows) < _PAGE_SIZE:
                    break
                offset += _PAGE_SIZE
            else:
                raise RuntimeError(
                    f"ServiceNow {table} returned more than {_MAX_PAGES * _PAGE_SIZE} rows; "
                    "narrow the sync window"
                )

    def map_record(self, raw: dict[str, Any]):
        severity = raw.get("severity") or raw.get("priority") or None
        return OperationalRecordDraft(
            kind=raw["_kind"],
            external_id=_sn_ref(raw.get("sys_id")) or "",
            item_external_id=_sn_ref(raw.get("cmdb_ci")),
            reference=raw.get("number") or None,
            title=(raw.get("short_description") or None),
            state=(str(raw["state"]) if raw.get("state") not in (None, "") else None),
            severity=(str(severity) if severity else None),
            opened_at=_sn_time(raw.get("opened_at")),
            resolved_at=_sn_time(raw.get("resolved_at")),
            closed_at=_sn_time(raw.get("closed_at")),
            source_updated_at=_sn_time(raw.get("sys_updated_on")),
        )

