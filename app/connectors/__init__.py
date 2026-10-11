"""
Connectors Package

Connector implementations for external systems, in progress:
- ServiceNow CMDB, Jira ALM, Datadog APM: authenticate and field-map
  records from the source system, then log them rather than persisting
  them -- there is no write path into this platform's data yet.
- Abacus EA Tool: fetches applications, capabilities and relationships
  from Avolution's API; also does not persist them.

No connector instance is registered with ConnectorManager anywhere in this
codebase, so the connector dashboard's manual sync action is not available
for any of the above today.
"""

from .abacus import AbacusConnector, create_abacus_connector
from .datadog import DatadogAPMConnector, create_datadog_connector
from .jira import JiraALMConnector, create_jira_connector
from .servicenow import ServiceNowCMDBConnector, create_servicenow_connector

__all__ = [
    "ServiceNowCMDBConnector",
    "create_servicenow_connector",
    "JiraALMConnector",
    "create_jira_connector",
    "DatadogAPMConnector",
    "create_datadog_connector",
    "AbacusConnector",
    "create_abacus_connector",
]
