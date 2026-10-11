"""
Jira Connector Service (COM-009)

Synchronous Jira REST API v3 integration for ARB → Epic push and
labelled-issue → WorkPackage import. Uses ConnectorConfig (connector_type='jira')
with the api_token stored encrypted via Fernet.

All HTTP calls use timeout=10. Methods are no-ops when the connector config
is absent, disabled, or missing instance_url.
"""

from app.services.archimate_backbone import sync_archimate_element
import base64
import logging
from typing import Any, Dict, List, Optional

import requests

from app import db
from app.services.connector_framework import ConnectorConfig, ConnectorStatus

logger = logging.getLogger(__name__)

CONNECTOR_TYPE = "jira"


class JiraConnectorError(Exception):
    """Raised when a Jira REST API call returns a non-2xx status."""


class JiraConnectorService:
    """
    Synchronous Jira connector.

    Responsibilities:
    - push_arb_decision_to_epic  — creates a Jira Epic from an approved ARBReviewItem
    - pull_issues_to_backlog     — imports Jira issues labelled 'archie-import' as WorkPackages
    """

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _get_config(self) -> Optional[ConnectorConfig]:
        """Return first active Jira ConnectorConfig, or None."""
        return (
            ConnectorConfig.query
            .filter_by(connector_type=CONNECTOR_TYPE, status=ConnectorStatus.ACTIVE.value)
            .first()
        )

    def _cfg_data(self, config: ConnectorConfig) -> Dict[str, Any]:
        return config.config or {}

    def _get_headers(self, config: ConnectorConfig) -> Dict[str, str]:
        """Return Jira Basic Auth headers (email:api_token, base64-encoded)."""
        cfg = self._cfg_data(config)
        email = cfg.get("email", "")
        api_token = cfg.get("api_token_encrypted") or cfg.get("api_token", "")

        if isinstance(api_token, (bytes, bytearray)):
            try:
                from app.modules.codegen.services.credential_encryption import decrypt_credential
                api_token = decrypt_credential(api_token) or ""
            except Exception:
                logger.warning("jira_connector: could not decrypt api_token")
                api_token = ""

        credentials = f"{email}:{api_token}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _instance_url(self, config: ConnectorConfig) -> str:
        return (self._cfg_data(config).get("instance_url") or "").rstrip("/")

    # ------------------------------------------------------------------
    # HTTP wrapper
    # ------------------------------------------------------------------

    def _handle_request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        json: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Execute an HTTP request with timeout=10.

        Returns the parsed JSON body on 2xx.
        Raises JiraConnectorError on non-2xx responses.
        """
        resp = requests.request(method, url, headers=headers, json=json, timeout=10)
        if not resp.ok:
            raise JiraConnectorError(
                f"Jira API {method} {url} returned {resp.status_code}: "
                f"{resp.text[:300]}"
            )
        if resp.content:
            return resp.json()
        return {}

    # ------------------------------------------------------------------
    # Push: ARB decision → Jira Epic
    # ------------------------------------------------------------------

    def push_arb_decision_to_epic(
        self, solution_id: int, arb_decision_id: int
    ) -> Dict[str, Any]:
        """
        Create a Jira Epic from an approved ARBReviewItem.

        Stores the returned Jira issue key in ARBReviewItem.jira_issue_key.

        Returns:
            {"jira_key": "ARCH-42", "jira_id": "10042"} on success.
            {} when connector is disabled/unconfigured, or item already has a key.
        """
        config = self._get_config()
        if not config:
            logger.debug("jira_connector: no active config — skipping push")
            return {}

        cfg = self._cfg_data(config)
        if not cfg.get("enabled") or not self._instance_url(config):
            logger.debug("jira_connector: disabled or no instance_url — skipping push")
            return {}

        from app.models.architecture_review_board import ARBReviewItem
        item = ARBReviewItem.query.get(arb_decision_id)
        if item is None:
            logger.warning("jira_connector: ARBReviewItem %s not found", arb_decision_id)
            return {}

        if getattr(item, "jira_issue_key", None):
            logger.debug(
                "jira_connector: item %s already has jira_issue_key=%s — skipping",
                arb_decision_id,
                item.jira_issue_key,
            )
            return {"jira_key": item.jira_issue_key}

        from app.models.solution_models import Solution
        solution = Solution.query.get(solution_id) if solution_id else None
        epic_summary = (solution.name if solution else None) or item.title

        description = _build_epic_description(item, solution)
        project_key = cfg.get("default_project_key", "ARCH")

        payload = {
            "fields": {
                "project": {"key": project_key},
                "issuetype": {"name": "Epic"},
                "summary": epic_summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description}],
                        }
                    ],
                },
                "labels": ["archie-arb"],
            }
        }

        try:
            url = f"{self._instance_url(config)}/rest/api/3/issue"
            headers = self._get_headers(config)
            result = self._handle_request("POST", url, headers, json=payload)
        except JiraConnectorError:
            logger.exception("jira_connector: failed to create Epic for ARBReviewItem %s", arb_decision_id)
            raise

        jira_key = result.get("key")
        jira_id = result.get("id")

        if jira_key:
            item.jira_issue_key = jira_key
            db.session.commit()
            logger.info(
                "jira_connector: created Epic %s for ARBReviewItem %s",
                jira_key,
                arb_decision_id,
            )

        return {"jira_key": jira_key, "jira_id": jira_id}

    # ------------------------------------------------------------------
    # Pull: Jira issues → WorkPackage backlog
    # ------------------------------------------------------------------

    def pull_issues_to_backlog(
        self, org_id: Any, jira_project_key: str
    ) -> List[Dict[str, Any]]:
        """
        Import Jira issues with label 'archie-import' from a project into WorkPackages.

        Args:
            org_id:           Organisation identifier (informational; used for future
                              multi-tenancy filtering).
            jira_project_key: Jira project key (e.g. 'ARCH').

        Returns:
            List of dicts with {'jira_key', 'work_package_id', 'created'}.
            Returns [] when connector is disabled/unconfigured.
        """
        config = self._get_config()
        if not config:
            return []

        cfg = self._cfg_data(config)
        if not cfg.get("enabled") or not self._instance_url(config):
            return []

        jql = f"project={jira_project_key} AND labels=archie-import"
        url = f"{self._instance_url(config)}/rest/api/3/search"
        headers = self._get_headers(config)

        try:
            result = self._handle_request(
                "GET",
                f"{url}?jql={jql}&maxResults=100"
                "&fields=summary,description,status,priority,issuetype",
                headers,
            )
        except JiraConnectorError:
            logger.exception("jira_connector: pull_issues_to_backlog failed for project %s", jira_project_key)
            return []

        from app.models.implementation_migration import WorkPackage

        imported: List[Dict[str, Any]] = []
        for issue in result.get("issues", []):
            key = issue.get("key", "")
            fields = issue.get("fields", {})
            summary = fields.get("summary") or key

            existing = WorkPackage.query.filter_by(
                context="jira", context_id=0
            ).filter(WorkPackage.name == key).first()

            if existing:
                imported.append({"jira_key": key, "work_package_id": existing.id, "created": False})
                continue

            wp = WorkPackage(
                name=key,
                summary=summary[:512] if summary else None,
                description=_format_jira_description(fields.get("description")),
                status=_map_jira_status(fields.get("status", {}).get("name", "")),
                priority=_map_jira_priority(fields.get("priority", {}).get("name", "")),
                context="jira",
            )
            db.session.add(wp)
            sync_archimate_element(wp)
            db.session.flush()
            imported.append({"jira_key": key, "work_package_id": wp.id, "created": True})

        if imported:
            db.session.commit()
            logger.info(
                "jira_connector: imported %d issues from project %s",
                len(imported),
                jira_project_key,
            )

        return imported

    # ------------------------------------------------------------------
    # Connection test (used by admin route)
    # ------------------------------------------------------------------

    def test_connection(self, instance_url: str, email: str, api_token: str) -> Dict[str, Any]:
        """
        Test Jira credentials by calling /rest/api/3/myself.

        Returns {"status": "ok"} or {"status": "error", "message": "..."}.
        """
        credentials = f"{email}:{api_token}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{instance_url.rstrip('/')}/rest/api/3/myself"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return {"status": "ok", "message": f"Connected as {data.get('displayName', email)}"}
            return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except requests.Timeout:
            return {"status": "error", "message": "Connection timed out (10s)"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)[:200]}


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _build_epic_description(item: Any, solution: Any) -> str:
    """Build a plain-text Epic description from ARB review item data."""
    parts = []
    if solution and solution.description:
        parts.append(f"Solution: {solution.description}")
    if item.decision_rationale:
        parts.append(f"Decision rationale: {item.decision_rationale}")
    if item.status:
        parts.append(f"ARB status: {item.status}")
    if item.decision_date:
        parts.append(f"Decided: {item.decision_date.strftime('%Y-%m-%d')}")
    parts.append("Source: Entelim ARB governance platform")
    return "\n".join(parts)


def _format_jira_description(description: Any) -> Optional[str]:
    """Extract plain text from a Jira Atlassian Document Format (ADF) description."""
    if description is None:
        return None
    if isinstance(description, str):
        return description
    if isinstance(description, dict):
        texts = []
        for block in description.get("content", []):
            for inline in block.get("content", []):
                if isinstance(inline.get("text"), str):
                    texts.append(inline["text"])
        return " ".join(texts) if texts else None
    return None


def _map_jira_status(jira_status: str) -> str:
    mapping = {
        "To Do": "planned",
        "In Progress": "in_progress",
        "Done": "completed",
        "Blocked": "blocked",
    }
    return mapping.get(jira_status, "planned")


def _map_jira_priority(jira_priority: str) -> str:
    mapping = {
        "Highest": "critical",
        "High": "high",
        "Medium": "medium",
        "Low": "low",
        "Lowest": "low",
    }
    return mapping.get(jira_priority, "medium")
