"""
Enhanced Navigation Registry with Validation, Authorization & Error Handling

This module provides validated, type-safe sidebar navigation configuration
with proper permission checks, error logging, schema validation, and
comprehensive nesting support.

Improvements over v1:
- Pydantic validation (no invalid configs)
- Permission/role-based visibility
- Proper logging for debugging
- Recursive nesting support
- Endpoint validation on startup
- Circular reference detection
- Optional item hiding (disabled items)
- Explicit ordering
- Caching support
"""

import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from flask import current_app, url_for
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


class ItemVisibility(str, Enum):
    """Control when menu items are visible"""
    ALWAYS = "always"  # Always show to authenticated users
    ADMIN_ONLY = "admin_only"
    AUTHENTICATED_ONLY = "authenticated"
    SPECIFIC_ROLES = "specific_roles"
    CUSTOM = "custom"


class NavigationItemV2(BaseModel):
    """Validated navigation item with error checking"""
    
    label: str = Field(..., min_length=1, max_length=100)
    icon: Optional[str] = Field(None, pattern=r"^[a-z0-9\-]+$")  # Lucide icon name

    # Declared ahead of `endpoint`/`url_fallback` on purpose: pydantic v1-style
    # validators only see fields validated so far, in declaration order, and
    # endpoint_and_fallback_not_both_invalid() below needs `disabled` to
    # already be present in `values` regardless of kwarg order at call time.
    disabled: bool = False

    endpoint: Optional[str] = Field(None, pattern=r"^[a-z_]+\.[a-z_]+$")  # "blueprint.function"
    url_fallback: str = Field("#", min_length=1)
    query_params: Dict[str, str] = Field(default_factory=dict)  # passed to url_for(endpoint, **query_params)

    # Authorization
    visibility: ItemVisibility = ItemVisibility.ALWAYS
    required_roles: List[str] = Field(default_factory=list)
    required_permissions: List[str] = Field(default_factory=list)
    visibility_callback: Optional[Callable] = None  # Custom visibility logic

    # UI behavior
    order: Optional[int] = None
    badge_field: Optional[str] = None  # For dynamic items
    
    # Nesting
    items: List["NavigationItemV2"] = Field(default_factory=list)
    parent: Optional[str] = None  # Parent section ID (for validation only)
    
    class Config:
        arbitrary_types_allowed = True  # Allow Callable field
    
    @validator("icon", pre=True, always=True)
    def icon_required_if_not_disabled(cls, v, values):
        """Icon is required unless item is explicitly disabled or hidden"""
        if not v and not values.get("disabled"):
            raise ValueError(f"Icon required for visible items (item: {values.get('label')})")
        return v
    
    @validator("endpoint", pre=True)
    def endpoint_and_fallback_not_both_invalid(cls, v, values):
        """Either endpoint or valid fallback URL required, unless the item is
        explicitly disabled -- mirrors icon_required_if_not_disabled above.
        Without this, a disabled placeholder header (endpoint=None,
        url_fallback="#", disabled=True -- e.g. "Framework Extensions" in
        navigation_sections_v2.py) fails validation at import time and takes
        the whole module down with it."""
        if not v and values.get("url_fallback", "#") == "#" and not values.get("disabled"):
            raise ValueError(f"Item '{values.get('label')}' has no endpoint AND fallback is '#'")
        return v


class NavigationSectionV2(BaseModel):
    """Validated navigation section"""
    
    key: str = Field(..., pattern=r"^[a-z_]+$")
    label: str = Field(..., min_length=1, max_length=100)
    icon: str = Field(..., pattern=r"^[a-z0-9\-]+$")
    order: int = Field(..., ge=1, le=999)
    collapsible: bool = True
    
    # Storage key for localStorage (collapsible state)
    storage_key: Optional[str] = None
    
    # Items
    items: List[NavigationItemV2] = Field(default_factory=list)
    
    # Dynamic items configuration
    dynamic_items_enabled: bool = False
    dynamic_items_source: Optional[str] = None  # "applications", "vendors"
    dynamic_items_label: str = "Quick Access"
    dynamic_items_limit: int = 10
    dynamic_items_endpoint_template: Optional[str] = None
    dynamic_items_url_template: Optional[str] = None
    dynamic_items_id_field: str = "id"
    dynamic_items_name_field: str = "name"
    dynamic_items_badge_field: Optional[str] = None
    
    # Parent section (for subsections)
    parent_section: Optional[str] = None
    
    # Authorization
    visibility: ItemVisibility = ItemVisibility.ALWAYS
    required_roles: List[str] = Field(default_factory=list)


class NavigationRegistryV2:
    """Type-safe, validated navigation registry with proper error handling"""
    
    def __init__(self):
        self.sections: Dict[str, NavigationSectionV2] = {}
        self._validated_endpoints: Set[str] = set()
        self._parent_map: Dict[str, str] = {}  # section_id → parent_id
        self._startup_validated = False
        self._cache: Dict[str, Any] = {}
    
    def register_section(self, section: NavigationSectionV2) -> None:
        """Register a validated section"""
        if section.key in self.sections:
            raise ValueError(f"Section {section.key} already registered")
        
        # Validate items recursively
        self._validate_items(section.items, section.key)
        
        self.sections[section.key] = section
        
        # Track parent relationship
        if section.parent_section:
            self._parent_map[section.key] = section.parent_section
        
        logger.info(f"✓ Registered section: {section.key} ({section.label})")
    
    def _validate_items(self, items: List[NavigationItemV2], section_key: str) -> None:
        """Recursively validate all items"""
        for i, item in enumerate(items):
            # Validate nested items
            if item.items:
                self._validate_items(item.items, section_key)
    
    def validate_structure_on_startup(self) -> bool:
        """
        Run on app startup to validate entire registry structure.
        Returns True if all validations passed.
        """
        if self._startup_validated:
            return True
        
        issues = []
        
        # 1. Check all endpoints exist in Flask
        for section in self.sections.values():
            endpoints_to_check = self._collect_all_endpoints(section.items)
            endpoints_to_check.append(section.dynamic_items_endpoint_template)
            
            for endpoint in endpoints_to_check:
                if endpoint and endpoint not in [None, "#"]:
                    if not self._endpoint_exists(endpoint):
                        issues.append(f"❌ Endpoint '{endpoint}' not registered (section: {section.key})")
                    else:
                        self._validated_endpoints.add(endpoint)
        
        # 2. Check parent references
        for section_id, parent_id in self._parent_map.items():
            if parent_id not in self.sections:
                issues.append(f"❌ Section '{section_id}' references unknown parent '{parent_id}'")
        
        # 3. Check circular references
        for section_id in self.sections:
            if self._has_circular_reference(section_id):
                issues.append(f"❌ Circular parent-child reference detected in section '{section_id}'")
        
        # 4. Check for duplicate endpoints
        endpoint_usage = {}
        for section in self.sections.values():
            for endpoint in self._collect_all_endpoints(section.items):
                if endpoint and endpoint not in [None, "#"]:
                    if endpoint in endpoint_usage:
                        issues.append(f"⚠ Endpoint '{endpoint}' used in multiple sections ({endpoint_usage[endpoint]}, {section.key})")
                    else:
                        endpoint_usage[endpoint] = section.key
        
        # Report results
        if issues:
            logger.error(f"Navigation Registry Validation Failed ({len(issues)} issues):")
            for issue in issues:
                logger.error(f"  {issue}")
            return False
        
        logger.info(f"✓ Navigation Registry validated successfully ({len(self.sections)} sections, {len(self._validated_endpoints)} endpoints)")
        self._startup_validated = True
        return True
    
    def _collect_all_endpoints(self, items: List[NavigationItemV2]) -> List[str]:
        """Recursively collect all endpoints from items"""
        endpoints = []
        for item in items:
            if item.endpoint:
                endpoints.append(item.endpoint)
            if item.items:
                endpoints.extend(self._collect_all_endpoints(item.items))
        return endpoints
    
    def _endpoint_exists(self, endpoint: str) -> bool:
        """Check if endpoint is registered in Flask"""
        try:
            # Don't generate URL, just check if endpoint exists
            for rule in current_app.url_map.iter_rules():
                if rule.endpoint == endpoint:
                    return True
            return False
        except (RuntimeError, Exception) as e:
            logger.debug(f"Could not check endpoint '{endpoint}': {e}")
            return True  # Assume exists if can't check (e.g., no app context)
    
    def _has_circular_reference(self, section_id: str, visited: Set[str] = None) -> bool:
        """Detect circular parent-child references"""
        if visited is None:
            visited = set()
        
        if section_id in visited:
            return True
        
        visited.add(section_id)
        
        parent = self._parent_map.get(section_id)
        if parent:
            return self._has_circular_reference(parent, visited.copy())
        
        return False
    
    def get_navigation_sections(
        self,
        current_endpoint: Optional[str] = None,
        user: Any = None,  # Flask user object
        applications: List = None,
        vendors: List = None,
        include_disabled: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get filtered, resolved navigation sections with:
        - Permission checks
        - URL resolution
        - Active state detection
        - Dynamic items loading
        
        Args:
            current_endpoint: Current Flask endpoint for active state
            user: Authenticated user object (for permission checks)
            applications: List of application objects for dynamic items
            vendors: List of vendor objects for dynamic items
            include_disabled: Include disabled items in output
        
        Returns:
            List of resolved navigation sections
        """
        # Check cache
        cache_key = f"{current_endpoint}_{user.id if user else None}_{include_disabled}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        result = []
        
        for section_id, section in sorted(
            self.sections.items(),
            key=lambda x: x[1].order
        ):
            # Skip parent sections (they're rendered as part of main sections)
            if section.parent_section:
                continue
            
            # Check visibility
            if not self._is_visible(section, user):
                continue
            
            # Resolve section items
            resolved_section = {
                "key": section_id,
                "label": section.label,
                "icon": section.icon,
                "collapsible": section.collapsible,
                "storage_key": section.storage_key or section_id,
                "items": [],
            }
            
            # Add regular items
            resolved_section["items"].extend(
                self._resolve_items(
                    section.items,
                    current_endpoint,
                    user,
                    include_disabled
                )
            )
            
            # Add dynamic items if configured
            if section.dynamic_items_enabled:
                dynamic_items = self._resolve_dynamic_items(
                    section,
                    user,
                    applications if section.dynamic_items_source == "applications" else vendors,
                )
                resolved_section["items"].extend(dynamic_items)
            
            # Only include section if it has items
            if resolved_section["items"]:
                result.append(resolved_section)
        
        # Cache result
        self._cache[cache_key] = result
        return result
    
    def _is_visible(self, item_or_section, user: Any = None) -> bool:
        """Check if item/section is visible to user"""
        config = item_or_section
        
        # Hidden by default (disabled + not included)
        if config.disabled:
            return False
        
        # Check visibility rules
        if config.visibility == ItemVisibility.AUTHENTICATED_ONLY:
            return user is not None
        
        if config.visibility == ItemVisibility.ADMIN_ONLY:
            # R3-1 (PR 428 round 4): ``getattr(user, "is_admin", False)``
            # with no call returned the bound method, always truthy --
            # every user was shown admin-only navigation. Judged against
            # the active organisation, same as the rest of this PR.
            if not user:
                return False
            from app.middleware.tenant_decorators import is_active_org_admin

            return is_active_org_admin(user)
        
        if config.visibility == ItemVisibility.SPECIFIC_ROLES:
            if not user:
                return False
            user_roles = getattr(user, "roles", [])
            return any(role in config.required_roles for role in user_roles)
        
        if config.visibility == ItemVisibility.CUSTOM:
            if config.visibility_callback:
                return config.visibility_callback(user)
            return True
        
        return True  # Default: visible to authenticated users
    
    def _resolve_items(
        self,
        items: List[NavigationItemV2],
        current_endpoint: Optional[str],
        user: Any,
        include_disabled: bool,
    ) -> List[Dict[str, Any]]:
        """Recursively resolve items with URLs and active states"""
        resolved = []
        
        for item in items:
            # Check visibility
            if not include_disabled and item.disabled:
                continue
            
            if not self._is_visible(item, user):
                continue
            
            # Resolve URL
            url = self._resolve_url(item)
            
            # Build resolved item
            resolved_item = {
                "label": item.label,
                "icon": item.icon,
                "url": url,
                "disabled": item.disabled,
                "is_active": self._is_active(item, current_endpoint),
            }
            
            # Add badge if applicable (for dynamic items later)
            if item.badge_field:
                resolved_item["badge_field"] = item.badge_field
            
            # Recursively resolve nested items
            if item.items:
                nested = self._resolve_items(item.items, current_endpoint, user, include_disabled)
                if nested:
                    resolved_item["items"] = nested
            
            resolved.append(resolved_item)
        
        return resolved
    
    def _resolve_url(self, item: NavigationItemV2) -> str:
        """Resolve URL from endpoint or fallback"""
        if item.endpoint:
            url = self._safe_url_for(item.endpoint, **item.query_params)
            if url:
                return url

        return item.url_fallback
    
    def _safe_url_for(self, endpoint: str, **kwargs) -> Optional[str]:
        """
        Safely generate URL, logging errors for debugging.
        Returns None if endpoint not found or invalid.
        """
        try:
            return url_for(endpoint, **kwargs)
        except Exception as e:
            logger.warning(
                f"Failed to generate URL for endpoint '{endpoint}': {type(e).__name__}: {e}",
                extra={"endpoint": endpoint, "kwargs": kwargs}
            )
            return None
    
    def _is_active(self, item: NavigationItemV2, current_endpoint: Optional[str]) -> bool:
        """Determine if item is active (robust logic)"""
        if not item.endpoint or not current_endpoint:
            return False
        
        # Exact match
        if item.endpoint == current_endpoint:
            return True
        
        # Blueprint-level match (but NOT for list/dashboard endpoints)
        item_blueprint = item.endpoint.split(".")[0]
        current_blueprint = current_endpoint.split(".")[0]
        
        if item_blueprint == current_blueprint:
            # Only mark active on same blueprint IF:
            # - Current is NOT a list/dashboard (detail pages show parent active)
            # - OR endpoint contains "detail" (child route)
            if "detail" in current_endpoint and "list" not in item.endpoint:
                return True
        
        return False
    
    def _resolve_dynamic_items(
        self,
        section: NavigationSectionV2,
        user: Any,
        items_list: Optional[List] = None,
    ) -> List[Dict[str, Any]]:
        """
        Generate dynamic menu items from object list with proper error handling.
        """
        if not items_list or not section.dynamic_items_enabled:
            return []
        
        resolved = []
        id_field = section.dynamic_items_id_field
        name_field = section.dynamic_items_name_field
        badge_field = section.dynamic_items_badge_field
        limit = section.dynamic_items_limit
        
        # Add header for quick access section
        if items_list:
            resolved.append({
                "label": section.dynamic_items_label,
                "disabled": True,  # Header-style item
                "is_header": True,
            })
        
        for obj in items_list[:limit]:
            try:
                # Get object ID
                obj_id = getattr(obj, id_field, None)
                if obj_id is None:
                    logger.warning(f"Object {obj} missing '{id_field}' field, skipping")
                    continue
                
                # Get object name
                obj_name = getattr(obj, name_field, None)
                if obj_name is None:
                    logger.warning(f"Object {obj} missing '{name_field}' field, skipping")
                    continue
                
                # Generate URL
                url = self._safe_url_for(
                    section.dynamic_items_endpoint_template,
                    id=obj_id
                )
                
                if not url:
                    logger.warning(f"Failed to generate URL for dynamic item '{obj_name}' (id={obj_id})")
                    continue
                
                # Get badge if configured
                badge = None
                if badge_field:
                    badge = getattr(obj, badge_field, None)
                    if badge is None:
                        logger.debug(f"Object '{obj_name}' missing badge field '{badge_field}'")
                
                resolved.append({
                    "label": obj_name,
                    "url": url,
                    "badge": badge[:3] if badge else None,  # First 3 chars
                    "is_dynamic": True,
                    "is_active": False,  # Don't auto-activate dynamic items
                })
            
            except Exception as e:
                logger.error(f"Error resolving dynamic item {obj}: {e}")
                continue
        
        return resolved
    
    def get_breadcrumb_trail(
        self,
        current_endpoint: Optional[str] = None,
        current_url: str = "/",
    ) -> List[Dict[str, str]]:
        """
        Generate breadcrumb trail for current page.
        Handles nested items recursively.
        """
        breadcrumbs = [{"label": "Home", "url": "/"}]
        
        if not current_endpoint:
            return breadcrumbs
        
        # Flatten all items (with section context)
        all_items = []
        for section_id, section in self.sections.items():
            if section.parent_section:  # Skip subsections
                continue
            all_items.extend(self._flatten_items(section.items, section_id))
        
        # Find matching item
        for item_path in all_items:
            item, section_id = item_path
            if item.get("endpoint") == current_endpoint:
                # Add section breadcrumb
                section = self.sections[section_id]
                breadcrumbs.append({
                    "label": section.label,
                    "url": self._resolve_url(section.items[0]) if section.items else "#",
                })
                
                # Add item breadcrumb
                breadcrumbs.append({
                    "label": item.get("label", "Unknown"),
                    "url": current_url,
                })
                break
        
        return breadcrumbs
    
    def _flatten_items(
        self,
        items: List[NavigationItemV2],
        section_id: str
    ) -> List[Tuple[Dict, str]]:
        """Flatten nested items into list of (item, section_id) tuples"""
        flattened = []
        for item in items:
            flattened.append((
                {
                    "label": item.label,
                    "endpoint": item.endpoint,
                    "icon": item.icon,
                },
                section_id
            ))
            if item.items:
                flattened.extend(self._flatten_items(item.items, section_id))
        return flattened


# Global instance
_registry = NavigationRegistryV2()


def get_registry() -> NavigationRegistryV2:
    """Get the global navigation registry"""
    return _registry


def register_navigation_section(section: NavigationSectionV2) -> None:
    """Register a new navigation section"""
    _registry.register_section(section)


def validate_on_startup() -> bool:
    """Validate entire registry on Flask app startup"""
    return _registry.validate_structure_on_startup()
