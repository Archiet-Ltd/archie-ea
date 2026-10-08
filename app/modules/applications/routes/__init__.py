"""
Applications routes -- decomposed from unified_applications_routes.py (10,736 lines).

The ``unified_applications_bp`` Blueprint is defined here and shared across all
sub-modules.  Each sub-module imports it and registers its routes.

Sub-modules:
- list_views:                    List/dashboard/table views (6 routes)
- crud_routes:                   Create, detail, edit, delete, bulk ops (8 routes)
- update_routes:                 CSRF-exempt AJAX updates (5 routes)
- vendor_display_routes:         Vendor list, create, detail pages (3 routes)
- import_export_routes:          Import page, AI review, export CSV (8 routes)
- import_sophisticated_routes:   Sophisticated import modal (8 routes)
- document_routes:               Document upload/download/delete, capability mapping (6 routes)
- vendor_api_routes:             Vendor matching, analysis, dashboard APIs (15 routes)
- element_routes:                ArchiMate element addition (16 routes)
- auto_mapping_routes:           Semantic linking, APQC enrichment, auto-map (8 routes)
- rationalization_api_routes:    Rationalization dashboard, element CRUD, templates (15 routes)
"""

from flask import Blueprint

unified_applications_bp = Blueprint(
    "unified_applications", __name__, url_prefix="/applications"
)

# Mark as guardrailed BEFORE routes are registered
from app.core.compat import mark_blueprint_guardrailed
mark_blueprint_guardrailed(unified_applications_bp)


# V-02 (S1, 17 Aug 2026 QA register): POST /applications/create returned 201
# for a Viewer while its sibling /applications/bulk-delete had both
# authorization and CSRF -- because protection here was applied
# endpoint-by-endpoint by hand across 11 sub-modules and ~90 routes (see the
# module docstring above), not by default. Auditing and patching each route
# individually would reproduce the exact failure mode the finding describes:
# the next new route in any of these files would again ship unprotected by
# default. A blueprint-wide default-deny closes the whole class at once.
#
# Baseline, not a replacement for the stricter route-level guards that
# already exist: `Permission.GENERAL` is the same bitfield every non-AI write
# route in the app checks (see app/models/user.py, ToolExecutor._user_can_write
# in the AI chat tool executor for the equivalent choke point on the agent
# side). A Viewer role carries permissions=0 and fails it; every other seeded
# role (User, Architect, Approver, Administrator) carries GENERAL and passes
# through unaffected. Routes that need something stricter still layer their
# own decorator (@require_roles("admin"), etc.) on top -- this hook only
# raises the floor, it never lowers a ceiling a route already set.
@unified_applications_bp.before_request
def _default_deny_unauthorized_writes():
    from flask import abort, g, jsonify, request
    from flask_login import current_user

    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if not current_user.is_authenticated:
        # Let @login_required (present on every route in this blueprint)
        # produce the normal 401/redirect -- this hook only adds a
        # permission floor for authenticated users, it does not replace
        # authentication.
        return None
    if request.endpoint == "unified_applications.delete_document_file":
        # This floor runs ahead of the view, so a document in another
        # organisation (or a missing one) previously 403'd here -- confirming
        # a row exists -- before delete_document_file's own tenant-scoped
        # lookup ever ran. Repeat that same lookup here, exactly as the view
        # does, so an out-of-tenant or missing document 404s instead. A
        # same-organisation document falls through to the unchanged floor
        # below, which still answers 403 for a caller without
        # Permission.GENERAL.
        from app.models.miscellaneous import ApplicationDocument

        doc_id = (request.view_args or {}).get("doc_id")
        query = ApplicationDocument.query.filter_by(id=doc_id)
        if not getattr(current_user, "is_platform_admin", False):
            query = query.filter_by(organization_id=g.current_org_id)
        if query.first() is None:
            abort(404)
    from app.models.user import Permission

    if current_user.can(Permission.GENERAL):
        return None
    return jsonify({
        "success": False,
        "error": "Your role does not have write access to Applications.",
        "code": "PERMISSION_DENIED",
    }), 403

# Import sub-modules to register their routes on the shared blueprint.
from . import (  # noqa: F401, E402
    auto_mapping_routes,
    coverage_routes,
    crud_routes,
    document_routes,
    element_routes,
    import_export_routes,
    import_sophisticated_routes,
    list_views,
    owner_routes,
    rationalization_api_routes,
    update_routes,
    vendor_api_routes,
    vendor_display_routes,
)
