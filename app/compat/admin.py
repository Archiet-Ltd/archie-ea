"""
Compatibility wrappers for the admin module (legacy -> v2).

Feature-flag gating: USE_ADMIN_COMPAT=true (env) enables the wrappers.
"""

from app.compat.deprecation_logger import CompatStats
from app.compat.feature_flag_gate import make_compat_config
from app.compat.mapping_registry import register_routes
from app.compat.wrapper_generator import make_legacy_wrapper

_config = make_compat_config("admin")


class AdminCompatStats(CompatStats):
    """Thread-safe hit counter for legacy admin endpoints."""
    pass


LEGACY_ROUTE_MAP = {
    # admin blueprint (38+ routes, url_prefix=/admin)
    "admin.index":                        {"url": "/admin/",                                    "v2": "admin.index",                        "method": "GET"},
    "admin.dashboard":                    {"url": "/admin/dashboard",                           "v2": "admin.dashboard",                    "method": "GET"},
    "admin.new_user":                     {"url": "/admin/new-user",                            "v2": "admin.new_user",                     "method": "GET,POST"},
    "admin.invite_user":                  {"url": "/admin/invite-user",                         "v2": "admin.invite_user",                  "method": "GET,POST"},
    "admin.registered_users":             {"url": "/admin/users",                               "v2": "admin.registered_users",             "method": "GET"},
    "admin.user_info":                    {"url": "/admin/user/<int:user_id>",                  "v2": "admin.user_info",                    "method": "GET"},
    "admin.change_user_email":            {"url": "/admin/user/<int:user_id>/change-email",     "v2": "admin.change_user_email",            "method": "GET,POST"},
    "admin.change_account_type":          {"url": "/admin/user/<int:user_id>/change-account-type", "v2": "admin.change_account_type",       "method": "GET,POST"},
    "admin.delete_user_request":          {"url": "/admin/user/<int:user_id>/delete",           "v2": "admin.delete_user_request",          "method": "GET"},
    "admin.delete_user":                  {"url": "/admin/user/<int:user_id>/_delete",          "v2": "admin.delete_user",                  "method": "POST"},
    "admin.update_editor_contents":       {"url": "/admin/_update_editor_contents",             "v2": "admin.update_editor_contents",       "method": "POST"},
    "admin.api_settings":                 {"url": "/admin/api-settings",                        "v2": "admin.api_settings",                 "method": "GET,POST"},
    "admin.delete_api_settings":          {"url": "/admin/api-settings/<int:settings_id>/delete", "v2": "admin.delete_api_settings",        "method": "POST"},
    "admin.test_api_settings":            {"url": "/admin/api-settings/<int:settings_id>/test", "v2": "admin.test_api_settings",            "method": "POST"},
    "admin.preview_env_keys":             {"url": "/admin/api-settings/env-keys",               "v2": "admin.preview_env_keys",             "method": "GET"},
    "admin.update_provider_model":        {"url": "/admin/api-settings/update-model",           "v2": "admin.update_provider_model",        "method": "POST"},
    "admin.load_env_keys":                {"url": "/admin/api-settings/load-env",               "v2": "admin.load_env_keys",                "method": "POST"},
    "admin.consolidation_status":         {"url": "/admin/consolidation",                       "v2": "admin.consolidation_status",         "method": "GET"},
    "admin.feature_flags":                {"url": "/admin/feature-flags",                       "v2": "admin.feature_flags",                "method": "GET"},
    "admin.feature_flag_new":             {"url": "/admin/feature-flags/new",                   "v2": "admin.feature_flag_new",             "method": "GET,POST"},
    "admin.feature_flag_edit":            {"url": "/admin/feature-flags/<int:id>/edit",         "v2": "admin.feature_flag_edit",            "method": "GET,POST"},
    "admin.feature_flag_toggle":          {"url": "/admin/feature-flags/<int:id>/toggle",       "v2": "admin.feature_flag_toggle",          "method": "POST"},
    "admin.feature_flag_delete":          {"url": "/admin/feature-flags/<int:id>/delete",       "v2": "admin.feature_flag_delete",          "method": "POST"},
    "admin.feature_flags_discover_sidebar": {"url": "/admin/feature-flags/discover-sidebar",    "v2": "admin.feature_flags_discover_sidebar", "method": "GET"},
    "admin.feature_flags_create_from_sidebar": {"url": "/admin/feature-flags/discover-sidebar/create", "v2": "admin.feature_flags_create_from_sidebar", "method": "POST"},
    "admin.abacus_settings":              {"url": "/admin/abacus-settings",                     "v2": "admin.abacus_settings",              "method": "GET,POST"},
    "admin.test_abacus_connection":       {"url": "/admin/abacus-settings/test-connection",     "v2": "admin.test_abacus_connection",       "method": "POST"},
    "admin.trigger_abacus_sync":          {"url": "/admin/abacus-settings/trigger-sync",        "v2": "admin.trigger_abacus_sync",          "method": "POST"},
    "admin.abacus_sync_status":           {"url": "/admin/abacus-settings/sync-status",         "v2": "admin.abacus_sync_status",           "method": "GET"},
    "admin.cancel_abacus_job":            {"url": "/admin/abacus-settings/cancel-job/<int:job_id>", "v2": "admin.cancel_abacus_job",        "method": "POST"},
    "admin.abacus_stats":                 {"url": "/admin/abacus-settings/stats",               "v2": "admin.abacus_stats",                 "method": "GET"},
    "admin.abacus_dashboard":             {"url": "/admin/abacus-dashboard",                    "v2": "admin.abacus_dashboard",             "method": "GET"},
    "admin.seed_management":              {"url": "/admin/seed-management",                     "v2": "admin.seed_management",              "method": "GET"},
    "admin.seed_status":                  {"url": "/admin/api/seed-status",                     "v2": "admin.seed_status",                  "method": "GET"},
    "admin.seed":                         {"url": "/admin/api/seed/<key>",                      "v2": "admin.seed",                         "method": "POST"},
    "admin.seed_all":                     {"url": "/admin/api/seed-all",                        "v2": "admin.seed_all",                     "method": "POST"},
    # sidebar_mgmt blueprint (5 routes, url_prefix=/api/admin/sidebar)
    "sidebar_mgmt.list_sidebar_items":    {"url": "/api/admin/sidebar/items",                   "v2": "sidebar_mgmt.list_sidebar_items",    "method": "GET"},
    "sidebar_mgmt.toggle_sidebar_item":   {"url": "/api/admin/sidebar/items/<int:item_id>/toggle", "v2": "sidebar_mgmt.toggle_sidebar_item", "method": "POST"},
    "sidebar_mgmt.toggle_section":        {"url": "/api/admin/sidebar/items/section/<section>/toggle", "v2": "sidebar_mgmt.toggle_section", "method": "POST"},
    "sidebar_mgmt.toggle_subsection":     {"url": "/api/admin/sidebar/items/subsection/<section>/<subsection>/toggle", "v2": "sidebar_mgmt.toggle_subsection", "method": "POST"},
    "sidebar_mgmt.reset_all_items":       {"url": "/api/admin/sidebar/items/reset",             "v2": "sidebar_mgmt.reset_all_items",       "method": "POST"},
    # deprecation blueprint (7 routes, url_prefix=/admin/deprecation)
    "deprecation.dashboard":              {"url": "/admin/deprecation/",                        "v2": "deprecation.dashboard",              "method": "GET"},
    "deprecation.api_stats":              {"url": "/admin/deprecation/api/stats",               "v2": "deprecation.api_stats",              "method": "GET"},
    "deprecation.api_alerts":             {"url": "/admin/deprecation/api/alerts",              "v2": "deprecation.api_alerts",             "method": "GET"},
    "deprecation.api_velocity":           {"url": "/admin/deprecation/api/velocity",            "v2": "deprecation.api_velocity",           "method": "GET"},
    "deprecation.api_export":             {"url": "/admin/deprecation/api/export",              "v2": "deprecation.api_export",             "method": "GET"},
    "deprecation.api_webhook":            {"url": "/admin/deprecation/api/webhook",             "v2": "deprecation.api_webhook",            "method": "POST"},
    "deprecation.api_webhook_test":       {"url": "/admin/deprecation/api/webhook/test",        "v2": "deprecation.api_webhook_test",       "method": "POST"},
}

register_routes("admin", LEGACY_ROUTE_MAP)

wrap_legacy_admin_bp = make_legacy_wrapper(_config, AdminCompatStats)
wrap_legacy_sidebar_mgmt_bp = make_legacy_wrapper(_config, AdminCompatStats)
wrap_legacy_deprecation_bp = make_legacy_wrapper(_config, AdminCompatStats)
