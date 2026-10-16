"""
ServiceNow CMDB Connector

Configuration management database integration with:
- CI/CD item synchronization
- Sub - 24h batch sync, <1min event sync
- Webhook support for real-time updates
- Field mapping for ArchiMate element correlation

API Reference: https://developer.servicenow.com/dev.do#!/reference/api/rome/rest/cmdb-api
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin


from ..services.connector_framework import (
    BaseConnector,
    ConnectorConfig,
    ConnectorType,
    FieldMapping,
)

logger = logging.getLogger(__name__)


class ServiceNowCMDBConnector(BaseConnector):
    """ServiceNow CMDB connector implementation."""

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.CMDB

    def get_required_config_fields(self) -> List[str]:
        return ["base_url", "username", "password", "client_id", "client_secret"]

    def get_field_mappings(self) -> List[FieldMapping]:
        """Return field mappings for ServiceNow CI items to ArchiMate elements."""
        return [
            # Basic CI fields
            FieldMapping("sys_id", "external_id", required=True),
            FieldMapping("name", "name", required=True),
            FieldMapping("short_description", "description"),
            FieldMapping("operational_status", "operational_status"),
            FieldMapping("install_status", "install_status"),
            # Classification mappings
            FieldMapping("sys_class_name", "ci_class", transform=self._map_ci_class_to_archimate),
            # Relationship mappings
            FieldMapping("parent", "parent_id"),
            FieldMapping("child", "child_ids", transform=self._parse_child_relationships),
            # Custom fields
            FieldMapping("u_business_service", "business_service"),
            FieldMapping("u_technical_owner", "technical_owner"),
            FieldMapping("u_business_owner", "business_owner"),
        ]

    def _map_ci_class_to_archimate(self, ci_class: str) -> str:
        """Map ServiceNow CI class to ArchiMate element type."""
        mapping = {
            "cmdb_ci_service": "BusinessService",
            "cmdb_ci_application": "ApplicationComponent",
            "cmdb_ci_server": "Node",
            "cmdb_ci_database": "Artifact",
            "cmdb_ci_network": "CommunicationNetwork",
            "cmdb_ci_ip_network": "CommunicationNetwork",
            "cmdb_ci_storage": "Artifact",
            "cmdb_ci_printer": "Node",
            "cmdb_ci_computer": "Node",
            "cmdb_ci_hardware": "Node",
        }
        return mapping.get(ci_class, "TechnologyService")

    def _parse_child_relationships(self, child_data: Any) -> List[str]:
        """Parse child relationship data."""
        if isinstance(child_data, list):
            return [child.get("sys_id") for child in child_data if child.get("sys_id")]
        return []

    async def test_connection(self) -> bool:
        """Test connectivity to ServiceNow instance."""
        try:
            # Get OAuth token first
            token = await self._get_oauth_token()
            if not token:
                return False

            # Test basic API call
            headers = {"Authorization": f"Bearer {token}"}
            url = urljoin(self.config.config["base_url"], "/api/now/table/cmdb_ci")

            async with self._outbound_session() as session:
                async with session.get(
                    url, headers=headers, params={"sysparm_limit": "1"}
                ) as response:
                    return response.status == 200

        except Exception as e:
            logger.error(f"ServiceNow connection test failed: {e}")
            return False

    async def _get_oauth_token(self) -> Optional[str]:
        """Get OAuth access token from ServiceNow."""
        try:
            token_url = urljoin(self.config.config["base_url"], "/oauth_token.do")
            auth_data = {
                "grant_type": "password",
                "client_id": self.config.config["client_id"],
                "client_secret": self.config.config["client_secret"],
                "username": self.config.config["username"],
                "password": self.config.config["password"],
            }

            async with self._outbound_session() as session:
                async with session.post(token_url, data=auth_data) as response:
                    if response.status == 200:
                        token_data = await response.json()
                        return token_data.get("access_token")

            logger.error(f"OAuth token request failed: {response.status}")
            return None

        except Exception as e:
            logger.error(f"OAuth token error: {e}")
            return None

    async def batch_sync(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """Perform batch synchronization of CMDB data."""
        try:
            token = await self._get_oauth_token()
            if not token:
                raise Exception("Failed to obtain OAuth token")

            headers = {"Authorization": f"Bearer {token}"}
            base_url = urljoin(self.config.config["base_url"], "/api/now/table/cmdb_ci")

            # Build query parameters
            params = {
                "sysparm_limit": "1000",  # Batch size
                "sysparm_fields": "sys_id,name,short_description,operational_status,install_status,sys_class_name,parent,u_business_service,u_technical_owner,u_business_owner",
            }

            if since:
                # Convert to ServiceNow datetime format
                since_str = since.strftime("%Y-%m-%d %H:%M:%S")
                params["sysparm_query"] = f"sys_updated_on>{since_str}"

            all_records = []
            offset = 0

            async with self._outbound_session() as session:
                while True:
                    params["sysparm_offset"] = str(offset)

                    async with session.get(base_url, headers=headers, params=params) as response:
                        if response.status != 200:
                            raise Exception(f"API request failed: {response.status}")

                        data = await response.json()
                        records = data.get("result", [])

                        if not records:
                            break

                        all_records.extend(records)
                        offset += len(records)

                        # Safety limit
                        if len(all_records) >= 10000:
                            logger.warning("Reached 10k record limit, stopping batch sync")
                            break

            # Process records
            processed = await self._process_cmdb_records(all_records)

            return {
                "status": "completed",
                "records_processed": len(all_records),
                "records_created": processed["created"],
                "records_updated": processed["updated"],
                "records_deleted": processed["deleted"],
            }

        except Exception as e:
            logger.error(f"Batch sync failed: {e}")
            raise

    async def _process_cmdb_records(self, records: List[Dict[str, Any]]) -> Dict[str, int]:
        """Process CMDB records and update knowledge graph."""
        created = 0
        updated = 0
        deleted = 0

        for record in records:
            try:
                # Apply field mappings
                mapped_data = {}
                for mapping in self.get_field_mappings():
                    try:
                        value = mapping.apply(record)
                        if value is not None:
                            mapped_data[mapping.target_field] = value
                    except ValueError as e:
                        logger.warning(f"Field mapping failed for {record.get('sys_id')}: {e}")
                        continue

                # Determine ArchiMate element type
                ci_class = record.get("sys_class_name", "")
                archimate_type = self._map_ci_class_to_archimate(ci_class)

                logger.warning(
                    "CMDB record %s (%s) mapped but not persisted — KG integration not available",
                    record.get("sys_id"),
                    archimate_type,
                )

            except Exception as e:
                logger.error(f"Failed to process CMDB record {record.get('sys_id')}: {e}")
                continue

        return {"created": created, "updated": updated, "deleted": deleted}

    async def incremental_sync(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle real-time CMDB updates from webhooks."""
        try:
            record = event_data.get("record", {})
            if not record:
                raise ValueError("No record data in webhook event")

            # Process single record
            processed = await self._process_cmdb_records([record])

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


def create_servicenow_connector(config: ConnectorConfig) -> ServiceNowCMDBConnector:
    """Factory function to create ServiceNow connector."""
    return ServiceNowCMDBConnector(config)
