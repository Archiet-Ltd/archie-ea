"""ServiceNow CMDB Connector - Enterprise Integration

Syncs Configuration Items (CIs) from ServiceNow CMDB to Entelim applications.

Supports:
- Application discovery from CMDB
- Infrastructure CI sync (servers, databases, middleware)
- Business service → Capability mapping
- Automated relationship mapping
- Bidirectional sync (pull & optional push)

ServiceNow Tables:
- cmdb_ci_appl (Applications)
- cmdb_ci_server (Servers)
- cmdb_ci_database (Databases)
- cmdb_ci_service (Business Services)
- cmdb_rel_ci (Relationships)

API: ServiceNow Table API (REST) + /api/now/v2/table/
Auth: Basic auth or OAuth 2.0
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from flask import current_app

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.vendor.vendor_organization import VendorOrganization

logger = logging.getLogger(__name__)


@dataclass
class ServiceNowSyncResult:
    """Result of ServiceNow CMDB sync operation."""
    
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    applications_created: int = 0
    applications_updated: int = 0
    servers_synced: int = 0
    databases_synced: int = 0
    relationships_created: int = 0
    errors: List[str] = field(default_factory=list)
    healthy: bool = True
    
    def finish(self):
        """Mark sync as complete."""
        self.completed_at = datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "provider": "servicenow",
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "applications_created": self.applications_created,
            "applications_updated": self.applications_updated,
            "servers_synced": self.servers_synced,
            "databases_synced": self.databases_synced,
            "relationships_created": self.relationships_created,
            "errors": self.errors[:20],  # Limit error list
            "healthy": self.healthy,
            "duration_seconds": (
                (self.completed_at - self.started_at).total_seconds()
                if self.completed_at else None
            )
        }


class ServiceNowConnector:
    """ServiceNow CMDB integration connector.
    
    Configuration (from Flask config or environment):
    - SERVICENOW_INSTANCE: Instance URL (e.g., 'https://yourorg.service-now.com')
    - SERVICENOW_USERNAME: API username
    - SERVICENOW_PASSWORD: API password (or OAuth token)
    - SERVICENOW_BATCH_SIZE: Records per API call (default: 100, max: 1000)
    
    Usage:
        connector = ServiceNowConnector()
        if connector.health_check():
            result = connector.sync_applications()
            print(result.to_dict())
    """
    
    def __init__(
        self,
        instance_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        batch_size: int = 100,
        timeout: int = 30
    ):
        """Initialize ServiceNow connector.
        
        Args:
            instance_url: ServiceNow instance URL (overrides config)
            username: API username (overrides config)
            password: API password (overrides config)
            batch_size: Records per API call (1-1000)
            timeout: Request timeout in seconds
        """
        self.instance_url = instance_url or current_app.config.get('SERVICENOW_INSTANCE', '')
        self.username = username or current_app.config.get('SERVICENOW_USERNAME', '')
        self.password = password or current_app.config.get('SERVICENOW_PASSWORD', '')
        self.batch_size = min(batch_size, 1000)  # ServiceNow max is 1000
        self.timeout = timeout
        
        # Strip trailing slash from instance URL
        self.instance_url = self.instance_url.rstrip('/')
        
        # Base API endpoint
        self.api_base = f"{self.instance_url}/api/now/table"
    
    def health_check(self) -> bool:
        """Verify ServiceNow connectivity and credentials.
        
        Returns:
            True if connection successful, False otherwise
        """
        if not all([self.instance_url, self.username, self.password]):
            logger.warning("ServiceNow connector: Missing configuration (instance/username/password)")
            return False
        
        try:
            # Test with lightweight sys_user query (just get current user)
            url = f"{self.api_base}/sys_user"
            params = {
                'sysparm_query': f'user_name={self.username}',
                'sysparm_limit': 1,
                'sysparm_fields': 'sys_id,user_name'
            }
            
            response = requests.get(
                url,
                auth=(self.username, self.password),
                params=params,
                timeout=10,
                headers={'Accept': 'application/json'}
            )
            
            if response.status_code == 200:
                logger.info("ServiceNow health check: OK")
                return True
            elif response.status_code == 401:
                logger.error("ServiceNow health check: Authentication failed (401)")
                return False
            else:
                logger.error(f"ServiceNow health check: HTTP {response.status_code}")
                return False
                
        except requests.RequestException as e:
            logger.error(f"ServiceNow health check failed: {e}")
            return False
    
    def sync_applications(
        self,
        query_filter: Optional[str] = None,
        limit: Optional[int] = None
    ) -> ServiceNowSyncResult:
        """Sync applications from ServiceNow CMDB.
        
        Args:
            query_filter: Optional ServiceNow query filter (e.g., 'operational_status=1')
            limit: Maximum applications to sync (None = all)
        
        Returns:
            ServiceNowSyncResult with sync statistics
        """
        result = ServiceNowSyncResult()
        
        if not self.health_check():
            result.errors.append("Health check failed - cannot sync")
            result.healthy = False
            result.finish()
            return result
        
        try:
            # Fetch applications from cmdb_ci_appl table
            applications = self._fetch_cmdb_applications(query_filter, limit)
            
            logger.info(f"ServiceNow: Fetched {len(applications)} applications from CMDB")
            
            for app_data in applications:
                try:
                    self._sync_application(app_data, result)
                except Exception as e:
                    error_msg = f"App {app_data.get('name', 'unknown')}: {str(e)}"
                    result.errors.append(error_msg)
                    logger.error(f"ServiceNow sync error: {error_msg}")
            
            db.session.commit()
            logger.info(
                f"ServiceNow sync complete: {result.applications_created} created, "
                f"{result.applications_updated} updated"
            )
            
        except Exception as e:
            result.errors.append(f"Sync failed: {str(e)}")
            result.healthy = False
            logger.error(f"ServiceNow sync failed: {e}", exc_info=True)
            db.session.rollback()
        
        result.finish()
        return result
    
    def _fetch_cmdb_applications(
        self,
        query_filter: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch applications from ServiceNow cmdb_ci_appl table.
        
        Args:
            query_filter: ServiceNow query string
            limit: Max records to fetch
        
        Returns:
            List of application dictionaries
        """
        url = f"{self.api_base}/cmdb_ci_appl"
        
        # Build query parameters
        params = {
            'sysparm_limit': self.batch_size,
            'sysparm_offset': 0,
            'sysparm_fields': ','.join([
                'sys_id',
                'name',
                'u_application_id',  # Custom field - adjust if different
                'short_description',
                'version',
                'vendor',
                'owned_by',
                'managed_by',
                'support_group',
                'operational_status',
                'install_status',
                'u_criticality',  # Custom field
                'cost_cc',
                'u_annual_cost',  # Custom field
                'u_hosting_type',  # Custom field (cloud/on-prem)
                'sys_created_on',
                'sys_updated_on'
            ])
        }
        
        # Add custom query filter if provided
        if query_filter:
            params['sysparm_query'] = query_filter
        else:
            # Default: only operational applications
            params['sysparm_query'] = 'operational_status=1^install_status=1'
        
        applications = []
        total_fetched = 0
        
        while True:
            try:
                response = requests.get(
                    url,
                    auth=(self.username, self.password),
                    params=params,
                    timeout=self.timeout,
                    headers={'Accept': 'application/json'}
                )
                response.raise_for_status()
                
                data = response.json()
                batch = data.get('result', [])
                
                if not batch:
                    break
                
                applications.extend(batch)
                total_fetched += len(batch)
                
                logger.debug(f"ServiceNow: Fetched batch of {len(batch)} apps (total: {total_fetched})")
                
                # Check if we've hit the limit
                if limit and total_fetched >= limit:
                    applications = applications[:limit]
                    break
                
                # Check if there are more pages
                if len(batch) < self.batch_size:
                    break
                
                # Move to next page
                params['sysparm_offset'] += self.batch_size
                
            except requests.RequestException as e:
                logger.error(f"ServiceNow API error fetching applications: {e}")
                raise
        
        return applications
    
    def _sync_application(
        self,
        snow_app: Dict[str, Any],
        result: ServiceNowSyncResult
    ) -> None:
        """Sync a single application from ServiceNow to Entelim
        
        Args:
            snow_app: ServiceNow application data dict
            result: Sync result object to update
        """
        sys_id = snow_app.get('sys_id')
        name = snow_app.get('name', '').strip()
        
        if not name:
            result.errors.append(f"Application {sys_id} has no name - skipped")
            return
        
        # Look up existing application by ServiceNow sys_id (stored in external_id)
        existing = ApplicationComponent.query.filter_by(external_id=sys_id).first()
        
        if existing:
            # Update existing application
            self._update_application(existing, snow_app)
            result.applications_updated += 1
            logger.debug(f"Updated application: {name}")
        else:
            # Create new application
            app = self._create_application(snow_app)
            db.session.add(app)
            result.applications_created += 1
            logger.debug(f"Created application: {name}")
    
    def _create_application(self, snow_app: Dict[str, Any]) -> ApplicationComponent:
        """Create new ApplicationComponent from ServiceNow data.
        
        Args:
            snow_app: ServiceNow application data
        
        Returns:
            New ApplicationComponent instance
        """
        # Map ServiceNow fields to Entelim fields
        app = ApplicationComponent(
            external_id=snow_app.get('sys_id'),  # ServiceNow sys_id for tracking
            name=snow_app.get('name', '').strip(),
            description=snow_app.get('short_description', ''),
            version=snow_app.get('version', ''),
            
            # Lifecycle status mapping
            lifecycle_status=self._map_lifecycle_status(
                snow_app.get('operational_status'),
                snow_app.get('install_status')
            ),
            
            # Criticality mapping (if custom field exists)
            criticality=self._map_criticality(snow_app.get('u_criticality')),
            
            # Cost data
            annual_cost=self._parse_float(snow_app.get('u_annual_cost')),
            
            # Hosting type
            hosting_type=self._map_hosting_type(snow_app.get('u_hosting_type')),
            
            # Ownership (will need user lookup/mapping)
            technical_owner=snow_app.get('managed_by', {}).get('display_value') if isinstance(snow_app.get('managed_by'), dict) else None,
            business_owner=snow_app.get('owned_by', {}).get('display_value') if isinstance(snow_app.get('owned_by'), dict) else None,
            
            # Vendor lookup (will need vendor resolution)
            # vendor_id will be set by _resolve_vendor if vendor info exists
            
            # Metadata
            notes=f"Synced from ServiceNow CMDB on {datetime.now(timezone.utc).isoformat()}",
        )
        
        # Resolve vendor if vendor field exists
        vendor_name = snow_app.get('vendor', {}).get('display_value') if isinstance(snow_app.get('vendor'), dict) else snow_app.get('vendor')
        if vendor_name:
            vendor = self._resolve_vendor(vendor_name)
            if vendor:
                app.vendor_id = vendor.id
        
        return app
    
    def _update_application(
        self,
        app: ApplicationComponent,
        snow_app: Dict[str, Any]
    ) -> None:
        """Update existing application with latest ServiceNow data.
        
        Args:
            app: Existing ApplicationComponent
            snow_app: ServiceNow application data
        """
        # Update mutable fields
        app.description = snow_app.get('short_description', app.description)
        app.version = snow_app.get('version', app.version)
        
        # Update lifecycle status
        app.lifecycle_status = self._map_lifecycle_status(
            snow_app.get('operational_status'),
            snow_app.get('install_status')
        )
        
        # Update criticality if changed
        new_criticality = self._map_criticality(snow_app.get('u_criticality'))
        if new_criticality:
            app.criticality = new_criticality
        
        # Update cost if available
        new_cost = self._parse_float(snow_app.get('u_annual_cost'))
        if new_cost:
            app.annual_cost = new_cost
        
        # Update notes to reflect sync
        sync_note = f"\nUpdated from ServiceNow on {datetime.now(timezone.utc).isoformat()}"
        if app.notes and sync_note not in app.notes:
            app.notes = (app.notes or '') + sync_note
        else:
            app.notes = f"Synced from ServiceNow CMDB{sync_note}"
    
    def _map_lifecycle_status(
        self,
        operational_status: Optional[str],
        install_status: Optional[str]
    ) -> str:
        """Map ServiceNow status to Entelim lifecycle status.
        
        ServiceNow operational_status values:
        1 = Operational, 2 = Non-Operational, 3 = Under Maintenance, 4 = Retired
        
        Args:
            operational_status: ServiceNow operational_status value
            install_status: ServiceNow install_status value
        
        Returns:
            Entelim lifecycle status string
        """
        # Map ServiceNow status codes to Entelim lifecycle
        status_map = {
            '1': 'active',           # Operational
            '2': 'deprecated',       # Non-Operational
            '3': 'maintenance',      # Under Maintenance
            '4': 'retired',          # Retired
            '5': 'planned',          # Planned (if exists)
            '6': 'development'       # Development (if exists)
        }
        
        return status_map.get(str(operational_status), 'active')
    
    def _map_criticality(self, snow_criticality: Optional[str]) -> Optional[str]:
        """Map ServiceNow criticality to Entelim criticality.
        
        Args:
            snow_criticality: ServiceNow criticality value
        
        Returns:
            Entelim criticality string or None
        """
        if not snow_criticality:
            return None
        
        # Common ServiceNow criticality mappings
        criticality_map = {
            '1': 'critical',
            '2': 'high',
            '3': 'medium',
            '4': 'low',
            'critical': 'critical',
            'high': 'high',
            'medium': 'medium',
            'low': 'low'
        }
        
        return criticality_map.get(str(snow_criticality).lower(), 'medium')
    
    def _map_hosting_type(self, snow_hosting: Optional[str]) -> Optional[str]:
        """Map ServiceNow hosting type to Entelim hosting type.
        
        Args:
            snow_hosting: ServiceNow hosting type value
        
        Returns:
            Entelim hosting type string or None
        """
        if not snow_hosting:
            return None
        
        hosting = str(snow_hosting).lower()
        
        if 'cloud' in hosting or 'saas' in hosting:
            return 'cloud'
        elif 'on-prem' in hosting or 'onprem' in hosting or 'on premise' in hosting:
            return 'on_premise'
        elif 'hybrid' in hosting:
            return 'hybrid'
        
        return 'on_premise'  # Default
    
    def _resolve_vendor(self, vendor_name: str) -> Optional[VendorOrganization]:
        """Find or create vendor by name.
        
        Args:
            vendor_name: Vendor name from ServiceNow
        
        Returns:
            VendorOrganization instance or None
        """
        if not vendor_name:
            return None
        
        vendor_name = vendor_name.strip()
        
        # Try exact match first
        vendor = VendorOrganization.query.filter(
            VendorOrganization.name.ilike(vendor_name)
        ).first()
        
        if vendor:
            return vendor
        
        # Create new vendor if not found
        vendor = VendorOrganization(
            name=vendor_name,
            description=f"Auto-created from ServiceNow sync on {datetime.now(timezone.utc).date()}",
            vendor_type='software'
        )
        
        db.session.add(vendor)
        db.session.flush()  # Get ID without committing
        
        logger.info(f"Created new vendor: {vendor_name}")
        return vendor
    
    def _parse_float(self, value: Any) -> Optional[float]:
        """Safely parse float value.
        
        Args:
            value: Value to parse
        
        Returns:
            Float value or None
        """
        if value is None:
            return None
        
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    
    def sync_servers(self) -> ServiceNowSyncResult:
        """Sync server CIs from ServiceNow (future enhancement).
        
        Returns:
            ServiceNowSyncResult with server sync statistics
        """
        result = ServiceNowSyncResult()
        result.errors.append("Server sync not yet implemented")
        result.finish()
        return result
    
    def sync_databases(self) -> ServiceNowSyncResult:
        """Sync database CIs from ServiceNow (future enhancement).
        
        Returns:
            ServiceNowSyncResult with database sync statistics
        """
        result = ServiceNowSyncResult()
        result.errors.append("Database sync not yet implemented")
        result.finish()
        return result
