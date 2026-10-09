"""
Route Discovery Service for Dynamic Sidebar Navigation

This service automatically discovers Flask routes and organizes them into
a hierarchical navigation structure for the admin sidebar.

Features:
- Automatic route discovery from Flask app
- Intelligent grouping by URL prefix and blueprint
- Metadata-driven icons and titles
- Caching for performance
- Permission-aware filtering

Author: LLM Assistant
Created: 2026 - 01 - 15
Category: services/navigation
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from flask import url_for


@dataclass
class RouteInfo:
    """Information about a discovered route"""

    endpoint: str
    url: str
    methods: List[str]
    blueprint: Optional[str]
    rule: str
    title: str
    icon: str
    category: str
    order: int = 999
    requires_auth: bool = True
    is_admin: bool = False


class RouteDiscoveryService:
    """Service for discovering and organizing Flask routes"""

    # Default icon mappings for common patterns
    DEFAULT_ICONS = {
        "dashboard": "layout-dashboard",
        "admin": "shield",
        "user": "users",
        "application": "package",
        "vendor": "building - 2",
        "capability": "layers",
        "architecture": "box",
        "implementation": "rocket",
        "strategy": "target",
        "governance": "scale",
        "analysis": "search",
        "report": "file-text",
        "setting": "settings",
        "api": "key",
        "home": "home",
        "overview": "layout-grid",
        "list": "list",
        "create": "plus-circle",
        "edit": "edit",
        "delete": "trash - 2",
        "detail": "eye",
        "chart": "bar-chart - 3",
        "graph": "trending-up",
        "import": "download",
        "export": "upload",
        "merge": "git-compare",
        "duplicate": "git-branch",
        "gap": "search",
        "health": "heart-pulse",
        "risk": "shield-alert",
        "roadmap": "route",
        "technology": "cpu",
        "data": "database",
        "solutions": "puzzle",
        "software": "code",
        "business": "building - 2",
        "workflow": "workflow",
        "process": "git-branch",
        "optimization": "zap",
        "impact": "target",
        "discovery": "compass",
        "ai": "brain",
        "chat": "message-circle",
        "tools": "wrench",
        "utilities": "tool",
        "template": "file-stack",
        "document": "file-text",
        "help": "help-circle",
        "about": "info",
        "login": "log-in",
        "logout": "log-out",
        "register": "user-plus",
        "profile": "user",
        "account": "user-circle",
    }

    # Category mappings for URL prefixes
    CATEGORY_MAPPING = {
        "/admin": "Administration",
        "/dashboard": "Dashboards",
        "/account": "Account",
        "/vendors": "Vendor Management",
        "/applications": "Application Management",
        "/capability-map": "Capability Framework",
        "/capability-management": "Capability Management",
        "/capability-governance": "Governance",
        "/advanced-governance": "Advanced Governance",
        "/implementation": "Implementation Planning",
        "/strategic": "Strategic Planning",
        "/architecture": "Architecture",
        "/api": "API",
        "/ai-chat": "AI Tools",
        "/tools": "Tools",
        "/reports": "Reports",
        "/settings": "Settings",
    }

    # Blueprint to category mapping
    BLUEPRINT_CATEGORIES = {
        "admin": "Administration",
        "dashboard": "Dashboards",
        "account": "Account",
        "vendors": "Vendor Management",
        "application_mgmt": "Application Management",
        "unified_applications": "Application Management",
        "capability_map": "Capability Framework",
        "capability_management": "Capability Management",
        "capability_governance": "Governance",
        "advanced_governance": "Advanced Governance",
        "implementation_planning": "Implementation Planning",
        "strategic": "Strategic Planning",
        "architecture": "Architecture",
        "enterprise": "Enterprise Architecture",
        "tools": "Tools",
        "unified_tools": "Tools",
        "unified_ai_chat": "AI Tools",
        "unified_duplicate": "Application Management",
        "consolidation_list": "Application Management",
        "policy_monitoring": "Governance",
        "business_capability": "Capability Framework",
        "business_capability_management": "Capability Management",
        "maturity_management": "Capability Management",
    }

    def __init__(self, app=None):
        self.app = app
        self._route_cache = {}
        self._navigation_cache = {}

    def init_app(self, app):
        """Initialize with Flask app"""
        self.app = app

    def discover_routes(self) -> List[RouteInfo]:
        """Discover all routes in the Flask application"""
        if not self.app:
            return []

        # Check cache first
        cache_key = "discovered_routes"
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]

        routes = []

        try:
            # Get all URL rules from the Flask app
            for rule in self.app.url_map.iter_rules():
                # Skip static and debug routes
                if self._should_skip_route(rule):
                    continue

                route_info = self._analyze_route(rule)
                if route_info:
                    routes.append(route_info)

            # Sort routes by category and order
            routes.sort(key=lambda r: (r.category, r.order, r.title))

            # Cache the results
            self._route_cache[cache_key] = routes

        except Exception as e:
            if self.app:
                self.app.logger.error(f"Error discovering routes: {e}")
            routes = []

        return routes

    def _should_skip_route(self, rule) -> bool:
        """Check if a route should be skipped"""
        # Skip static files
        if rule.endpoint == "static":
            return True

        # Skip debug toolbar
        if "debug" in rule.endpoint or "toolbar" in rule.endpoint:
            return True

        # Skip routes without GET methods (likely API endpoints)
        if not any(method in rule.methods for method in ["GET", "HEAD"]):
            return True

        # Skip internal/admin routes that shouldn't be in navigation
        skip_patterns = [
            r"^_",
            r"^csrf",
            r"^login_required",
            r"^logout",
            r"^register",
            r"^reset_password",
            r"^confirm_email",
            r"^change_password",
            r"^auth\.",
            r"^api\.",
            r"^webhook",
            r"^health",
            r"^ping",
        ]

        for pattern in skip_patterns:
            if re.match(pattern, rule.endpoint):
                return True

        return False

    def _analyze_route(self, rule) -> Optional[RouteInfo]:
        """Analyze a Flask rule and create RouteInfo"""
        try:
            # Get the URL for this route
            url = self._get_url_for_rule(rule)
            if not url:
                return None

            # Extract information
            endpoint = rule.endpoint
            methods = list(rule.methods or [])
            blueprint = self._get_blueprint_name(endpoint)
            rule_string = str(rule)

            # Generate metadata
            title = self._generate_title(endpoint, rule_string)
            icon = self._get_icon_for_route(endpoint, rule_string)
            category = self._get_category_for_route(endpoint, rule_string, blueprint)
            order = self._get_order_for_route(endpoint, category)
            requires_auth = self._requires_auth(endpoint)
            is_admin = self._is_admin_route(endpoint, category)

            return RouteInfo(
                endpoint=endpoint,
                url=url,
                methods=methods,
                blueprint=blueprint,
                rule=rule_string,
                title=title,
                icon=icon,
                category=category,
                order=order,
                requires_auth=requires_auth,
                is_admin=is_admin,
            )

        except Exception as e:
            if self.app:
                self.app.logger.warning(f"Error analyzing route {rule.endpoint}: {e}")
            return None

    def _get_url_for_rule(self, rule) -> Optional[str]:
        """Generate URL for a rule"""
        try:
            # For routes with no arguments, we can generate the URL directly
            if not rule.arguments or rule.rule == "/":
                return rule.rule

            # For routes with arguments, try to generate with dummy values
            kwargs = {}
            for arg in rule.arguments:
                if arg in ["id"]:
                    kwargs[arg] = "1"
                elif arg in ["page"]:
                    kwargs[arg] = "1"
                elif arg in ["slug"]:
                    kwargs[arg] = "example"
                else:
                    # Skip routes with complex arguments for now
                    return None

            return url_for(rule.endpoint, **kwargs)

        except Exception:
            # If URL generation fails, return the rule pattern
            return rule.rule

    def _get_blueprint_name(self, endpoint: str) -> Optional[str]:
        """Extract blueprint name from endpoint"""
        if "." in endpoint:
            return endpoint.split(".")[0]
        return None

    def _generate_title(self, endpoint: str, rule: str) -> str:
        """Generate a human-readable title for the route"""
        # Convert endpoint to title
        parts = endpoint.split(".")
        if len(parts) > 1:
            # Use the last part as the base
            title = parts[-1]
        else:
            title = parts[0]

        # Convert snake_case to Title Case
        title = re.sub(r"_", " ", title)
        title = title.title()

        # Handle common patterns
        title_mappings = {
            "Index": "Dashboard",
            "List": "All",
            "Create": "Create",
            "Edit": "Edit",
            "Delete": "Delete",
            "Detail": "Details",
            "View": "View",
            "Show": "View",
            "Update": "Update",
            "Add": "Add",
            "New": "New",
            "Manage": "Manage",
            "Admin": "Admin",
            "Settings": "Settings",
            "Profile": "Profile",
            "Account": "Account",
            "Login": "Login",
            "Logout": "Logout",
            "Register": "Register",
            "Home": "Home",
            "Main": "Dashboard",
            "Dashboard": "Dashboard",
            "Overview": "Overview",
            "Summary": "Summary",
            "Report": "Report",
            "Reports": "Reports",
            "Analytics": "Analytics",
            "Stats": "Statistics",
            "Metrics": "Metrics",
            "Health": "Health",
            "Status": "Status",
            "Help": "Help",
            "About": "About",
            "Contact": "Contact",
            "Support": "Support",
            "Docs": "Documentation",
            "Guide": "Guide",
            "Tutorial": "Tutorial",
            "Example": "Example",
            "Demo": "Demo",
            "Test": "Test",
            "Dev": "Development",
            "Prod": "Production",
            "Staging": "Staging",
            "Live": "Live",
            "Public": "Public",
            "Private": "Private",
            "Internal": "Internal",
            "External": "External",
            "System": "System",
            "Config": "Configuration",
            "Setup": "Setup",
            "Install": "Installation",
            "Deploy": "Deployment",
            "Build": "Build",
            "Release": "Release",
            "Version": "Version",
            "History": "History",
            "Log": "Logs",
            "Error": "Errors",
            "Exception": "Exceptions",
            "Warning": "Warnings",
            "Notice": "Notices",
            "Info": "Info",
            "Debug": "Debug",
            "Trace": "Trace",
        }

        # Apply mappings
        for key, value in title_mappings.items():
            if title == key:
                return value

        # If no mapping found, return the title as-is
        return title

    def _get_icon_for_route(self, endpoint: str, rule: str) -> str:
        """Get appropriate icon for a route"""
        # Convert to lowercase for matching
        endpoint_lower = endpoint.lower()
        rule_lower = rule.lower()

        # Check for exact matches first
        for pattern, icon in self.DEFAULT_ICONS.items():
            if pattern in endpoint_lower or pattern in rule_lower:
                return icon

        # Default icon
        return "circle"

    def _get_category_for_route(self, endpoint: str, rule: str, blueprint: Optional[str]) -> str:
        """Determine the category for a route"""
        # Check blueprint mapping first
        if blueprint and blueprint in self.BLUEPRINT_CATEGORIES:
            return self.BLUEPRINT_CATEGORIES[blueprint]

        # Check URL prefix mapping
        for prefix, category in self.CATEGORY_MAPPING.items():
            if rule.startswith(prefix):
                return category

        # Check endpoint patterns
        if "admin" in endpoint.lower():
            return "Administration"
        elif "dashboard" in endpoint.lower():
            return "Dashboards"
        elif "account" in endpoint.lower():
            return "Account"
        elif "vendor" in endpoint.lower():
            return "Vendor Management"
        elif "application" in endpoint.lower():
            return "Application Management"
        elif "capability" in endpoint.lower():
            return "Capability Framework"
        elif "architecture" in endpoint.lower():
            return "Architecture"
        elif "implementation" in endpoint.lower():
            return "Implementation Planning"
        elif "strategic" in endpoint.lower():
            return "Strategic Planning"
        elif "governance" in endpoint.lower():
            return "Governance"
        elif "ai" in endpoint.lower() or "chat" in endpoint.lower():
            return "AI Tools"
        elif "tool" in endpoint.lower():
            return "Tools"
        elif "api" in endpoint.lower():
            return "API"
        elif "report" in endpoint.lower():
            return "Reports"
        elif "setting" in endpoint.lower():
            return "Settings"

        # Default category
        return "Other"

    def _get_order_for_route(self, endpoint: str, category: str) -> int:
        """Get display order for a route"""
        # Common ordering patterns
        order_patterns = {
            # Dashboard/home items
            "index": 1,
            "dashboard": 1,
            "home": 1,
            "overview": 2,
            "summary": 3,
            # List/view items
            "list": 10,
            "all": 11,
            "view": 12,
            "show": 13,
            "detail": 14,
            # Create/edit items
            "create": 20,
            "add": 21,
            "new": 22,
            "edit": 23,
            "update": 24,
            "modify": 25,
            # Management items
            "manage": 30,
            "admin": 31,
            "settings": 32,
            "config": 33,
            "setup": 34,
            # Analysis items
            "analysis": 40,
            "analytics": 41,
            "report": 42,
            "reports": 43,
            "stats": 44,
            "metrics": 45,
            # Special items
            "health": 50,
            "status": 51,
            "monitor": 52,
            "log": 53,
            "audit": 54,
            # User items
            "profile": 60,
            "account": 61,
            "user": 62,
            "login": 70,
            "logout": 71,
            "register": 72,
        }

        endpoint_lower = endpoint.lower()

        # Check for patterns in endpoint
        for pattern, order in order_patterns.items():
            if pattern in endpoint_lower:
                return order

        # Category-specific defaults
        category_defaults = {
            "Dashboards": 1,
            "Administration": 10,
            "Application Management": 20,
            "Vendor Management": 30,
            "Capability Framework": 40,
            "Architecture": 50,
            "Implementation Planning": 60,
            "Strategic Planning": 70,
            "Governance": 80,
            "AI Tools": 90,
            "Tools": 100,
            "API": 110,
            "Reports": 120,
            "Settings": 130,
            "Account": 140,
            "Other": 999,
        }

        return category_defaults.get(category, 999)

    def _requires_auth(self, endpoint: str) -> bool:
        """Check if route requires authentication"""
        # Public routes that don't require auth
        public_patterns = [
            "login",
            "register",
            "home",
            "index",
            "about",
            "help",
            "contact",
            "public",
            "static",
        ]

        endpoint_lower = endpoint.lower()
        for pattern in public_patterns:
            if pattern in endpoint_lower:
                return False

        # Default to requiring auth
        return True

    def _is_admin_route(self, endpoint: str, category: str) -> bool:
        """Check if route is admin-only"""
        return category == "Administration" or "admin" in endpoint.lower()

    def build_navigation_tree(self, user=None) -> Dict[str, List[RouteInfo]]:
        """Build hierarchical navigation tree"""
        # Check cache first
        cache_key = f"navigation_tree_{id(user)}"
        if cache_key in self._navigation_cache:
            return self._navigation_cache[cache_key]

        # Discover all routes
        routes = self.discover_routes()

        # Filter routes based on user permissions
        if user:
            routes = [r for r in routes if self._user_can_access(user, r)]
        else:
            # Only show public routes if no user
            routes = [r for r in routes if not r.requires_auth]

        # Group by category
        navigation = defaultdict(list)
        for route in routes:
            navigation[route.category].append(route)

        # Sort routes within each category
        for category in navigation:
            navigation[category].sort(key=lambda r: (r.order, r.title))

        # Convert to regular dict and cache
        result = dict(navigation)
        self._navigation_cache[cache_key] = result

        return result

    def _user_can_access(self, user, route: RouteInfo) -> bool:
        """Check if user can access a route"""
        # Admin routes require admin role
        if route.is_admin:
            # R3-1 (PR 428 round 4): ``getattr(user, "is_admin", False)``
            # with no call returned the bound method, always truthy -- every
            # user was shown every admin route in navigation. Judged against
            # the active organisation, same as the rest of this PR.
            from app.middleware.tenant_decorators import is_active_org_admin

            return is_active_org_admin(user)

        # Other routes just need to be logged in
        return True

    def clear_cache(self):
        """Clear all caches"""
        self._route_cache.clear()
        self._navigation_cache.clear()

    def get_route_by_endpoint(self, endpoint: str) -> Optional[RouteInfo]:
        """Get route info by endpoint name"""
        routes = self.discover_routes()
        for route in routes:
            if route.endpoint == endpoint:
                return route
        return None

    def get_routes_by_category(self, category: str) -> List[RouteInfo]:
        """Get all routes in a category"""
        navigation = self.build_navigation_tree()
        return navigation.get(category, [])

    def get_categories(self) -> List[str]:
        """Get all available categories"""
        navigation = self.build_navigation_tree()
        return sorted(navigation.keys())


# Global instance for use in context processors
route_discovery = RouteDiscoveryService()
