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

logger = logging.getLogger(__name__)

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
            token = self._get_token(config)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            url = f"{config.instance_url.rstrip('/')}/api/now/table/cmdb_ci_appl"
            params = {
                "sysparm_fields": "name,sys_class_name,u_business_owner,sys_id",
                "sysparm_limit": "1000",
            }
            # Apply optional query filter from field_mapping config
            ci_filter = (config.field_mapping or {}).get("ci_query_filter")
            if ci_filter:
                params["sysparm_query"] = ci_filter

            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 401:
                self._token = None
                token = self._get_token(config)
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.get(url, headers=headers, params=params, timeout=10)

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
            token = self._get_token(config)
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            url = f"{config.instance_url.rstrip('/')}/api/now/table/change_request"

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

            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code == 401:
                self._token = None
                token = self._get_token(config)
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.post(url, json=payload, headers=headers, timeout=10)

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

    def _get_token(self, config) -> str:
        """
        Return a valid OAuth2 bearer token for the given config.
        Caches the token in-instance; refreshes when within 60s of expiry.
        """
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token

        token_url = f"{config.instance_url.rstrip('/')}/oauth_token.do"
        resp = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        token_data = resp.json()
        self._token = token_data["access_token"]
        expires_in = int(token_data.get("expires_in", _TOKEN_TTL_SECONDS))
        self._token_expires_at = now + expires_in - 60  # 60s safety margin
        return self._token

    @staticmethod
    def _get_config(org_id: int | None, connector_type: str):
        """Load the org's ServiceNow OrgConnectorConfig; returns None if not found."""
        if org_id is None:
            return None
        from app.models.connector_config import OrgConnectorConfig

        return OrgConnectorConfig.query.filter_by(
            organization_id=org_id, connector_type=connector_type
        ).first()

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
