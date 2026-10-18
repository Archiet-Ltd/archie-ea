# Import route modules AFTER main blueprint is imported to avoid circular imports
# These imports register the routes with the blueprint via decorators
from app.main import capability_framework_routes  # noqa
from app.main import errors  # noqa
# framework_management_routes deleted — empty shell "Capability Framework" page removed
from app.main import routes_application_roadmap  # noqa
from app.main import routes_archimate_roadmap  # noqa
from app.main import routes_capability_analysis  # noqa
from app.main import routes_capability_roadmap  # noqa
# routes_capability_roadmap_enhancements deleted — 5 low-value capability pages removed
from app.main import routes_enterprise_architecture  # noqa
from app.main import routes_hybrid_mapping  # noqa
# routes_hybrid_roadmap deleted — /hybrid-roadmap redirects to /capability-roadmap
# routes_options_comparison deleted — /roadmap-options had no real data
# NOTE: routes_project_task_dashboard, routes_project_task_tracker,
# routes_project_tasks_dashboard, and routes_sales_dashboard deleted —
# all carried WARNING: THIS FILE IS NOT REGISTERED headers and hardcoded items=[].
# Removed in dead-code-route-cleanup (Phase 10).
from app.main import routes_strategic_roadmap  # noqa
# routes_technology_roadmap deleted — /technology-roadmap redirects to /capability-roadmap
# agent-implementation UI route module deleted — template-only page, no buyer question behind it
from app.main import routes_vendor_analysis  # noqa
from app.main.views import main  # noqa

# from app.main import routes_generator  # noqa - Disabled due to missing dependencies
