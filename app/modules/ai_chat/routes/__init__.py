"""
Unified AI Chat routes -- decomposed from unified_ai_chat_routes.py (4,247 lines).

The ``unified_ai_chat_bp`` Blueprint is defined here and shared across all
sub-modules.  Each sub-module imports it and registers its routes.

Sub-modules:
- chat_views:             Template-rendering views (index, document-upload, etc.)
- chat_core:              Chat messaging, models, domains, personas, templates, history, sessions
- document_routes:        Document upload, CRUD, re-analyze, create-elements, feedback, compare
- workflow_routes:        Data modification + ArchiMate/APQC generate/apply, bulk-process
- chat_workflows:         Chat-driven architect workflows (chat/* prefix)
- entity_routes:          Entity matching, business output transformation, architecture gen
- analytics_routes:       Analytics, NL query, recommendations
- legacy_compat:          Legacy redirects, error handlers, health endpoint
- generate_routes:        Structured deliverable generator (solution analysis, SAD, visual, roadmap, risk, org, benefit, feasibility, full-package)
- approval_routes:        CRUD approval API (GET pending, POST approve/reject) for approval_modal.js
"""

from flask import Blueprint

unified_ai_chat_bp = Blueprint("unified_ai_chat", __name__, url_prefix="/ai-chat")

# Import sub-modules to register their routes on the shared blueprint.
from . import (  # noqa: F401, E402
    chat_views,
    chat_core,
    document_routes,
    workflow_routes,
    chat_workflows,
    entity_routes,
    analytics_routes,
    legacy_compat,
    generate_routes,
    prompt_template_routes,
    chat_admin_routes,
    approval_routes,
    metrics_routes,
    page_guide_routes,
    agent_oversight_routes,
)

from .run_record_routes import register_run_record_routes

register_run_record_routes(unified_ai_chat_bp)
