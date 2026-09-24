"""Wraps n8n REST API for creating and managing integration workflows.

This is the ONLY file that knows about n8n's API. If we swap to
Temporal, Prefect, or custom workers later, only this file changes.
"""
import logging

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from flask import current_app

from app.extensions import db
from app.modules.codegen.models import SolutionConnector
from app.modules.codegen.services.credential_vault import CredentialVault
from app.modules.intelligence.services.connector_allowlist import (
    assert_connector_permitted,
)

logger = logging.getLogger(__name__)

FREQUENCY_TO_CRON = {
    "hourly": "0 * * * *",
    "daily": "0 8 * * *",
    "every_5_min": "*/5 * * * *",
}


# ── Per-connector workflow template builders ─────────────────────────────


def _schedule_node(cron: str) -> dict:
    """Standard schedule trigger node used by all workflows."""
    return {
        "name": "Schedule",
        "type": "n8n-nodes-base.scheduleTrigger",
        "parameters": {
            "rule": {"interval": [{"field": "cronExpression", "expression": cron}]}
        },
    }


def _push_node(target_api_url: str, connector_type: str) -> dict:
    """Standard push-to-Entelim node used by all workflows."""
    return {
        "name": "Push",
        "type": "n8n-nodes-base.httpRequest",
        "parameters": {
            "url": f"{target_api_url}/api/import",
            "method": "POST",
            "body": "={{$json}}",
            "headers": {"parameters": [{"name": "X-Source-Connector", "value": connector_type}]},
        },
    }


def _transform_node(connector_type: str) -> dict:
    """Standard transform node that tags data with source connector."""
    return {
        "name": "Transform",
        "type": "n8n-nodes-base.set",
        "parameters": {
            "values": {"string": [{"name": "source", "value": connector_type}]}
        },
    }


def _wrap_workflow(name: str, nodes: list, connections: dict) -> dict:
    """Standard workflow wrapper."""
    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }


def _build_salesforce_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.salesforce",
            "parameters": {
                "operation": "getAll",
                "resource": list(object_mappings.keys())[0] if object_mappings else "Account",
                "options": {},
            },
            "credentials": {
                "salesforceOAuth2Api": {
                    "instanceUrl": credentials.get("instance_url", ""),
                    "clientId": credentials.get("client_id", ""),
                    "clientSecret": credentials.get("client_secret", ""),
                }
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


def _build_sap_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    entity = list(object_mappings.keys())[0] if object_mappings else "BusinessPartner"
    base = credentials.get("server_url", "").rstrip("/")
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "url": f"{base}/sap/opu/odata/sap/API_{entity.upper()}_SRV/{entity}Set",
                "method": "GET",
                "authentication": "genericCredentialType",
                "genericAuthType": "httpBasicAuth",
                "options": {"headers": {"parameters": [{"name": "sap-client", "value": credentials.get("client", "100")}]}},
            },
            "credentials": {
                "httpBasicAuth": {
                    "user": credentials.get("username", ""),
                    "password": credentials.get("password", ""),
                }
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


def _build_servicenow_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    table = list(object_mappings.keys())[0] if object_mappings else "incident"
    base = credentials.get("instance_url", "").rstrip("/")
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "url": f"{base}/api/now/table/{table}",
                "method": "GET",
                "authentication": "genericCredentialType",
                "genericAuthType": "oAuth2Api",
            },
            "credentials": {
                "oAuth2Api": {
                    "clientId": credentials.get("client_id", ""),
                    "clientSecret": credentials.get("client_secret", ""),
                    "accessTokenUrl": f"{base}/oauth_token.do",
                }
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


def _build_jira_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.jira",
            "parameters": {
                "operation": "getAll",
                "resource": "issue",
                "options": {},
            },
            "credentials": {
                "jiraSoftwareCloudApi": {
                    "domain": credentials.get("base_url", ""),
                    "email": credentials.get("email", ""),
                    "apiToken": credentials.get("api_token", ""),
                }
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


def _build_sharepoint_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.microsoftSharePoint",
            "parameters": {
                "operation": "getAll",
                "resource": "listItem",
            },
            "credentials": {
                "microsoftSharePointOAuth2Api": {
                    "tenantId": credentials.get("tenant_id", ""),
                    "clientId": credentials.get("client_id", ""),
                    "clientSecret": credentials.get("client_secret", ""),
                }
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


def _build_google_sheets_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {
                "operation": "read",
                "sheetId": credentials.get("spreadsheet_id", ""),
                "range": "Sheet1",
            },
            "credentials": {
                "googleSheetsOAuth2Api": {
                    "serviceAccountKey": credentials.get("credentials_json", ""),
                }
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


def _build_postgresql_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    table = list(object_mappings.keys())[0] if object_mappings else "data"
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.postgres",
            "parameters": {
                "operation": "executeQuery",
                "query": f"SELECT * FROM {table}",
            },
            "credentials": {
                "postgres": {
                    "host": credentials.get("host", ""),
                    "port": int(credentials.get("port", 5432)),
                    "database": credentials.get("database", ""),
                    "user": credentials.get("username", ""),
                    "password": credentials.get("password", ""),
                }
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


def _build_slack_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.slack",
            "parameters": {
                "operation": "getAll",
                "resource": "message",
                "channelId": credentials.get("channel_id", ""),
            },
            "credentials": {
                "slackApi": {
                    "accessToken": credentials.get("bot_token", ""),
                }
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


def _build_teams_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.microsoftTeams",
            "parameters": {
                "operation": "getAll",
                "resource": "channelMessage",
            },
            "credentials": {
                "microsoftTeamsOAuth2Api": {
                    "tenantId": credentials.get("tenant_id", ""),
                    "clientId": credentials.get("client_id", ""),
                    "clientSecret": credentials.get("client_secret", ""),
                }
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


def _build_generic_workflow(connector_type, credentials, object_mappings, target_api_url, cron):
    """Fallback: generic HTTP request workflow for unknown connector types."""
    nodes = [
        _schedule_node(cron),
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "url": credentials.get("base_url", ""),
                "method": "GET",
                "authentication": "genericCredentialType",
            },
        },
        _transform_node(connector_type),
        _push_node(target_api_url, connector_type),
    ]
    return _wrap_workflow(f"Entelim Sync: {connector_type}", nodes, {
        "Schedule": {"main": [[{"node": "Fetch"}]]},
        "Fetch": {"main": [[{"node": "Transform"}]]},
        "Transform": {"main": [[{"node": "Push"}]]},
    })


_WORKFLOW_BUILDERS = {
    "salesforce": _build_salesforce_workflow,
    "sap": _build_sap_workflow,
    "servicenow": _build_servicenow_workflow,
    "jira": _build_jira_workflow,
    "sharepoint": _build_sharepoint_workflow,
    "google_sheets": _build_google_sheets_workflow,
    "postgresql": _build_postgresql_workflow,
    "rest_api": _build_generic_workflow,
    "slack": _build_slack_workflow,
    "microsoft_teams": _build_teams_workflow,
}


class ConnectorOrchestrator:
    """Create and manage n8n workflows for data sync."""

    def __init__(self):
        self._api_url = current_app.config.get("N8N_API_URL", "")
        self._api_token = current_app.config.get("N8N_API_TOKEN", "")

    def _n8n_request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Send authenticated request to n8n API."""
        url = f"{self._api_url}{path}"
        headers = {
            "X-N8N-API-KEY": self._api_token,
            "Content-Type": "application/json",
        }
        return requests.request(method, url, headers=headers, timeout=30, **kwargs)

    def create_sync_workflow(
        self,
        solution_id: int,
        connector_type: str,
        credentials: dict,
        object_mappings: dict,
        target_api_url: str,
        frequency: str = "hourly",
    ) -> SolutionConnector:
        """Create an n8n workflow for syncing data from external system.

        Credentials are stored encrypted via CredentialVault, then injected
        into the n8n workflow JSON at creation time. They are never persisted
        in n8n's own credential store.

        Raises ``ConnectorNotPermitted`` before anything is stored when
        *connector_type* is not on the allowlist -- this is the boundary
        that holds for any caller, whether or not it checked first.
        """
        assert_connector_permitted(connector_type)

        # Store credentials in vault (encrypted at rest)
        vault = CredentialVault()
        vault.store(solution_id, connector_type, credentials)

        workflow_def = self._build_workflow(
            connector_type, credentials, object_mappings, target_api_url, frequency
        )

        try:
            resp = self._n8n_request("POST", "/api/v1/workflows", json=workflow_def)
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to create n8n workflow: {resp.text[:300]}")

            workflow_data = resp.json()
            workflow_id = str(workflow_data.get("id", ""))

            # Activate the workflow
            self._n8n_request("POST", f"/api/v1/workflows/{workflow_id}/activate")
        except RequestsConnectionError as exc:
            raise RuntimeError(f"n8n unreachable: {exc}") from exc

        connector = SolutionConnector(
            solution_id=solution_id,
            connector_type=connector_type,
            n8n_workflow_id=workflow_id,
            sync_frequency=frequency,
            object_mappings=object_mappings,
            credential_ref=f"vault:{solution_id}:{connector_type}",
        )
        db.session.add(connector)
        db.session.commit()
        return connector

    def test_connection(self, connector_type: str, credentials: dict) -> dict:
        """Test connectivity to external system.

        Returns {"success": bool, "message": str}.
        """
        try:
            assert_connector_permitted(connector_type)
        except Exception as e:
            return {"success": False, "message": str(e)}

        try:
            if connector_type == "rest_api":
                resp = requests.get(
                    credentials.get("base_url", ""), timeout=10
                )
                return {
                    "success": resp.status_code < 400,
                    "message": f"HTTP {resp.status_code}",
                }
            return {
                "success": True,
                "message": "Connection parameters accepted (live test not yet implemented for this connector type)",
            }
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _build_workflow(self, connector_type, credentials, object_mappings, target_api_url, frequency):
        """Build n8n workflow JSON using per-connector templates."""
        cron = FREQUENCY_TO_CRON.get(frequency, "0 * * * *")
        builder = _WORKFLOW_BUILDERS.get(connector_type, _build_generic_workflow)
        return builder(connector_type, credentials, object_mappings, target_api_url, cron)

    def discover_objects(self, connector_type: str) -> list[str]:
        """Return the list of available source objects for a connector type.

        This is a catalog lookup -- it returns the objects defined in the
        connector catalog, not a live query to the external system.
        For live discovery, use test_connection() first, then this method
        returns the static object list as a starting point for mapping.
        """
        from app.modules.codegen.services.connector_catalog_service import ConnectorCatalogService
        catalog = ConnectorCatalogService()
        entry = catalog.get_connector(connector_type)
        if not entry:
            return []
        return list(entry.get("objects", []))

    def map_objects(self, connector: SolutionConnector, object_mappings: dict) -> dict:
        """Update the object mappings for an existing connector.

        Returns {"success": bool, "mappings": dict}.
        """
        connector.object_mappings = object_mappings
        db.session.commit()
        return {"success": True, "mappings": object_mappings}

    def schedule_sync(self, connector: SolutionConnector, frequency: str) -> dict:
        """Update the sync frequency for an existing connector.

        Updates both the local record and the n8n workflow schedule.
        Returns {"success": bool} or {"success": False, "error": str}.
        """
        cron = FREQUENCY_TO_CRON.get(frequency, "0 * * * *")
        try:
            if connector.n8n_workflow_id:
                resp = self._n8n_request(
                    "PATCH",
                    f"/api/v1/workflows/{connector.n8n_workflow_id}",
                    json={
                        "nodes": [{
                            "name": "Schedule",
                            "type": "n8n-nodes-base.scheduleTrigger",
                            "parameters": {
                                "rule": {"interval": [{"field": "cronExpression", "expression": cron}]}
                            },
                        }],
                    },
                )
                if resp.status_code not in (200, 201):
                    return {"success": False, "error": f"n8n API error: {resp.text[:200]}"}

            connector.sync_frequency = frequency
            db.session.commit()
            return {"success": True}
        except Exception as exc:
            logger.error("schedule_sync failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def delete_workflow(self, connector: SolutionConnector) -> dict:
        """Delete an n8n workflow and remove the connector record.

        Deactivates the workflow first, then deletes it from n8n,
        then removes the local DB record.
        Returns {"success": bool} or {"success": False, "error": str}.
        """
        try:
            if connector.n8n_workflow_id:
                self._n8n_request(
                    "POST",
                    f"/api/v1/workflows/{connector.n8n_workflow_id}/deactivate",
                )
                self._n8n_request(
                    "DELETE",
                    f"/api/v1/workflows/{connector.n8n_workflow_id}",
                )
            db.session.delete(connector)
            db.session.commit()
            return {"success": True}
        except Exception as exc:
            logger.error("delete_workflow failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def pause_workflow(self, workflow_id: str) -> dict:
        """Deactivate an n8n workflow (pause sync).

        Returns {"success": bool} or {"success": False, "error": str}.
        """
        try:
            resp = self._n8n_request("POST", f"/api/v1/workflows/{workflow_id}/deactivate")
            if resp.status_code not in (200, 201):
                return {"success": False, "error": f"n8n deactivate failed: {resp.text[:200]}"}
            return {"success": True}
        except Exception as exc:
            logger.error("pause_workflow failed: %s", exc)
            return {"success": False, "error": f"n8n unreachable: {exc}"}

    def resume_workflow(self, workflow_id: str) -> dict:
        """Reactivate an n8n workflow (resume sync).

        Returns {"success": bool} or {"success": False, "error": str}.
        """
        try:
            resp = self._n8n_request("POST", f"/api/v1/workflows/{workflow_id}/activate")
            if resp.status_code not in (200, 201):
                return {"success": False, "error": f"n8n activate failed: {resp.text[:200]}"}
            return {"success": True}
        except Exception as exc:
            logger.error("resume_workflow failed: %s", exc)
            return {"success": False, "error": f"n8n unreachable: {exc}"}

    def get_sync_history(self, workflow_id: str, limit: int = 20) -> dict:
        """Fetch recent execution history from n8n for a workflow.

        Returns {"success": bool, "executions": list} or {"success": False, "error": str}.
        """
        try:
            resp = self._n8n_request(
                "GET",
                f"/api/v1/executions?workflowId={workflow_id}&limit={limit}",
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"n8n executions query failed: {resp.text[:200]}", "executions": []}
            data = resp.json()
            executions = data.get("data", []) if isinstance(data, dict) else []
            return {"success": True, "executions": executions}
        except Exception as exc:
            logger.error("get_sync_history failed: %s", exc)
            return {"success": False, "error": f"n8n unreachable: {exc}", "executions": []}
