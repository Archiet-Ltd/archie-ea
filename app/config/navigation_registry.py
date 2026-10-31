"""
Navigation Registry for Unified Sidebar

This module provides auto-discovery and centralized configuration for
sidebar navigation items. It maps unified blueprint routes to navigation
sections with proper grouping, icons, and ordering.

Usage:
    from app.config.navigation_registry import get_navigation_sections

    # In context processor or template
    nav_sections = get_navigation_sections()
"""

from typing import Any, Dict, List, Optional

from flask import url_for  # dead-code-ok

# Navigation Section Registry
# Each section defines a group of related navigation items
NAVIGATION_REGISTRY = {
    # =========================================================================
    # HOME SECTION
    # =========================================================================
    "home": {
        "label": "Home",
        "icon": "home",
        "order": 1,
        "collapsible": False,
        "items": [
            {
                "label": "Dashboard",
                "icon": "gauge",
                "endpoint": "admin.index",
                "url_fallback": "/admin/",
            },
            {
                "label": "Overview",
                "icon": "layout-grid",
                "endpoint": "main.index",
                "url_fallback": "/",
            },
        ],
    },
    # =========================================================================
    # APPLICATION PORTFOLIO SECTION (unified_applications)
    # =========================================================================
    "application_portfolio": {
        "label": "Application Portfolio",
        "icon": "package",
        "order": 2,
        "collapsible": True,
        "storage_key": "applications",
        "items": [
            {
                "label": "All Applications",
                "icon": "list",
                "endpoint": "unified_applications.application_list",
                "url_fallback": "/applications/",
            },
            {
                "label": "Create Application",
                "icon": "plus-circle",
                "endpoint": "unified_applications.application_create",
                "url_fallback": "/applications/create",
            },
            {
                "label": "Application Dashboard",
                "icon": "bar-chart - 3",
                "endpoint": "unified_applications.dashboard",
                "url_fallback": "/applications/dashboard",
            },
            {
                "label": "Duplicate Detection",
                "icon": "git-compare",
                "endpoint": "unified_duplicate.enterprise_dashboard",
                "url_fallback": "/duplicate-detection/",
            },
            {
                "label": "Consolidation List",
                "icon": "list-x",
                "endpoint": "consolidation_list.dashboard",
                "url_fallback": "/consolidation-list/",
            },
            {
                "label": "Application Consolidation",
                "icon": "merge",
                "endpoint": "unified_low_priority.consolidation_dashboard",
                "url_fallback": "/enterprise/consolidation",
            },
        ],
        "dynamic_items": {
            "enabled": True,
            "source": "applications",
            "label": "Quick Access",
            "limit": 10,
            "endpoint_template": "unified_applications.application_detail",
            "url_template": "/applications/{id}",
            "id_field": "id",
            "name_field": "name",
            "badge_field": "component_type",
        },
    },
    # =========================================================================
    # VENDOR MANAGEMENT SECTION
    # =========================================================================
    "vendor_management": {
        "label": "Vendor Management",
        "icon": "building - 2",
        "order": 3,
        "collapsible": True,
        "storage_key": "vendors",
        "items": [
            {
                "label": "Vendor Dashboard",
                "icon": "building",
                "endpoint": "vendors.vendors_dashboard",
                "url_fallback": "/applications/vendors/",
            },
            {
                "label": "Create Vendor",
                "icon": "plus-circle",
                "endpoint": "vendors.create_vendor",
                "url_fallback": "/applications/vendors/create",
            },
        ],
        "dynamic_items": {
            "enabled": True,
            "source": "vendors",
            "label": "Quick Access",
            "limit": 8,
            "endpoint_template": "vendors.vendor_detail",
            "url_template": "/applications/vendors/{id}",
            "id_field": "id",
            "name_field": "name",
            "badge_field": "vendor_type",
        },
    },
    # =========================================================================
    # ENTERPRISE ARCHITECTURE SECTION (unified_low_priority)
    # =========================================================================
    "enterprise_architecture": {
        "label": "Enterprise Architecture",
        "icon": "building - 2",
        "order": 4,
        "collapsible": True,
        "storage_key": "enterprise",
        "items": [
            {
                "label": "Enterprise Dashboard",
                "icon": "layout-dashboard",
                "endpoint": "unified_low_priority.enterprise_dashboard",
                "url_fallback": "/enterprise/",
            },
            {
                "label": "Architecture Models",
                "icon": "box",
                "endpoint": "unified_low_priority.architecture_dashboard",
                "url_fallback": "/enterprise/architecture",
            },
            {
                "label": "Capability Map",
                "icon": "map",
                "endpoint": "unified_low_priority.capability_map_dashboard",
                "url_fallback": "/enterprise/capability-map",
            },
            {
                "label": "Strategic Planning",
                "icon": "target",
                "endpoint": "unified_low_priority.strategic_dashboard",
                "url_fallback": "/enterprise/strategic",
            },
            {
                "label": "Policy Monitoring",
                "icon": "shield-check",
                "endpoint": "unified_low_priority.policy_monitoring_dashboard",
                "url_fallback": "/enterprise/policy-monitoring",
            },
        ],
    },
    # =========================================================================
    # ARCHITECTURE MODELS SUB-SECTION
    # =========================================================================
    "architecture_models": {
        "label": "Architecture Models",
        "icon": "box",
        "order": 5,
        "collapsible": True,
        "storage_key": "arch_models",
        "parent": "enterprise_architecture",
        "items": [
            {
                "label": "ArchiMate Elements",
                "icon": "table",
                "endpoint": "archimate_crud.dashboard",
                "url_fallback": "/architecture/dashboard",
            },
            {
                "label": "Solutions Architecture",
                "icon": "puzzle",
                "endpoint": "solution_design.list_solutions",
                "url_fallback": "/solutions/",
            },
        ],
    },
    # =========================================================================
    # AI & ANALYSIS SECTION
    # =========================================================================
    "ai_analysis": {
        "label": "AI & Analysis",
        "icon": "brain",
        "order": 6,
        "collapsible": True,
        "storage_key": "ai_analysis",
        "items": [
            {
                "label": "AI Assistant",
                "icon": "message-square",
                "endpoint": "unified_ai_chat.index",
                "url_fallback": "/ai-chat/",
            },
            {
                "label": "Architecture Assistant",
                "icon": "sparkles",
                "endpoint": "architect_ui.architecture_assistant",
                "url_fallback": "/architecture-assistant/",
            },
            {
                "label": "Market Intelligence",
                "icon": "trending-up",
                "endpoint": "architect_ui.market_intelligence",
                "url_fallback": "/market-intelligence/",
            },
            {
                # Repointed to the live strategic impact-analysis dashboard (BA
                # persona work) — the old "#" placeholder route was never built.
                "label": "Impact Analysis",
                "icon": "git-compare",
                "endpoint": "strategic.impact_analysis",
                "url_fallback": "/strategic/impact-analysis",
                "disabled": False,
            },
        ],
    },
    # =========================================================================
    # ARCHITECTURE TOOLS SECTION
    # =========================================================================
    "architecture_tools": {
        "label": "Architecture Tools",
        "icon": "layout",
        "order": 5,
        "collapsible": True,
        "storage_key": "arch_tools",
        "items": [
            {
                "label": "Solution Composer",
                "icon": "puzzle",
                "endpoint": "solution_design.list_solutions",
                "url_fallback": "/solutions/",
            },
            {
                "label": "Roadmap Builder",
                "icon": "route",
                "endpoint": "genome_roadmap.index",
                "url_fallback": "/genome/roadmap/",
            },
        ],
    },
    # =========================================================================
    # FRAMEWORK MANAGEMENT SECTION
    # =========================================================================
    "framework_management": {
        "label": "Framework Management",
        "icon": "layers",
        "order": 7,
        "collapsible": True,
        "storage_key": "framework_mgmt",
        "items": [
            {
                "label": "Framework Overview",
                "icon": "layers",
                "endpoint": "main.framework_management.dashboard",
                "url_fallback": "/framework-management/",
            },
            {
                "label": "Manufacturing Excellence",
                "icon": "settings",
                "collapsible": True,
                "storage_key": "manufacturing",
                "items": [
                    {
                        "label": "Dashboard",
                        "icon": "bar-chart",
                        "endpoint": "main.framework_management.framework_dashboard",
                        "url_fallback": "/framework-management/manufacturing/dashboard",
                    },
                    {
                        "label": "Data Table",
                        "icon": "table",
                        "endpoint": "main.framework_management.framework_table",
                        "url_fallback": "/framework-management/manufacturing/table",
                    },
                ],
            },
            {
                "label": "Framework Extensions",
                "icon": "puzzle",
                "collapsible": True,
                "storage_key": "extensions",
                "items": [
                    {
                        "label": "Manufacturing Basic",
                        "icon": "package",
                        "endpoint": "main.framework_management.extension_dashboard",
                        "url_fallback": "/framework-management/extensions/manufacturing-basic",
                    },
                    {
                        "label": "Manufacturing Advanced",
                        "icon": "package",
                        "endpoint": "main.framework_management.extension_dashboard",
                        "url_fallback": "/framework-management/extensions/manufacturing-advanced",
                    },
                    {
                        "label": "Digital Transformation",
                        "icon": "zap",
                        "endpoint": "main.framework_management.extension_dashboard",
                        "url_fallback": "/framework-management/extensions/digital-transformation",
                    },
                ],
            },
            {
                "label": "Framework Templates",
                "icon": "file-text",
                "collapsible": True,
                "storage_key": "templates",
                "items": [
                    {
                        "label": "Small Enterprise",
                        "icon": "briefcase",
                        "endpoint": "main.framework_management.template_dashboard",
                        "url_fallback": "/framework-management/templates/small-enterprise",
                    },
                    {
                        "label": "Large Enterprise",
                        "icon": "building",
                        "endpoint": "main.framework_management.template_dashboard",
                        "url_fallback": "/framework-management/templates/large-enterprise",
                    },
                    {
                        "label": "Generic Business",
                        "icon": "briefcase",
                        "endpoint": "main.framework_management.template_dashboard",
                        "url_fallback": "/framework-management/templates/generic-business",
                    },
                ],
            },
            {
                "label": "Capability Maturity",
                "icon": "trending-up",
                "endpoint": "maturity_management.frameworks_overview",
                "url_fallback": "/capability-maturity/frameworks",
            },
            {
                "label": "Framework Config",
                "icon": "settings",
                "endpoint": "framework_config_ui.framework_config_dashboard",
                "url_fallback": "/framework-config/",
            },
        ],
    },
    # =========================================================================
    # TOOLS & UTILITIES SECTION
    # =========================================================================
    "tools_utilities": {
        "label": "Tools & Utilities",
        "icon": "wrench",
        "order": 8,
        "collapsible": True,
        "storage_key": "tools",
        "items": [
            {
                "label": "Model Registry",
                "icon": "layout-grid",
                "endpoint": "dynamic_dashboards.model_registry_index",
                "url_fallback": "/auto-dashboard/",
            },
            {
                "label": "Users",
                "icon": "users",
                "endpoint": "admin.registered_users",
                "url_fallback": "/admin/users",
            },
            {
                "label": "API Settings",
                "icon": "key",
                "endpoint": "admin.api_settings",
                "url_fallback": "/admin/api-settings",
            },
        ],
    },
    # =========================================================================
    # OTHER SECTION
    # =========================================================================
    "other": {
        "label": "Other",
        "icon": "more-horizontal",
        "order": 99,
        "collapsible": False,
        "items": [
            {
                "label": "Documentation",
                "icon": "help-circle",
                "endpoint": None,
                "url_fallback": "#",
                "disabled": True,
            },
        ],
    },
}


def safe_url_for(endpoint: str, **kwargs) -> Optional[str]:
    """
    Safely generate URL for an endpoint, returning None if endpoint doesn't exist.
    """
    try:
        return url_for(endpoint, **kwargs)
    except Exception:
        return None


def get_item_url(item: Dict[str, Any], **kwargs) -> str:
    """
    Get URL for a navigation item, using endpoint if available, fallback otherwise.
    """
    if item.get("endpoint"):
        url = safe_url_for(item["endpoint"], **kwargs)
        if url:
            return url
    return item.get("url_fallback", "#")


def is_item_active(
    item: Dict[str, Any], current_endpoint: str, view_args: Dict = None
) -> bool:
    """
    Check if a navigation item is active based on current endpoint.
    """
    if not item.get("endpoint"):
        return False

    endpoint = item["endpoint"]

    # Exact match
    if current_endpoint == endpoint:
        return True

    # Prefix match for sub-routes (e.g., unified_applications.application_detail)
    if current_endpoint and endpoint:
        # Get blueprint prefix
        item_blueprint = endpoint.split(".")[0] if "." in endpoint else endpoint
        current_blueprint = (
            current_endpoint.split(".")[0]
            if "." in current_endpoint
            else current_endpoint
        )

        # Check if same blueprint and current is a child route
        if item_blueprint == current_blueprint:
            # For list/dashboard routes, only active on exact match
            if "list" in endpoint or "dashboard" in endpoint or "index" in endpoint:
                return current_endpoint == endpoint
            # For detail routes, check if we're on a related page
            return True

    return False


def get_navigation_sections(
    current_endpoint: str = None,
    view_args: Dict = None,
    applications: List = None,
    vendors: List = None,
) -> List[Dict[str, Any]]:
    """
    Get all navigation sections with resolved URLs and active states.

    Args:
        current_endpoint: The current request endpoint for active state detection
        view_args: The current request view args
        applications: List of applications for dynamic items
        vendors: List of vendors for dynamic items

    Returns:
        List of navigation sections with resolved items
    """
    sections = []

    for section_id, section_config in sorted(
        NAVIGATION_REGISTRY.items(), key=lambda x: x[1].get("order", 99)
    ):
        # Skip child sections (they're rendered within parent)
        if section_config.get("parent"):
            continue

        section = {
            "id": section_id,
            "label": section_config["label"],
            "icon": section_config["icon"],
            "collapsible": section_config.get("collapsible", True),
            "storage_key": section_config.get("storage_key", section_id),
            "items": [],
            "has_active": False,
        }

        # Process static items
        for item in section_config.get("items", []):
            resolved_item = {
                "label": item["label"],
                "icon": item["icon"],
                "url": get_item_url(item),
                "endpoint": item.get("endpoint"),
                "disabled": item.get("disabled", False),
                "is_active": is_item_active(item, current_endpoint, view_args),
            }

            if resolved_item["is_active"]:
                section["has_active"] = True

            section["items"].append(resolved_item)

        # Process dynamic items (Quick Access lists)
        dynamic_config = section_config.get("dynamic_items", {})
        if dynamic_config.get("enabled"):
            source = dynamic_config.get("source")
            items_list = None

            if source == "applications" and applications:
                items_list = applications
            elif source == "vendors" and vendors:
                items_list = vendors

            if items_list:
                limit = dynamic_config.get("limit", 10)
                section["dynamic_items"] = {
                    "label": dynamic_config.get("label", "Quick Access"),
                    "items": [],
                }

                for obj in items_list[:limit]:
                    obj_id = getattr(obj, dynamic_config.get("id_field", "id"), None)
                    obj_name = getattr(
                        obj, dynamic_config.get("name_field", "name"), "Unknown"
                    )
                    badge_field = dynamic_config.get("badge_field")
                    badge = getattr(obj, badge_field, None) if badge_field else None

                    # Try to generate URL
                    endpoint = dynamic_config.get("endpoint_template")
                    if endpoint and obj_id:
                        url = safe_url_for(endpoint, id=obj_id)
                    else:
                        url_template = dynamic_config.get("url_template", "#")
                        url = url_template.format(id=obj_id) if obj_id else "#"

                    # Check if active
                    is_active = False
                    if current_endpoint == endpoint and view_args:
                        is_active = str(view_args.get("id")) == str(obj_id)

                    section["dynamic_items"]["items"].append(
                        {
                            "id": obj_id,
                            "label": obj_name,
                            "url": url or "#",
                            "badge": badge[:3] if badge else None,
                            "is_active": is_active,
                        }
                    )

        sections.append(section)

    return sections


def get_breadcrumb_trail(
    current_endpoint: str, view_args: Dict = None
) -> List[Dict[str, str]]:
    """
    Generate breadcrumb trail based on current endpoint.

    Returns:
        List of breadcrumb items with label and url
    """
    breadcrumbs = [{"label": "Home", "url": "/"}]

    if not current_endpoint:
        return breadcrumbs

    # Find the section containing this endpoint
    for section_id, section_config in NAVIGATION_REGISTRY.items():
        for item in section_config.get("items", []):
            if item.get("endpoint") == current_endpoint:
                # Add section as breadcrumb
                breadcrumbs.append(
                    {
                        "label": section_config["label"],
                        "url": get_item_url(section_config["items"][0])
                        if section_config["items"]
                        else "#",
                    }
                )
                # Add current item
                breadcrumbs.append({"label": item["label"], "url": get_item_url(item)})
                return breadcrumbs

    return breadcrumbs


# Route mapping for backward compatibility
# Maps old blueprint.function names to new unified ones
ROUTE_MIGRATION_MAP = {
    # Application Management
    "application_mgmt.application_list": "unified_applications.application_list",
    "application_mgmt.application_create": "unified_applications.application_create",
    "application_mgmt.application_detail": "unified_applications.application_detail",
    "application_mgmt.dashboard": "unified_applications.dashboard",
    "applications.list": "unified_applications.application_list",
    "applications.create": "unified_applications.application_create",
    # Duplicate Detection
    "simple_duplicate.index": "unified_duplicate.enterprise_dashboard",
    "simple_duplicate.dashboard": "unified_duplicate.simple_dashboard",
    "duplicate_detection.dashboard": "unified_duplicate.enterprise_dashboard",
    # Enterprise/Low Priority
    "architecture.dashboard": "unified_low_priority.architecture_dashboard",
    "capability_map.index": "unified_low_priority.capability_map_dashboard",
    "strategic.dashboard": "unified_low_priority.strategic_dashboard",
    "consolidation.dashboard": "unified_low_priority.consolidation_dashboard",
    "policy_monitoring.dashboard": "unified_low_priority.policy_monitoring_dashboard",
    # Framework Management (Legacy)
    "main.capability_framework.dashboard": "main.framework_management.dashboard",
}


def migrate_endpoint(old_endpoint: str) -> str:
    """
    Migrate old endpoint name to new unified endpoint.

    Args:
        old_endpoint: Old blueprint.function name

    Returns:
        New unified endpoint name, or original if no migration exists
    """
    return ROUTE_MIGRATION_MAP.get(old_endpoint, old_endpoint)
