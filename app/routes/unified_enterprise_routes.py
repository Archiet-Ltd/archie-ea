"""
UNIFIED ENTERPRISE ARCHITECTURE BLUEPRINT
Consolidates all architecture-related routes into a single comprehensive blueprint
following the Option 1 Full Consolidation strategy.

URL Structure:
/enterprise/* - Enterprise Architecture
/enterprise/data/* - Data Architecture
/enterprise/solutions/* - Solutions Architecture
/enterprise/software/* - Software Architecture
/enterprise/strategic/* - Strategic Planning
/enterprise/implementation/* - Implementation Planning
"""

# Side-effect import, NOT dead code: this is the only module that imports
# app/models/metrics.py, and that import is what registers the
# `application_metrics_snapshots` table on db.metadata. Removing it (as
# `ruff --fix --select F401` did) silently drops the table from the ORM —
# caught by comparing db.metadata.tables before and after.
from ..models.metrics import ApplicationMetricsSnapshot  # noqa: F401
import logging

from flask import (
    Blueprint,
    current_app,  # dead-code-ok
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required  # dead-code-ok
from sqlalchemy import or_  # dead-code-ok
from sqlalchemy.exc import SQLAlchemyError

from .. import db
from ..security.audit import audit_logger, AuditEventType, AuditEventSeverity
from ..exceptions import (  # dead-code-ok
    DatabaseError,
)
from ..utils.api_helpers import api_error
from ..models import (  # dead-code-ok
    ConceptualDataModel,
    Contract,
    DesignPattern,
    LogicalDataModel,
    PhysicalDataModel,
    SoftwareDependency,
    SoftwareModule,
    Solution,
    SolutionPattern,
)
from ..models.business_capabilities import (  # dead-code-ok
    BusinessCapability,
)
from ..models.archimate_core import ArchiMateElement
from ..models.implementation_migration import Gap
from ..models.implementation_migration import Plateau
from ..models.unified_work_package import UnifiedWorkPackage
from ..models.implementation_migration import WorkPackage  # dead-code-ok
from app.services import work_package_service
from app.utils.pagination import safe_int_arg
from app.utils.tenant import current_organization_id

logger = logging.getLogger(__name__)

# Create unified enterprise architecture blueprint
enterprise_bp = Blueprint("enterprise", __name__, url_prefix="/enterprise")

# ============================================================================
# DATA ARCHITECTURE ROUTES
# ============================================================================


@enterprise_bp.route("/data_architecture_dashboard")
@login_required
def data_architecture_dashboard():
    """Data Architecture Dashboard"""
    try:
        # Get metrics
        conceptual_count = ConceptualDataModel.query.count()
        logical_count = LogicalDataModel.query.count()
        physical_count = PhysicalDataModel.query.count()

        return render_template(
            "enterprise/data_architecture_dashboard.html",
            conceptual_count=conceptual_count,
            logical_count=logical_count,
            physical_count=physical_count,
        )
    except SQLAlchemyError as e:
        current_app.logger.error(
            f"Database error loading data architecture dashboard: {e}"
        )
        raise DatabaseError(
            message=f"Failed to load dashboard data: {str(e)}",
            user_message="Unable to load the data architecture dashboard. Please try again.",
            recovery_action="Refresh the page. If the problem persists, contact support.",
        )


@enterprise_bp.route("/data/models")
@login_required
def data_models():
    """Data Models Overview - renders existing data architecture dashboard.

    Passes the same counts as `data_architecture_dashboard`, because both render
    `enterprise/data_architecture_dashboard.html` and its metric tiles read
    `conceptual_count` / `logical_count` / `physical_count`. This route used to
    pass three *lists* under different names; the template never read them, so
    every tile fell through `value=... or 0` and displayed 0 whatever the real
    count was — a fabricated zero indistinguishable from a measured one. The
    lists were also never rendered, so fetching up to 1500 rows was pure waste.
    """
    try:
        return render_template(
            "enterprise/data_architecture_dashboard.html",
            conceptual_count=ConceptualDataModel.query.count(),
            logical_count=LogicalDataModel.query.count(),
            physical_count=PhysicalDataModel.query.count(),
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error loading data models: {e}")
        raise DatabaseError(
            message=f"Failed to load data models: {str(e)}",
            user_message="Unable to load data models. Please try again.",
            recovery_action="Return to the dashboard and try again.",
        )


@enterprise_bp.route("/api/data-models")
@login_required
def api_data_models():
    """Get all data architecture models."""
    try:
        conceptual_models = ConceptualDataModel.query.limit(500).all()
        logical_models = LogicalDataModel.query.limit(500).all()
        physical_models = PhysicalDataModel.query.limit(500).all()

        return jsonify(
            {
                "conceptual_models": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "description": m.description,
                        "business_domain": m.business_domain,
                        "scope": m.scope,
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                    }
                    for m in conceptual_models
                ],
                "logical_models": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "description": m.description,
                        "normalization_level": m.normalization_level,
                        "design_pattern": m.design_pattern,
                        "conceptual_model_id": m.conceptual_model_id,
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                    }
                    for m in logical_models
                ],
                "physical_models": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "description": m.description,
                        "database_type": m.database_type,
                        "deployment_environment": m.deployment_environment,
                        "logical_model_id": m.logical_model_id,
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                    }
                    for m in physical_models
                ],
            }
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error fetching data models: {e}")
        raise DatabaseError(
            message=f"Failed to fetch data models: {str(e)}",
            user_message="Unable to retrieve data models from the database.",
            recovery_action="Please try again. If the problem persists, contact support.",
        )


# ============================================================================
# SOLUTIONS ARCHITECTURE ROUTES
# ============================================================================


@enterprise_bp.route("/solutions_architecture_dashboard")
@login_required
def solutions_architecture_dashboard():
    """Solutions Architecture Dashboard"""
    try:
        solution_count = Solution.query.count()
        pattern_count = SolutionPattern.query.count()
        contract_count = Contract.query.count()

        return render_template(
            "enterprise/solutions_architecture_dashboard.html",
            solution_count=solution_count,
            pattern_count=pattern_count,
            contract_count=contract_count,
        )
    except SQLAlchemyError as e:
        current_app.logger.error(
            f"Database error loading solutions architecture dashboard: {e}"
        )
        raise DatabaseError(
            message=f"Failed to load solutions dashboard: {str(e)}",
            user_message="Unable to load the solutions architecture dashboard.",
            recovery_action="Refresh the page. If the problem persists, contact support.",
        )


@enterprise_bp.route("/api/solutions")
@login_required
def api_solutions():
    """Get all solutions architecture models."""
    try:
        solutions = Solution.query.limit(500).all()
        patterns = SolutionPattern.query.limit(500).all()
        contracts = Contract.query.limit(500).all()

        return jsonify(
            {
                "solutions": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "description": s.description,
                        "business_domain": s.business_domain,
                        "solution_type": s.solution_type,
                        "created_at": s.created_at.isoformat()
                        if s.created_at
                        else None,
                    }
                    for s in solutions
                ],
                "patterns": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "pattern_category": p.pattern_category,
                        "applicability": p.applicability,
                        "created_at": p.created_at.isoformat()
                        if p.created_at
                        else None,
                    }
                    for p in patterns
                ],
                "contracts": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "contract_type": c.contract_type,
                        "provider": c.provider,
                        "status": c.status,
                        "created_at": c.created_at.isoformat()
                        if c.created_at
                        else None,
                    }
                    for c in contracts
                ],
            }
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error fetching solutions: {e}")
        raise DatabaseError(
            message=f"Failed to fetch solutions data: {str(e)}",
            user_message="Unable to retrieve solutions from the database.",
            recovery_action="Please try again. If the problem persists, contact support.",
        )


# ============================================================================
# SOFTWARE ARCHITECTURE ROUTES
# ============================================================================


@enterprise_bp.route("/software_architecture_dashboard")
@login_required
def software_architecture_dashboard():
    """Software Architecture Dashboard"""
    try:
        component_count = ArchiMateElement.query.filter_by(
            type="ApplicationComponent", layer="Application"
        ).count()
        service_count = ArchiMateElement.query.filter_by(
            type="ApplicationService", layer="Application"
        ).count()
        interface_count = ArchiMateElement.query.filter_by(
            type="ApplicationInterface", layer="Application"
        ).count()
        dependency_count = SoftwareDependency.query.count()

        return render_template(
            "enterprise/software_architecture_dashboard.html",
            component_count=component_count,
            service_count=service_count,
            interface_count=interface_count,
            dependency_count=dependency_count,
        )
    except SQLAlchemyError as e:
        logger.error(
            f"Database error loading software architecture dashboard: {e}",
            exc_info=True,
        )
        raise DatabaseError(
            message=f"Failed to load software dashboard: {str(e)}",
            user_message="Unable to load the software architecture dashboard.",
            recovery_action="Refresh the page. If the problem persists, contact support.",
        )


@enterprise_bp.route("/api/software")
@login_required
def api_software():
    """Get all software architecture models."""
    try:
        modules = SoftwareModule.query.limit(500).all()
        patterns = DesignPattern.query.limit(500).all()
        dependencies = SoftwareDependency.query.limit(500).all()

        return jsonify(
            {
                "modules": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "description": m.description,
                        "module_type": m.module_type,
                        "technology_stack": m.technology_stack,
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                    }
                    for m in modules
                ],
                "patterns": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "pattern_language": p.pattern_language,
                        "complexity_level": p.complexity_level,
                        "created_at": p.created_at.isoformat()
                        if p.created_at
                        else None,
                    }
                    for p in patterns
                ],
                "dependencies": [
                    {
                        "id": d.id,
                        "source_module": d.source_module,
                        "target_module": d.target_module,
                        "dependency_type": d.dependency_type,
                        "strength": d.strength,
                        "created_at": d.created_at.isoformat()
                        if d.created_at
                        else None,
                    }
                    for d in dependencies
                ],
            }
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error fetching software models: {e}")
        raise DatabaseError(
            message=f"Failed to fetch software models: {str(e)}",
            user_message="Unable to retrieve software architecture models.",
            recovery_action="Please try again. If the problem persists, contact support.",
        )


# ============================================================================
# STRATEGIC PLANNING ROUTES
# ============================================================================


@enterprise_bp.route("/strategic")
@login_required
def strategic_planning_dashboard():
    """Strategic Planning Dashboard"""
    try:
        # Get strategic metrics with ArchiMate fallback for empty tables
        from app.services.gap_register_service import count_gaps

        gap_count = count_gaps(current_user.organization_id)
        if gap_count == 0:
            gap_count = ArchiMateElement.query.filter(
                ArchiMateElement.type.in_(["Gap", "GAP"])
            ).count()

        plateau_count = 0
        try:
            plateau_count = Plateau.query.count()
        except Exception as exc:
            logger.debug(f"Plateau query fallback: {exc}")
        if plateau_count == 0:
            plateau_count = ArchiMateElement.query.filter(
                ArchiMateElement.type.in_(["Plateau", "PLATEAU"])
            ).count()

        workpackage_count = 0
        try:
            workpackage_count = work_package_service.query_for(current_organization_id()).count()
        except Exception as exc:
            logger.debug(f"WorkPackage query fallback: {exc}")
        if workpackage_count == 0:
            workpackage_count = ArchiMateElement.query.filter(
                ArchiMateElement.type.in_(["WorkPackage", "WORK_PACKAGE"])
            ).count()

        return render_template(
            "enterprise/strategic_planning_dashboard.html",
            gap_count=gap_count,
            plateau_count=plateau_count,
            workpackage_count=workpackage_count,
        )
    except SQLAlchemyError as e:
        logger.error(
            f"Database error loading strategic planning dashboard: {e}",
            exc_info=True,
        )
        raise DatabaseError(
            message=f"Failed to load strategic dashboard: {str(e)}",
            user_message="Unable to load the strategic planning dashboard.",
            recovery_action="Refresh the page. If the problem persists, contact support.",
        )


@enterprise_bp.route("/strategic/capability-health")
@login_required
def capability_health():
    """Capability Health Assessment — paginated to keep response times under 3 s."""
    try:
        page = safe_int_arg('page', 1, minimum=1)
        per_page = safe_int_arg('per_page', 50, minimum=1, maximum=500)
        # Guard against absurdly large page sizes
        per_page = min(per_page, 200)

        pagination = BusinessCapability.query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        capabilities = pagination.items

        return render_template(
            "enterprise/capability_health.html",
            capabilities=capabilities,
            pagination=pagination,
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error loading capability health: {e}")
        raise DatabaseError(
            message=f"Failed to load capability health data: {str(e)}",
            user_message="Unable to load capability health assessment.",
            recovery_action="Return to the dashboard and try again.",
        )


@enterprise_bp.route("/strategic/investment-matrix")
@login_required
def investment_matrix():
    """Investment Matrix"""
    try:
        capabilities = BusinessCapability.query.limit(500).all()
        return render_template(
            "enterprise/investment_matrix.html", capabilities=capabilities
        )
    except Exception as e:
        current_app.logger.error(f"Error loading investment matrix: {e}")
        flash("Error loading investment matrix", "error")
        return redirect(url_for("enterprise.strategic_planning_dashboard"))


@enterprise_bp.route("/strategic/risk-assessment")
@login_required
def risk_assessment():
    """Risk Assessment"""
    try:
        gaps = Gap.query.filter(Gap.gap_kind != "plateau_transition").limit(500).all()

        return render_template("enterprise/risk_assessment.html", gaps=gaps)
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error loading risk assessment: {e}")
        raise DatabaseError(
            message=f"Failed to load risk assessment: {str(e)}",
            user_message="Unable to load risk assessment data.",
            recovery_action="Return to the dashboard and try again.",
        )


@enterprise_bp.route("/strategic/technology-roadmap")
@login_required
def technology_roadmap():
    """Technology Roadmap"""
    try:
        workpackages = WorkPackage.query.limit(500).all()

        return render_template(
            "enterprise/technology_roadmap.html", workpackages=workpackages
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error loading technology roadmap: {e}")
        raise DatabaseError(
            message=f"Failed to load technology roadmap: {str(e)}",
            user_message="Unable to load technology roadmap.",
            recovery_action="Return to the dashboard and try again.",
        )


# ============================================================================
# IMPLEMENTATION PLANNING ROUTES
# ============================================================================


# implementation_planning_dashboard removed — empty shell page, frozen sidebar link


@enterprise_bp.route("/implementation/work-packages")
@login_required
def work_packages():
    """Work Packages Management.

    The page is an Alpine table (`workPackagesTable()` in
    static/js/enterprise/work_packages_table.js) that loads rows from
    `/enterprise/api/work-packages` below. It never read a server-rendered
    `workpackages` variable, so the hardcoded `workpackages=[]` that used to
    be passed here was dead — and read as a permanently-empty page (S-11).
    """
    return render_template("enterprise/work_packages.html")


@enterprise_bp.route("/api/work-packages", methods=["GET"])
@login_required
def api_list_work_packages():
    """Paginated work packages API."""
    page = safe_int_arg('page', 1, minimum=1)
    per_page = min(safe_int_arg('per_page', 25, minimum=1, maximum=500), 100)
    search = request.args.get("q") or request.args.get("search", "")
    status_filter = request.args.get("status", "")
    sort_by = request.args.get("sort", "created_at")
    sort_dir = request.args.get("dir", "desc")

    ALLOWED_SORT = {"id", "name", "status", "priority", "target_date", "created_at", "percent_complete"}
    if sort_by not in ALLOWED_SORT:
        sort_by = "created_at"

    UWP = UnifiedWorkPackage
    q = work_package_service.query_for(current_organization_id())
    if search:
        q = q.filter(or_(
            UWP.name.ilike(f"%{search}%"),
            UWP.description.ilike(f"%{search}%"),
        ))
    if status_filter:
        q = q.filter(UWP.status == status_filter)

    sort_col = getattr(UWP, _WP_SORT_COLUMNS.get(sort_by, "created_at"), UWP.created_at)
    q = q.order_by(sort_col.asc() if sort_dir == "asc" else sort_col.desc(), UWP.id.asc())

    paginated = q.paginate(page=page, per_page=per_page, error_out=False)
    offset = (page - 1) * per_page

    items = []
    for idx, wp in enumerate(paginated.items):
        items.append({
            "id": wp.id,
            "row_number": offset + idx + 1,
            "name": wp.name or "",
            "summary": wp.summary,
            "status": wp.status or "Planned",
            "priority": wp.priority or "Normal",
            "percent_complete": wp.progress_percentage or 0,
            "target_date": str(wp.end_date.date()) if wp.end_date else None,
            "togaf_phase": wp.togaf_phase or "",
            "created_at": str(wp.created_at) if wp.created_at else None,
        })

    return jsonify({
        "work_packages": items,
        "total": paginated.total,
        "page": page,
        "pages": paginated.pages,
        "per_page": per_page,
    })


# The table's sort keys, as columns of the one work package store.
_WP_SORT_COLUMNS = {
    "id": "id", "name": "name", "status": "status", "priority": "priority",
    "target_date": "end_date", "created_at": "created_at",
    "percent_complete": "progress_percentage",
}


def _milestones_for(wp):
    """Delivery milestones for a work package, via the projects that deliver it.

    Projects still key on the retired work_packages table, so the unified row
    is resolved to that id first. Returns [] rather than raising if the project
    models are unavailable -- the Gantt degrades to bars without markers, which
    is honest; it must never invent a milestone.
    """
    try:
        legacy = work_package_service.legacy_id(wp, "work_packages")
        legacy_wp = WorkPackage.query.filter_by(id=legacy).first() if legacy else None
        out = []
        for project in getattr(legacy_wp, "projects", None) or []:
            for ms in project.milestones:
                target = ms.actual_date or ms.target_date
                if not target:
                    continue
                out.append({
                    "id": str(ms.id),
                    "name": ms.name,
                    "date": target.isoformat(),
                    "status": ms.status,
                    "project": project.name,
                })
        return sorted(out, key=lambda m: m["date"])
    except Exception:
        logger.exception("Could not resolve milestones for work package %s", getattr(wp, "id", "?"))
        return []


@enterprise_bp.route("/api/work-packages/gantt", methods=["GET"])
@login_required
def api_work_packages_gantt():
    """Work packages in the shape the Gantt component consumes.

    Separate from api_list_work_packages because that endpoint is a paginated
    table feed (row_number, summary, percent_complete) while the Gantt needs a
    full unpaginated timeline (start/end dates, progress, cost, owner).
    Reads the one work package store, this organisation's rows only.
    """
    try:
        UWP = UnifiedWorkPackage
        q = work_package_service.query_for(current_organization_id())
        status_filter = request.args.get("status", "")
        if status_filter:
            q = q.filter(UWP.status == status_filter)

        # Undated packages cannot be placed on a timeline; excluding them keeps the
        # chart honest rather than inventing a start date. They remain visible in
        # the table feed above.
        q = q.filter(UWP.start_date.isnot(None))
        work_packages = q.order_by(UWP.start_date.asc(), UWP.id.asc()).all()

        items = []
        for wp in work_packages:
            items.append({
                "id": wp.id,
                "name": wp.name or "",
                "description": wp.description or "",
                "assigned_to": wp.assigned_to,
                "business_capability": wp.business_capability,
                "status": wp.status or "planned",
                "start_date": wp.start_date.isoformat() if wp.start_date else None,
                "end_date": wp.end_date.isoformat() if wp.end_date else None,
                "progress_percentage": wp.progress_percentage or 0,
                "estimated_cost": wp.estimated_cost,
                "layer": wp.layer or wp.element_type or "implementation",
                "milestones": _milestones_for(wp),
            })

        return jsonify({"work_packages": items, "total": len(items)})
    except Exception:
        logger.exception("Failed to build Gantt work-package feed")
        return jsonify({"error": "Failed to load work packages"}), 500


# CSRF: Protected via X-CSRFToken header sent by Platform.fetch
# Work-package fields whose DB columns are Date / Integer / Float. The Create and
# Edit forms serialise empty inputs as "" (empty string), which Postgres rejects
# with `invalid input syntax for type date: ""` (and for integer/float) — that was
# the 500 on "New Work Package -> Create" with only a Name filled in. Normalise ""
# (and whitespace) to NULL, and parse the values that are present.
_WP_DATE_FIELDS = {"start_date", "target_date", "completed_date"}
_WP_INT_FIELDS = {
    "estimated_effort_hours", "actual_effort_hours", "percent_complete", "level",
    "sequence_order", "plateau_id", "architecture_id", "owner_id", "capability_id",
    "parent_id",
}
_WP_FLOAT_FIELDS = {"estimated_cost", "actual_cost"}


def _normalise_wp_payload(data):
    """Coerce empty strings to None and parse date/number fields in-place.

    Raises ValueError with a user-facing message on a malformed value so the
    caller can return a 400 rather than letting it 500 at flush time.
    """
    from datetime import datetime as _dt

    for key in list(data.keys()):
        value = data[key]
        if isinstance(value, str) and value.strip() == "":
            data[key] = None
            value = None
        if value is None:
            continue
        if key in _WP_DATE_FIELDS and isinstance(value, str):
            try:
                data[key] = _dt.strptime(value.strip(), "%Y-%m-%d").date()
            except ValueError:
                raise ValueError(f"{key} must be a valid date (YYYY-MM-DD)")
        elif key in _WP_INT_FIELDS and isinstance(value, str):
            try:
                data[key] = int(value.strip())
            except ValueError:
                raise ValueError(f"{key} must be a whole number")
        elif key in _WP_FLOAT_FIELDS and isinstance(value, str):
            try:
                data[key] = float(value.strip())
            except ValueError:
                raise ValueError(f"{key} must be a number")
    return data


def _wp_error(exc):
    if isinstance(exc, work_package_service.WorkPackageNotFound):
        return api_error(str(exc), "NOT_FOUND", 404)
    return api_error(str(exc), "INVALID_FIELD")


def _audit_wp(action, wp_id, name, status=None, severity=AuditEventSeverity.MEDIUM):
    try:
        details = {"name": name,
                   "user_id": current_user.id if current_user.is_authenticated else None}
        if status is not None:
            details["status"] = status
        audit_logger.log_event(
            AuditEventType.DATA_MODIFICATION,
            severity,
            action,
            resource_type="work_package",
            resource_id=str(wp_id),
            details=details,
            compliance_flags=["SOC2"],
        )
    except Exception as _exc:
        logger.warning("audit log failed for %s work package %s: %s", action, wp_id, _exc)


@enterprise_bp.route("/api/work-packages", methods=["POST"])
@login_required
def api_create_work_package():
    """Create a new work package in the one store. PROD-008"""
    data = request.get_json(force=True) or {}

    if not data.get("name", "").strip():
        return api_error("name is required", "MISSING_NAME")

    try:
        _normalise_wp_payload(data)
        wp = work_package_service.create_work_package(
            organization_id=current_organization_id(),
            user_id=current_user.id if current_user.is_authenticated else None,
            **work_package_service.from_form(data),
        )
    except (ValueError, work_package_service.WorkPackageError) as exc:
        db.session.rollback()
        return _wp_error(exc) if isinstance(exc, work_package_service.WorkPackageError) \
            else api_error(str(exc), "INVALID_FIELD")
    db.session.commit()
    _audit_wp("create", wp.id, wp.name, wp.status)
    return jsonify({"status": "created", "id": wp.id}), 201


# CSRF: Protected via X-CSRFToken header sent by Platform.fetch
@enterprise_bp.route("/api/work-packages/<int:wp_id>", methods=["PATCH"])
@login_required
def api_update_work_package(wp_id):
    """Update a work package. PROD-008"""
    data = request.get_json(force=True) or {}

    try:
        _normalise_wp_payload(data)
        wp = work_package_service.update_work_package(
            wp_id,
            organization_id=current_organization_id(),
            user_id=current_user.id if current_user.is_authenticated else None,
            **work_package_service.from_form(data),
        )
    except (ValueError, work_package_service.WorkPackageError) as exc:
        db.session.rollback()
        return _wp_error(exc) if isinstance(exc, work_package_service.WorkPackageError) \
            else api_error(str(exc), "INVALID_FIELD")
    db.session.commit()
    _audit_wp("update", wp_id, wp.name, wp.status)
    return jsonify({"status": "ok", "id": wp.id})


@enterprise_bp.route("/api/work-packages/bulk", methods=["DELETE"])
@login_required
def api_bulk_delete_work_packages():
    """Bulk delete work packages (this organisation's own only)."""
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids or not isinstance(ids, list):
        return api_error("ids list required", "MISSING_IDS")
    org_id = current_organization_id()
    deleted = 0
    for raw_id in ids:
        try:
            wp = work_package_service.get_work_package(int(raw_id), org_id)
        except (TypeError, ValueError):
            continue
        if wp is None:
            continue
        wp_id, wp_name = wp.id, wp.name
        work_package_service.delete_work_package(wp_id, organization_id=org_id)
        deleted += 1
        _audit_wp("delete", wp_id, wp_name, severity=AuditEventSeverity.HIGH)
    db.session.commit()
    return jsonify({"deleted": deleted})


@enterprise_bp.route("/api/work-packages/<int:wp_id>", methods=["DELETE"])
@login_required
def api_delete_work_package(wp_id):
    """Delete one work package. The 2 Sep 2026 audit (F-06) found rows had no
    delete at all -- only the bulk path existed. Mirrors the bulk handler:
    another organisation's id is answered as a missing one, and the deletion
    is audit-logged with the same SOC2 flag."""
    org_id = current_organization_id()
    wp = work_package_service.get_work_package(wp_id, org_id)
    if wp is None:
        return api_error("Work package not found", "NOT_FOUND", 404)
    wp_name = wp.name
    work_package_service.delete_work_package(wp_id, organization_id=org_id)
    _audit_wp("delete", wp_id, wp_name, severity=AuditEventSeverity.HIGH)
    db.session.commit()
    return jsonify({"deleted": 1, "id": wp_id})


@enterprise_bp.route("/implementation/plateaus")
@login_required
def plateaus():
    """Implementation Plateaus"""
    try:
        plateaus = Plateau.query.limit(500).all()
        return render_template("enterprise/plateaus.html", plateaus=plateaus)
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error loading plateaus: {e}")
        raise DatabaseError(
            message=f"Failed to load plateaus: {str(e)}",
            user_message="Unable to load implementation plateaus.",
            recovery_action="Return to the dashboard and try again.",
        )


_GAP_SORT_COLUMNS = {
    "name": Gap.name,
    "gap_type": Gap.gap_type,
    "priority": Gap.priority,
    "resolution_status": Gap.resolution_status,
    "impact": Gap.impact,
}


@enterprise_bp.route("/implementation/gap-analysis")
@login_required
def gap_analysis():
    """Gap Analysis — the enterprise Gap register (canonical, S-11).

    `adm_kanban_view.gap_analysis` is a *different* view over different rows
    (KanbanCard rows with arch_element_type='Gap', plus the cards that close
    them) and is reached from the ADM Kanban board itself, so it is not
    redirected here. This one lists the ArchiMate Implementation & Migration
    `Gap` model and is the one linked from navigation.
    """
    # T-14 (2 Sep 2026 audit): one of six tables with no sort at all. Same
    # server-side, query-param pattern as /risks/ — the columns are real,
    # indexed String fields, so this is a cheap, safe sort.
    sort_key = request.args.get("sort", "name")
    direction = request.args.get("dir", "asc")
    column = _GAP_SORT_COLUMNS.get(sort_key, Gap.name)
    order = column.desc() if direction == "desc" else column.asc()
    try:
        from app.services.gap_register_service import count_gaps

        gaps = (
            Gap.query.filter(Gap.gap_kind != "plateau_transition")
            .order_by(order, Gap.id)
            .limit(500)
            .all()
        )
        total_gap_count = count_gaps(current_user.organization_id)

        return render_template(
            "enterprise/gap_analysis.html", gaps=gaps,
            total_gap_count=total_gap_count,
            current_sort=sort_key if sort_key in _GAP_SORT_COLUMNS else "name",
            current_dir=direction if direction in ("asc", "desc") else "asc",
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error loading gap analysis: {e}")
        raise DatabaseError(
            message=f"Failed to load gap analysis: {str(e)}",
            user_message="Unable to load gap analysis.",
            recovery_action="Return to the dashboard and try again.",
        )


# ============================================================================
# ANALYSIS & AI TOOLS ROUTES
# ============================================================================


@enterprise_bp.route("/analysis/ai-architecture")
@login_required
def ai_architecture_analysis():
    """AI Architecture Analysis — displays architectural intelligence dashboard"""
    try:
        from app.models.ai_recommendations import AIRecommendation
        from app.models.implementation_migration import Gap
        from app.models.application_portfolio import ApplicationComponent

        # Fetch recent AI recommendations
        ai_recommendations = (
            AIRecommendation.query.order_by(AIRecommendation.created_at.desc())
            .limit(20)
            .all()
        )

        # Count gaps by severity
        _cap_gaps = Gap.query.filter(Gap.gap_kind != "plateau_transition")
        gaps_by_severity = {
            "critical": _cap_gaps.filter_by(severity="critical").count(),
            "high": _cap_gaps.filter_by(severity="high").count(),
            "medium": _cap_gaps.filter_by(severity="medium").count(),
            "low": _cap_gaps.filter_by(severity="low").count(),
        }

        # Fetch applications needing review
        apps_needing_review = (
            ApplicationComponent.query.filter_by(lifecycle_status="under_review")
            .order_by(ApplicationComponent.name)
            .limit(10)
            .all()
        )

        # Total counts
        from app.services.gap_register_service import count_gaps

        total_recommendations = AIRecommendation.query.count()
        total_gaps = count_gaps(current_user.organization_id)

        return render_template(
            "enterprise/ai_architecture_analysis.html",
            ai_recommendations=ai_recommendations,
            gaps_by_severity=gaps_by_severity,
            apps_needing_review=apps_needing_review,
            total_recommendations=total_recommendations,
            total_gaps=total_gaps,
        )
    except Exception as e:
        current_app.logger.error(f"Error loading AI architecture analysis: {e}")
        raise DatabaseError(
            message=f"Failed to load AI architecture analysis: {str(e)}",
            user_message="Unable to load AI architecture analysis.",
            recovery_action="Return to the dashboard and try again.",
        )


# gap_discovery removed — empty shell page, frozen sidebar link


@enterprise_bp.route("/analysis/impact-analysis")
@login_required
def impact_analysis():
    """Impact Analysis — assess downstream impact of architecture changes"""
    try:
        from app.models.traceability import ImpactAnalysisResult
        from app.models.application_portfolio import ApplicationComponent
        from app.models.business_capabilities import BusinessCapability
        from sqlalchemy import func

        # Get filter parameters from query string
        selected_element_type = request.args.get(
            "element_type"
        )  # 'application' or 'capability'
        selected_element_id = request.args.get("element_id", type=int)

        # Fetch all applications and capabilities for dropdowns (id + name only)
        applications = (
            db.session.query(ApplicationComponent.id, ApplicationComponent.name)
            .order_by(ApplicationComponent.name)
            .all()
        )

        capabilities = (
            db.session.query(BusinessCapability.id, BusinessCapability.name)
            .order_by(BusinessCapability.name)
            .all()
        )

        # Impact analyses have no organisation column: they belong to the
        # organisation of the user who ran them, so every read below starts
        # from the organisation-scoped query.
        organization_id = current_user.organization_id

        # Fetch recent impact analyses (last 20)
        recent_analyses = (
            ImpactAnalysisResult.for_organization(organization_id)
            .order_by(ImpactAnalysisResult.created_at.desc())
            .limit(20)
            .all()
        )

        # Filter analyses if element type and id provided
        filtered_analyses = None
        if selected_element_type and selected_element_id:
            filtered_analyses = (
                ImpactAnalysisResult.for_organization(organization_id)
                .filter_by(
                    trigger_element_type=selected_element_type,
                    trigger_element_id=selected_element_id,
                )
                .order_by(ImpactAnalysisResult.created_at.desc())
                .all()
            )

        # Calculate summary metrics
        total_analyses = ImpactAnalysisResult.for_organization(organization_id).count()
        critical_count = (
            ImpactAnalysisResult.for_organization(organization_id)
            .filter_by(overall_severity="critical")
            .count()
        )
        high_count = (
            ImpactAnalysisResult.for_organization(organization_id)
            .filter_by(overall_severity="high")
            .count()
        )

        # Calculate average affected applications
        avg_result = (
            ImpactAnalysisResult.for_organization(organization_id)
            .with_entities(func.avg(ImpactAnalysisResult.affected_applications_count))
            .scalar()
        )
        avg_affected_applications = round(avg_result, 1) if avg_result else 0

        return render_template(
            "enterprise/impact_analysis.html",
            recent_analyses=recent_analyses,
            applications=applications,
            capabilities=capabilities,
            selected_element_type=selected_element_type,
            selected_element_id=selected_element_id,
            filtered_analyses=filtered_analyses,
            total_analyses=total_analyses,
            critical_count=critical_count,
            high_count=high_count,
            avg_affected_applications=avg_affected_applications,
        )
    except Exception as e:
        current_app.logger.error(f"Error loading impact analysis: {e}")
        raise DatabaseError(
            message=f"Failed to load impact analysis: {str(e)}",
            user_message="Unable to load impact analysis.",
            recovery_action="Return to the dashboard and try again.",
        )


@enterprise_bp.route("/analysis/process-optimization")
@login_required
def process_optimization():
    """Process Optimization — identify automation and simplification opportunities"""
    try:
        from app.models.process_data import BusinessProcess
        from app.models.industry_apqc import IndustryProcessRecommendation
        from sqlalchemy import func

        # Total process count
        total_processes = BusinessProcess.query.count()

        # Count processes by status
        processes_by_status = {}
        status_counts = (
            db.session.query(BusinessProcess.status, func.count(BusinessProcess.id))
            .group_by(BusinessProcess.status)
            .all()
        )

        for status, count in status_counts:
            if status:
                processes_by_status[status] = count

        # Average automation percentage
        avg_result = (
            db.session.query(func.avg(BusinessProcess.automation_percentage))
            .filter(BusinessProcess.automation_percentage.isnot(None))
            .scalar()
        )
        avg_automation = round(avg_result, 1) if avg_result else 0.0

        # Low automation processes (automation < 30%)
        low_automation_processes = (
            BusinessProcess.query.filter(BusinessProcess.automation_percentage < 30)
            .order_by(BusinessProcess.automation_percentage.asc())
            .limit(15)
            .all()
        )

        # Fetch optimization recommendations (last 20)
        optimization_recommendations = (
            IndustryProcessRecommendation.query.order_by(
                IndustryProcessRecommendation.created_at.desc()
            )
            .limit(20)
            .all()
        )

        # High complexity processes (those with many subprocesses)
        # Query for processes that have more than 3 child processes
        from sqlalchemy.orm import aliased

        Child = aliased(BusinessProcess)
        high_complexity_processes = (
            db.session.query(BusinessProcess)
            .join(
                Child,
                Child.parent_process_id == BusinessProcess.id,
                isouter=True,
            )
            .group_by(BusinessProcess.id)
            .having(func.count(Child.id) > 3)
            .all()
        )

        # Count low automation processes
        low_automation_count = BusinessProcess.query.filter(
            BusinessProcess.automation_percentage < 30
        ).count()

        # Count open recommendations
        open_recommendations_count = IndustryProcessRecommendation.query.filter_by(
            status="pending"
        ).count()

        return render_template(
            "enterprise/process_optimization.html",
            total_processes=total_processes,
            processes_by_status=processes_by_status,
            avg_automation=avg_automation,
            low_automation_processes=low_automation_processes,
            optimization_recommendations=optimization_recommendations,
            high_complexity_processes=high_complexity_processes,
            low_automation_count=low_automation_count,
            open_recommendations_count=open_recommendations_count,
        )
    except Exception as e:
        current_app.logger.error(f"Error loading process optimization: {e}")
        raise DatabaseError(
            message=f"Failed to load process optimization: {str(e)}",
            user_message="Unable to load process optimization tool.",
            recovery_action="Return to the dashboard and try again.",
        )


# ============================================================================
# MAIN ENTERPRISE DASHBOARD
# ============================================================================


@enterprise_bp.route("/")
@login_required
def enterprise_dashboard():
    """Main Enterprise Architecture Dashboard"""
    try:
        # Get overall metrics
        data_models_count = (
            ConceptualDataModel.query.count()
            + LogicalDataModel.query.count()
            + PhysicalDataModel.query.count()
        )
        solutions_count = Solution.query.count()
        software_modules_count = SoftwareModule.query.count()

        # Gaps with ArchiMate fallback for empty tables
        from app.services.gap_register_service import count_gaps

        gaps_count = count_gaps(current_user.organization_id)
        if gaps_count == 0:
            gaps_count = ArchiMateElement.query.filter(
                ArchiMateElement.type.in_(["Gap", "GAP"])
            ).count()

        return render_template(
            "enterprise/enterprise_dashboard.html",
            data_models_count=data_models_count,
            solutions_count=solutions_count,
            software_modules_count=software_modules_count,
            gaps_count=gaps_count,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Enterprise dashboard stats error: {e}", exc_info=True)
        flash("Error loading dashboard", "error")
        # None, not 0. "0 gaps" on this page is read as an all-clear.
        return render_template(
            "enterprise/enterprise_dashboard.html",
            data_models_count=None,
            solutions_count=None,
            software_modules_count=None,
            gaps_count=None,
            load_error="Enterprise dashboard counts could not be read.",
        )
