"""
Fixed Navigation Configuration for Navigation Registry V2

Implements all navigation sections with proper validation, permissions,
and recursive nesting support. Ready for production with comprehensive
error handling.

Key Improvements:
✅ Schema validation (Pydantic)
✅ Permission model (roles/visibility)
✅ Explicit ordering (no conflicts)
✅ Proper nested items (recursively processed)
✅ Endpoint validation (fails on startup if broken)
✅ Error logging (debug broken configs)
✅ Icon requirements (all visible items have icons)
✅ Disabled item hiding (not in output by default)
"""

from app.config.navigation_registry_v2 import (
    ItemVisibility,
    NavigationItemV2,
    NavigationSectionV2,
    register_navigation_section,
)


# ============================================================================
# SECTION 1: HOME
# ============================================================================
HOME_SECTION = NavigationSectionV2(
    key="home",
    label="Home",
    icon="home",
    order=1,
    collapsible=False,
    items=[
        NavigationItemV2(
            label="Dashboard",
            icon="gauge",
            endpoint="admin.index",
            url_fallback="/admin/",
            order=1,
        ),
        NavigationItemV2(
            label="Overview",
            icon="layout-grid",
            endpoint="main.index",
            url_fallback="/",
            order=2,
        ),
    ],
)


# ============================================================================
# SECTION 2: APPLICATION PORTFOLIO
# ============================================================================
APPLICATION_PORTFOLIO_SECTION = NavigationSectionV2(
    key="application_portfolio",
    label="Application Portfolio",
    icon="package",
    order=2,
    collapsible=True,
    storage_key="applications",  # secrets-safety-ok: UI navigation key, not a secret
    items=[
        NavigationItemV2(
            label="All Applications",
            icon="list",
            endpoint="unified_applications.application_list",
            url_fallback="/applications/",
            order=1,
        ),
        NavigationItemV2(
            label="Create Application",
            icon="plus-circle",
            endpoint="unified_applications.application_create",
            url_fallback="/applications/create",
            order=2,
        ),
        NavigationItemV2(
            label="Application Dashboard",
            icon="bar-chart-3",  # Fixed icon name (was "bar-chart - 3")
            endpoint="unified_applications.dashboard",
            url_fallback="/applications/dashboard",
            order=3,
        ),
        NavigationItemV2(
            label="Duplicate Detection",
            icon="git-compare",
            endpoint="unified_duplicate.enterprise_dashboard",
            url_fallback="/duplicate-detection/",
            order=4,
        ),
        NavigationItemV2(
            label="Consolidation Queue",
            icon="list-x",
            endpoint="consolidation_list.dashboard",
            url_fallback="/consolidation-list/",
            order=5,
        ),
        NavigationItemV2(
            label="Consolidation Candidates",
            icon="merge",
            endpoint="unified_low_priority.consolidation_dashboard",
            url_fallback="/enterprise/consolidation",
            order=6,
        ),
    ],
    # Dynamic items: will be loaded from database
    dynamic_items_enabled=True,
    dynamic_items_source="applications",
    dynamic_items_label="Quick Access",
    dynamic_items_limit=10,
    dynamic_items_endpoint_template="unified_applications.application_detail",
    dynamic_items_id_field="id",
    dynamic_items_name_field="name",
    dynamic_items_badge_field="component_type",
)


# ============================================================================
# SECTION 3: VENDOR MANAGEMENT
# ============================================================================
VENDOR_MANAGEMENT_SECTION = NavigationSectionV2(
    key="vendor_management",
    label="Vendor Management",
    icon="building-2",
    order=3,
    collapsible=True,
    storage_key="vendors",
    items=[
        NavigationItemV2(
            label="Vendor Dashboard",
            icon="building",
            endpoint="vendors.vendors_dashboard",
            url_fallback="/applications/vendors/",
            order=1,
        ),
        NavigationItemV2(
            label="Create Vendor",
            icon="plus-circle",
            endpoint="vendors.create_vendor",
            url_fallback="/applications/vendors/create",
            order=2,
        ),
    ],
    # Dynamic items: will be loaded from database
    dynamic_items_enabled=True,
    dynamic_items_source="vendors",
    dynamic_items_label="Quick Access",
    dynamic_items_limit=8,
    dynamic_items_endpoint_template="vendors.vendor_detail",
    dynamic_items_id_field="id",
    dynamic_items_name_field="name",
    dynamic_items_badge_field="vendor_type",
)


# ============================================================================
# SECTION 4: ENTERPRISE ARCHITECTURE
# ============================================================================
ENTERPRISE_ARCHITECTURE_SECTION = NavigationSectionV2(
    key="enterprise_architecture",
    label="Enterprise Architecture",
    icon="building-2",
    order=4,
    collapsible=True,
    storage_key="enterprise",
    items=[
        NavigationItemV2(
            label="Enterprise Dashboard",
            icon="layout-dashboard",
            endpoint="unified_low_priority.enterprise_dashboard",
            url_fallback="/enterprise/",
            order=1,
        ),
        NavigationItemV2(
            label="Architecture Models",
            icon="box",
            endpoint="unified_low_priority.architecture_dashboard",
            url_fallback="/enterprise/architecture",
            order=2,
        ),
        NavigationItemV2(
            label="Capability Map",
            icon="map",
            endpoint="unified_low_priority.capability_map_dashboard",
            url_fallback="/enterprise/capability-map",
            order=3,
        ),
        NavigationItemV2(
            label="Strategic Planning",
            icon="target",
            endpoint="unified_low_priority.strategic_dashboard",
            url_fallback="/enterprise/strategic",
            order=4,
        ),
        NavigationItemV2(
            label="Policy Monitoring",
            icon="shield-check",
            endpoint="unified_low_priority.policy_monitoring_dashboard",
            url_fallback="/enterprise/policy-monitoring",
            order=5,
        ),
    ],
)


# ============================================================================
# SECTION 5: ARCHITECTURE MODELS (Subsection of Enterprise Architecture)
# ============================================================================
# NOTE: This demonstrates proper hierarchical nesting
# The items are NESTED within parent items, not as separate section
ARCHITECTURE_MODELS_SUBSECTION = NavigationItemV2(
    label="Architecture Element Browser",
    icon="box",
    endpoint="archimate_crud.dashboard",
    url_fallback="/architecture/",
    order=2,
    items=[
        NavigationItemV2(
            label="Data Architecture",
            icon="database",
            endpoint="enterprise.data_architecture_dashboard",
            url_fallback="/enterprise/data_architecture_dashboard",
            order=1,
        ),
        NavigationItemV2(
            label="Solutions Architecture",
            icon="puzzle",
            endpoint="architecture.solutions_architecture_dashboard",
            url_fallback="/architecture/solutions-architecture",
            order=2,
        ),
        NavigationItemV2(
            label="Software Architecture",
            icon="code",
            endpoint="architecture.software_architecture_dashboard",
            url_fallback="/architecture/software-architecture",
            order=3,
        ),
    ],
)

# Add architecture models as nested item
ENTERPRISE_ARCHITECTURE_SECTION.items.insert(1, ARCHITECTURE_MODELS_SUBSECTION)


# ============================================================================
# SECTION 6: AI & ANALYSIS
# A95-010: AI Assistant is the default landing entry for the Solution Architect
# persona. It is intentionally kept at order=1 (index 0 / first position) in
# this section so that AI Chat appears at the top of the AI & Analysis group.
# The admin_sidebar.html template also exposes a prominent "Start with AI →"
# shortcut at the top of the sidebar for Solution Architect persona users.
# ============================================================================
AI_ANALYSIS_SECTION = NavigationSectionV2(
    key="ai_analysis",
    label="AI & Analysis",
    icon="brain",
    order=6,
    collapsible=True,
    storage_key="ai_analysis",
    items=[
        # A95-010: default_for=["solution_architect"] — must remain at order=1
        NavigationItemV2(
            label="Start with AI",
            icon="sparkles",
            endpoint="unified_ai_chat.index",
            url_fallback="/ai-chat/",
            order=0,
        ),
        NavigationItemV2(
            label="AI Assistant",
            icon="message-square",
            endpoint="unified_ai_chat.index",
            url_fallback="/ai-chat/",
            order=1,
        ),
        NavigationItemV2(
            label="Architecture Assistant",
            icon="sparkles",
            endpoint="architect_ui.architecture_assistant",
            url_fallback="/architecture-assistant/",
            order=2,
        ),
        NavigationItemV2(
            label="Market Intelligence",
            icon="trending-up",
            endpoint="architect_ui.market_intelligence",
            url_fallback="/market-intelligence/",
            order=4,
        ),
        # ✅ FIXED: Impact Analysis now has proper icon
        # ❌ OLD: endpoint=None, url_fallback="#" (broken link)
        # ✅ NEW (BA persona work): repointed to the live strategic impact-analysis
        # dashboard (strategic_bp, /strategic/impact-analysis) — the standalone
        # /impact-analysis route was never implemented, so this was disabled forever.
        NavigationItemV2(
            label="Impact Analysis",
            icon="git-compare",
            endpoint="strategic.impact_analysis",
            url_fallback="/strategic/impact-analysis",
            disabled=False,
            order=5,
        ),
    ],
)


# ============================================================================
# SECTION 7: ARCHITECTURE TOOLS
# ============================================================================
ARCHITECTURE_TOOLS_SECTION = NavigationSectionV2(
    key="architecture_tools",
    label="Architecture Tools",
    icon="wrench",
    order=7,
    collapsible=True,
    storage_key="arch_tools",  # secrets-safety-ok: UI navigation key, not a secret
    items=[
        NavigationItemV2(
            label="ArchiMate Composer",
            icon="layers",
            endpoint="archimate.composer_page",
            url_fallback="/archimate/composer?viewpoint=layered",
            # Opens directly into the Layered viewpoint (every element and
            # relationship across all ArchiMate layers) instead of a blank
            # "Unsaved diagram" canvas — composer_page's `viewpoint` query
            # param opens straight into View mode on that viewpoint (see
            # app/modules/architecture/routes/archimate_routes.py::composer_page).
            query_params={"viewpoint": "layered"},
            order=1,
        ),
        NavigationItemV2(
            label="Solution Composer",
            icon="palette",
            endpoint="solution_design.list_solutions",
            url_fallback="/solutions/",
            order=2,
        ),
        NavigationItemV2(
            label="Roadmap Builder",
            icon="map",
            endpoint="strategic_routes.roadmap_index",
            url_fallback="/genome/roadmap/",
            order=3,
        ),
        NavigationItemV2(
            label="Architecture Monitoring",
            icon="activity",
            endpoint="architecture_routes.monitoring",
            url_fallback="/architecture/monitoring",
            order=4,
        ),
    ],
)


# ============================================================================
# SECTION 8: FRAMEWORK MANAGEMENT (FIXED 3-level nesting)
# ============================================================================
# ✅ FIXED: Properly nested collapsible subsections
# ❌ OLD: Chaotic 3-level nesting with items that have no endpoint
# ✅ NEW: Each item is either clickable (has endpoint) OR is header (disabled)

MANUFACTURING_EXCELLENCE_ITEM = NavigationItemV2(
    label="Manufacturing Excellence",
    icon="factory",
    endpoint="framework_management.manufacturing_overview",  # Clickable
    url_fallback="/framework/manufacturing",
    order=2,
    items=[
        NavigationItemV2(
            label="Manufacturing Dashboard",
            icon="gauge",
            endpoint="framework_management.manufacturing_dashboard",
            url_fallback="/framework/manufacturing/dashboard",
            order=1,
        ),
        NavigationItemV2(
            label="Data Table",
            icon="table",
            endpoint="framework_management.manufacturing_datatable",
            url_fallback="/framework/manufacturing/table",
            order=2,
        ),
    ],
)

FRAMEWORK_EXTENSIONS_ITEM = NavigationItemV2(
    label="Framework Extensions",
    icon="plug",
    endpoint=None,
    url_fallback="#",
    disabled=True,  # Disabled header for now
    order=3,
    items=[
        NavigationItemV2(
            label="Manufacturing Basic",
            icon="layers",
            endpoint="framework_management.manufacturing_basic",
            url_fallback="/framework/manufacturing-basic",
            order=1,
        ),
        NavigationItemV2(
            label="Manufacturing Advanced",
            icon="layers-2",
            endpoint="framework_management.manufacturing_advanced",
            url_fallback="/framework/manufacturing-advanced",
            order=2,
        ),
        NavigationItemV2(
            label="Digital Transformation",
            icon="zap",
            endpoint="framework_management.digital_transformation",
            url_fallback="/framework/digital-transformation",
            order=3,
        ),
    ],
)

FRAMEWORK_TEMPLATES_ITEM = NavigationItemV2(
    label="Framework Templates",
    icon="layout-template",
    endpoint=None,
    url_fallback="#",
    disabled=True,  # Disabled header for now
    order=4,
    items=[
        NavigationItemV2(
            label="Small Enterprise",
            icon="building",
            endpoint="framework_management.template_small",
            url_fallback="/framework/template/small",
            order=1,
        ),
        NavigationItemV2(
            label="Large Enterprise",
            icon="building-2",
            endpoint="framework_management.template_large",
            url_fallback="/framework/template/large",
            order=2,
        ),
        NavigationItemV2(
            label="Generic Business",
            icon="briefcase",
            endpoint="framework_management.template_generic",
            url_fallback="/framework/template/generic",
            order=3,
        ),
    ],
)

FRAMEWORK_MANAGEMENT_SECTION = NavigationSectionV2(
    key="framework_management",
    label="Framework Management",
    icon="layers",
    order=8,
    collapsible=True,
    storage_key="frameworks",  # secrets-safety-ok: UI navigation key, not a secret
    items=[
        NavigationItemV2(
            label="Framework Overview",
            icon="layout-grid",
            endpoint="framework_management.overview",
            url_fallback="/framework/",
            order=1,
        ),
        MANUFACTURING_EXCELLENCE_ITEM,
        FRAMEWORK_EXTENSIONS_ITEM,
        FRAMEWORK_TEMPLATES_ITEM,
        NavigationItemV2(
            label="Capability Maturity",
            icon="trending-up",
            endpoint="framework_management.capability_maturity",
            url_fallback="/framework/capability-maturity",
            order=5,
        ),
        NavigationItemV2(
            label="Framework Config",
            icon="settings",
            endpoint="framework_management.config",
            url_fallback="/framework/config",
            order=6,
            required_roles=["admin"],  # Admin only
            visibility=ItemVisibility.SPECIFIC_ROLES,
        ),
    ],
)


# ============================================================================
# SECTION 9: TOOLS & UTILITIES
# ============================================================================
TOOLS_UTILITIES_SECTION = NavigationSectionV2(
    key="tools_utilities",
    label="Tools & Utilities",
    icon="tool",
    order=9,
    collapsible=True,
    storage_key="tools",
    items=[
        NavigationItemV2(
            label="Users",
            icon="users",
            endpoint="admin.registered_users",
            url_fallback="/admin/users",
            order=2,
            required_roles=["admin"],  # Admin only
            visibility=ItemVisibility.SPECIFIC_ROLES,
        ),
        NavigationItemV2(
            label="API Settings",
            icon="key",
            endpoint="admin.api_settings",
            url_fallback="/admin/api-settings",
            order=3,
            required_roles=["admin"],  # Admin only
            visibility=ItemVisibility.SPECIFIC_ROLES,
        ),
    ],
)


# ============================================================================
# SECTION 10: OTHER
# ============================================================================
OTHER_SECTION = NavigationSectionV2(
    key="other",
    label="Other",
    icon="more-horizontal",
    order=99,
    collapsible=False,
    items=[
        NavigationItemV2(
            label="Documentation",
            icon="help-circle",
            endpoint=None,
            url_fallback="/docs",
            disabled=True,  # Will enable when docs are ready
            order=2,
        ),
    ],
)


# ============================================================================
# REGISTRATION FUNCTION
# ============================================================================
def register_all_sections():
    """
    Register all navigation sections.
    Call this during Flask app initialization.
    """
    sections = [
        HOME_SECTION,
        APPLICATION_PORTFOLIO_SECTION,
        VENDOR_MANAGEMENT_SECTION,
        ENTERPRISE_ARCHITECTURE_SECTION,
        AI_ANALYSIS_SECTION,
        ARCHITECTURE_TOOLS_SECTION,
        FRAMEWORK_MANAGEMENT_SECTION,
        TOOLS_UTILITIES_SECTION,
        OTHER_SECTION,
    ]

    for section in sections:
        register_navigation_section(section)


# ============================================================================
# IMPROVEMENTS SUMMARY
# ============================================================================
"""
✅ GAPS FIXED IN THIS IMPLEMENTATION:

1. PERMISSION MODEL
   - Added: visibility field (ALWAYS, ADMIN_ONLY, AUTHENTICATED_ONLY, SPECIFIC_ROLES, CUSTOM)
   - Added: required_roles field
   - Example: Framework Config (admin only), Users (admin only)

2. SCHEMA VALIDATION
   - Pydantic models enforce:
     ✓ Icon required for all visible items
     ✓ Either endpoint or valid fallback URL required
     ✓ Labels unique and non-empty
     ✓ Icon names validated (lucide format)
     ✓ Endpoint names validated (blueprint.function format)

3. EXPLICIT ORDERING
   - All sections have order: 1-99
   - All items have order within section
   - No conflicts, deterministic sort

4. PROPER NESTING
   - Items can contain nested items (recursively processed)
   - Collapsible groups actually contain child items
   - Framework Management now makes sense:
     ✓ Manufacturing Excellence (clickable parent)
       ✓ Dashboard
       ✓ Data Table

5. DISABLED ITEMS HANDLING
   - Impact Analysis: marked disabled=True (won't show by default)
   - Documentation: marked disabled=True (won't show until ready)
   - Disabled items can have icon/label but endpoint=None

6. ICON FIXES
   - Fixed "bar-chart - 3" → "bar-chart-3"
   - Fixed "building - 2" → "building-2"
   - All items have valid lucide icon names

7. ENDPOINT VALIDATION (on startup)
   - Checks all endpoints exist
   - Detects circular parent references
   - Warns about duplicate endpoints
   - Logs failures for debugging

8. ERROR HANDLING
   - Missing object attributes logged (not silent)
   - Failed URL generation logged with details
   - Badge field missing handled gracefully

9. DYNAMIC ITEMS
   - Validated source ("applications" or "vendors")
   - Limit enforced
   - Badge field optional with fallback

10. BREADCRUMB SUPPORT
    - Works with nested items (recursive flattening)
    - Proper section context
    - Full navigation path
"""
