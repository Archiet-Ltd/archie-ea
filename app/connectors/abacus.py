"""
Avolution Abacus Connector

Enterprise Architecture repository integration with:
- OAuth2 client credentials authentication
- Application inventory synchronization
- Business capability hierarchy import
- Application-Capability relationship mapping
- Sub - 24h batch sync for architecture data

API Reference: https://help.avolutionsoftware.com/api/
Example Corp Abacus Instance: https://abacus.example.com/api/

Design Pattern:
- Follow BaseConnector interface
- OAuth2 token management with automatic refresh
- Field mapping DSL for Abacus → A.R.C.I.E transformation
- Merge conflict resolution (alias fields: abacus_name + name)
- Preserve A.R.C.I.E enrichments (TCO, rationalization, vendor analysis)
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import aiohttp

from ..config.abacus_field_mapping import (
    get_application_mappings,
    get_capability_mappings,
    get_outconnection_mappings,
    get_relationship_mappings,
)
from ..services.connector_framework import (
    BaseConnector,
    ConnectorConfig,
    ConnectorType,
    FieldMapping,
)

logger = logging.getLogger(__name__)


class AbacusConnector(BaseConnector):
    """Avolution Abacus EA repository connector implementation."""

    def __init__(self, config):
        """Initialize connector with token cache."""
        super().__init__(config)
        self._cached_token = None
        self._token_expires_at = None

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.EA_TOOL

    def get_required_config_fields(self) -> List[str]:
        """Required configuration fields for Abacus connector."""
        return ["base_url", "client_id", "client_secret"]

    def get_field_mappings(self) -> List[FieldMapping]:
        """
        Return field mappings for Abacus objects to A.R.C.I.E models.

        Now uses comprehensive field mapping configuration from
        app/config/abacus_field_mapping.py which includes:
        - Application mappings (20+ fields)
        - Capability mappings (12+ fields)
        - Relationship mappings (3+ fields)
        - Conflict resolution rules
        - Data type transformations
        - Validation rules
        """
        # Convert FieldMappingRule objects to FieldMapping objects
        # for backward compatibility with BaseConnector interface
        mappings = []

        # Add application mappings
        for rule in get_application_mappings():
            if not rule.abacus_field.startswith("_SYSTEM"):  # Skip system-generated
                mappings.append(
                    FieldMapping(
                        source_field=rule.abacus_field,
                        target_field=rule.arcie_field,
                        required=rule.required,
                        transform=rule.transform,
                    )
                )

        # Add capability mappings
        for rule in get_capability_mappings():
            if not rule.abacus_field.startswith("_SYSTEM"):
                mappings.append(
                    FieldMapping(
                        source_field=rule.abacus_field,
                        target_field=rule.arcie_field,
                        required=rule.required,
                        transform=rule.transform,
                    )
                )

        # Add relationship mappings
        for rule in get_relationship_mappings():
            mappings.append(
                FieldMapping(
                    source_field=rule.abacus_field,
                    target_field=rule.arcie_field,
                    required=rule.required,
                    transform=rule.transform,
                )
            )

        return mappings

    async def test_connection(self) -> bool:
        """Test connectivity to Abacus instance."""
        try:
            # Get OAuth token first
            token = await self._get_oauth_token()
            if not token:
                logger.error("Failed to obtain OAuth token")
                return False

            # Test basic API call - fetch architectures
            headers = {"Authorization": f"Bearer {token}"}
            url = urljoin(self.config.config["base_url"], "/api/Architectures")

            async with self._outbound_session() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(
                            f"Abacus connection successful. Found {len(data.get('value', []))} architectures."
                        )
                        return True
                    else:
                        logger.error(f"Abacus API returned status: {response.status}")
                        return False

        except asyncio.TimeoutError:
            logger.error("Abacus connection test timed out")
            return False
        except Exception as e:
            logger.error(f"Abacus connection test failed: {e}")
            return False

    async def _get_oauth_token(self) -> Optional[str]:
        """
        Get OAuth access token from Abacus using client credentials flow.
        Caches token to avoid multiple requests during parallel fetches.

        POST /api/Token
        Body: grant_type=client_credentials&client_id=...&client_secret=...
        """
        # Return cached token if still valid (with 60s buffer)
        if self._cached_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at:
                return self._cached_token

        try:
            token_url = urljoin(self.config.config["base_url"], "/api/Token")
            auth_data = {
                "grant_type": "client_credentials",
                "client_id": self.config.config["client_id"],
                "client_secret": self.config.config["client_secret"],
            }
            auth_headers = {"Content-Type": "application/x-www-form-urlencoded"}

            logger.info(f"Requesting OAuth token from: {token_url}")
            logger.debug(f"Using client_id: {self.config.config['client_id']}")

            async with self._outbound_session() as session:
                async with session.post(
                    token_url,
                    data=auth_data,
                    headers=auth_headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    logger.info(f"OAuth response status: {response.status}")
                    if response.status == 200:
                        token_data = await response.json()
                        access_token = token_data.get("access_token")
                        expires_in = token_data.get("expires_in", 3600)  # Default 1 hour
                        if access_token:
                            # Cache the token with expiry (subtract 60s buffer)
                            self._cached_token = access_token
                            self._token_expires_at = datetime.now() + timedelta(
                                seconds=max(expires_in - 60, 60)
                            )
                            logger.info("✅ Successfully obtained Abacus OAuth token")
                            return access_token
                        else:
                            logger.error("❌ No access_token in response")
                            return None
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"❌ OAuth token request failed: {response.status} - {error_text}"
                        )
                        return None

        except Exception as e:
            logger.error(f"❌ OAuth token error: {e}", exc_info=True)
            return None

    async def fetch_applications(self) -> List[Dict[str, Any]]:
        """
        Fetch applications from Abacus with full pagination support.

        GET /api/Components with OData query
        Returns list of application objects with embedded OutConnections.

        Per Minerva API docs:
        - Response limited to 512 entries max
        - Must use $skip or @odata.nextLink for pagination
        - OutConnections contains relationships (not separate endpoint)
        - Full extractions should run during 3:00-5:00 AM GMT
        """
        try:
            logger.info("🔄 Starting fetch_applications...")
            token = await self._get_oauth_token()
            if not token:
                logger.error("❌ Failed to obtain OAuth token for applications fetch")
                raise Exception("Failed to obtain OAuth token")

            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

            # Build base URL properly - ensure no double /api/
            base_url = self.config.config["base_url"].rstrip("/")
            if not base_url.endswith("/api"):
                base_url = f"{base_url}/api"
            url = f"{base_url}/Components"

            logger.info(f"Components endpoint URL: {url}")

            # BUILD FILTER: Start with apps + has name
            filter_parts = ["startsWith(ComponentTypeName,'Apps')", "Name ne null", "Name ne ''"]

            # Country filter via OutConnections (relationship: "App is used in Country")
            # Config: ABACUS_FILTER_COUNTRIES=United Kingdom,Ireland (comma-separated)
            # Set to empty or "all" to fetch all applications globally
            import os

            # Dynamic OutConnection filters from admin config
            config_dict = {}
            if hasattr(self, "config") and self.config and hasattr(self.config, "config"):
                if isinstance(self.config.config, dict):
                    config_dict = self.config.config

            # Generic filters: list of {connection_type, values} dicts
            dynamic_filters = config_dict.get("filters", [])

            # Legacy: filter_countries still supported for backwards compatibility
            filter_countries = config_dict.get("filter_countries", "")
            if not filter_countries:
                filter_countries = os.getenv("ABACUS_FILTER_COUNTRIES", "").strip()
            if not filter_countries:
                legacy_uk = os.getenv("ABACUS_FILTER_UK_ONLY", "false").lower() == "true"
                if legacy_uk:
                    filter_countries = "United Kingdom"

            if filter_countries and filter_countries.lower() != "all":
                # Convert legacy country filter to dynamic filter format
                dynamic_filters.append({
                    "connection_type": "App is used in Country",
                    "values": [c.strip() for c in filter_countries.split(",") if c.strip()]
                })

            # Apply all dynamic filters as OData OutConnections/any clauses
            applied_filters = []
            for f in dynamic_filters:
                conn_type = f.get("connection_type", "")
                values = f.get("values", [])
                if not conn_type or not values:
                    continue
                value_conditions = " or ".join(
                    f"o/SinkComponentName eq '{v}'" for v in values
                )
                odata_filter = (
                    f"OutConnections/any(o: "
                    f"o/ConnectionTypeName eq '{conn_type}' and "
                    f"({value_conditions})"
                    f")"
                )
                filter_parts.append(odata_filter)
                applied_filters.append(f"{conn_type}: {values}")

            if applied_filters:
                logger.info(f"Sync filters ENABLED: {applied_filters}")
            else:
                logger.info("No sync filters - fetching all applications")

            base_filter = " and ".join(filter_parts)
            logger.info(f"📋 OData Filter: {base_filter}")

            applications = []
            skip = 0
            page_size = 100  # Per docs: use $top with reasonable value
            max_iterations = 100  # Safety limit: 100 pages * 100 = 10,000 max records

            async with self._outbound_session() as session:
                for iteration in range(max_iterations):
                    params = {
                        "$filter": base_filter,
                        "$select": "EEID,Name,Description,ComponentTypeName,ArchitectureName",
                        "$expand": "Properties($select=Name,Value),OutConnections($select=ConnectionTypeName,SinkComponentName)",
                        "$top": str(page_size),
                        "$skip": str(skip),
                    }

                    logger.info(
                        f"📡 Requesting page {iteration + 1} with skip={skip}, top={page_size}"
                    )
                    async with session.get(
                        url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        logger.info(f"Response status for page {iteration + 1}: {response.status}")

                        if response.status == 200:
                            response_text = await response.text()
                            try:
                                data = json.loads(response_text)
                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse JSON: {response_text[:200]}")
                                break

                            raw_apps = data.get("value", [])
                            logger.info(
                                f"✅ Page {iteration + 1}: Received {len(raw_apps)} applications"
                            )

                            if not raw_apps:
                                # No more records
                                logger.info(
                                    f"ℹ️ No more records at skip={skip}. Total fetched: {len(applications)}"
                                )
                                break

                            # Parse and transform applications
                            for app in raw_apps:
                                applications.append(self._transform_application(app))

                            logger.debug(
                                f"Page {iteration + 1}: fetched {len(raw_apps)} apps (total: {len(applications)})"
                            )

                            # Check for @odata.nextLink (preferred pagination method)
                            next_link = data.get("@odata.nextLink")
                            if next_link:
                                # nextLink is a full URL supplied by the remote side:
                                # it may only continue on the configured host.
                                url = self._same_origin_link(next_link)
                                params = {}  # nextLink includes all params
                                skip = 0  # Reset skip since nextLink handles it
                            elif len(raw_apps) < page_size:
                                # Received fewer than requested = last page
                                break
                            else:
                                # Continue with $skip pagination
                                skip += len(raw_apps)
                        else:
                            error_text = await response.text()
                            raise Exception(f"API request failed: {response.status} - {error_text}")

            logger.info(f"Fetched {len(applications)} applications from Abacus (paginated)")
            return applications

        except Exception as e:
            logger.error(f"Failed to fetch applications: {e}")
            raise

    async def discover_filter_options(self) -> Dict[str, List[str]]:
        """
        Discover available filter dimensions from Abacus by scanning all app OutConnections.

        Paginates through ALL applications (not a sample) to extract every unique
        ConnectionTypeName + SinkComponentName pair.
        Returns: {"App is used in Country": ["United Kingdom", "France", ...], ...}
        """
        try:
            token = await self._get_oauth_token()
            if not token:
                return {}

            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            base_url = self.config.config["base_url"].rstrip("/")
            if not base_url.endswith("/api"):
                base_url = f"{base_url}/api"
            url = f"{base_url}/Components"

            dimensions: Dict[str, set] = {}
            skip = 0
            page_size = 500

            async with self._outbound_session() as session:
                for _ in range(20):  # Max 20 pages = 10,000 apps
                    params = {
                        "$filter": "startsWith(ComponentTypeName,'Apps') and Name ne null",
                        "$select": "EEID,Name",
                        "$expand": "OutConnections($select=ConnectionTypeName,SinkComponentName)",
                        "$top": str(page_size),
                        "$skip": str(skip),
                    }

                    async with session.get(
                        url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        if response.status != 200:
                            logger.warning("discover_filter_options: API returned %s", response.status)
                            break

                        data = await response.json()
                        items = data.get("value", [])

                    if not items:
                        break

                    for app in items:
                        for conn in app.get("OutConnections", []):
                            conn_type = conn.get("ConnectionTypeName", "")
                            sink_name = conn.get("SinkComponentName", "")
                            if conn_type and sink_name:
                                if conn_type not in dimensions:
                                    dimensions[conn_type] = set()
                                dimensions[conn_type].add(sink_name)

                    if len(items) < page_size:
                        break
                    skip += page_size

            logger.info("discover_filter_options: scanned %d apps, found %d dimensions", skip + len(items), len(dimensions))
            return {k: sorted(v) for k, v in sorted(dimensions.items())}

        except Exception as e:
            logger.error("discover_filter_options failed: %s", e)
            return {}

    async def discover_component_types(self) -> List[Dict[str, Any]]:
        """Discover all ComponentType names available in the Abacus instance.

        Calls GET /Components with $apply=groupby to get distinct ComponentTypeName
        values with counts. Returns a list of {name, count} dicts.
        """
        try:
            token = await self._get_oauth_token()
            if not token:
                return []

            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            base_url = self.config.config["base_url"].rstrip("/")
            if not base_url.endswith("/api"):
                base_url = f"{base_url}/api"
            url = f"{base_url}/Components"

            # Use OData groupby to get distinct types with counts
            params = {
                "$apply": "groupby((ComponentTypeName), aggregate($count as Count))",
                "$orderby": "Count desc",
            }

            async with self._outbound_session() as session:
                async with session.get(
                    url, headers=headers, params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        # Fallback: scan first page and collect unique types
                        logger.warning("groupby not supported (%s), falling back to scan", response.status)
                        return await self._discover_types_fallback(headers, url)

                    data = await response.json()
                    items = data.get("value", [])

            result = []
            for item in items:
                name = item.get("ComponentTypeName", "")
                count = item.get("Count", 0)
                if name:
                    result.append({"name": name, "count": count})

            logger.info("discover_component_types: found %d types", len(result))
            return result

        except Exception as e:
            logger.error("discover_component_types failed: %s", e)
            return []

    async def _discover_types_fallback(self, headers: dict, url: str) -> List[Dict[str, Any]]:
        """Fallback: scan components to collect unique ComponentTypeName values."""
        type_counts: Dict[str, int] = {}
        skip = 0
        page_size = 1000

        async with self._outbound_session() as session:
            for _ in range(10):
                params = {
                    "$select": "ComponentTypeName",
                    "$top": str(page_size),
                    "$skip": str(skip),
                }
                async with session.get(
                    url, headers=headers, params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        break
                    data = await response.json()
                    items = data.get("value", [])

                if not items:
                    break

                for item in items:
                    t = item.get("ComponentTypeName", "")
                    if t:
                        type_counts[t] = type_counts.get(t, 0) + 1

                if len(items) < page_size:
                    break
                skip += page_size

        return sorted(
            [{"name": k, "count": v} for k, v in type_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

    def _transform_application(self, abacus_app: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform Abacus application object to A.R.C.I.E format.

        Real Abacus API returns (per Minerva docs):
        - EEID (not Id) - primary identifier
        - Properties as array of {Name, Value} objects
        - OutConnections for relationships (ConnectionTypeName, SinkComponentName)
        """
        transformed = {}

        # DEBUG: Log first app to see what fields we're actually getting
        if not hasattr(self, "_logged_first_app"):
            logger.info(f"🔍 DEBUG: First application raw data keys: {list(abacus_app.keys())}")
            logger.info(f"🔍 DEBUG: First application EEID: {abacus_app.get('EEID')}")
            logger.info(f"🔍 DEBUG: First application Name: [{abacus_app.get('Name')}]")
            logger.info(
                f"🔍 DEBUG: First application Description: [{abacus_app.get('Description')}]"
            )
            logger.info(
                f"🔍 DEBUG: First application ComponentTypeName: {abacus_app.get('ComponentTypeName')}"
            )
            self._logged_first_app = True

        # Map EEID to external_id (primary key for sync)
        eeid = abacus_app.get("EEID", "")
        transformed["external_id"] = str(eeid)
        transformed["eeid"] = eeid  # Keep original for reference
        
        # Name extraction with fallback chain
        # AC-1,2,3,4: Try root Name → Properties array → application_code → EEID
        props = self._parse_properties_array(abacus_app.get("Properties", []))
        name = self._clean_text(abacus_app.get("Name", "").strip())
        if name:
            logger.debug(f"EEID {eeid}: Name from root-level Name field: {name}")
            transformed["name"] = name
        else:
            # Fallback 1: Check Properties array for Name
            name = props.get("Name", "").strip() or props.get("Application Name", "").strip()
            if name:
                logger.debug(f"EEID {eeid}: Name from Properties array: {name}")
                transformed["name"] = name
            else:
                # Fallback 2: Use application_code (APP ID)
                name = props.get("APP ID", "").strip()
                if name:
                    logger.debug(f"EEID {eeid}: Name from application_code (APP ID): {name}")
                    transformed["name"] = name
                else:
                    # Fallback 3: Use EEID as last resort
                    logger.debug(f"EEID {eeid}: Name from EEID as fallback: {eeid}")
                    transformed["name"] = str(eeid) if eeid else "Unknown Application"
        
        # Description extraction with same fallback chain
        # AC-7: Apply same logic to descriptions
        description = self._clean_text(abacus_app.get("Description", "").strip())
        if description:
            logger.debug(f"EEID {eeid}: Description from root-level Description field")
            transformed["description"] = description
        else:
            # Fallback 1: Check Properties array for Description
            description = self._clean_text(props.get("Description", "").strip())
            if description:
                logger.debug(f"EEID {eeid}: Description from Properties array")
                transformed["description"] = description
            else:
                # Fallback 2: Use Category
                description = self._clean_text(props.get("Category", "").strip())
                if description:
                    logger.debug(f"EEID {eeid}: Description from Category: {description}")
                    transformed["description"] = description
                else:
                    # Fallback 3: Use empty string (Description is optional)
                    logger.debug(f"EEID {eeid}: No description found, using empty string")
                    transformed["description"] = ""

        # Convert Properties array to dict for easier lookup
        # Per docs: Properties is array of {Name, Value} objects
        # Already parsed above in name/description extraction
        
        transformed["application_id"] = props.get("APP ID", f"APP-{eeid}")
        transformed["apps_portal_url"] = props.get("Apps Portal URL", "")
        transformed["category"] = props.get("Category", "")
        transformed["deployment_scope"] = props.get("Deployment Scope", "")
        transformed["status"] = props.get("Application Status", "")
        transformed["criticality"] = props.get("Criticality", "")

        # Properties (discovered 2026-03-18): Owner/domain NOT in Properties array.
        # They come from OutConnections (App Business Owner, Application Manager, etc.)
        # Defaults here; overridden by OutConnections extraction below.
        transformed["business_owner"] = ""
        transformed["technical_owner"] = ""
        transformed["application_owner"] = ""
        transformed["vendor_name"] = ""  # Not in Abacus (no vendor connection type)
        transformed["business_domain"] = ""
        transformed["business_criticality"] = props.get("Business Criticality", "") or props.get("Criticality", "")
        transformed["application_category"] = props.get("Category", "")
        # Security classification fields (available in Abacus)
        transformed["data_classification"] = props.get("Data Sensitivity Type", "")
        transformed["availability_level"] = props.get("Availability Level", "")
        transformed["confidentiality_level"] = props.get("Confidentiality Level", "")
        transformed["integrity_level"] = props.get("Integrity Level", "")
        # Risk data (available in Abacus)
        transformed["risk_score"] = props.get("Risk Score", "")
        transformed["risk_assessment_status"] = props.get("Risk Assessment Status", "")
        # Risk Level: use direct value if available, else derive from PSAT Status
        risk_level = props.get("Risk Level", "")
        if not risk_level:
            psat = props.get("PSAT Status", "").upper()
            psat_risk_map = {
                "DONE": "LOW",
                "GO_BUILD": "LOW",
                "CONDITIONAL_GO_LIVE": "MODERATE",
                "PARTIAL_CONDITION_GO_LIVE": "MODERATE",
                "SECURITY_INTERVIEW": "MODERATE",
                "SO_REVIEW_REQUESTED": "MODERATE",
                "GO_DESIGN": "MODERATE",
                "UNKNOWN": "HIGH",
            }
            risk_level = psat_risk_map.get(psat, "")
        transformed["risk_level"] = risk_level
        # Management
        transformed["managed_type"] = props.get("Managed type", "")
        transformed["go_live_year"] = props.get("First Go-Live (year)", "")

        # Cost and financial data — write to existing ApplicationComponent financial columns
        raw_cost = (
            props.get("Annual Cost", "")
            or props.get("Total Cost of Ownership", "")
            or props.get("License Cost", "")
            or props.get("Annual License Cost", "")
            or props.get("Budget", "")
        )
        if raw_cost:
            try:
                import re as _re
                cost_num = float(_re.sub(r"[^\d.]", "", str(raw_cost)))
                transformed["annual_cost"] = cost_num
            except (ValueError, TypeError):
                transformed["annual_cost"] = None
        else:
            transformed["annual_cost"] = None

        transformed["cost_centre"] = (
            props.get("Cost Centre", "")
            or props.get("Cost Center", "")
            or props.get("Department", "")
        )
        transformed["number_of_users"] = (
            props.get("Number of Users", "")
            or props.get("Active Users", "")
            or props.get("User Count", "")
        )

        # Lifecycle: extract raw value then normalize
        raw_lifecycle = props.get(
            "Lifecycle Status", props.get("Application Status", "")
        )
        transformed["lifecycle_status_raw"] = raw_lifecycle  # Keep raw for debugging
        # Normalize lifecycle to human-readable values
        from app.config.abacus_field_mapping import normalize_lifecycle_status, derive_deployment_from_lifecycle
        transformed["lifecycle_status"] = normalize_lifecycle_status(raw_lifecycle) if raw_lifecycle else ""
        # Derive deployment_status from normalized lifecycle
        normalized = transformed["lifecycle_status"]
        transformed["deployment_status"] = derive_deployment_from_lifecycle(normalized) if normalized else ""

        # Store all properties as JSON for future field extraction
        transformed["abacus_properties"] = str(props) if props else ""

        # Extract OutConnections (relationships) - these are embedded per Minerva docs
        out_connections = abacus_app.get("OutConnections", [])
        transformed["_out_connections"] = out_connections  # Preserve for relationship extraction

        # Extract owner/manager/team from OutConnections (discovered 2026-03-18)
        # Connection types: "App Business Owner", "Application Manager",
        # "IT Security Officer", "Business Unit Owner of the App",
        # "IT Unit Managing the App", "Business Security Officer"
        conn_map = {}
        for conn in out_connections:
            ct = conn.get("ConnectionTypeName", "")
            sink = conn.get("SinkComponentName", "")
            if ct and sink:
                conn_map.setdefault(ct, sink)  # Keep first (primary)

        if conn_map.get("App Business Owner"):
            transformed["business_owner"] = conn_map["App Business Owner"]
        if conn_map.get("Application Manager"):
            transformed["application_owner"] = conn_map["Application Manager"]
        if conn_map.get("IT Security Officer"):
            transformed["technical_owner"] = conn_map["IT Security Officer"]
        if conn_map.get("Business Unit Owner of the App"):
            transformed["business_domain"] = conn_map["Business Unit Owner of the App"]

        # Mark as Abacus source with import timestamp
        transformed["abacus_source"] = True
        transformed["discovered_by_ai"] = False
        transformed["abacus_import_timestamp"] = datetime.now().isoformat()

        return transformed

    def _parse_properties_array(self, properties: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Parse Minerva Properties array format to dict.

        Minerva API returns Properties as:
        [{"Name": "APP ID", "Value": "APP-123"}, {"Name": "Category", "Value": "Business Application"}, ...]

        Returns dict: {"APP ID": "APP-123", "Category": "Business Application", ...}
        """
        props = {}
        if not properties:
            return props

        for prop in properties:
            if isinstance(prop, dict):
                name = prop.get("Name")
                value = prop.get("Value")
                if name:
                    props[name] = value
        return props

    def _clean_text(self, text: str) -> str:
        """
        Strip invisible Unicode characters that cause WIN1252 encoding errors.

        PostgreSQL client encoding WIN1252 cannot handle zero-width spaces (U+200B),
        zero-width non-joiners (U+200C/D), BOM (U+FEFF), etc.
        """
        if not text:
            return text
        # Remove zero-width and invisible Unicode characters
        return re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff\u00ad]', '', text)

    async def fetch_capabilities(self) -> List[Dict[str, Any]]:
        """
        Fetch business capabilities from Abacus with pagination.

        GET /api/Components with capability filter
        Returns list of capability objects with hierarchy.

        Per Minerva API docs:
        - Response limited to 512 entries max
        - Must use $skip or @odata.nextLink for pagination
        """
        try:
            token = await self._get_oauth_token()
            if not token:
                raise Exception("Failed to obtain OAuth token")

            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

            # Build base URL properly
            base_url = self.config.config["base_url"].rstrip("/")
            if not base_url.endswith("/api"):
                base_url = f"{base_url}/api"
            url = f"{base_url}/Components"

            # Query parameters for capabilities
            base_filter = "startsWith(ComponentTypeName,'Cap')"

            capabilities = []
            skip = 0
            page_size = 100
            max_iterations = 100  # Safety limit

            async with self._outbound_session() as session:
                for iteration in range(max_iterations):
                    params = {
                        "$filter": base_filter,
                        "$select": "EEID,Name,Description,ComponentTypeName",
                        "$expand": "Properties($select=Name,Value),OutConnections($select=ConnectionTypeName,SinkComponentName,SinkComponentEEID)",
                        "$top": str(page_size),
                        "$skip": str(skip),
                    }

                    async with session.get(
                        url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        if response.status == 200:
                            response_text = await response.text()
                            try:
                                data = json.loads(response_text)
                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse JSON: {response_text[:200]}")
                                break

                            raw_caps = data.get("value", [])

                            if not raw_caps:
                                break

                            # Parse and transform capabilities
                            for cap in raw_caps:
                                capabilities.append(self._transform_capability(cap))

                            logger.debug(
                                f"Capabilities page {iteration + 1}: {len(raw_caps)} (total: {len(capabilities)})"
                            )

                            # Check for pagination
                            next_link = data.get("@odata.nextLink")
                            if next_link:
                                # remote-supplied: may only continue on the configured host
                                url = self._same_origin_link(next_link)
                                params = {}
                                skip = 0
                            elif len(raw_caps) < page_size:
                                break
                            else:
                                skip += len(raw_caps)
                        else:
                            error_text = await response.text()
                            raise Exception(f"API request failed: {response.status} - {error_text}")

            logger.info(f"Fetched {len(capabilities)} capabilities from Abacus (paginated)")
            return capabilities

        except Exception as e:
            logger.error(f"Failed to fetch capabilities: {e}")
            raise

    # Connection types that indicate parent-child composition in capabilities
    _COMPOSITION_CONNECTION_TYPES = {
        "compositionrelationship",
        "composition",
        "is composed of",
        "is part of",
        "aggregationrelationship",
        "aggregation",
    }

    def _transform_capability(self, abacus_cap: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform Abacus capability object to A.R.C.I.E format.

        Per Minerva API docs:
        - EEID is the primary identifier (not Id)
        - Properties is array of {Name, Value} objects
        - OutConnections contains composition relationships for hierarchy
        """
        transformed = {}

        # Map EEID (primary identifier per Minerva docs)
        eeid = abacus_cap.get("EEID", "")
        transformed["external_id"] = str(eeid)
        transformed["eeid"] = eeid
        transformed["archimate_id"] = str(eeid)  # For Abacus EEID storage

        # Core fields
        transformed["name"] = self._clean_text(abacus_cap.get("Name", ""))
        transformed["description"] = self._clean_text(abacus_cap.get("Description", ""))
        transformed["component_type"] = abacus_cap.get("ComponentTypeName", "")

        # Parse Properties array (Minerva format: [{Name, Value}, ...])
        props = self._parse_properties_array(abacus_cap.get("Properties", []))

        # Map properties to capability fields
        transformed["level"] = self._parse_capability_level(
            props.get("Level", props.get("Capability Level", "L1"))
        )
        transformed["domain"] = props.get("Domain", props.get("Business Domain", ""))
        transformed["category"] = props.get("Category", "")
        transformed["strategic_importance"] = props.get("Strategic Importance", "")

        # Extract parent EEID from OutConnections (CompositionRelationship)
        # Abacus encodes parent-child hierarchy via composition connections
        parent_eeid = self._extract_parent_eeid(abacus_cap.get("OutConnections", []))
        if parent_eeid:
            transformed["parent_archimate_element_id"] = str(parent_eeid)

        # Alias fields for merge conflict resolution
        transformed["abacus_name"] = transformed["name"]
        transformed["abacus_description"] = transformed["description"]

        # System-generated flags
        transformed["discovered_by_ai"] = False
        transformed["abacus_source"] = True

        return transformed

    def _extract_parent_eeid(self, out_connections: List[Dict[str, Any]]) -> Optional[str]:
        """
        Extract parent capability EEID from OutConnections composition relationships.

        Looks for CompositionRelationship or similar hierarchy connection types
        in the OutConnections array and returns the target (parent) EEID.

        Args:
            out_connections: List of OutConnection dicts from Abacus API

        Returns:
            Parent EEID string if found, None otherwise
        """
        if not out_connections:
            return None

        for conn in out_connections:
            if not isinstance(conn, dict):
                continue
            conn_type = (conn.get("ConnectionTypeName") or "").lower().strip()
            if conn_type in self._COMPOSITION_CONNECTION_TYPES:
                sink_eeid = conn.get("SinkComponentEEID")
                if sink_eeid:
                    return str(sink_eeid)
                # Fallback: use SinkComponentName for name-based resolution
                # (handled downstream in import service)
        return None

    def _parse_capability_level(self, value: Any) -> int:
        """Parse capability level to integer (1, 2, 3)."""
        if not value:
            return 1

        value_str = str(value).upper().strip()

        if "L1" in value_str or "STRATEGIC" in value_str or value_str == "1":
            return 1
        elif "L2" in value_str or "TACTICAL" in value_str or value_str == "2":
            return 2
        elif "L3" in value_str or "OPERATIONAL" in value_str or value_str == "3":
            return 3

        return 1

    # Connection types that are NOT capability relationships - skip these
    _NON_CAPABILITY_CONNECTION_TYPES = {
        "app is used in country",
        "app is managed by",
        "is used in country",
        "is managed by",
        "is owned by",
        "has owner",
        "has business owner",
        "has technical owner",
        "has it owner",
        "is deployed on",
        "runs on",
        "is hosted on",
        "app is hosted by",
        "is used by",
        "is accessed via",
        "has access method",
    }

    def extract_all_typed_connections(
        self,
        applications: List[Dict[str, Any]],
        outconnection_mappings: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Extract ALL OutConnections, categorised as capability or typed relationships.

        Instead of discarding non-capability connections, this method routes
        them through the OutConnection → ArchiMate mapping from
        ``abacus_field_mapping.DEFAULT_OUTCONNECTION_MAPPINGS`` (or a custom
        mapping dict supplied by the caller).

        Args:
            applications: List of transformed applications (with _out_connections).
            outconnection_mappings: Optional override for the default mapping.
                Keys are lowercase connection type names, values are dicts with
                ``rel_type``, ``source_type``, ``target_type``.

        Returns:
            Dict with two keys:
            - ``capability_relationships``: same format as the old
              ``extract_relationships_from_applications`` output.
            - ``typed_relationships``: connections that matched a known
              OutConnection mapping, each annotated with ArchiMate metadata.
        """
        if outconnection_mappings is None:
            outconnection_mappings = get_outconnection_mappings()

        capability_relationships: List[Dict[str, Any]] = []
        typed_relationships: List[Dict[str, Any]] = []
        unmapped_types: Dict[str, int] = {}
        logged_first = False

        for app in applications:
            app_eeid = app.get("eeid") or app.get("external_id")
            app_name = app.get("name", "")
            out_connections = app.get("_out_connections", [])

            # Diagnostic: log connection types from first app
            if not logged_first and out_connections:
                conn_types = set()
                for c in out_connections:
                    if isinstance(c, dict):
                        conn_types.add(c.get("ConnectionTypeName", ""))
                logger.info(
                    f"Connection types in first app ({app_name}): {sorted(conn_types)}"
                )
                logged_first = True

            for conn in out_connections:
                if not isinstance(conn, dict):
                    continue

                conn_type = conn.get("ConnectionTypeName", "")
                conn_type_lower = conn_type.lower().strip()
                target_name = conn.get("SinkComponentName", "")

                # Route the connection:
                # 1. If it has an explicit ArchiMate mapping → typed_relationships
                # 2. If it's in the non-capability set but unmapped → typed with fallback
                # 3. Otherwise → capability_relationships (existing behaviour)
                mapping = outconnection_mappings.get(conn_type_lower)

                if mapping:
                    # Has an explicit ArchiMate mapping → typed relationship
                    typed_relationships.append({
                        "source_eeid": app_eeid,
                        "source_name": app_name,
                        "connection_type": conn_type,
                        "target_name": target_name,
                        "abacus_source": True,
                        "rel_type": mapping["rel_type"],
                        "source_type": mapping["source_type"],
                        "target_type": mapping["target_type"],
                        "unknown_mapping": False,
                    })
                elif conn_type_lower in self._NON_CAPABILITY_CONNECTION_TYPES:
                    # Non-capability type with no mapping → capture with
                    # association fallback so no data is lost.
                    typed_relationships.append({
                        "source_eeid": app_eeid,
                        "source_name": app_name,
                        "connection_type": conn_type,
                        "target_name": target_name,
                        "abacus_source": True,
                        "rel_type": "association",
                        "source_type": "ApplicationComponent",
                        "target_type": "ApplicationComponent",
                        "unknown_mapping": True,
                    })
                    unmapped_types[conn_type] = unmapped_types.get(conn_type, 0) + 1
                else:
                    # Capability-related connection (existing behaviour)
                    capability_relationships.append({
                        "source_eeid": app_eeid,
                        "source_name": app_name,
                        "connection_type": conn_type,
                        "target_name": target_name,
                        "abacus_source": True,
                    })

        if unmapped_types:
            logger.info(
                "Unmapped OutConnection types (captured with association fallback): "
                + ", ".join(f"{k}: {v}" for k, v in sorted(unmapped_types.items()))
            )

        logger.info(
            f"Extracted {len(capability_relationships)} capability relationships "
            f"and {len(typed_relationships)} typed relationships from OutConnections"
        )
        return {
            "capability_relationships": capability_relationships,
            "typed_relationships": typed_relationships,
        }

    def extract_relationships_from_applications(
        self, applications: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Extract capability relationships from OutConnections (backward-compatible).

        This delegates to :meth:`extract_all_typed_connections` and returns
        only the ``capability_relationships`` portion, preserving the
        original return type for existing callers.
        """
        result = self.extract_all_typed_connections(applications)
        return result["capability_relationships"]

    async def fetch_relationships(self) -> List[Dict[str, Any]]:
        """
        Fetch Application-Capability relationships from Abacus.

        IMPORTANT: Per Minerva API documentation, relationships are embedded
        in the Components response via OutConnections, NOT via a separate endpoint.

        This method fetches applications with OutConnections expanded and
        extracts relationships from them.
        """
        try:
            # Fetch applications (which include OutConnections)
            applications = await self.fetch_applications()

            # Extract relationships from embedded OutConnections
            relationships = self.extract_relationships_from_applications(applications)

            logger.info(
                f"Fetched {len(relationships)} relationships from Abacus (via OutConnections)"
            )
            return relationships

        except Exception as e:
            logger.error(f"Failed to fetch relationships: {e}")
            raise

    def _transform_relationship(
        self, connection: Dict[str, Any], source_app: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Transform Abacus OutConnection to A.R.C.I.E relationship format.

        Per Minerva docs, OutConnections contain:
        - ConnectionTypeName: Type of relationship
        - SinkComponentName: Name of target component
        """
        return {
            "source_id": source_app.get("external_id", ""),
            "source_name": source_app.get("name", ""),
            "relationship_type": connection.get("ConnectionTypeName", ""),
            "target_name": connection.get("SinkComponentName", ""),
            "abacus_source": True,
        }

    async def batch_sync(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Perform batch synchronization of Abacus data.

        Fetches applications, capabilities, and relationships.
        Returns statistics on records processed.
        """
        try:
            logger.info(f"Starting Abacus batch sync (since: {since})")

            # Pre-fetch OAuth token to avoid race conditions in parallel requests
            token = await self._get_oauth_token()
            if not token:
                raise Exception("Failed to obtain OAuth token")

            # Fetch apps and capabilities in parallel (relationships come from apps)
            apps_task = self.fetch_applications()
            caps_task = self.fetch_capabilities()

            applications, capabilities = await asyncio.gather(apps_task, caps_task)

            # Extract relationships from applications (OutConnections)
            relationships = self.extract_relationships_from_applications(applications)

            return {
                "status": "completed",
                "applications_fetched": len(applications),
                "capabilities_fetched": len(capabilities),
                "relationships_fetched": len(relationships),
                "applications": applications,
                "capabilities": capabilities,
                "relationships": relationships,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Batch sync failed: {e}", exc_info=True)
            return {"status": "failed", "error": str(e), "timestamp": datetime.utcnow().isoformat()}


def create_abacus_connector(config: ConnectorConfig) -> AbacusConnector:
    """Factory function to create Abacus connector."""
    return AbacusConnector(config)
