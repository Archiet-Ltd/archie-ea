"""
Jira ALM Connector

Application lifecycle management integration with:
- Issue tracking and project management
- Webhook support for real-time updates
- Field mapping for ALM artifact correlation
- Bidirectional sync capabilities

API Reference: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""

import base64
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin


from app.services.connector_framework import (
    BaseConnector,
    ConnectorConfig,
    ConnectorType,
    FieldMapping,
)

logger = logging.getLogger(__name__)


class JiraALMConnector(BaseConnector):
    """Jira ALM connector implementation."""

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.ALM

    def get_required_config_fields(self) -> List[str]:
        return ["base_url", "username", "api_token", "project_keys"]

    def get_field_mappings(self) -> List[FieldMapping]:
        """Return field mappings for Jira issues to ALM artifacts."""
        return [
            # Basic issue fields
            FieldMapping("id", "external_id", required=True),
            FieldMapping("key", "issue_key", required=True),
            FieldMapping("fields.summary", "title", required=True),
            FieldMapping("fields.description", "description"),
            FieldMapping("fields.status.name", "status"),
            FieldMapping("fields.priority.name", "priority"),
            FieldMapping("fields.issuetype.name", "issue_type"),
            # Assignment fields
            FieldMapping("fields.assignee.displayName", "assignee"),
            FieldMapping("fields.reporter.displayName", "reporter"),
            # Date fields
            FieldMapping("fields.created", "created_date", transform=self._parse_datetime),
            FieldMapping("fields.updated", "updated_date", transform=self._parse_datetime),
            FieldMapping("fields.duedate", "due_date", transform=self._parse_date),
            # Custom fields (configurable)
            FieldMapping("fields.customfield_10000", "epic_link"),  # Example custom field
            FieldMapping("fields.customfield_10001", "story_points"),  # Example custom field
        ]

    def _parse_datetime(self, date_str: str) -> Optional[datetime]:
        """Parse Jira datetime string."""
        if not date_str:
            return None
        try:
            # Jira uses ISO format
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse Jira date string."""
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return None

    async def test_connection(self) -> bool:
        """Test connectivity to Jira instance."""
        try:
            headers = self._get_auth_headers()
            url = urljoin(self.config.config["base_url"], "/rest/api/3/myself")

            async with self._outbound_session() as session:
                async with session.get(url, headers=headers) as response:
                    return response.status == 200

        except Exception as e:
            logger.error(f"Jira connection test failed: {e}")
            return False

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for Jira API."""
        credentials = f"{self.config.config['username']}:{self.config.config['api_token']}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded_credentials}", "Content-Type": "application/json"}

    async def batch_sync(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """Perform batch synchronization of Jira data."""
        try:
            project_keys = self.config.config.get("project_keys", [])
            if not project_keys:
                raise ValueError("No project keys configured")

            all_issues = []

            for project_key in project_keys:
                issues = await self._fetch_project_issues(project_key, since)
                all_issues.extend(issues)

            # Write the issues into the organisation's model
            return await self._process_jira_issues(all_issues)

        except Exception as e:
            logger.error(f"Batch sync failed: {e}")
            raise

    async def _fetch_project_issues(
        self, project_key: str, since: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Fetch issues for a specific project."""
        headers = self._get_auth_headers()
        base_url = urljoin(self.config.config["base_url"], "/rest/api/3/search")

        # Build JQL query
        jql = f"project = {project_key}"
        if since:
            jql += f" AND updated > '{since.strftime('%Y-%m-%d %H:%M')}'"

        params = {
            "jql": jql,
            "maxResults": 100,
            "fields": "id,key,summary,description,status,priority,issuetype,assignee,reporter,created,updated,duedate,customfield_10000,customfield_10001",
        }

        all_issues = []
        start_at = 0

        async with self._outbound_session() as session:
            while True:
                params["startAt"] = start_at

                async with session.get(base_url, headers=headers, params=params) as response:
                    if response.status != 200:
                        raise Exception(f"Jira API request failed: {response.status}")

                    data = await response.json()
                    issues = data.get("issues", [])

                    if not issues:
                        break

                    all_issues.extend(issues)
                    start_at += len(issues)

                    # Safety limit
                    if len(all_issues) >= 5000:
                        logger.warning(f"Reached 5k issue limit for project {project_key}")
                        break

        return all_issues

    def _issue_to_element(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """One Jira issue as a work package: the issue id is its identity, the summary its name."""
        mapped = self._apply_field_mappings(issue)
        # Jira's v3 API returns the description as a rich-text document, not text.
        description = mapped.get("description")
        return {
            "external_id": mapped["external_id"],
            "name": mapped["title"],
            "description": description if isinstance(description, str) else None,
            "element_type": "WorkPackage",
        }

    async def _process_jira_issues(self, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Write Jira issues into the organisation's model; returns the sync counts."""
        return self.persist_records(
            issues, source_system="jira", to_element=self._issue_to_element
        )

    async def incremental_sync(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle real-time Jira updates from webhooks."""
        try:
            issue = event_data.get("issue", {})
            if not issue:
                raise ValueError("No issue data in webhook event")

            # Process single issue
            return await self._process_jira_issues([issue])

        except Exception as e:
            logger.error(f"Incremental sync failed: {e}")
            raise

    # =========================================================================
    # PUSH (write) methods — A.R.C.I.E → Jira
    # =========================================================================

    async def create_issue(
        self, project_key: str, issue_type: str, fields_dict: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new Jira issue.

        Args:
            project_key: Jira project key (e.g. 'EA').
            issue_type: Issue type name (e.g. 'Task').
            fields_dict: Pre-built fields dict (summary, description, priority, etc.).

        Returns:
            Dict with 'key' and 'id' of created issue.

        Raises:
            JiraAPIError on non-201 response.
        """
        headers = self._get_auth_headers()
        url = urljoin(self.config.config["base_url"], "/rest/api/3/issue")

        payload = {
            "fields": {
                "project": {"key": project_key},
                "issuetype": {"name": issue_type},
                **fields_dict,
            }
        }

        async with self._outbound_session() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                body = await response.json()
                if response.status != 201:
                    error_messages = body.get("errors", body.get("errorMessages", []))
                    raise JiraAPIError(
                        f"Create issue failed ({response.status}): {error_messages}"
                    )
                return {"key": body["key"], "id": body["id"]}

    async def update_issue(
        self, issue_key: str, fields_dict: Dict[str, Any]
    ) -> None:
        """Update an existing Jira issue (delta update — only changed fields).

        Args:
            issue_key: Jira issue key (e.g. 'EA-123').
            fields_dict: Only the fields that changed.

        Raises:
            JiraAPIError on non-204 response.
        """
        headers = self._get_auth_headers()
        url = urljoin(
            self.config.config["base_url"], f"/rest/api/3/issue/{issue_key}"
        )
        payload = {"fields": fields_dict}

        async with self._outbound_session() as session:
            async with session.put(url, headers=headers, json=payload) as response:
                if response.status not in (200, 204):
                    body = await response.json()
                    error_messages = body.get("errors", body.get("errorMessages", []))
                    raise JiraAPIError(
                        f"Update issue {issue_key} failed ({response.status}): {error_messages}"
                    )

    async def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """Fetch a single Jira issue by key (for drift detection reads).

        Args:
            issue_key: Jira issue key (e.g. 'EA-123').

        Returns:
            Full issue dict from Jira API.
        """
        headers = self._get_auth_headers()
        url = urljoin(
            self.config.config["base_url"], f"/rest/api/3/issue/{issue_key}"
        )

        async with self._outbound_session() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    raise JiraAPIError(
                        f"Get issue {issue_key} failed ({response.status})"
                    )
                return await response.json()

    async def discover_fields(
        self, project_key: str, issue_type_id: str
    ) -> List[Dict[str, Any]]:
        """Discover available fields for a project/issue-type combination.

        Uses the createmeta endpoint to find custom field IDs.
        Result is cached for 1 hour to avoid excessive API calls.

        Args:
            project_key: Jira project key.
            issue_type_id: Numeric issue type ID.

        Returns:
            List of field dicts with 'key', 'name', 'required', 'schema'.
        """
        cache_key = f"{project_key}:{issue_type_id}"
        now = time.time()

        if not hasattr(self, "_field_cache"):
            self._field_cache = {}

        cached = self._field_cache.get(cache_key)
        if cached and (now - cached["timestamp"]) < 3600:
            return cached["fields"]

        headers = self._get_auth_headers()
        url = urljoin(
            self.config.config["base_url"],
            f"/rest/api/3/issue/createmeta/{project_key}/issuetypes/{issue_type_id}",
        )

        async with self._outbound_session() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    raise JiraAPIError(
                        f"Field discovery failed ({response.status})"
                    )
                data = await response.json()
                fields = data.get("values", data.get("fields", []))

                self._field_cache[cache_key] = {
                    "fields": fields,
                    "timestamp": now,
                }
                return fields

    async def get_project_components(
        self, project_key: str
    ) -> List[Dict[str, Any]]:
        """Get all components for a Jira project.

        Args:
            project_key: Jira project key.

        Returns:
            List of component dicts with 'id', 'name', 'description'.
        """
        headers = self._get_auth_headers()
        url = urljoin(
            self.config.config["base_url"],
            f"/rest/api/3/project/{project_key}/components",
        )

        async with self._outbound_session() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    raise JiraAPIError(
                        f"Get components failed ({response.status})"
                    )
                return await response.json()

    async def create_component(
        self, project_key: str, name: str, description: str = ""
    ) -> Dict[str, Any]:
        """Create a component in a Jira project (idempotent — checks existing first).

        Args:
            project_key: Jira project key.
            name: Component name.
            description: Optional description.

        Returns:
            Component dict with 'id' and 'name'.
        """
        existing = await self.get_project_components(project_key)
        for comp in existing:
            if comp.get("name", "").lower() == name.lower():
                return comp

        headers = self._get_auth_headers()
        url = urljoin(self.config.config["base_url"], "/rest/api/3/component")

        payload = {
            "project": project_key,
            "name": name,
            "description": description,
        }

        async with self._outbound_session() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status != 201:
                    body = await response.json()
                    raise JiraAPIError(
                        f"Create component failed ({response.status}): {body}"
                    )
                return await response.json()

    async def create_issue_link(
        self, inward_key: str, outward_key: str, link_type: str = "Blocks"
    ) -> None:
        """Create a directional link between two Jira issues.

        Args:
            inward_key: The issue that is blocked / the inward side (e.g. "KAN-12").
            outward_key: The issue doing the blocking / outward side (e.g. "KAN-10").
            link_type: Jira link type name (default: "Blocks").
        """
        headers = self._get_auth_headers()
        url = urljoin(self.config.config["base_url"], "/rest/api/3/issueLink")

        payload = {
            "type": {"name": link_type},
            "inwardIssue": {"key": inward_key},
            "outwardIssue": {"key": outward_key},
        }

        async with self._outbound_session() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status not in (200, 201, 204):
                    body = await response.text()
                    raise JiraAPIError(
                        f"Create issue link failed ({response.status}): {body}"
                    )


class JiraAPIError(Exception):
    """Raised when a Jira REST API call returns an error status."""


def create_jira_connector(config: ConnectorConfig) -> JiraALMConnector:
    """Factory function to create Jira connector."""
    return JiraALMConnector(config)
