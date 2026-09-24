"""
DEPRECATED: This file is migrated to app/modules/solutions_strategic/.
Registration is now centralized via app.modules.solutions_strategic.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Solution Design Workflow Routes

Complete CRUD operations for Solutions with ArchiMate 3.2 elements and APQC processes.
Implements the Design Solution workflow as a 4 - step modal with full database integration.

# mass-deletion-ok — duplicate route handlers were removed: lifecycle CRUD, notifications,
# and AI routes were already registered via solution_phase_routes.py / solution_link_routes.py /
# solution_ai_routes.py.  This file now contains only the core solution routes.

Routes:
- GET  /solutions/ - List all solutions
- GET  /solutions/create - Show creation modal
- POST /solutions/ - Create new solution
- GET  /solutions/<id> - View solution details
- GET  /solutions/<id>/edit - Edit solution
- POST /solutions/<id> - Update solution
- POST /solutions/<id>/delete - Delete solution
- GET  /api/solutions - JSON API for solutions
"""

from app.services.archimate_backbone import sync_archimate_element
import json
import logging
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload  # dead-code-ok
from werkzeug.exceptions import HTTPException

from app import csrf, db
from app.decorators import audit_log, require_roles
from app.models.application_portfolio import ApplicationComponent
from app.models.apqc_process import APQCProcess, ProcessApplicationMapping
from app.models.solution_sad_models import SolutionADRDirect, SolutionAPQCProcess
from app.models.solution_governance import SolutionNotification
from app.models.solution_models import Solution
from app.utils.route_guards import require_entity
from app.services.feature_flag_service import FeatureFlagService
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

solution_design_bp = Blueprint("solution_design", __name__, url_prefix="/solutions")


def _check_solution_access(solution, user=None) -> bool:
    """Return True if `user` (default: current_user) may access this solution.

    Access is granted to:
    - The creator (solution.created_by_id)
    - Admins (user.is_admin)
    - Named stakeholders (owner, sponsor, tech lead) matched by email

    `user` is accepted explicitly so this check can run outside an HTTP
    request (e.g. from the AI-chat tool executor), where flask_login's
    `current_user` proxy has nothing to resolve against.
    """
    user = user if user is not None else current_user
    if solution.created_by_id == user.id:
        return True
    is_admin = getattr(user, "is_admin", False)
    if (is_admin() if callable(is_admin) else bool(is_admin)):
        return True
    if getattr(user, "is_platform_admin", False):
        return True
    _stakeholder_emails = [
        solution.solution_owner,
        solution.business_sponsor,
        solution.technical_lead,
    ]
    return any(
        f and user.email and f.strip().lower() == user.email.strip().lower()
        for f in _stakeholder_emails
    )


def _capability_gap_severity(capability) -> str | None:
    """Derive a simple maturity gap severity for detail rendering."""
    current = getattr(capability, "current_maturity_level", None)
    target = getattr(capability, "target_maturity_level", None)
    if current is None or target is None:
        return None

    gap = max((target or 0) - (current or 0), 0)
    if gap >= 3:
        return "critical"
    if gap == 2:
        return "high"
    if gap == 1:
        return "medium"
    return "none"


def _get_solution_capabilities_payload(solution: Solution) -> list[dict]:
    """Return capabilities for a solution from direct mappings or linked apps."""
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capability import BusinessCapability
    from app.models.solution_architect_models import SolutionProblemDefinition
    from app.models.solution_models import SolutionCapabilityMapping

    capabilities: list[dict] = []

    # Dual-path merge: collect mappings from BOTH paths, then deduplicate.
    # Path 1: problem_id (analysis session)
    mappings = []
    if solution.analysis_session_id:
        problem_definition = SolutionProblemDefinition.query.filter_by(
            session_id=solution.analysis_session_id
        ).first()
        if problem_definition:
            mappings.extend(
                SolutionCapabilityMapping.query.filter_by(
                    problem_id=problem_definition.id
                ).all()
            )

    # Path 2: direct solution_id (always check — covers chat-linked capabilities)
    direct = SolutionCapabilityMapping.query.filter_by(
        solution_id=solution.id, problem_id=None
    ).all()
    mappings.extend(direct)

    # Deduplicate by capability_id (first occurrence wins)
    seen: set[int] = set()
    unique_mappings = []
    for m in mappings:
        if m.capability_id and m.capability_id not in seen:
            seen.add(m.capability_id)
            unique_mappings.append(m)
    mappings = unique_mappings

    if mappings:
        capability_ids = [m.capability_id for m in mappings]
        capabilities_by_id = (
            {
                capability.id: capability
                for capability in BusinessCapability.query.filter(
                    BusinessCapability.id.in_(capability_ids)
                ).all()
            }
            if capability_ids
            else {}
        )

        for mapping in mappings:
            capability = capabilities_by_id.get(mapping.capability_id)
            if not capability:
                continue
            capabilities.append(
                {
                    "id": capability.id,
                    "mapping_id": mapping.id,
                    "capability_id": capability.id,
                    "capability_name": capability.name,
                    "name": capability.name,
                    "description": capability.description,
                    "domain": capability.business_domain or "",
                    "business_domain": capability.business_domain or "",
                    "category": mapping.support_level or "required",
                    "support_level": mapping.support_level or "required",
                    "priority": mapping.priority,
                    "notes": mapping.notes,
                    "rationale": mapping.rationale,
                    "maturity_current": getattr(
                        capability, "current_maturity_level", None
                    ),
                    "maturity_target": getattr(
                        capability, "target_maturity_level", None
                    ),
                    "gap_severity": _capability_gap_severity(capability),
                }
            )

    if capabilities:
        return capabilities
    solution_app_table = db.metadata.tables.get("solution_applications")
    if solution_app_table is None:
        return []

    rows = (
        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        db.session.query(BusinessCapability, ApplicationCapabilityMapping)
        .join(
            ApplicationCapabilityMapping,
            ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
        )
        .join(
            solution_app_table,
            solution_app_table.c.application_component_id
            == ApplicationCapabilityMapping.application_component_id,
        )
        .filter(solution_app_table.c.solution_id == solution.id)
        .order_by(BusinessCapability.name)
        .all()
    )

    deduped: dict[int, dict] = {}
    for capability, mapping in rows:
        existing = deduped.get(capability.id)
        coverage = mapping.coverage_percentage or 0
        candidate = {
            "id": capability.id,
            "mapping_id": None,
            "capability_id": capability.id,
            "capability_name": capability.name,
            "name": capability.name,
            "description": capability.description,
            "domain": capability.business_domain or "",
            "business_domain": capability.business_domain or "",
            "category": mapping.support_level or "partial",
            "support_level": mapping.support_level or "partial",
            "coverage_percentage": coverage,
            "priority": None,
            "notes": mapping.gap_description,
            "rationale": mapping.relationship_type,
            "maturity_current": getattr(capability, "current_maturity_level", None),
            "maturity_target": getattr(capability, "target_maturity_level", None),
            "gap_severity": _capability_gap_severity(capability),
        }
        if existing is None or coverage > (existing.get("coverage_percentage") or 0):
            deduped[capability.id] = candidate

    return list(deduped.values())


def _build_solution_worklist_summaries(solutions: list[Solution]) -> tuple[dict[int, dict], dict[str, int]]:
    """Return architect-facing worklist summaries for the provided solutions."""
    if not solutions:
        return {}, {
            "needs_setup": 0,
            "in_design": 0,
            "needs_attention": 0,
            "ready_for_review": 0,
        }

    from app.models.solution_architect_models import (
        SolutionConstraint,
        SolutionDriver,
        SolutionGoal,
        SolutionProblemDefinition,
        SolutionRecommendation,
        SolutionRequirement,
    )
    from app.models.solution_lifecycle_models import SolutionMetric, SolutionPlateau, SolutionRisk
    from app.models.solution_models import (
        SolutionArchiMateElement,
        SolutionCapabilityMapping,
        solution_applications,
        solution_vendor_products,
    )

    solution_ids = [solution.id for solution in solutions]
    analysis_session_ids = [solution.analysis_session_id for solution in solutions if solution.analysis_session_id]

    def _count_rows(query, key_index=0, value_index=1):
        return {
            row[key_index]: int(row[value_index] or 0)
            for row in query.all()
        }

    counts = {
        "apps": _count_rows(
            db.session.query(
                solution_applications.c.solution_id,
                func.count(solution_applications.c.application_component_id),
            )
            .filter(solution_applications.c.solution_id.in_(solution_ids))
            .group_by(solution_applications.c.solution_id)
        ),
        "vendors": _count_rows(
            db.session.query(
                solution_vendor_products.c.solution_id,
                func.count(solution_vendor_products.c.vendor_product_id),
            )
            .filter(solution_vendor_products.c.solution_id.in_(solution_ids))
            .group_by(solution_vendor_products.c.solution_id)
        ),
        "archimate": _count_rows(
            db.session.query(
                SolutionArchiMateElement.solution_id,
                func.count(SolutionArchiMateElement.id),
            )
            .filter(SolutionArchiMateElement.solution_id.in_(solution_ids))
            .group_by(SolutionArchiMateElement.solution_id)
        ),
        "risks": _count_rows(
            db.session.query(SolutionRisk.solution_id, func.count(SolutionRisk.id))
            .filter(SolutionRisk.solution_id.in_(solution_ids))
            .group_by(SolutionRisk.solution_id)
        ),
        "adrs": _count_rows(
            db.session.query(SolutionADRDirect.solution_id, func.count(SolutionADRDirect.id))
            .filter(SolutionADRDirect.solution_id.in_(solution_ids))
            .group_by(SolutionADRDirect.solution_id)
        ),
        "apqc": _count_rows(
            db.session.query(SolutionAPQCProcess.solution_id, func.count(SolutionAPQCProcess.id))
            .filter(SolutionAPQCProcess.solution_id.in_(solution_ids))
            .group_by(SolutionAPQCProcess.solution_id)
        ),
        "capabilities_direct": _count_rows(
            db.session.query(SolutionCapabilityMapping.solution_id, func.count(SolutionCapabilityMapping.id))
            .filter(
                SolutionCapabilityMapping.solution_id.in_(solution_ids),
                SolutionCapabilityMapping.solution_id.isnot(None),
            )
            .group_by(SolutionCapabilityMapping.solution_id)
        ),
    }

    problem_ids_by_solution_id: dict[int, int] = {}
    if analysis_session_ids:
        problem_defs = (
            SolutionProblemDefinition.query
            .filter(SolutionProblemDefinition.session_id.in_(analysis_session_ids))
            .all()
        )
        session_to_problem = {
            problem_def.session_id: problem_def.id
            for problem_def in problem_defs
        }
        for solution in solutions:
            if solution.analysis_session_id and solution.analysis_session_id in session_to_problem:
                problem_ids_by_solution_id[solution.id] = session_to_problem[solution.analysis_session_id]

    problem_ids = list(problem_ids_by_solution_id.values())
    capabilities_by_problem: dict[int, int] = {}
    drivers_by_problem: dict[int, int] = {}
    goals_by_problem: dict[int, int] = {}

    if problem_ids:
        capabilities_by_problem = _count_rows(
            db.session.query(SolutionCapabilityMapping.problem_id, func.count(SolutionCapabilityMapping.id))
            .filter(
                SolutionCapabilityMapping.problem_id.in_(problem_ids),
                SolutionCapabilityMapping.problem_id.isnot(None),
            )
            .group_by(SolutionCapabilityMapping.problem_id)
        )
        drivers_by_problem = _count_rows(
            db.session.query(SolutionDriver.problem_id, func.count(SolutionDriver.id))
            .filter(SolutionDriver.problem_id.in_(problem_ids))
            .group_by(SolutionDriver.problem_id)
        )
        goals_by_problem = _count_rows(
            db.session.query(SolutionGoal.problem_id, func.count(SolutionGoal.id))
            .filter(SolutionGoal.problem_id.in_(problem_ids))
            .group_by(SolutionGoal.problem_id)
        )

    recommendations_by_session: dict[int, int] = {}
    selected_rec_by_session: dict[int, bool] = {}
    if analysis_session_ids:
        recommendations_by_session = _count_rows(
            db.session.query(SolutionRecommendation.session_id, func.count(SolutionRecommendation.id))
            .filter(SolutionRecommendation.session_id.in_(analysis_session_ids))
            .group_by(SolutionRecommendation.session_id)
        )
        # Check which sessions have a selected/recommended option (rank=1 or is_recommended)
        selected_sessions = (
            db.session.query(SolutionRecommendation.session_id)
            .filter(
                SolutionRecommendation.session_id.in_(analysis_session_ids),
                db.or_(
                    SolutionRecommendation.is_recommended == True,  # noqa: E712
                    SolutionRecommendation.rank == 1,
                ),
            )
            .distinct()
            .all()
        )
        selected_rec_by_session = {row[0]: True for row in selected_sessions}

    # SD-005: Additional entity counts for weighted maturity score (same formula as detail page)
    constraints_by_problem: dict[int, int] = {}
    if problem_ids:
        constraints_by_problem = _count_rows(
            db.session.query(SolutionConstraint.problem_id, func.count(SolutionConstraint.id))
            .filter(SolutionConstraint.problem_id.in_(problem_ids))
            .group_by(SolutionConstraint.problem_id)
        )

    # Requirements can be linked via problem_id OR solution_id
    requirements_by_problem: dict[int, int] = {}
    requirements_by_solution: dict[int, int] = {}
    if problem_ids:
        requirements_by_problem = _count_rows(
            db.session.query(SolutionRequirement.problem_id, func.count(SolutionRequirement.id))
            .filter(SolutionRequirement.problem_id.in_(problem_ids))
            .group_by(SolutionRequirement.problem_id)
        )
    if solution_ids:
        requirements_by_solution = _count_rows(
            db.session.query(SolutionRequirement.solution_id, func.count(SolutionRequirement.id))
            .filter(
                SolutionRequirement.solution_id.in_(solution_ids),
                SolutionRequirement.solution_id.isnot(None),
            )
            .group_by(SolutionRequirement.solution_id)
        )

    plateaus_by_solution: dict[int, int] = _count_rows(
        db.session.query(SolutionPlateau.solution_id, func.count(SolutionPlateau.id))
        .filter(SolutionPlateau.solution_id.in_(solution_ids))
        .group_by(SolutionPlateau.solution_id)
    ) if solution_ids else {}

    metrics_by_solution: dict[int, int] = _count_rows(
        db.session.query(SolutionMetric.solution_id, func.count(SolutionMetric.id))
        .filter(SolutionMetric.solution_id.in_(solution_ids))
        .group_by(SolutionMetric.solution_id)
    ) if solution_ids else {}

    # SD-005: Batch cross-layer ArchiMate relationship count per solution
    # Mirrors the ARCH_relationships check from the detail page (ENT-101)
    cross_layer_by_solution: dict[int, int] = {}
    try:
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.models.solution_element import SolutionElement
        # Build {solution_id: set(element_ids)} mapping
        sol_elem_rows = (
            db.session.query(SolutionElement.solution_id, SolutionElement.archimate_element_id)
            .filter(SolutionElement.solution_id.in_(solution_ids))
            .all()
        )
        sol_elem_map: dict[int, set[int]] = {}
        all_elem_ids: set[int] = set()
        for sol_id, elem_id in sol_elem_rows:
            sol_elem_map.setdefault(sol_id, set()).add(elem_id)
            all_elem_ids.add(elem_id)

        if all_elem_ids:
            # Fetch layers for all relevant elements in one query
            elem_layers = {
                row[0]: (row[1] or "").lower()
                for row in db.session.query(ArchiMateElement.id, ArchiMateElement.layer)
                .filter(ArchiMateElement.id.in_(all_elem_ids))
                .all()
            }
            # Fetch relationships where both ends are in our element set
            rels = (
                db.session.query(
                    ArchiMateRelationship.source_id,
                    ArchiMateRelationship.target_id,
                )
                .filter(
                    ArchiMateRelationship.source_id.in_(all_elem_ids),
                    ArchiMateRelationship.target_id.in_(all_elem_ids),
                )
                .all()
            )
            # Count cross-layer relationships per solution
            for sol_id, elem_ids in sol_elem_map.items():
                cross_count = 0
                for src_id, tgt_id in rels:
                    if src_id in elem_ids and tgt_id in elem_ids:
                        if elem_layers.get(src_id, "") != elem_layers.get(tgt_id, ""):
                            cross_count += 1
                cross_layer_by_solution[sol_id] = cross_count
    except Exception as e:
        logger.debug("SD-005: Cross-layer relationship batch query failed: %s", e)

    summaries: dict[int, dict] = {}
    architect_stats = {
        "needs_setup": 0,
        "in_design": 0,
        "needs_attention": 0,
        "ready_for_review": 0,
    }

    for solution in solutions:
        problem_id = problem_ids_by_solution_id.get(solution.id)
        capability_count = (
            counts["capabilities_direct"].get(solution.id, 0)
            + capabilities_by_problem.get(problem_id, 0)
        )
        driver_count = drivers_by_problem.get(problem_id, 0)
        goal_count = goals_by_problem.get(problem_id, 0)
        constraint_count = constraints_by_problem.get(problem_id, 0)
        requirement_count = (
            requirements_by_problem.get(problem_id, 0)
            + requirements_by_solution.get(solution.id, 0)
        )
        recommendation_count = recommendations_by_session.get(solution.analysis_session_id, 0) if solution.analysis_session_id else 0
        has_selected_rec = selected_rec_by_session.get(solution.analysis_session_id, False) if solution.analysis_session_id else False
        app_count = counts["apps"].get(solution.id, 0)
        vendor_count = counts["vendors"].get(solution.id, 0)
        archimate_count = counts["archimate"].get(solution.id, 0)
        risk_count = counts["risks"].get(solution.id, 0)
        adr_count = counts["adrs"].get(solution.id, 0)
        apqc_count = counts["apqc"].get(solution.id, 0)
        plateau_count = plateaus_by_solution.get(solution.id, 0)
        metric_count = metrics_by_solution.get(solution.id, 0)

        # SD-005: Weighted maturity score — same formula as detail page (_build_solution_detail_context)
        # Weights mirror the detail page's maturity_checks dict (SDX-017)
        maturity_weights = {
            "A_drivers":          (8,  driver_count > 0),
            "A_goals":            (7,  goal_count > 0),
            "A_constraints":      (5,  constraint_count > 0),
            "BCD_requirements":   (10, requirement_count > 0),
            "BCD_capabilities":   (8,  archimate_count > 0),
            "CD_risks":           (7,  risk_count > 0),
            "E_options":          (10, recommendation_count > 0),
            "E_recommendation":   (8,  has_selected_rec),
            "F_plateaus":         (5,  plateau_count > 0),
            "G_arb":              (7,  solution.governance_status not in (None, "draft")),
            "H_metrics":          (5,  metric_count > 0),
            "ARCH_relationships": (7,  cross_layer_by_solution.get(solution.id, 0) >= 5),
        }
        maturity_total_weight = sum(w for w, _ in maturity_weights.values())
        maturity_earned_weight = sum(w for w, passed in maturity_weights.values() if passed)
        readiness_pct = round((maturity_earned_weight / maturity_total_weight) * 100) if maturity_total_weight else 0
        readiness_passed = sum(1 for _, passed in maturity_weights.values() if passed)
        readiness_total = len(maturity_weights)

        if not solution.description or capability_count == 0:
            next_action = "Map the business problem and capabilities"
            work_bucket = "needs_setup"
        elif recommendation_count == 0:
            next_action = "Define the preferred solution option"
            work_bucket = "in_design"
        elif archimate_count == 0 or app_count == 0:
            next_action = "Link architecture and affected applications"
            work_bucket = "in_design"
        elif risk_count == 0 or adr_count == 0:
            next_action = "Capture risks and architecture decisions"
            work_bucket = "needs_attention"
        elif solution.governance_status in {"rejected", "withdrawn"}:
            next_action = "Resolve governance issues before resubmission"
            work_bucket = "needs_attention"
        else:
            next_action = "Prepare for ARB review"
            work_bucket = "ready_for_review"

        architect_stats[work_bucket] += 1

        summaries[solution.id] = {
            "phase_label": solution.adm_phase_label or f"Phase {solution.adm_phase or 'A'}",
            "governance_status": solution.governance_status or "draft",
            "owner_display": solution.technical_lead or solution.solution_owner or "Unassigned",
            "readiness_passed": readiness_passed,
            "readiness_total": readiness_total,
            "readiness_pct": readiness_pct,
            "capability_count": capability_count,
            "application_count": app_count,
            "vendor_count": vendor_count,
            "archimate_count": archimate_count,
            "risk_count": risk_count,
            "adr_count": adr_count,
            "apqc_count": apqc_count,
            "recommendation_count": recommendation_count,
            "next_action": next_action,
            "work_bucket": work_bucket,
        }

    return summaries, architect_stats


def _auto_link_archimate_for_apps(solution_id: int, app_ids: list[int]) -> dict:
    """Auto-create SolutionElement links for apps that have archimate_element_id.

    Also auto-imports ArchiMate relationships where both source and target are
    now linked to the solution. Returns counts for response enrichment.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_element import SolutionElement

    auto_linked = 0
    auto_imported = 0

    if not app_ids:
        return {"auto_linked_elements": 0, "auto_imported_relationships": 0}

    # Find apps with archimate_element_id
    apps_with_elements = (
        ApplicationComponent.query
        .filter(
            ApplicationComponent.id.in_(app_ids),
            ApplicationComponent.archimate_element_id.isnot(None),
        )
        .all()
    )

    new_element_ids = []
    for app_comp in apps_with_elements:
        elem_id = app_comp.archimate_element_id
        # Skip if already linked
        existing = SolutionElement.query.filter_by(
            solution_id=solution_id, archimate_element_id=elem_id
        ).first()
        if existing:
            continue

        # Get the element's layer
        elem = ArchiMateElement.query.get(elem_id)
        layer = elem.layer if elem else "application"

        se = SolutionElement(
            solution_id=solution_id,
            archimate_element_id=elem_id,
            layer=layer,
        )
        db.session.add(se)
        new_element_ids.append(elem_id)
        auto_linked += 1

    if not new_element_ids:
        return {"auto_linked_elements": 0, "auto_imported_relationships": 0}

    db.session.flush()

    # Get all element IDs now linked to this solution
    all_linked = {
        r[0] for r in
        db.session.query(SolutionElement.archimate_element_id)
        .filter_by(solution_id=solution_id)
        .all()
    }

    # Find relationships where both ends are in the solution's element set
    # and at least one end is a newly-linked element
    if len(all_linked) >= 2:
        candidate_rels = (
            ArchiMateRelationship.query
            .filter(
                ArchiMateRelationship.source_id.in_(all_linked),
                ArchiMateRelationship.target_id.in_(all_linked),
                db.or_(
                    ArchiMateRelationship.source_id.in_(new_element_ids),
                    ArchiMateRelationship.target_id.in_(new_element_ids),
                ),
            )
            .all()
        )
        auto_imported = len(candidate_rels)

    logger.info(
        "[ENT-099] Auto-linked %d elements and %d relationships for solution %d",
        auto_linked, auto_imported, solution_id,
    )
    return {
        "auto_linked_elements": auto_linked,
        "auto_imported_relationships": auto_imported,
    }

_EDITABLE_FIELDS = frozenset({
    'name', 'description', 'solution_type', 'business_domain', 'complexity_level',
    'status', 'deployment_status', 'planned_start_date', 'planned_end_date',
    'target_completion_date', 'solution_owner', 'business_sponsor', 'technical_lead',
    'security_lead', 'data_protection_officer', 'business_value', 'scope_description',
    'scope_in', 'scope_out', 'affected_systems', 'estimated_cost', 'roi_percentage',
})
_DATE_FIELDS = frozenset({'planned_start_date', 'planned_end_date', 'target_completion_date'})
_DECIMAL_FIELDS = frozenset({'estimated_cost', 'roi_percentage'})
# String column max lengths, mirroring app/models/solution_models.py — validated up
# front so an oversized value is a 400 instead of a DB DataError caught as a 500.
_STRING_FIELD_MAX_LENGTHS = {
    'name': 255,
    'business_domain': 100,
    'solution_type': 50,
    'complexity_level': 20,
    'solution_owner': 255,
    'business_sponsor': 255,
    'technical_lead': 255,
    'security_lead': 255,
    'data_protection_officer': 255,
}


# ---------------------------------------------------------------------------
# Serialisation helpers used by view_solution (also defined in solution_phase_routes
# for use by phase-lifecycle routes, but defined here to avoid circular imports).
# ---------------------------------------------------------------------------

def _get_reasoning_state_dict(solution_id: int) -> dict:
    """Return the most recent AI reasoning state for a solution as a display dict."""
    try:
        from app.models.solution_reasoning import SolutionAIReasoningState
        state = SolutionAIReasoningState.query.filter_by(
            solution_id=solution_id
        ).order_by(SolutionAIReasoningState.created_at.desc()).first()
        if not state:
            return None
        ctx = state.context_snapshot or {}
        trace = state.reasoning_trace or {}
        return {
            "id": state.id,
            "adm_phase": state.adm_phase,
            "created_at": state.created_at.strftime("%Y-%m-%d %H:%M UTC") if state.created_at else None,
            "confidence_pct": round((state.confidence_score_pct or 0) * 100) if (state.confidence_score_pct or 0) <= 1 else round(state.confidence_score_pct or 0),
            "llm_provider": ctx.get("llm_provider") or ctx.get("provider") or "AI",
            "entities_created": ctx.get("entities_created") or {},
            "data_sources": list((state.data_sources_used or {}).keys()),
            "steps_count": trace.get("total_steps") or len(trace.get("steps") or []),
            "execution_ms": trace.get("execution_time_ms"),
            "user_feedback": state.user_feedback,
        }
    except Exception as e:
        db.session.rollback()
        logger.debug(f"Could not fetch reasoning state: {e}")
        return None


def _driver_to_dict(d):
    return {
        "id": d.id, "name": d.name, "description": d.description,
        "driver_type": d.driver_type.value if d.driver_type else "internal",
        "impact_level": d.impact_level, "urgency": d.urgency,
        "source": d.source, "ai_generated": bool(d.ai_generated),
    }


def _goal_to_dict(g):
    return {
        "id": g.id, "name": g.name, "description": g.description,
        "priority": g.priority, "measurement_criteria": g.measurement_criteria,
        "ai_generated": bool(g.ai_generated),
    }


def _constraint_to_dict(c):
    return {
        "id": c.id, "name": c.name, "description": c.description,
        "constraint_type": c.constraint_type.value if c.constraint_type else "technical",
        "value": c.value, "severity": c.severity, "ai_generated": bool(c.ai_generated),
    }


def _requirement_to_dict(r):
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "requirement_type": r.requirement_type.value if r.requirement_type else "functional",
        "priority": r.priority,
        "is_mandatory": r.is_mandatory,
        "source": r.source,
        "rationale": r.rationale,
        "acceptance_criteria": r.acceptance_criteria,
        "ai_generated": r.ai_generated,
        # REQ-001: workflow / triage fields
        "status": r.status or "open",
        "owner": r.owner or "",
        "assumptions": r.assumptions or "",
        "dependencies_text": r.dependencies_text or "",
        "moscow_priority": r.moscow_priority or "",
        "togaf_phase": r.togaf_phase or "",
    }


def _get_solution_requirements(solution: Solution):
    """Return requirements from both analysis-session and direct solution links."""
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionRequirement,
    )

    requirements_all = []

    if getattr(solution, "analysis_session_id", None):
        session_obj = SolutionAnalysisSession.query.get(solution.analysis_session_id)
        if session_obj and session_obj.problem_definition:
            requirements_all = SolutionRequirement.query.filter_by(
                problem_id=session_obj.problem_definition.id
            ).all()

    if hasattr(SolutionRequirement, "solution_id"):
        direct_requirements = SolutionRequirement.query.filter_by(
            solution_id=solution.id
        ).all()
        existing_ids = {requirement.id for requirement in requirements_all}
        for requirement in direct_requirements:
            if requirement.id not in existing_ids:
                requirements_all.append(requirement)

    return requirements_all


def _get_solution_applications(solution_id: int):
    """Return linked applications for a solution.

    Queries via two paths:
    Path 1 — solution_applications junction table (canonical, explicit links)
    Path 2 — SolutionArchiMateElement rows where element_table = 'application_components'
              (catches apps linked through ArchiMate layer without explicit junction entry)
    Results are deduplicated by application ID.
    """
    apps: list = []
    seen_ids: set = set()

    # Path 1: explicit solution_applications junction
    solution_app_table = db.metadata.tables.get("solution_applications")
    if solution_app_table is not None:
        try:
            junction_apps = (
                db.session.query(ApplicationComponent)
                .join(
                    solution_app_table,
                    ApplicationComponent.id == solution_app_table.c.application_component_id,
                )
                .filter(solution_app_table.c.solution_id == solution_id)
                .all()
            )
            for app in junction_apps:
                if app.id not in seen_ids:
                    apps.append(app)
                    seen_ids.add(app.id)
        except Exception as e:
            logger.warning("solution_applications junction query failed for solution %s: %s", solution_id, e)

    # Path 2: ArchiMate application layer elements (fallback / supplement)
    if not apps:
        try:
            from app.models.solution_models import SolutionArchiMateElement
            archimate_links = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id,
                element_table="application_components",
            ).all()
            if archimate_links:
                app_ids = [link.element_id for link in archimate_links]
                archimate_apps = ApplicationComponent.query.filter(
                    ApplicationComponent.id.in_(app_ids)
                ).all()
                for app in archimate_apps:
                    if app.id not in seen_ids:
                        apps.append(app)
                        seen_ids.add(app.id)
        except Exception as e:
            logger.warning("ArchiMate application fallback query failed for solution %s: %s", solution_id, e)

    return apps


def _serialize_solution_application(application):
    return {
        "id": application.id,
        "app_id": application.id,
        "name": application.name,
        "app_name": application.name,
        "app_abbreviation": getattr(application, "app_abbreviation", None) or "",
        "app_type": getattr(application, "app_abbreviation", None) or "",
        "vendor": getattr(application, "vendor", None) or "",
        "vendor_name": getattr(application, "vendor", None) or "",
        "status": getattr(application, "lifecycle_stage", None)
        or getattr(application, "status", None)
        or "",
        "lifecycle_status": getattr(application, "lifecycle_status", None)
        or getattr(application, "lifecycle_stage", None)
        or getattr(application, "status", None)
        or "",
    }


def _serialize_solution_vendor_product(product, junction_data=None):
    junction_data = junction_data or {}
    return {
        "id": product.id,
        "product_id": product.id,
        "name": product.name,
        "vendor_id": getattr(product, "vendor_organization_id", None),
        "vendor_name": (
            product.vendor_organization.name
            if getattr(product, "vendor_organization", None)
            else "Unknown"
        ),
        "category": getattr(product, "category", None) or "",
        "licensing_model": getattr(product, "licensing_model", None) or "",
        "product_family_name": getattr(product, "product_family_name", None) or "",
        "implementation_type": junction_data.get("implementation_type", ""),
        "license_count": junction_data.get("license_count"),
        # ENT-006: Additional fields for vendor comparison table
        "deployment_model": getattr(product, "deployment_model", None) or "",
        "market_position": getattr(product, "market_position", None) or "",
        "base_license_cost_annual": (
            float(product.base_license_cost_annual)
            if getattr(product, "base_license_cost_annual", None)
            else None
        ),
        "implementation_cost_estimate": (
            float(product.implementation_cost_estimate)
            if getattr(product, "implementation_cost_estimate", None)
            else None
        ),
        "support_cost_percentage": getattr(product, "support_cost_percentage", None),
        "scalability_rating": getattr(product, "scalability_rating", None),
        "security_rating": getattr(product, "security_rating", None),
        "usability_rating": getattr(product, "usability_rating", None),
        "reliability_rating": getattr(product, "reliability_rating", None),
        "product_type": getattr(product, "product_type", None) or "",
        "target_market": getattr(product, "target_market", None) or "",
    }


def _recommendation_to_dict(r):
    _cost = getattr(r, "estimated_cost", None) or getattr(r, "estimated_cost_max", None)
    _rank = getattr(r, "rank", None) or 0
    return {
        "id": r.id,
        "option_type": r.option_type.value if r.option_type else "build",
        "rank": _rank,
        "score": float(r.score) if getattr(r, "score", None) else None,
        "name": getattr(r, "name", None) or getattr(r, "title", None) or f"Option {_rank}",
        "description": getattr(r, "description", None) or getattr(r, "justification", None),
        "rationale": getattr(r, "rationale", None),
        "estimated_cost": float(_cost) if _cost else None,
        "selected": getattr(r, "is_recommended", False) or (_rank == 1),
        "is_recommended": getattr(r, "is_recommended", False),
        "ai_generated": bool(getattr(r, "ai_generated", False)),
    }


def _create_notification(user_id, notification_type, message, solution_id=None):
    """Create a solution lifecycle notification (ENT-020). Caller must commit.

    PLT-017: Checks the target user's notification_preferences before inserting.
    Notification type → preference key mapping:
      arb_submission / outcome_recorded → arb_decisions
      phase_advance / solution_update   → solution_updates
      assignment                        → assignment_changes
      weekly_digest                     → weekly_digest
      comment_mention                   → mention_notifications
    """
    if not user_id:
        return
    # PLT-017: Preference key lookup — defaults to True when preference not set
    _type_to_pref = {
        "arb_submission": "arb_decisions",
        "outcome_recorded": "arb_decisions",
        "phase_advance": "solution_updates",
        "solution_update": "solution_updates",
        "assignment": "assignment_changes",
        "weekly_digest": "weekly_digest",
        "comment_mention": "mention_notifications",
    }
    pref_key = _type_to_pref.get(notification_type)
    if pref_key:
        try:
            from app.models.user import User
            target_user = db.session.get(User, user_id)
            if target_user and not target_user.get_notification_preference(pref_key):
                return
        except Exception as e:
            logger.debug("Could not check notification preference for user %s: %s", user_id, e)
    try:
        n = SolutionNotification(
            solution_id=solution_id,
            user_id=user_id,
            type=notification_type,
            message=message,
        )
        db.session.add(n)
    except Exception as e:
        logger.debug("Could not create notification: %s", e)


# =============================================================================
# LIST SOLUTIONS
# =============================================================================


_WORKLIST_BUCKETS = frozenset(["needs_setup", "in_design", "needs_attention", "ready_for_review"])


class _ManualPagination:
    """Lightweight pagination wrapper for in-Python-filtered lists."""

    def __init__(self, items_all: list, page: int, per_page: int):
        self.total = len(items_all)
        self.page = page
        self.per_page = per_page
        self.pages = max(1, (self.total + per_page - 1) // per_page)
        start = (page - 1) * per_page
        self.items = items_all[start: start + per_page]
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1
        self.next_num = page + 1

    def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
        last = 0
        for num in range(1, self.pages + 1):
            if (
                num <= left_edge
                or (self.page - left_current - 1 < num < self.page + right_current)
                or num > self.pages - right_edge
            ):
                if last + 1 != num:
                    yield None
                yield num
                last = num


@solution_design_bp.route("/", methods=["GET"])
@login_required
def list_solutions():
    """List all solutions with filtering and search."""
    try:
        # Get filter parameters
        search = request.args.get("search", "").strip()
        status_filter = request.args.get("status", "").strip()
        domain_filter = request.args.get("domain", "").strip()
        type_filter = request.args.get("type", "").strip()
        created_after = request.args.get("created_after", "").strip()
        created_before = request.args.get("created_before", "").strip()
        page = safe_int_arg('page', 1, minimum=1)
        per_page = min(safe_int_arg('per_page', 20, minimum=1, maximum=500), 200)

        # Worklist bucket filter (?status=needs_setup/in_design/…) — these are
        # computed classifications, NOT stored DB statuses, so we handle them
        # separately after fetching all accessible solutions.
        ws_filter = ""
        show_all_statuses = False
        if status_filter in _WORKLIST_BUCKETS:
            ws_filter = status_filter
            status_filter = ""
        elif status_filter == "all":
            # "all" means "show every status, including the ones the default
            # shell/archived exclusion below would otherwise hide" — it is not a
            # literal DB status value, so leaving it unhandled fell through to a
            # `WHERE status = 'all'` that matched no row ever.
            status_filter = ""
            show_all_statuses = True

        # PLT-019: BU scope resolution ────────────────────────────────────────
        # If the user has a business unit set (PLT-018), filter solutions whose
        # business_domain contains the BU actor name. Admin can pass ?bu=all to bypass.
        bu_filter_active = False
        bu_name = None
        show_all_override = False
        _user_bu_id = getattr(current_user, "business_unit_id", None)  # model-safety-ok
        _bu_all_requested = request.args.get("bu", "").strip().lower() == "all"
        if _bu_all_requested and hasattr(current_user, "is_admin") and current_user.is_admin():
            show_all_override = True
        elif _user_bu_id:
            try:
                from app.models.business_layer import BusinessActor as _BusinessActor
                _bu_actor = db.session.get(_BusinessActor, _user_bu_id)
                if _bu_actor:
                    bu_name = _bu_actor.name
                    bu_filter_active = True
            except Exception as _bu_exc:
                logger.warning(
                    "PLT-019: could not resolve business_unit_id=%s for solutions: %s",
                    _user_bu_id, _bu_exc,
                )

        # Build base query — admins and review-role personas see all solutions;
        # solution architects and below see only their own.
        _can_see_all = (
            (hasattr(current_user, 'is_admin') and current_user.is_admin())
            or (hasattr(current_user, 'can_vote_arb') and current_user.can_vote_arb())
            or (hasattr(current_user, 'can_manage_portfolio') and current_user.can_manage_portfolio())
            or getattr(current_user, 'enterprise_role', None) in ('enterprise_architect', 'cto', 'platform_admin')
        )
        if _can_see_all:
            query = Solution.query.filter(~Solution.name.like("[DELETED]%"))
        else:
            query = Solution.query.filter_by(created_by_id=current_user.id).filter(~Solution.name.like("[DELETED]%"))

        # PLT-019: Apply BU domain scope filter
        if bu_filter_active and not show_all_override and bu_name:
            _safe_bu = bu_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.filter(
                Solution.business_domain.ilike(f"%{_safe_bu}%", escape="\\")
            )

        # ENT-023: Compute stats on accessible set BEFORE search/status/domain filters
        # Single GROUP BY query instead of 4 separate COUNTs
        _base = query
        _status_counts = {
            row[0]: int(row[1])
            for row in db.session.query(Solution.status, func.count(Solution.id))
            .filter(Solution.id.in_(_base.with_entities(Solution.id).scalar_subquery()))
            .group_by(Solution.status)
            .all()
        }
        total = sum(_status_counts.values())
        planned = _status_counts.get('planned', 0)
        in_progress = _status_counts.get('in_progress', 0)
        deployed = _status_counts.get('deployed', 0)

        # Apply search filter (escape LIKE wildcards to prevent injection)
        if search:
            safe_search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.filter(
                or_(
                    Solution.name.ilike(f"%{safe_search}%", escape="\\"),
                    Solution.description.ilike(f"%{safe_search}%", escape="\\"),
                    Solution.business_domain.ilike(f"%{safe_search}%", escape="\\"),
                )
            )

        # Apply status filter
        if status_filter:
            query = query.filter(Solution.status == status_filter)
        elif show_all_statuses:
            # ?status=all — explicit escape hatch, skip the default
            # shell/archived exclusion entirely rather than narrowing further.
            pass
        elif not search:
            # Default: exclude only archived solutions and empty draft shells.
            #
            # S-01 (17 Aug 2026 QA addendum): "empty" was judged on description
            # length alone, so a draft with no description was hidden no matter
            # how much architecture it contained. That hid solution 17 — the only
            # solution in the whole instance carrying real content (4 elements, 3
            # relationships, 25% blueprint completeness) — while the page asserted
            # "Page 1 of 1". A shell is now one with no description AND no
            # blueprint narrative AND no version history, so real work is never
            # hidden by an unfilled text box. The count of what this hides is
            # surfaced to the user below rather than left silent.
            _is_shell = db.and_(
                Solution.status == "draft",
                db.or_(Solution.description.is_(None), db.func.length(Solution.description) <= 20),
                db.or_(Solution.section_narratives.is_(None), db.cast(Solution.section_narratives, db.Text).in_(("{}", "null", ""))),
                db.or_(Solution.version.is_(None), Solution.version <= 1),
            )
            query = query.filter(Solution.status != "archived").filter(db.not_(_is_shell))

        # Apply domain filter
        if domain_filter:
            query = query.filter(Solution.business_domain == domain_filter)

        # Apply type filter
        if type_filter:
            query = query.filter(Solution.solution_type == type_filter)

        # Apply date range filter
        if created_after:
            try:
                from datetime import datetime as _dt
                query = query.filter(Solution.created_at >= _dt.strptime(created_after, "%Y-%m-%d"))
            except ValueError:
                pass
        if created_before:
            try:
                from datetime import datetime as _dt, timedelta as _td
                query = query.filter(Solution.created_at < _dt.strptime(created_before, "%Y-%m-%d") + _td(days=1))
            except ValueError:
                pass

        # Execute query with pagination — sort by maturity desc (real work first), then newest
        _ordered = query.order_by(
            db.func.coalesce(Solution.maturity_current, 0).desc(),
            Solution.updated_at.desc(),
        )

        if ws_filter:
            # Worklist buckets are computed values — must classify all accessible
            # solutions in Python, then paginate the filtered list.
            _all_solutions = _ordered.all()
            try:
                _all_summaries, workspace_stats = _build_solution_worklist_summaries(_all_solutions)
            except Exception as _ws_err:
                current_app.logger.error("Error building worklist summaries: %s", _ws_err)
                _all_summaries, workspace_stats = {}, {"needs_setup": 0, "in_design": 0, "needs_attention": 0, "ready_for_review": 0}
            _filtered = [s for s in _all_solutions if _all_summaries.get(s.id, {}).get("work_bucket") == ws_filter]
            pagination = _ManualPagination(_filtered, page, per_page)
            workspace_summaries = {s.id: _all_summaries[s.id] for s in pagination.items if s.id in _all_summaries}
            all_filtered_ids = [s.id for s in _filtered]
        else:
            pagination = _ordered.paginate(page=page, per_page=per_page, error_out=False)
            # ENT-100: Defensive error handling for worklist summaries
            try:
                workspace_summaries, workspace_stats = _build_solution_worklist_summaries(pagination.items)
            except Exception as worklist_err:
                current_app.logger.error("Error building worklist summaries: %s", worklist_err)
                workspace_summaries, workspace_stats = {}, {"needs_setup": 0, "in_design": 0, "needs_attention": 0, "ready_for_review": 0}
            # Populate all_filtered_ids so "Select all X" bulk delete works across pages
            all_filtered_ids = [r[0] for r in _ordered.with_entities(Solution.id).all()]

        # Get distinct statuses and domains for filters
        statuses = db.session.query(Solution.status).distinct().all()
        statuses = [s[0] for s in statuses if s[0]]

        domains = db.session.query(Solution.business_domain).distinct().all()
        domains = [d[0] for d in domains if d[0]]

        solution_types = db.session.query(Solution.solution_type).distinct().all()
        solution_types = [t[0] for t in solution_types if t[0]]

        # Calculate statistics (ENT-023: use scoped pre-filter counts)
        stats = {
            "total": total,
            "planned": planned,
            "in_progress": in_progress,
            "deployed": deployed,
        }

        # ENT-100: workspace_summaries/workspace_stats computed above (ws_filter branch or normal branch)

        # PLT-002: Derive completeness scores from already-fetched workspace_summaries (zero extra queries)
        completeness_scores = {
            _sol.id: {
                "score": workspace_summaries.get(_sol.id, {}).get("readiness_pct", 0),
                "filled_count": workspace_summaries.get(_sol.id, {}).get("readiness_passed", 0),
                "total": workspace_summaries.get(_sol.id, {}).get("readiness_total", 12),
            }
            for _sol in pagination.items
        }

        # ── Unified hidden-solutions disclosure (round 4) ───────────────────
        # Rounds 1-3 each added a separate, conditionally-gated disclosure
        # block for ONE filter type (ownership, then BU, then search/status),
        # leaving the identical false "create your first solution" empty
        # state reachable through whichever filter type wasn't covered yet —
        # domain/type/date filters (D2) and worklist-bucket clicks via the
        # stat cards (D3). Replaced with ONE canonical breakdown, computed
        # unconditionally, whenever the org has solutions:
        #   org_total       — every solution in the org, excluding
        #                      [DELETED]% rows — same grain as the Health
        #                      Scorecard tile, ALWAYS org-wide (D1: never a
        #                      BU-scoped subtotal presented as if it were
        #                      the org total).
        #   hidden_by_role_filter — org_total - accessible_count. Captures
        #                      BOTH ownership scoping and BU-domain scoping
        #                      in one number, because `_base`/
        #                      `_accessible_count` already has both baked in
        #                      (ownership filter at line ~1080, BU domain
        #                      filter at line ~1083, `_base = query` line
        #                      ~1091) — no separate hidden_by_bu_filter is
        #                      needed any more.
        #   hidden_by_filters — accessible_count - final_visible_count,
        #                      where final_visible_count is `pagination.total`
        #                      taken AFTER every filter (search, status,
        #                      domain, type, dates, the default shell
        #                      exclusion, AND the worklist-bucket filter) has
        #                      already been applied above — `pagination` is
        #                      `_ManualPagination` (post-bucket-filter) on the
        #                      ws_filter branch and the SQL-paginated object
        #                      otherwise, so this one number is automatically
        #                      correct for any current or future filter
        #                      without a per-filter-type conditional.
        hidden_by_default_filter = 0  # kept for template/test back-compat; now an alias of hidden_by_filters
        hidden_by_role_filter = 0
        hidden_by_bu_filter = 0  # kept for template/test back-compat; folded into hidden_by_role_filter
        hidden_by_search_filter = 0  # kept for template/test back-compat; now an alias of hidden_by_filters
        hidden_by_filters = 0
        org_total = None

        try:
            _accessible_count = _base.with_entities(Solution.id).count()
        except Exception as _acc_err:  # a count must never 500 the page
            logger.warning("solutions list: accessible-count unavailable: %s", _acc_err)
            _accessible_count = 0

        try:
            org_total = Solution.query.filter(~Solution.name.like("[DELETED]%")).count()
        except Exception as _org_err:  # a count must never 500 the page
            logger.warning("solutions list: org-total unavailable: %s", _org_err)
            org_total = _accessible_count

        try:
            _final_visible_count = pagination.total if pagination is not None else 0
        except Exception:
            _final_visible_count = 0

        # Unconditional on `_can_see_all`: `_accessible_count` (from `_base`)
        # already carries the ownership filter ONLY for non-privileged users
        # (line ~1080) but the BU-domain filter for EVERY user regardless of
        # `_can_see_all` (line ~1083, PLT-019) — so a privileged user scoped
        # to a business unit whose domain matches none of the org's
        # solutions must still see this number (R2-1's bug, now fixed at the
        # unified level rather than re-special-cased).
        hidden_by_role_filter = max(0, org_total - _accessible_count)
        hidden_by_filters = max(0, _accessible_count - _final_visible_count)
        # Back-compat aliases — the template's per-cause testids/messages
        # still read these three names; they now all describe the same
        # `hidden_by_filters` number rather than three independently-derived
        # ones, so they can never disagree with each other again.
        hidden_by_default_filter = hidden_by_filters
        hidden_by_search_filter = hidden_by_filters

        # Describe which filters are actually active, for the disclosure
        # message ("K are hidden by your current filters: search 'x',
        # domain 'Finance', ..."), covering every filter this route
        # supports — not a hand-picked subset (D2/D3).
        active_filter_descriptions = []
        _default_shell_filter_active = not status_filter and not show_all_statuses and not search
        if _default_shell_filter_active:
            active_filter_descriptions.append("the default filter (empty drafts and archived solutions)")
        if search:
            active_filter_descriptions.append(f'search "{search}"')
        if status_filter:
            active_filter_descriptions.append(f'status "{status_filter}"')
        if ws_filter:
            _ws_labels = {
                "needs_setup": "Needs Setup", "in_design": "In Design",
                "needs_attention": "Needs Attention", "ready_for_review": "Ready for Review",
            }
            active_filter_descriptions.append(f'worklist "{_ws_labels.get(ws_filter, ws_filter)}"')
        if domain_filter:
            active_filter_descriptions.append(f'domain "{domain_filter}"')
        if type_filter:
            active_filter_descriptions.append(f'type "{type_filter}"')
        if created_after:
            active_filter_descriptions.append(f'created after {created_after}')
        if created_before:
            active_filter_descriptions.append(f'created before {created_before}')
        if bu_filter_active and not show_all_override:
            active_filter_descriptions.append(f'business unit "{bu_name}"')

        # Show the "New Programme" CTA only to users who can actually create one
        # (audit F-04: otherwise the wizard is a six-step dead end).
        from app.modules.transformation_room.programme_service import (
            TransformationProgrammeService,
        )
        can_create_programme = TransformationProgrammeService.can_create_programme(current_user)

        # D4 (round-4 refuter finding, security-adjacent): this used to splat
        # every raw `request.args` key into `url_for(**_clear_filter_args)`,
        # which reserves keywords like `endpoint`/`_external`/`_scheme`/
        # `_method`/`_anchor` — `?endpoint=x` raised TypeError, `?_scheme=
        # https` raised ValueError, `?_method=POST` raised BuildError, all
        # caught by the blanket `except Exception` below and silently
        # re-rendered as the FALSE first-run empty state with no disclosure
        # vars at all. D6: the "clear filters" action must also actually
        # clear every filter it claims to (domain/type/dates/bu too, not
        # just search/status/page) and be reachable from every empty state.
        # A single unqualified link to the bare list URL satisfies both: no
        # user-supplied keys ever reach `url_for`, and every filter is gone.
        clear_filters_url = url_for("solution_design.list_solutions")

        return render_template(
            "solutions/list.html",
            can_create_programme=can_create_programme,
            hidden_by_default_filter=hidden_by_default_filter,
            hidden_by_role_filter=hidden_by_role_filter,
            hidden_by_bu_filter=hidden_by_bu_filter,
            hidden_by_search_filter=hidden_by_search_filter,
            hidden_by_filters=hidden_by_filters,
            active_filter_descriptions=active_filter_descriptions,
            clear_filters_url=clear_filters_url,
            org_total=org_total,
            solutions=pagination.items,
            pagination=pagination,
            per_page=per_page,
            total_count=pagination.total,
            stats=stats,
            statuses=statuses,
            domains=domains,
            total=total,
            planned=planned,
            in_progress=in_progress,
            deployed=deployed,
            search=search,
            selected_status=status_filter,
            selected_domain=domain_filter,
            selected_type=type_filter,
            created_after=created_after,
            created_before=created_before,
            solution_types=solution_types,
            workspace_summaries=workspace_summaries,
            workspace_stats=workspace_stats,
            bu_filter_active=bu_filter_active,
            bu_name=bu_name,
            show_all_override=show_all_override,
            completeness_scores=completeness_scores,
            selected_workspace=ws_filter,
            all_filtered_ids=all_filtered_ids,
        )
    except Exception as e:
        import traceback as _tb
        # ENT-100: Enhanced error logging for diagnosis
        error_details = {
            "error_type": type(e).__name__,
            "error_msg": str(e),
            "traceback": _tb.format_exc(),
            "user_id": current_user.id if current_user else None,
            "filters": {
                "search": search if 'search' in locals() else None,
                "status": status_filter if 'status_filter' in locals() else None,
                "domain": domain_filter if 'domain_filter' in locals() else None,
            }
        }
        current_app.logger.error(f"Error loading solutions: {json.dumps(error_details, indent=2)}")
        flash(f"Error loading solutions: {str(e)}. The error has been logged. Please contact support if this persists.", "error")
        # total_count=None so the header reads an em dash rather than "0
        # solutions", which the user would take as a fact about their estate.
        return render_template("solutions/list.html", solutions=[], pagination=None,
                             can_create_programme=False,
                             total_count=None, stats=None,
                             statuses=[], domains=[], solution_types=[],
                             workspace_summaries={}, workspace_stats=None,
                             load_error="The solution list could not be read.")


# =============================================================================
# /solutions/create — permanent redirect (GET only, POST deleted)
# The old create form is gone. GET permanently redirects to /architecture-journey/.
# POST was deleted — use POST /api/solutions (architecture_routes.py) instead.
# =============================================================================


@solution_design_bp.route("/create", methods=["GET"])
@login_required
def create_solution():
    """Permanent redirect: /solutions/create → /architecture-journey/

    The create form has been retired. This 301 keeps old bookmarks and
    navigation links working. Do not add POST back — use POST /api/solutions.
    """
    return redirect("/architecture-journey/", 301)




# =============================================================================
# HELPER FUNCTIONS (create-from-wizard)
# =============================================================================


def _parse_cost(cost_str):
    """Parse a cost string like '£50,000', '$1.2M', or '$500k-$800k' into a Decimal or None.

    Ranges (e.g. '$500k-$800k') are resolved to their midpoint.
    Categorical words ('low', 'medium', 'high') return None.
    """
    if not cost_str or not isinstance(cost_str, str):
        return None
    import re
    from decimal import Decimal, InvalidOperation

    def _parse_single(s):
        s = s.strip()
        multiplier = 1
        upper = s.upper()
        if upper.endswith('M'):
            multiplier = 1_000_000
            s = s[:-1]
        elif upper.endswith('K'):
            multiplier = 1_000
            s = s[:-1]
        s = re.sub(r'[£$€,\s]', '', s)
        if not s:
            return None
        try:
            return Decimal(s) * multiplier
        except (InvalidOperation, ValueError):
            return None

    # Handle range format: '$500k-$800k' or '$1.2M-$2M'
    if '-' in cost_str:
        # Split on hyphen that separates two cost tokens (not a negative sign)
        parts = re.split(r'-(?=[£$€\d])', cost_str.strip())
        if len(parts) == 2:
            low = _parse_single(parts[0])
            high = _parse_single(parts[1])
            if low is not None and high is not None:
                return (low + high) / 2
            return low or high

    return _parse_single(cost_str)


def _extract_list(text):
    """Split a text block into a list of non-empty lines/items."""
    if not text:
        return []
    if isinstance(text, list):
        return [str(item).strip() for item in text if str(item).strip()]
    return [line.strip() for line in str(text).replace('\r\n', '\n').split('\n') if line.strip()]


def _condition_comment_section_name(version_id: int, condition_idx: int) -> str:
    return f"phase_g_condition_{version_id}_{condition_idx}"


def _condition_actor_name(user_obj) -> str:
    if hasattr(user_obj, "full_name"):
        try:
            full_name = user_obj.full_name()
        except TypeError:
            full_name = None
        if full_name:
            return full_name
    return getattr(user_obj, "email", None) or getattr(user_obj, "username", None) or f"User {getattr(user_obj, 'id', 'unknown')}"


def _user_display_name(user_id: int | None) -> str | None:
    if not user_id:
        return None
    from app.models.user import User

    user_obj = db.session.get(User, user_id)
    if not user_obj:
        return None
    return _condition_actor_name(user_obj)


def _merge_condition_comments(solution_id: int, section_name: str, inline_comments) -> list[dict]:
    from app.models.solution_models import SolutionComment

    merged: list[dict] = []
    seen: set[tuple] = set()

    for comment in inline_comments or []:
        if not isinstance(comment, dict):
            continue
        normalized = {
            "author_id": comment.get("author_id"),
            "author_name": comment.get("author_name") or "Unknown User",
            "content": comment.get("content") or "",
            "created_at": comment.get("created_at"),
        }
        marker = (
            normalized["author_id"],
            normalized["author_name"],
            normalized["content"],
            normalized["created_at"],
        )
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(normalized)

    persisted_comments = (
        SolutionComment.query
        .filter_by(solution_id=solution_id, section_name=section_name)
        .order_by(SolutionComment.created_at.asc(), SolutionComment.id.asc())
        .all()
    )
    for comment in persisted_comments:
        serialized = comment.to_dict()
        marker = (
            serialized.get("author_id"),
            serialized.get("author_name"),
            serialized.get("content"),
            serialized.get("created_at"),
        )
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(serialized)

    return merged


def _normalize_approval_conditions(solution_id: int, version_id: int, conditions) -> list[dict]:
    normalized_conditions: list[dict] = []

    for idx, condition in enumerate(conditions or []):
        raw = condition if isinstance(condition, dict) else {"condition": str(condition)}
        text = (raw.get("text") or raw.get("condition") or raw.get("description") or "").strip()
        status = (raw.get("status") or "").strip() or "open"
        owner_id = raw.get("owner_id")
        section_name = _condition_comment_section_name(version_id, idx)

        normalized = dict(raw)
        normalized["text"] = text
        normalized["condition"] = text
        normalized["status"] = status
        normalized["owner_id"] = owner_id
        normalized["owner_name"] = raw.get("owner_name") or _user_display_name(owner_id)
        normalized["addressed_by_name"] = raw.get("addressed_by_name") or _user_display_name(raw.get("addressed_by_id"))
        normalized["verified_by_name"] = raw.get("verified_by_name") or _user_display_name(raw.get("verified_by_id"))
        normalized["comments"] = _merge_condition_comments(solution_id, section_name, raw.get("comments"))
        normalized_conditions.append(normalized)

    return normalized_conditions


def _format_arb_history_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d %b %Y")


def _normalize_arb_review_condition(condition) -> str:
    if isinstance(condition, dict):
        return (
            str(
                condition.get("text")
                or condition.get("condition")
                or condition.get("description")
                or ""
            ).strip()
        )
    return str(condition).strip()


def _serialize_arb_review_history_item(review) -> dict:
    comments = sorted(
        review.comments or [],
        key=lambda comment: (comment.created_at or datetime.min, comment.id or 0),
    )

    return {
        "id": review.id,
        "review_number": review.review_number,
        "status": review.status,
        "decision": review.decision,
        "decision_label": (review.decision or review.status or "pending").replace("_", " ").title(),
        "submitted_at": _format_arb_history_timestamp(review.submitted_at),
        "decision_date": _format_arb_history_timestamp(review.decision_date),
        "submitter_name": _condition_actor_name(review.submitter) if review.submitter else None,
        "reviewer_name": _condition_actor_name(review.reviewer) if review.reviewer else None,
        "decided_by_name": _condition_actor_name(review.decided_by) if review.decided_by else None,
        "decision_rationale": review.decision_rationale,
        "conditions": [
            text for text in (_normalize_arb_review_condition(condition) for condition in (review.conditions or [])) if text
        ],
        "comments": [
            {
                "id": comment.id,
                "author_name": _condition_actor_name(comment.user) if comment.user else "Unknown User",
                "comment_type": comment.comment_type or "general",
                "content": comment.content,
                "created_at": _format_arb_history_timestamp(comment.created_at),
            }
            for comment in comments
            if comment.content
        ],
    }


def _current_user_can_verify_conditions() -> bool:
    role_name = getattr(current_user, "role_name", "")
    enterprise_role = getattr(current_user, "enterprise_role", "")
    return bool(
        getattr(current_user, "is_admin", lambda: False)()
        or role_name == "enterprise_architect"
        or enterprise_role == "enterprise_architect"
    )


def _current_user_can_address_conditions() -> bool:
    role_name = getattr(current_user, "role_name", "")
    enterprise_role = getattr(current_user, "enterprise_role", "")
    return bool(
        _current_user_can_verify_conditions()
        or role_name == "architect"
        or enterprise_role == "architect"
    )


# =============================================================================
# CREATE SOLUTION FROM ARCHITECTURE ASSISTANT WIZARD
# =============================================================================


@solution_design_bp.route("/create-from-wizard", methods=["POST"])
@login_required
def create_from_wizard():
    """Create a Solution record from the Architecture Assistant wizard submission.

    Accepts the wizard payload (scope, capabilities, gap analysis, selected option)
    and creates a full Solution with linked analysis session,
    problem definition, motivational elements, capability mappings, and recommendation.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON payload provided"}), 400

        title = (data.get("title") or "").strip()
        scope = data.get("scope") or {}
        capabilities = data.get("capabilities") or []
        gap_analysis = data.get("gap_analysis") or {}
        selected_option = data.get("selected_option") or {}

        # Derive a name if title is empty
        if not title:
            cap_names = ", ".join(c.get("name", "") for c in capabilities[:3])
            option_name = selected_option.get("name", "Solution")
            title = f"{option_name} for {cap_names}" if cap_names else option_name

        # Determine owner name
        owner = current_user.full_name() or current_user.email

        now = datetime.utcnow()

        # --- 1. Create the Solution record ---
        solution = Solution(
            name=title[:255],
            description=(scope.get("problem") or "")[:2000] or None,
            solution_type=data.get("solution_type", "Platform"),
            status="planned",
            deployment_status="design",
            governance_status="draft",
            adm_phase="E",
            solution_owner=owner,
            created_by_id=current_user.id,
            estimated_cost=_parse_cost(selected_option.get("cost_estimate")),
        )

        # Set ADM phase completion timestamps based on available data
        if scope.get("problem"):
            solution.adm_phase_a_completed_at = now
        if capabilities:
            solution.adm_phase_b_completed_at = now
        if selected_option:
            solution.adm_phase_e_completed_at = now

        db.session.add(solution)
        db.session.flush()  # Get solution.id

        # --- 2. Create SolutionAnalysisSession ---
        from app.models.solution_architect_models import (
            ConstraintType,
            DriverType,
            RecommendationOptionType,
            SolutionAnalysisSession,
            SolutionConstraint,
            SolutionDriver,
            SolutionGoal,
            SolutionPrinciple,
            SolutionProblemDefinition,
            SolutionRecommendation,
            SolutionSessionStatus,
        )

        session_record = SolutionAnalysisSession(
            name=f"Architecture Assistant: {title[:180]}",
            description="Auto-created from Architecture Assistant wizard submission",
            status=SolutionSessionStatus.COMPLETED,
            created_by_id=current_user.id,
        )
        db.session.add(session_record)
        db.session.flush()

        # Link session to solution
        solution.analysis_session_id = session_record.id

        # --- 3. Create SolutionProblemDefinition ---
        problem_def = SolutionProblemDefinition(
            session_id=session_record.id,
            problem_description=(scope.get("problem") or "No problem statement provided")[:2000],
            business_context=(scope.get("definition") or "")[:2000] or None,
        )
        db.session.add(problem_def)
        db.session.flush()

        # --- 4. Create motivational elements from scope data ---

        # Drivers from stakeholders text
        stakeholders_text = scope.get("stakeholders") or ""
        for item in _extract_list(stakeholders_text):
            driver = SolutionDriver(
                problem_id=problem_def.id,
                name=item[:200],
                description=f"Stakeholder: {item}",
                driver_type=DriverType.STAKEHOLDER,
                source="architecture_assistant",
            )
            db.session.add(driver)
            sync_archimate_element(driver)

        # Goals from gap analysis
        gap_summary = gap_analysis.get("summary") or gap_analysis.get("gap_description") or ""
        if gap_summary:
            goal = SolutionGoal(
                problem_id=problem_def.id,
                name=f"Address: {gap_summary[:190]}",
                description=gap_summary,
            )
            db.session.add(goal)
            sync_archimate_element(goal)

        # Constraints from scope
        constraints_text = scope.get("constraints") or ""
        for item in _extract_list(constraints_text):
            constraint = SolutionConstraint(
                problem_id=problem_def.id,
                constraint_type=ConstraintType.TECHNICAL,
                name=item[:200],
                description=item,
                source="architecture_assistant",
            )
            db.session.add(constraint)
            sync_archimate_element(constraint)

        # Principles from scope
        principles_data = scope.get("principles") or []
        for item in _extract_list(principles_data):
            principle = SolutionPrinciple(
                problem_id=problem_def.id,
                name=item[:200],
                statement=item,
                source="architecture_assistant",
            )
            db.session.add(principle)

        # --- 5. Create SolutionCapabilityMapping records ---
        from app.models.solution_models import SolutionCapabilityMapping as SCM

        for cap in capabilities:
            cap_id = cap.get("id")
            if cap_id:
                try:
                    cap_id_int = int(cap_id)
                except (ValueError, TypeError):
                    continue
                mapping = SCM(
                    problem_id=problem_def.id,
                    capability_id=cap_id_int,
                    support_level="required",
                    notes="Mapped via Architecture Assistant wizard",
                    created_by_id=current_user.id,
                )
                db.session.add(mapping)

        # --- 6. Create SolutionRecommendation for selected option ---
        if selected_option.get("name"):
            # Determine option type
            option_name_lower = (selected_option.get("name") or "").lower()
            if "build" in option_name_lower or "custom" in option_name_lower:
                option_type = RecommendationOptionType.BUILD
            elif "reuse" in option_name_lower or "existing" in option_name_lower:
                option_type = RecommendationOptionType.REUSE
            elif "partner" in option_name_lower:
                option_type = RecommendationOptionType.PARTNER
            elif "hybrid" in option_name_lower:
                option_type = RecommendationOptionType.HYBRID
            else:
                option_type = RecommendationOptionType.BUY

            cost_val = _parse_cost(selected_option.get("cost_estimate"))
            recommendation = SolutionRecommendation(
                session_id=session_record.id,
                option_type=option_type,
                rank=1,
                score=float(selected_option.get("score") or selected_option.get("fit_score") or 0),
                estimated_cost_min=cost_val,
                estimated_cost_max=cost_val,
                pros=selected_option.get("pros") or [],
                cons=selected_option.get("cons") or [],
                justification=f"Selected via Architecture Assistant wizard: {selected_option.get('name')}",
                vendor_products=[selected_option.get("vendor")] if selected_option.get("vendor") else [],
            )
            db.session.add(recommendation)

        db.session.commit()

        # Sync solution analysis data to ArchiMate repository elements
        try:
            from app.services.solution_archimate_sync_service import sync_all_for_solution
            sync_all_for_solution(solution.id)
            db.session.commit()
        except Exception as sync_err:
            logger.warning(f"ArchiMate sync failed for solution {solution.id}: {sync_err}")

        redirect_url = url_for("solution_design.view_solution", solution_id=solution.id)
        return jsonify({
            "success": True,
            "solution_id": solution.id,
            "redirect_url": redirect_url,
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating solution from wizard: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to create solution record"}), 500


# =============================================================================
# VIEW SOLUTION
# =============================================================================


def _get_solution_viewpoints(solution_id):
    """Return saved viewpoint diagrams for a solution, serialised for the template."""
    try:
        from app.models.archimate_core import SavedDiagram
        diagrams = SavedDiagram.query.filter_by(solution_id=solution_id).order_by(
            SavedDiagram.updated_at.desc()
        ).all()
        return [d.to_dict() for d in diagrams]
    except Exception as exc:
        logger.warning("Could not load viewpoints for solution %s: %s", solution_id, exc)
        return []


def _build_solution_detail_context(solution):
    """Build the full template context dict for solution detail/edit views.

    Accepts a Solution ORM object (already fetched and access-checked).
    Returns a dict suitable for ``render_template("solutions/detail.html", **ctx)``.
    """
    solution_id = solution.id

    # Get related applications through junction table
    applications = []
    try:
        applications = _get_solution_applications(solution_id)
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not retrieve applications: {e}")
        applications = []

    # Get associated processes (indirect via applications + direct links)
    processes = []
    try:
        indirect_procs = (
            db.session.query(APQCProcess)
            .join(ProcessApplicationMapping)
            .filter(
                ProcessApplicationMapping.application_id.in_(
                    [a.id for a in applications] if applications else [0]
                )
            )
            .distinct()
            .all()
        )
        direct_apqc_links = SolutionAPQCProcess.query.filter_by(solution_id=solution_id).all()
        direct_apqc_ids = [link.apqc_process_id for link in direct_apqc_links]
        direct_procs = APQCProcess.query.filter(APQCProcess.id.in_(direct_apqc_ids)).all() if direct_apqc_ids else []
        all_process_ids = set(p.id for p in indirect_procs) | set(p.id for p in direct_procs)
        processes = APQCProcess.query.filter(APQCProcess.id.in_(all_process_ids)).all() if all_process_ids else []
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not retrieve processes: {e}")

    # Note: contracts_count and stats dict removed (SDX-007) — computed but never rendered

    # Load analysis session data if linked (SDX-011: enhanced to also query direct solution_id links)
    analysis_data = {}
    try:
        from app.models.solution_architect_models import (
            SolutionAnalysisSession, SolutionDriver, SolutionGoal, SolutionRequirement,
            SolutionConstraint, SolutionRecommendation,
        )
        drivers_all = []
        goals_all = []
        requirements_all = []
        constraints_all = []
        recommendations_all = []

        # Path 1: via analysis_session_id -> problem_definition (single FK on Solution)
        seen_session_ids = set()
        if hasattr(solution, 'analysis_session_id') and solution.analysis_session_id:
            session_obj = SolutionAnalysisSession.query.get(solution.analysis_session_id)
            if session_obj and session_obj.problem_definition:
                pd = session_obj.problem_definition
                drivers_all = SolutionDriver.query.filter_by(problem_id=pd.id).all()
                goals_all = SolutionGoal.query.filter_by(problem_id=pd.id).all()
                requirements_all = SolutionRequirement.query.filter_by(problem_id=pd.id).all()
                constraints_all = SolutionConstraint.query.filter_by(problem_id=pd.id).all()
                recommendations_all = SolutionRecommendation.query.filter_by(session_id=session_obj.id).all()
                seen_session_ids.add(session_obj.id)

        # Path 2: query all SolutionAnalysisSessions by solution_id (covers solutions without
        # analysis_session_id set, or those with additional sessions created via the wizard)
        try:
            extra_sessions = SolutionAnalysisSession.query.filter_by(id=solution.analysis_session_id).all()
            existing_driver_ids = {d.id for d in drivers_all}
            existing_goal_ids = {g.id for g in goals_all}
            existing_constraint_ids = {c.id for c in constraints_all}
            existing_rec_ids = {r.id for r in recommendations_all}
            for sess in extra_sessions:
                if sess.id in seen_session_ids:
                    continue
                seen_session_ids.add(sess.id)
                if sess.problem_definition:
                    pd2 = sess.problem_definition
                    for d in SolutionDriver.query.filter_by(problem_id=pd2.id).all():
                        if d.id not in existing_driver_ids:
                            drivers_all.append(d)
                            existing_driver_ids.add(d.id)
                    for g in SolutionGoal.query.filter_by(problem_id=pd2.id).all():
                        if g.id not in existing_goal_ids:
                            goals_all.append(g)
                            existing_goal_ids.add(g.id)
                    for c in SolutionConstraint.query.filter_by(problem_id=pd2.id).all():
                        if c.id not in existing_constraint_ids:
                            constraints_all.append(c)
                            existing_constraint_ids.add(c.id)
                for rec in SolutionRecommendation.query.filter_by(session_id=sess.id).all():
                    if rec.id not in existing_rec_ids:
                        recommendations_all.append(rec)
                        existing_rec_ids.add(rec.id)
        except Exception as e2:
            logger.warning("Path 2 session query failed: %s", e2)

        requirements_all = _get_solution_requirements(solution)

        analysis_data = {
            "drivers": drivers_all,
            "goals": goals_all,
            "requirements": requirements_all,
            "constraints": constraints_all,
            "recommendations": recommendations_all,
        }
    except Exception as e:
        db.session.rollback()
        logger.warning("Failed to load analysis data: %s", e)

    # Count ARB reviews linked to this solution
    arb_reviews = []
    arb_review_history = []
    arb_conditions = []
    try:
        from app.models.architecture_review_board import ARBReviewComment, ARBReviewItem

        arb_reviews = (
            ARBReviewItem.query.options(
                joinedload(ARBReviewItem.submitter),
                joinedload(ARBReviewItem.reviewer),
                joinedload(ARBReviewItem.decided_by),
                joinedload(ARBReviewItem.comments).joinedload(ARBReviewComment.user),
            )
            .filter_by(solution_id=solution.id)
            .order_by(ARBReviewItem.created_at.asc(), ARBReviewItem.id.asc())
            .all()
        )
        arb_review_history = [
            _serialize_arb_review_history_item(review) for review in reversed(arb_reviews)
        ]
        # Extract conditions from latest approved_with_conditions review (SDX-004)
        for rev in reversed(arb_reviews):
            if rev.decision == "approved_with_conditions" and rev.conditions:
                arb_conditions = rev.conditions
                break
    except Exception as e:
        db.session.rollback()
        logger.warning("Failed to load ARB reviews: %s", e)

    # PLT-016: Load latest conditionally-approved version with trackable conditions
    conditional_version = None
    conditional_version_conditions = []
    try:
        from app.models.solution_governance import SolutionVersion
        cond_ver = (
            SolutionVersion.query
            .filter_by(solution_id=solution.id, approval_status="conditional")
            .order_by(SolutionVersion.version_number.desc())
            .first()
        )
        if cond_ver and cond_ver.approval_conditions:
            conditional_version = cond_ver
            conditional_version_conditions = _normalize_approval_conditions(
                solution.id,
                cond_ver.id,
                cond_ver.approval_conditions,
            )
    except Exception as e:
        logger.warning("Failed to load conditional version: %s", e)

    # Load lifecycle data (risks, metrics, tco, plateaus)
    risks = []
    metrics = []
    tco_items = []
    plateaus = []
    try:
        from app.models.solution_lifecycle_models import (
            SolutionMetric, SolutionPlateau, SolutionRisk, SolutionTCOItem,
        )
        risks = SolutionRisk.query.filter_by(solution_id=solution.id).all()
        metrics = SolutionMetric.query.filter_by(solution_id=solution.id).all()
        tco_items = SolutionTCOItem.query.filter_by(solution_id=solution.id).order_by(
            SolutionTCOItem.option_label, SolutionTCOItem.year
        ).all()
        plateaus = SolutionPlateau.query.filter_by(solution_id=solution.id).order_by(
            SolutionPlateau.order
        ).all()
    except Exception as e:
        db.session.rollback()
        logger.warning("Failed to load lifecycle data: %s", e)

    # Load stakeholder mappings (SolutionStakeholderMapping — SDX-018)
    stakeholder_mappings = []
    try:
        from app.models.solution_stakeholder import SolutionStakeholderMapping
        mappings = SolutionStakeholderMapping.query.filter_by(
            solution_id=solution.id
        ).all()
        for m in mappings:
            stakeholder_mappings.append(m.to_dict(include_stakeholder=True))
    except Exception as e:
        db.session.rollback()
        logger.warning("Failed to load stakeholder mappings: %s", e)

    # Serialize drivers, goals, constraints, requirements, recommendations for Alpine
    drivers_list = analysis_data.get("drivers", []) if analysis_data else []
    goals_list = analysis_data.get("goals", []) if analysis_data else []
    constraints_list = analysis_data.get("constraints", []) if analysis_data else []
    requirements_list = analysis_data.get("requirements", []) if analysis_data else []
    recommendations_list = analysis_data.get("recommendations", []) if analysis_data else []

    def _enrich_business_elements(elements):
        """Resolve APQC process names for business elements that have apqc_process_id."""
        if not elements:
            return []
        apqc_ids = [e.apqc_process_id for e in elements if e.apqc_process_id]
        apqc_map = {}
        if apqc_ids:
            try:
                from app.models.apqc_process import APQCProcess
                procs = APQCProcess.query.filter(APQCProcess.id.in_(apqc_ids)).all()
                apqc_map = {p.id: f"{p.process_code} {p.process_name}" for p in procs}
            except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.exception("Failed to operation")
                pass
        result = []
        for e in elements:
            d = e.to_dict()
            d["apqc_process_name"] = apqc_map.get(e.apqc_process_id) if e.apqc_process_id else None
            result.append(d)
        return result

    # SAD gap models (20 models completing TOGAF SAD coverage)
    sad_data = {}
    try:
        from app.models.solution_sad_models import (
            SolutionIntegrationFlow, SolutionComposition, RiskSnapshot,
            SolutionQualityAttribute, SolutionSLA, MigrationDependency,
            SolutionInvestmentPhase, SolutionGovernanceException,
            SolutionComplianceMapping, SolutionChangeRequest,
            SolutionFeasibilityReview, SolutionBenefitRealization,
            SolutionOrgImpact, SolutionLessonLearned,
            SolutionPrincipleSAD, SolutionAssessmentSAD, SolutionStakeholderSAD,
            SolutionBusinessElement, SolutionAppElement, SolutionTechElement,
        )
        sid = solution.id
        sad_data = {
            "integration_flows": [i.to_dict() for i in SolutionIntegrationFlow.query.filter_by(solution_id=sid).all()],
            "composition": [i.to_dict() for i in SolutionComposition.query.filter_by(solution_id=sid).all()],
            "risk_snapshots": [i.to_dict() for i in RiskSnapshot.query.filter_by(solution_id=sid).order_by(RiskSnapshot.snapshot_date).all()],
            "quality_attributes": [i.to_dict() for i in SolutionQualityAttribute.query.filter_by(solution_id=sid).all()],
            "slas": [i.to_dict() for i in SolutionSLA.query.filter_by(solution_id=sid).all()],
            "migration_dependencies": [i.to_dict() for i in MigrationDependency.query.filter_by(solution_id=sid).all()],
            "investment_phases": [i.to_dict() for i in SolutionInvestmentPhase.query.filter_by(solution_id=sid).order_by(SolutionInvestmentPhase.phase_number).all()],
            "governance_exceptions": [i.to_dict() for i in SolutionGovernanceException.query.filter_by(solution_id=sid).all()],
            "compliance_mappings": [i.to_dict() for i in SolutionComplianceMapping.query.filter_by(solution_id=sid).all()],
            "change_requests": [i.to_dict() for i in SolutionChangeRequest.query.filter_by(solution_id=sid).order_by(SolutionChangeRequest.submitted_date.desc()).all()],
            "feasibility_reviews": [i.to_dict() for i in SolutionFeasibilityReview.query.filter_by(solution_id=sid).all()],
            "benefit_realizations": [i.to_dict() for i in SolutionBenefitRealization.query.filter_by(solution_id=sid).all()],
            "org_impacts": [i.to_dict() for i in SolutionOrgImpact.query.filter_by(solution_id=sid).all()],
            "lessons_learned": [i.to_dict() for i in SolutionLessonLearned.query.filter_by(solution_id=sid).all()],
            # ArchiMate 3.2 phase elements
            "principles": [i.to_dict() for i in SolutionPrincipleSAD.query.filter_by(solution_id=sid).all()],
            "assessments": [i.to_dict() for i in SolutionAssessmentSAD.query.filter_by(solution_id=sid).all()],
            "stakeholders_sad": [i.to_dict() for i in SolutionStakeholderSAD.query.filter_by(solution_id=sid).all()],
            "business_elements": _enrich_business_elements(SolutionBusinessElement.query.filter_by(solution_id=sid).all()),
            "app_elements": [i.to_dict() for i in SolutionAppElement.query.filter_by(solution_id=sid).all()],
            "tech_elements": [i.to_dict() for i in SolutionTechElement.query.filter_by(solution_id=sid).all()],
        }
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not load SAD data: {e}")

    # Serialize lifecycle data for Alpine.js initialData
    try:
        lifecycle_json = {
            "risks": [r.to_dict() for r in risks],
            "metrics": [m.to_dict() for m in metrics],
            "tcoItems": [i.to_dict() for i in tco_items],
            "plateaus": [p.to_dict() for p in plateaus],
            "drivers": [_driver_to_dict(d) for d in drivers_list],
            "goals": [_goal_to_dict(g) for g in goals_list],
            "constraints": [_constraint_to_dict(c) for c in constraints_list],
            "requirements": [_requirement_to_dict(r) for r in requirements_list],
            "recommendations": [_recommendation_to_dict(r) for r in recommendations_list],
            "sad": sad_data,
        }
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not serialize lifecycle data: {e}")
        lifecycle_json = {"risks": [], "metrics": [], "tcoItems": [], "plateaus": [],
                          "drivers": [], "goals": [], "constraints": [], "requirements": [],
                          "recommendations": [], "sad": {}}

    # Load ArchiMate elements linked to this solution
    archimate_elements = []
    phase_gate = {"valid": True, "errors": [], "warnings": []}
    try:
        from app.models.solution_models import SolutionArchiMateElement
        junctions = SolutionArchiMateElement.query.filter_by(
            solution_id=solution.id
        ).order_by(
            SolutionArchiMateElement.layer_type,
            SolutionArchiMateElement.element_name,
        ).all()
        for j in junctions:
            # Derive a human-friendly element type from the table name
            # e.g. "archimate_drivers" -> "Driver", "archimate_goals" -> "Goal"
            _table = j.element_table or ""
            _type_label = _table.replace("archimate_", "").replace("solution_", "").rstrip("s").replace("_", " ").title()
            archimate_elements.append({
                "id": j.element_id,
                "table": j.element_table,
                "type": _type_label,
                "layer": j.layer_type,
                "name": j.element_name,
                "relationship": j.relationship_type,
                "is_new": j.is_new_element,
            })
        _phase_str = str(solution.adm_phase or "A").upper()[:1]
        phase_gate = solution.validate_phase_gate(_phase_str if _phase_str in "ABCDEFGH" else "A")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not load ArchiMate elements: {e}")

    # Load vendor products linked to this solution (with junction metadata)
    vendor_products_list = []
    vendor_products_junction = {}  # product_id -> {implementation_type, license_count}
    try:
        vendor_products_list = list(solution.vendor_products.all())
        from app.models.solution_models import solution_vendor_products as svp_table
        junction_rows = db.session.execute(  # tenant-exempt: scoped via solution FK
            svp_table.select().where(svp_table.c.solution_id == solution.id)
        ).fetchall()
        vendor_products_junction = {
            r.vendor_product_id: {
                "implementation_type": r.implementation_type or "",
                "license_count": r.license_count,
            }
            for r in junction_rows
        }
    except Exception as e:
        db.session.rollback()
        logger.debug(f"Could not query vendor products: {e}")

    # Load ADRs linked to this solution (session-based + direct links)
    adr_list = []
    try:
        from app.models.solution_architect_models import SolutionADRLink
        from app.models.adr import ArchitectureDecisionRecord
        session_adr_ids = set()
        if solution.analysis_session_id:
            adr_links = SolutionADRLink.query.filter_by(
                session_id=solution.analysis_session_id
            ).all()
            session_adr_ids = set(link.adr_id for link in adr_links)
        direct_adr_links = SolutionADRDirect.query.filter_by(solution_id=solution_id).all()
        direct_adr_ids = set(link.adr_id for link in direct_adr_links)
        all_adr_ids = session_adr_ids | direct_adr_ids
        if all_adr_ids:
            adr_list = ArchitectureDecisionRecord.query.filter(
                ArchitectureDecisionRecord.id.in_(all_adr_ids)
            ).order_by(ArchitectureDecisionRecord.adr_number).all()
    except Exception as e:
        db.session.rollback()
        logger.debug(f"Could not query ADRs: {e}")

    from config import CurrencyConfig
    currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "£")

    # Build analysis origin context (SDX-013)
    analysis_origin = None
    try:
        if solution.analysis_session_id and solution.analysis_session:
            sess = solution.analysis_session
            problem = getattr(sess, 'problem_definition', None)
            recs = getattr(sess, 'recommendations', []) or []
            selected_rec = None
            for r in recs:
                if r.rank == 1:
                    selected_rec = r
                    break
            if not selected_rec and recs:
                selected_rec = recs[0]
            analysis_origin = {
                "session_id": sess.id,
                "session_name": sess.name,
                "problem_statement": problem.problem_description if problem else None,
                "recommendation_type": selected_rec.option_type.value.title() if selected_rec and selected_rec.option_type else None,
                "recommendation_score": round(selected_rec.score) if selected_rec and selected_rec.score else None,
                "created_at": sess.created_at.strftime("%Y-%m-%d") if sess.created_at else None,
            }
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not build analysis origin: {e}")

    # Compute solution maturity score (SDX-017)
    maturity_gaps = []
    maturity_score = 0
    try:
        maturity_checks = {
            "A_drivers": {"weight": 8, "label": "Phase A: Drivers defined", "anchor": "phase-a", "entity_type": "driver"},
            "A_goals": {"weight": 7, "label": "Phase A: Goals defined", "anchor": "phase-a", "entity_type": "goal"},
            "A_constraints": {"weight": 5, "label": "Phase A: Constraints defined", "anchor": "phase-a", "entity_type": "constraint"},
            "BCD_requirements": {"weight": 10, "label": "Phase B-D: Requirements defined", "anchor": "phase-bcd", "entity_type": "requirement"},
            "BCD_capabilities": {"weight": 8, "label": "Phase B-D: Capabilities mapped", "anchor": "phase-bcd", "entity_type": None},
            "CD_risks": {"weight": 7, "label": "Phase C-D: Risks identified", "anchor": "phase-risks", "entity_type": "risk"},
            "E_options": {"weight": 10, "label": "Phase E: Options evaluated", "anchor": "phase-e", "entity_type": "option"},
            "E_recommendation": {"weight": 8, "label": "Phase E: Recommendation selected", "anchor": "phase-e", "entity_type": None},
            "F_plateaus": {"weight": 5, "label": "Phase F: Transition plateaus defined", "anchor": "phase-a", "entity_type": "plateau"},
            "G_arb": {"weight": 7, "label": "Phase G: ARB submission", "anchor": "phase-g", "entity_type": None},
            "H_metrics": {"weight": 5, "label": "Phase H: Success metrics defined", "anchor": "phase-a", "entity_type": "metric"},
            "ARCH_relationships": {"weight": 7, "label": "ArchiMate relationships defined (≥5 cross-layer)", "anchor": "archimate", "entity_type": None},
        }
        total_weight = sum(c["weight"] for c in maturity_checks.values())
        earned_weight = 0

        lifecycle = json.loads(lifecycle_json) if isinstance(lifecycle_json, str) else lifecycle_json

        def _has(key):
            v = lifecycle.get(key, [])
            return len(v) > 0 if isinstance(v, list) else bool(v)

        checks_passed = {
            "A_drivers": _has("drivers"),
            "A_goals": _has("goals"),
            "A_constraints": _has("constraints"),
            "BCD_requirements": _has("requirements"),
            "BCD_capabilities": bool(archimate_elements),
            "CD_risks": _has("risks"),
            "E_options": _has("recommendations"),
            "E_recommendation": any(
                r.get("selected") or r.get("is_recommended")
                for r in (lifecycle.get("recommendations") or [])
            ),
            "F_plateaus": _has("plateaus"),
            "G_arb": solution.governance_status not in (None, "draft"),
            "H_metrics": _has("metrics"),
            "ARCH_relationships": False,
        }

        # ENT-101: Count cross-layer relationships for maturity check
        try:
            from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
            from app.models.solution_element import SolutionElement
            # Get element IDs linked to this solution
            sol_elem_ids = {
                r[0] for r in
                db.session.query(SolutionElement.archimate_element_id)
                .filter_by(solution_id=solution.id)
                .all()
            }
            if sol_elem_ids:
                # Count relationships where both ends are in solution and layers differ
                cross_layer_count = 0
                rels = (
                    ArchiMateRelationship.query
                    .filter(
                        ArchiMateRelationship.source_id.in_(sol_elem_ids),
                        ArchiMateRelationship.target_id.in_(sol_elem_ids),
                    )
                    .all()
                )
                elem_cache = {}
                for rel in rels:
                    if rel.source_id not in elem_cache:
                        elem_cache[rel.source_id] = ArchiMateElement.query.get(rel.source_id)
                    if rel.target_id not in elem_cache:
                        elem_cache[rel.target_id] = ArchiMateElement.query.get(rel.target_id)
                    src = elem_cache.get(rel.source_id)
                    tgt = elem_cache.get(rel.target_id)
                    if src and tgt and (src.layer or '').lower() != (tgt.layer or '').lower():
                        cross_layer_count += 1
                checks_passed["ARCH_relationships"] = cross_layer_count >= 5
        except Exception as e:
            logger.debug("Relationship maturity check failed: %s", e)

        for key, check in maturity_checks.items():
            if checks_passed.get(key):
                earned_weight += check["weight"]
            else:
                maturity_gaps.append({
                    "phase": check["label"],
                    "anchor": check["anchor"],
                    "entity_type": check.get("entity_type"),
                    "weight": check["weight"],
                })

        maturity_score = round((earned_weight / total_weight) * 100) if total_weight else 0

        # ENT-009: Pre-compute projected score for each gap (what score you'd reach by closing it)
        for gap in maturity_gaps:
            gap["projected_score"] = round(((earned_weight + gap["weight"]) / total_weight) * 100) if total_weight else 0
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not compute maturity score: {e}")

    # Note: benchmark_data removed (SDX-007) — computed but never rendered in template

    # Serialize linked entities for Alpine.js reactive arrays
    try:
        applications_json = [_serialize_solution_application(a) for a in applications]
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not serialize applications: {e}")
        applications_json = []
    try:
        vendor_products_json = [
            _serialize_solution_vendor_product(
                v, vendor_products_junction.get(v.id, {})
            )
            for v in vendor_products_list
        ]
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not serialize vendor products: {e}")
        vendor_products_json = []
    _saas_keywords = ('subscription', 'saas', 'cloud', 'per user', 'per contact', 'per module', 'per seat', 'per vcore')
    has_subscription_vendors = any(
        any(k in (vp.get('licensing_model') or '').lower() for k in _saas_keywords)
        for vp in vendor_products_json
    )
    try:
        adr_list_json = [
            {
                "id": a.id, "adr_number": a.adr_number, "title": a.title,
                "status": a.status or "draft",
                "decision": (a.decision or "")[:120],
            }
            for a in adr_list
        ]
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not serialize ADRs: {e}")
        adr_list_json = []
    try:
        processes_json = [
            {"id": p.id, "process_code": p.process_code, "process_name": p.process_name}
            for p in processes
        ]
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not serialize processes: {e}")
        processes_json = []

    try:
        capabilities_json = _get_solution_capabilities_payload(solution)
    except Exception as e:
        logger.warning(f"Could not serialize capabilities: {e}")
        capabilities_json = []

    recommendations_list = analysis_data.get("recommendations", []) if analysis_data else []
    drivers_list = analysis_data.get("drivers", []) if analysis_data else []
    goals_list = analysis_data.get("goals", []) if analysis_data else []
    try:
        arb_readiness = solution.arb_readiness
    except Exception as e:
        db.session.rollback()
        logger.warning("Failed to compute ARB readiness: %s", e)
        arb_readiness = None
    workspace_summary = {
        "phase_label": solution.adm_phase_label or f"Phase {solution.adm_phase or 'A'}",
        "governance_status": solution.governance_status or "draft",
        "maturity_score": maturity_score,
        "maturity_gap_count": len(maturity_gaps or []),
        "phase_gate_valid": bool((phase_gate or {}).get("valid", True)),
        "phase_gate_issue_count": len((phase_gate or {}).get("errors", [])) + len((phase_gate or {}).get("warnings", [])),
        "arb_readiness_pct": getattr(arb_readiness, "completion_percentage", 0) or 0,
        "arb_can_submit": bool(getattr(arb_readiness, "can_submit", False)),
        "linked_application_count": len(applications or []),
        "linked_process_count": len(processes or []),
        "linked_capability_count": len(capabilities_json or []),
        "linked_vendor_count": len(vendor_products_list or []),
        "linked_archimate_count": len(archimate_elements or []),
        "risk_count": len(risks or []),
        "adr_count": len(adr_list or []),
        "recommendation_count": len(recommendations_list or []),
        "focus_areas": [
            focus_area
            for focus_area, is_missing in [
                ("Business context", not solution.description),
                ("Drivers and goals", not (drivers_list or goals_list)),
                ("Preferred option", not recommendations_list),
                ("Application footprint", not applications),
                ("ArchiMate model", not archimate_elements),
                ("Risk register", not risks),
                ("Architecture decisions", not adr_list),
            ]
            if is_missing
        ][:3],
    }

    return {
        "solution": solution,
        "applications": applications,
        "processes": processes,
        "analysis_data": analysis_data,
        "arb_reviews": arb_reviews,
        "arb_review_history": arb_review_history,
        "arb_conditions": arb_conditions,
        "conditional_version_id": conditional_version.id if conditional_version else None,
        "conditional_version_conditions": conditional_version_conditions,
        "conditional_approval_blocked": bool(conditional_version_conditions) and not all(
            cond.get("status") == "verified" for cond in conditional_version_conditions
        ),
        "conditional_approval_verified_count": sum(
            1 for cond in conditional_version_conditions if cond.get("status") == "verified"
        ),
        "can_address_conditions": _current_user_can_address_conditions(),
        "can_verify_conditions": _current_user_can_verify_conditions(),
        "risks": risks,
        "metrics": metrics,
        "tco_items": tco_items,
        "currency_symbol": currency_symbol,
        "plateaus": plateaus,
        "lifecycle_json": lifecycle_json,
        "archimate_elements": archimate_elements,
        "phase_gate": phase_gate,
        "vendor_products": vendor_products_list,
        "adr_list": adr_list,
        "sad_data": sad_data,
        "analysis_origin": analysis_origin,
        "applications_json": applications_json,
        "vendor_products_json": vendor_products_json,
        "has_subscription_vendors": has_subscription_vendors,
        "adr_list_json": adr_list_json,
        "processes_json": processes_json,
        "capabilities_json": capabilities_json,
        "stakeholder_mappings": stakeholder_mappings,
        "maturity_score": maturity_score,
        "maturity_gaps": maturity_gaps,
        "workspace_summary": workspace_summary,
        "reasoning_state": _get_reasoning_state_dict(solution.id),
        "llm_available": FeatureFlagService._is_llm_configured(),
        "saved_viewpoints": _get_solution_viewpoints(solution_id),
        "app_element_count": sum(1 for e in archimate_elements if (e.get("layer") or "").lower() == "application"),
        "architecture_completeness": solution.architecture_completeness_score,
    }




def _build_solution_detail_context_fallback(solution):
    """Minimal context when _build_solution_detail_context crashes.

    Provides every key the template expects so it renders with empty sections
    instead of a 500 or a silent redirect.
    """
    from config import CurrencyConfig
    currency_symbol = CurrencyConfig.get_currency_config().get("symbol", "£")
    return {
        "solution": solution,
        "applications": [],
        "processes": [],
        "analysis_data": {},
        "arb_reviews": [],
        "arb_review_history": [],
        "arb_conditions": [],
        "conditional_version_id": None,
        "conditional_version_conditions": [],
        "conditional_approval_blocked": False,
        "conditional_approval_verified_count": 0,
        "can_address_conditions": False,
        "can_verify_conditions": False,
        "risks": [],
        "metrics": [],
        "tco_items": [],
        "currency_symbol": currency_symbol,
        "plateaus": [],
        "lifecycle_json": {"risks": [], "metrics": [], "tcoItems": [], "plateaus": [],
                           "drivers": [], "goals": [], "constraints": [], "requirements": [],
                           "recommendations": [], "sad": {}},
        "archimate_elements": [],
        "phase_gate": {"valid": True, "errors": [], "warnings": []},
        "vendor_products": [],
        "adr_list": [],
        "sad_data": {},
        "analysis_origin": None,
        "applications_json": [],
        "vendor_products_json": [],
        "has_subscription_vendors": False,
        "adr_list_json": [],
        "processes_json": [],
        "capabilities_json": [],
        "stakeholder_mappings": [],
        "maturity_score": 0,
        "maturity_gaps": [],
        "workspace_summary": {
            "phase_label": f"Phase {solution.adm_phase or 'A'}",
            "governance_status": solution.governance_status or "draft",
            "maturity_score": 0, "maturity_gap_count": 0,
            "phase_gate_valid": True, "phase_gate_issue_count": 0,
            "arb_readiness_pct": 0, "arb_can_submit": False,
            "linked_application_count": 0, "linked_process_count": 0,
            "linked_capability_count": 0, "linked_vendor_count": 0,
            "linked_archimate_count": 0, "risk_count": 0,
            "adr_count": 0, "recommendation_count": 0,
            "focus_areas": [],
        },
        "reasoning_state": None,
        "llm_available": False,
        "saved_viewpoints": [],
        "app_element_count": 0,
        "architecture_completeness": {"score": 0, "filled": [], "missing": [], "filled_count": 0, "total": 14},
    }


@solution_design_bp.route("/<int:solution_id>/api/phase-summary", methods=["GET"])
@login_required
def api_phase_summary(solution_id: int):
    """SOL-005: Per-phase entity counts and completeness for interpretation callouts."""
    solution = Solution.query.get_or_404(solution_id)
    try:
        ctx = _build_solution_detail_context(solution)
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to build context"}), 500

    lifecycle = ctx.get("lifecycle_json") or {}
    if isinstance(lifecycle, str):
        lifecycle = json.loads(lifecycle)

    ws = ctx.get("workspace_summary") or {}
    caps = ctx.get("capabilities_json") or []
    apps = ctx.get("applications_json") or []
    vendors = ctx.get("vendor_products_json") or []
    procs = ctx.get("processes_json") or []
    adrs = ctx.get("adr_list_json") or []
    archimate = ctx.get("archimate_elements") or []
    risks = lifecycle.get("risks") or []
    metrics = lifecycle.get("metrics") or []
    plateaus = lifecycle.get("plateaus") or []
    tco = lifecycle.get("tco_items") or []
    recs = lifecycle.get("recommendations") or []
    drivers = lifecycle.get("drivers") or []
    goals = lifecycle.get("goals") or []
    constraints = lifecycle.get("constraints") or []
    requirements = lifecycle.get("requirements") or []

    stk_count = sum(1 for f in [
        solution.solution_owner, solution.business_sponsor,
        solution.technical_lead, solution.architecture_lead,
    ] if f)

    def _phase(present, missing, total_checks, filled_checks):
        pct = round((filled_checks / total_checks) * 100) if total_checks else 0
        return {"present": present, "missing": missing, "completePct": pct}

    def _check(items, label):
        return (len(items), label) if items else (0, label)

    phases = {}

    # sec-1: Executive Summary
    has_desc = bool(solution.description or solution.business_value or getattr(solution, "value_proposition", None))
    phases["sec-1"] = _phase(
        ["description"] if has_desc else [],
        [] if has_desc else ["description"],
        1, 1 if has_desc else 0
    )

    # sec-2: Strategic Context (Phase A)
    sec2_items = [
        (len(drivers), "drivers"), (len(goals), "goals"),
        (len(constraints), "constraints"), (len(requirements), "requirements"),
        (stk_count, "stakeholders"),
    ]
    phases["sec-2"] = _phase(
        [f"{c} {item}" for c, item in sec2_items if c > 0],
        [item for c, item in sec2_items if c == 0],
        len(sec2_items), sum(1 for c, _ in sec2_items if c > 0),
    )

    # sec-3: Business Architecture (Phase B)
    sec3_items = [
        (len(caps), "capabilities"), (len(apps), "applications"),
        (len(vendors), "vendor products"), (len(procs), "APQC processes"),
    ]
    phases["sec-3"] = _phase(
        [f"{c} {item}" for c, item in sec3_items if c > 0],
        [item for c, item in sec3_items if c == 0],
        len(sec3_items), sum(1 for c, _ in sec3_items if c > 0),
    )

    # sec-4: Application & Technology (Phases C/D)
    app_elems = len([e for e in archimate if (e.get("layer") or getattr(e, "layer", "")) == "application"])
    tech_elems = len([e for e in archimate if (e.get("layer") or getattr(e, "layer", "")) == "technology"])
    sec4_items = [(app_elems, "app elements"), (tech_elems, "tech elements"), (len(apps), "applications")]
    phases["sec-4"] = _phase(
        [f"{c} {item}" for c, item in sec4_items if c > 0],
        [item for c, item in sec4_items if c == 0],
        len(sec4_items), sum(1 for c, _ in sec4_items if c > 0),
    )

    # sec-5: Options & Financial (Phase E)
    selected = len([r for r in recs if r.get("is_recommended") or r.get("selected")])
    sec5_items = [(len(recs), "options"), (selected, "selected options"), (len(tco), "TCO items")]
    phases["sec-5"] = _phase(
        [f"{c} {item}" for c, item in sec5_items if c > 0],
        [item for c, item in sec5_items if c == 0],
        len(sec5_items), sum(1 for c, _ in sec5_items if c > 0),
    )

    # sec-6: Delivery (Phase F)
    sec6_items = [(len(plateaus), "plateaus"), (len(tco), "TCO items")]
    phases["sec-6"] = _phase(
        [f"{c} {item}" for c, item in sec6_items if c > 0],
        [item for c, item in sec6_items if c == 0],
        len(sec6_items), sum(1 for c, _ in sec6_items if c > 0),
    )

    # sec-7: Governance (Phase G)
    arb_submitted = solution.governance_status not in (None, "draft")
    phases["sec-7"] = _phase(
        ["ARB submitted"] if arb_submitted else [],
        ["ARB submission"] if not arb_submitted else [],
        1, 1 if arb_submitted else 0,
    )

    # sec-8: Risks & Decisions
    sec8_items = [(len(risks), "risks"), (len(adrs), "ADRs")]
    phases["sec-8"] = _phase(
        [f"{c} {item}" for c, item in sec8_items if c > 0],
        [item for c, item in sec8_items if c == 0],
        len(sec8_items), sum(1 for c, _ in sec8_items if c > 0),
    )

    # sec-9: Operational Readiness (Phase H)
    sec9_items = [(len(metrics), "metrics")]
    phases["sec-9"] = _phase(
        [f"{c} {item}" for c, item in sec9_items if c > 0],
        [item for c, item in sec9_items if c == 0],
        len(sec9_items), sum(1 for c, _ in sec9_items if c > 0),
    )

    # sec-10: Traceability Evidence
    sec10_items = [
        (len(drivers), "drivers"), (len(goals), "goals"),
        (len(requirements), "requirements"), (len(caps), "capabilities"),
    ]
    phases["sec-10"] = _phase(
        [f"{c} {item}" for c, item in sec10_items if c > 0],
        [item for c, item in sec10_items if c == 0],
        len(sec10_items), sum(1 for c, _ in sec10_items if c > 0),
    )

    return jsonify({
        "phases": phases,
        "maturity_score": ws.get("maturity_score", 0),
        "arb_readiness_pct": ws.get("arb_readiness_pct", 0),
    })


def _build_blueprint_context(solution):
    """Build template context for the blueprint page.

    Organizes data by viewpoint section instead of TOGAF phase.
    Does NOT modify _build_solution_detail_context() — legacy page untouched.
    """
    import json as _json
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BLUEPRINT_SECTIONS,
        BlueprintCompletenessService,
        compute_blueprint_completeness,
    )
    svc = BlueprintCompletenessService()

    # Get or compute scores
    scores = solution.section_scores or {}
    if not scores:
        try:
            scores = svc.score_all(solution.id)
        except Exception as e:
            logger.error("Failed to compute blueprint scores for solution %s: %s", solution.id, e)
            scores = {}

    # Seed narratives from journey data on first visit
    if not solution.section_narratives and solution.problem_clarification:
        narratives = {}
        pc = solution.problem_clarification
        narratives["executive_summary"] = pc if isinstance(pc, str) else _json.dumps(pc, indent=2)
        solution.section_narratives = narratives
        db.session.commit()

    # Pre-compute TOGAF phase checklist (avoids {% do %} in Jinja)
    phase_defs = [
        ("Phase A", ["vision_motivation"]),
        ("Phase B", ["business_process_view", "value_stream_map"]),
        ("Phase C", ["application_cooperation", "data_information"]),
        ("Phase D", ["deployment_view", "network_communication"]),
        ("Phase E", ["gap_analysis"]),
        ("Phase F", ["transition_roadmap", "work_packages"]),
    ]
    phase_checklist = []
    for phase_name, section_ids in phase_defs:
        phase_scores = [scores.get(sid, {}).get("overall", 0) for sid in section_ids if scores.get(sid)]
        phase_avg = int(sum(phase_scores) / len(phase_scores)) if phase_scores else 0
        phase_checklist.append({"name": phase_name, "passed": phase_avg >= 80})

    # Load SAD gap models for reusable partials (mirrors _build_solution_detail_context)
    sad_data = {}
    try:
        from app.models.solution_sad_models import (
            SolutionIntegrationFlow, SolutionComposition, RiskSnapshot,
            SolutionQualityAttribute, SolutionSLA, SolutionInvestmentPhase, SolutionGovernanceException,
            SolutionComplianceMapping, SolutionChangeRequest,
            SolutionFeasibilityReview, SolutionBenefitRealization,
            SolutionOrgImpact, SolutionLessonLearned,
            SolutionAppElement, SolutionTechElement,
        )
        sid = solution.id
        sad_data = {
            "integration_flows": [i.to_dict() for i in SolutionIntegrationFlow.query.filter_by(solution_id=sid).all()],
            "composition": [i.to_dict() for i in SolutionComposition.query.filter_by(solution_id=sid).all()],
            "risk_snapshots": [i.to_dict() for i in RiskSnapshot.query.filter_by(solution_id=sid).order_by(RiskSnapshot.snapshot_date).all()],
            "quality_attributes": [i.to_dict() for i in SolutionQualityAttribute.query.filter_by(solution_id=sid).all()],
            "slas": [i.to_dict() for i in SolutionSLA.query.filter_by(solution_id=sid).all()],
            "investment_phases": [i.to_dict() for i in SolutionInvestmentPhase.query.filter_by(solution_id=sid).order_by(SolutionInvestmentPhase.phase_number).all()],
            "governance_exceptions": [i.to_dict() for i in SolutionGovernanceException.query.filter_by(solution_id=sid).all()],
            "compliance_mappings": [i.to_dict() for i in SolutionComplianceMapping.query.filter_by(solution_id=sid).all()],
            "change_requests": [i.to_dict() for i in SolutionChangeRequest.query.filter_by(solution_id=sid).order_by(SolutionChangeRequest.submitted_date.desc()).all()],
            "feasibility_reviews": [i.to_dict() for i in SolutionFeasibilityReview.query.filter_by(solution_id=sid).all()],
            "benefit_realizations": [i.to_dict() for i in SolutionBenefitRealization.query.filter_by(solution_id=sid).all()],
            "org_impacts": [i.to_dict() for i in SolutionOrgImpact.query.filter_by(solution_id=sid).all()],
            "lessons_learned": [i.to_dict() for i in SolutionLessonLearned.query.filter_by(solution_id=sid).all()],
            "app_elements": [i.to_dict() for i in SolutionAppElement.query.filter_by(solution_id=sid).all()],
            "tech_elements": [i.to_dict() for i in SolutionTechElement.query.filter_by(solution_id=sid).all()],
        }
    except Exception as e:
        db.session.rollback()
        logger.warning("Blueprint: could not load SAD data for solution %s: %s", solution.id, e)

    # Merge narrative text from solution.section_narratives into section definitions
    # so blueprint.js can hydrate textareas on page load
    import copy as _copy
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import SECTION_TITLES
    merged_defs = _copy.deepcopy(svc.SECTION_DEFINITIONS)
    saved_narratives = solution.section_narratives or {}
    for section_id in merged_defs:
        if section_id in saved_narratives:
            merged_defs[section_id]["narrative"] = saved_narratives[section_id]
        # Expose human-readable name so the link-elements picker can show the section title
        merged_defs[section_id]["name"] = SECTION_TITLES.get(section_id, section_id)

    # Populate ArchiMate elements per section so blueprint.js sectionElements are hydrated
    type_elements: dict = {}
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.archimate_core import ArchiMateElement as _ArchiMateElement
        junctions = (
            db.session.query(SolutionArchiMateElement, _ArchiMateElement)
            .join(_ArchiMateElement, SolutionArchiMateElement.element_id == _ArchiMateElement.id)
            .filter(SolutionArchiMateElement.solution_id == solution.id)
            .all()
        )
        for junction, elem in junctions:
            etype = elem.type or ""
            type_elements.setdefault(etype, []).append({
                "id": junction.id,
                "element_id": elem.id,
                "name": elem.name or junction.element_name or "",
                "type": etype,
                "layer": elem.layer or junction.layer_type or "",
                "element_role": junction.element_role,
                "description": elem.description or "",
            })
        for section_id, section_def in merged_defs.items():
            required = section_def.get("required_types", [])
            section_elems = []
            for req_type in required:
                section_elems.extend(type_elements.get(req_type, []))
            merged_defs[section_id]["elements"] = section_elems
    except Exception as _e:
        logger.warning("Blueprint: could not hydrate section elements for solution %s: %s", solution.id, _e)
        for section_id in merged_defs:
            merged_defs[section_id].setdefault("elements", [])

    # DR-BP-01: per-section has-content flag, used by the template to collapse
    # sections that carry zero real data instead of rendering a 1,200-1,650px
    # empty shell (sparse solutions were producing ~21,000px of blank scroll).
    # Derived from the same signals already computed above — never guessed:
    #   - a saved narrative (merged_defs[...]["narrative"])
    #   - linked ArchiMate elements for the section's required_types (merged_defs[...]["elements"])
    #   - the SAD sub-records a few sections render via included partials
    _sad_supplement = {
        "application_cooperation": ("composition",),
        "data_information": ("integration_flows",),
        "deployment_view": ("tech_elements",),
        "work_packages": ("investment_phases", "benefit_realizations", "lessons_learned"),
        "security_viewpoint": ("risk_snapshots",),
        "nfr_satisfaction": ("quality_attributes", "slas"),
    }
    section_has_content: dict = {}
    for section_id, section_def in merged_defs.items():
        has = bool(section_def.get("narrative")) or bool(section_def.get("elements"))
        if not has:
            for sad_key in _sad_supplement.get(section_id, ()):
                if sad_data.get(sad_key):
                    has = True
                    break
        if not has and section_id == "erp_fit_gap":
            has = bool(scores.get("erp_fit_gap", {}).get("element_count"))
        if not has and section_id == "integration_architecture":
            has = bool(scores.get("integration_architecture", {}).get("total_flows"))
        section_has_content[section_id] = has

    # transition_roadmap's gantt is fed by legacy WorkPackage rows joined via
    # solution_work_packages (see api_get_roadmap_tasks) — a separate model
    # from the ArchiMate WorkPackage elements merged_defs["elements"] covers,
    # so it needs its own real-data check rather than being guessed empty.
    if not section_has_content.get("transition_roadmap"):
        try:
            from app.models.solution_models import solution_work_packages as _swp
            has_wp = db.session.query(_swp.c.work_package_id).filter(
                _swp.c.solution_id == solution.id
            ).limit(1).count() > 0
            if has_wp:
                section_has_content["transition_roadmap"] = True
        except Exception as exc:
            logger.debug("suppressed error checking transition_roadmap work packages for solution %s: %s", solution.id, exc)

    # Motivation layer entities for blueprint.js lifecycleData (vision_motivation section)
    lifecycle_json = {
        "drivers": type_elements.get("Driver", []),
        "goals": type_elements.get("Goal", []),
        "constraints": type_elements.get("Constraint", []),
    }

    # LLM availability flag for AI generation buttons
    try:
        from app.modules.solutions_strategic.v2.services.feature_flag_service import FeatureFlagService
        llm_available = FeatureFlagService._is_llm_configured()
    except Exception:
        llm_available = False

    # Latest ARB review id (for inline approve/reject)
    arb_review_id = None
    try:
        from app.models.architecture_review_board import ARBReviewItem
        latest_review = (
            ARBReviewItem.query
            .filter_by(solution_id=solution.id)
            .order_by(ARBReviewItem.created_at.desc())
            .first()
        )
        if latest_review:
            arb_review_id = latest_review.id
    except Exception as exc:
        logger.debug("suppressed error in _build_blueprint_context (app/modules/solutions_strategic/v2/routes/solution_design_routes.py): %s", exc)

    return {
        "solution": solution,
        "scores": scores,
        # S-02/S-03: the single completeness computation. Every surface on the
        # blueprint page (ring, "N of M sections", gap advisor, header strip)
        # reads from this — none of them may re-derive it.
        "completeness": compute_blueprint_completeness(scores),
        "blueprint_sections": BLUEPRINT_SECTIONS,
        "section_definitions": merged_defs,
        "section_has_content": section_has_content,
        "next_actions": svc.get_next_actions(solution.id, precomputed_scores=scores),
        "arb_ready": svc.check_arb_ready(solution.id, precomputed_scores=scores),
        "phase_checklist": phase_checklist,
        "sad_data": sad_data,
        "llm_available": llm_available,
        "arb_review_id": arb_review_id,
        "lifecycle_json": lifecycle_json,
    }


@solution_design_bp.route("/<int:solution_id>", methods=["GET"])
@login_required
def view_solution(solution_id: int):
    """View solution details."""
    solution = Solution.query.get_or_404(solution_id)

    # Verify ownership — allow creator, admins, and named stakeholders (FIX-08)
    if not _check_solution_access(solution):
        abort(403)

    # Blueprint page ships ON by default (wired, reachable). Explicit config/env
    # value can opt OUT; ?edit=1 forces the legacy detail/editor view.
    _bp_setting = current_app.config.get("USE_BLUEPRINT_PAGE")
    if _bp_setting is None:
        _bp_setting = os.environ.get("USE_BLUEPRINT_PAGE", "true")
    use_blueprint = (
        str(_bp_setting).strip().lower() in ("true", "1", "yes", "on")
        and request.args.get("edit") != "1"
    )

    if use_blueprint:
        try:
            ctx = _build_blueprint_context(solution)

            # Fire proactive analysis in background — does not block page render
            import threading as _t
            def _run_proactive(app_ref, sol_id):
                with app_ref.app_context():
                    try:
                        from app.modules.ai_chat.services.proactive_analysis_service import ProactiveAnalysisService
                        from app.models.copilot_insight import CopilotInsight
                        from app import db
                        svc = ProactiveAnalysisService()
                        new_insights = svc.analyse_solution(sol_id)
                        for insight in new_insights:
                            existing = CopilotInsight.query.filter_by(
                                solution_id=sol_id,
                                insight_type=insight.insight_type,
                                seen=False,
                                dismissed=False,
                            ).first()
                            if not existing:
                                db.session.add(insight)
                        db.session.commit()
                    except Exception as _e:
                        logger.debug("Proactive analysis failed for sol %s: %s", sol_id, _e)

            _t.Thread(
                target=_run_proactive,
                args=(current_app._get_current_object(), solution.id),
                daemon=True,
            ).start()

            ctx["show_created_banner"] = request.args.get("created") == "1"
            return render_template("solutions/blueprint.html", **ctx)
        except Exception as e:
            logger.error("Blueprint page failed for solution %s: %s. Falling back to legacy.", solution.id, e, exc_info=True)
            # Fall through to legacy detail page

    # AIC-312: Check for active workbench workspace linked to this solution
    active_workspace_id = None
    try:
        from app.models.solution_architect_models import SolutionAnalysisSession, SolutionSessionStatus
        sessions = (
            SolutionAnalysisSession.query
            .filter_by(status=SolutionSessionStatus.IN_PROGRESS)
            .order_by(SolutionAnalysisSession.updated_at.desc())
            .all()
        )
        for ws in sessions:
            meta = ws.custom_metadata or {}
            if meta.get("solution_id") == solution_id:
                active_workspace_id = ws.id
                break
    except Exception as _ws_err:
        logger.debug("AIC-312: workspace lookup skipped: %s", _ws_err)

    try:
        ctx = _build_solution_detail_context(solution)
    except Exception as e:
        import traceback as _tb
        _tb_str = _tb.format_exc()
        logger.error(f"Error building solution context: {str(e)}\n{_tb_str}")
        db.session.rollback()
        # Render with minimal context instead of redirecting — page should
        # degrade gracefully rather than silently hiding the error.
        ctx = _build_solution_detail_context_fallback(solution)
    ctx["show_created_banner"] = request.args.get("created") == "1"
    ctx["active_workspace_id"] = active_workspace_id
    return render_template("solutions/detail.html", **ctx)


# =============================================================================
# AI COPILOT: PROACTIVE INSIGHTS API
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/copilot-insights", methods=["GET"])
@login_required
def get_copilot_insights(solution_id: int):
    """Return unseen, undismissed CopilotInsights for the blueprint page."""
    Solution.query.get_or_404(solution_id)
    from app.models.copilot_insight import CopilotInsight
    insights = CopilotInsight.get_unseen_for_solution(solution_id)
    for i in insights:
        i.seen = True
    db.session.commit()
    return jsonify({"insights": [i.to_dict() for i in insights]})


@solution_design_bp.route("/<int:solution_id>/copilot-insights/<int:insight_id>/dismiss", methods=["POST"])
@login_required
def dismiss_copilot_insight(solution_id: int, insight_id: int):
    """Dismiss a CopilotInsight — it will not reappear."""
    from app.models.copilot_insight import CopilotInsight
    insight = CopilotInsight.query.filter_by(id=insight_id, solution_id=solution_id).first_or_404()
    insight.dismissed = True
    db.session.commit()
    return jsonify({"success": True})


# =============================================================================
# SAD-11: AI REASONING EXPLAINABILITY API
# =============================================================================


@solution_design_bp.route("/api/<int:solution_id>/reasoning/<int:reasoning_id>", methods=["GET"])
@login_required
def api_reasoning_detail(solution_id, reasoning_id):
    """Return AI reasoning state detail for explainability modal."""
    try:
        from app.models.solution_reasoning import SolutionAIReasoningState
        state = SolutionAIReasoningState.query.filter_by(
            id=reasoning_id, solution_id=solution_id
        ).first()
        if not state:
            return jsonify({"error": "Reasoning state not found"}), 404
        ctx = state.context_snapshot or {}
        trace = state.reasoning_trace or {}
        return jsonify({
            "id": state.id,
            "adm_phase": state.adm_phase,
            "created_at": state.created_at.strftime("%Y-%m-%d %H:%M UTC") if state.created_at else None,
            "confidence_pct": round((state.confidence_score_pct or 0) * 100) if (state.confidence_score_pct or 0) <= 1 else round(state.confidence_score_pct or 0),
            "llm_provider": ctx.get("llm_provider") or ctx.get("provider") or "AI",
            "entities_created": ctx.get("entities_created") or {},
            "data_sources": list((state.data_sources_used or {}).keys()),
            "steps_count": trace.get("total_steps") or len(trace.get("steps") or []),
            "execution_ms": trace.get("execution_time_ms"),
            "user_feedback": state.user_feedback,
            "reasoning_trace": trace,
            "context_snapshot": ctx,
        })
    except Exception as e:
        db.session.rollback()
        logger.warning("Reasoning detail fetch failed: %s", e)
        return jsonify({"error": "Could not load reasoning data"}), 500


# =============================================================================
# CODE SPEC INFERENCE — LLM-powered field proposals (Phase 2)
# =============================================================================


@solution_design_bp.route("/api/<int:solution_id>/infer-code-specs", methods=["POST"])
@login_required
def api_infer_code_specs(solution_id):
    """Use LLM to propose schema fields for all app elements in a solution."""
    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)

    try:
        from app.modules.solutions_strategic.v2.services.code_spec_inference import infer_all_code_specs
        results = infer_all_code_specs(solution_id)
        return jsonify({"success": True, "elements": results})
    except Exception as e:
        db.session.rollback()
        logger.error("Code spec inference failed for solution %s: %s", solution_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/api/<int:solution_id>/confirm-code-spec/<int:element_id>", methods=["POST"])
@login_required
def api_confirm_code_spec(solution_id, element_id):
    """Confirm (and optionally edit) LLM-proposed fields for an app element."""
    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)

    from app.models.solution_sad_models import SolutionAppElement
    element = SolutionAppElement.query.filter_by(
        id=element_id, solution_id=solution_id
    ).first_or_404()

    data = request.get_json(silent=True) or {}
    fields = data.get("fields", [])
    if not fields:
        return jsonify({"success": False, "error": "No fields provided"}), 400

    # Save confirmed fields as code_spec
    element.code_spec = {
        "fields": fields,
        "confirmed_at": datetime.utcnow().isoformat() + "Z",
        "confirmed_by": current_user.email,
    }
    db.session.commit()

    return jsonify({"success": True, "element_id": element_id, "field_count": len(fields)})


# =============================================================================
# SPEC GENERATION — Blueprint to API Contracts (Phase 1)
# =============================================================================


@solution_design_bp.route("/api/<int:solution_id>/generate-specs", methods=["POST"])
@login_required
def api_generate_specs(solution_id):
    """Generate OpenAPI, JSON Schema, and AsyncAPI specs from solution blueprint."""
    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)

    try:
        from app.modules.solutions_strategic.v2.services.spec_generator import SolutionSpecGenerator
        generator = SolutionSpecGenerator(solution_id)
        bundle = generator.generate()
        if not bundle.get("success", True):
            return jsonify(bundle), 422
        return jsonify(bundle)
    except Exception as e:
        db.session.rollback()
        logger.error("Spec generation failed for solution %s: %s", solution_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/api/<int:solution_id>/generate-specs/download", methods=["GET"])
@login_required
def api_download_specs(solution_id):
    """Download generated specs as a ZIP file."""
    import io
    import zipfile

    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)

    try:
        from app.modules.solutions_strategic.v2.services.spec_generator import SolutionSpecGenerator
        generator = SolutionSpecGenerator(solution_id)
        bundle = generator.generate()
    except Exception as e:
        db.session.rollback()
        logger.error("Spec download failed for solution %s: %s", solution_id, e)
        return jsonify({"success": False, "error": "Failed to generate specs. Please try again."}), 500

    if not bundle.get("success", True):
        error_msg = bundle.get("errors", [{}])[0].get("message", "Generation failed")
        return jsonify({"success": False, "error": error_msg, "errors": bundle.get("errors", [])}), 422

    # Build ZIP in memory
    buf = io.BytesIO()
    slug = bundle["solution_name"].lower().replace(" ", "-")[:40]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # OpenAPI
        import yaml
        try:
            openapi_str = yaml.dump(dict(bundle["openapi"]), default_flow_style=False, sort_keys=False, allow_unicode=True)
        except Exception:
            openapi_str = json.dumps(bundle["openapi"], indent=2)
        zf.writestr(f"{slug}/openapi.yaml", openapi_str)

        # JSON Schemas
        for schema_name, schema in (bundle.get("schemas") or {}).items():
            zf.writestr(f"{slug}/schemas/{schema_name}.json", json.dumps(dict(schema), indent=2))

        # AsyncAPI
        if bundle.get("asyncapi"):
            try:
                asyncapi_str = yaml.dump(dict(bundle["asyncapi"]), default_flow_style=False, sort_keys=False, allow_unicode=True)
            except Exception:
                asyncapi_str = json.dumps(bundle["asyncapi"], indent=2)
            zf.writestr(f"{slug}/asyncapi.yaml", asyncapi_str)

        # Contract Tests
        if bundle.get("contract_tests"):
            zf.writestr(f"{slug}/tests/contract_tests.json", json.dumps(bundle["contract_tests"], indent=2))

        # Summary + README
        zf.writestr(f"{slug}/GENERATED.md", _build_readme(bundle))

        # Getting Started guide
        zf.writestr(f"{slug}/GETTING_STARTED.md", _build_getting_started(slug))

    buf.seek(0)
    from flask import send_file
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{slug}-specs.zip",
    )


def _build_readme(bundle):
    """Build a README for the generated spec bundle."""
    s = bundle.get("summary", {})
    sol = s.get("source_data", {})
    specs = s.get("specs_generated", {})
    maturity = s.get("spec_maturity", {})
    lines = [
        f"# {bundle['solution_name']} — Generated API Contracts",
        "",
        f"Generated by Entelim on {bundle['generated_at']}",
        f"Solution ID: {bundle['solution_id']}",
        f"Spec Version: {bundle.get('version', '1.0.0')}",
        f"Spec Maturity: {maturity.get('score', 0):.0%} ({maturity.get('rating', 'unknown')})",
        "",
        "## What's in this bundle",
        "",
        f"- **openapi.yaml** — OpenAPI 3.1 spec ({specs.get('openapi_paths', 0)} paths, {specs.get('openapi_schemas', 0)} schemas)",
        f"- **schemas/** — {specs.get('json_schemas', 0)} standalone JSON Schemas",
    ]
    if specs.get("asyncapi_channels", 0) > 0:
        lines.append(f"- **asyncapi.yaml** — AsyncAPI 2.6 spec ({specs['asyncapi_channels']} channels)")
    if specs.get("contract_tests", 0) > 0:
        lines.append(f"- **tests/contract_tests.json** — {specs['contract_tests']} contract test definitions")
    lines.append("- **GETTING_STARTED.md** — Developer onboarding guide")
    lines += [
        "",
        "## Source architecture data",
        "",
        "| Element Type | Count |",
        "|---|---|",
        f"| Application elements | {sol.get('app_elements', 0)} |",
        f"| Business elements | {sol.get('business_elements', 0)} |",
        f"| Technology elements | {sol.get('tech_elements', 0)} |",
        f"| Integration flows | {sol.get('integration_flows', 0)} |",
        f"| Requirements | {sol.get('requirements', 0)} |",
        f"| Quality attributes | {sol.get('quality_attributes', 0)} |",
        f"| SLAs | {sol.get('slas', 0)} |",
        f"| ArchiMate elements | {sol.get('archimate_elements', 0)} |",
        "",
        "## Traceability",
        "",
        "Every path and schema includes `x-archimate-source` linking back to the",
        "ArchiMate element that generated it. Use these IDs to trace code back to",
        "the approved architecture in Entelim",
        "",
        "## Warnings",
        "",
    ]
    for w in bundle.get("warnings", []):
        lines.append(f"- **{w.get('code', '')}:** {w.get('message', '')}")
    if not bundle.get("warnings"):
        lines.append("No warnings.")
    lines.append("")
    return "\n".join(lines)


def _build_getting_started(slug):
    """Build a developer-facing getting started guide."""
    return f"""# Getting Started with {slug} API Contracts

## Prerequisites

- [openapi-generator-cli](https://openapi-generator.tech/docs/installation/) (v7+)
- Python 3.10+ or Node.js 18+ (for generated server/client)
- Docker (optional, for mock server)

## Generate Server Stubs

### Python (Flask)
```bash
openapi-generator-cli generate -i openapi.yaml -g python-flask -o server/
cd server && pip install -r requirements.txt && python -m openapi_server
```

### Python (FastAPI)
```bash
openapi-generator-cli generate -i openapi.yaml -g python-fastapi -o server/
cd server && pip install -r requirements.txt && uvicorn main:app
```

### TypeScript (Express)
```bash
openapi-generator-cli generate -i openapi.yaml -g typescript-express-server -o server/
cd server && npm install && npm start
```

### Java (Spring Boot)
```bash
openapi-generator-cli generate -i openapi.yaml -g spring -o server/
cd server && mvn spring-boot:run
```

## Generate Client SDKs

```bash
# TypeScript/Axios
openapi-generator-cli generate -i openapi.yaml -g typescript-axios -o client-ts/

# Python
openapi-generator-cli generate -i openapi.yaml -g python -o client-py/
```

## Run Mock Server

Using [Prism](https://stoplight.io/open-source/prism):
```bash
npx @stoplight/prism-cli mock openapi.yaml --port 4010
```

Or using Docker:
```bash
docker run --rm -p 4010:4010 -v $(pwd):/spec stoplight/prism:4 mock /spec/openapi.yaml -h 0.0.0.0
```

## Run Contract Tests

The `tests/contract_tests.json` file contains test definitions generated from
the OpenAPI spec. Use your preferred test runner to validate implementations:

```python
import json, requests

with open('tests/contract_tests.json') as f:
    tests = json.load(f)

for test in tests:
    resp = requests.request(test['method'], f'http://localhost:4010{{test["path"]}}')
    assert resp.status_code == test['expected_status'], f'{{test["test_name"]}} failed'
```

## Architecture Traceability

Every endpoint and schema includes `x-archimate-source` linking to the
ArchiMate element in Entelim that generated it. Use this to:

1. Understand WHY an endpoint exists (architecture rationale)
2. Trace code changes back to architecture decisions
3. Verify implementation matches approved design

## Next Steps

1. Generate server stubs from `openapi.yaml`
2. Implement business logic in the generated route handlers
3. Run contract tests to validate your implementation
4. Deploy and link back to the solution in Entelim
"""


# =============================================================================
# ARCHITECTURE DECISION RECORDS (GOV-02)
# =============================================================================


VALID_DECISION_TYPES = {"technology_choice", "vendor_selection", "pattern_selection", "integration_approach"}
VALID_DECISION_STATUSES = {"proposed", "approved", "rejected", "superseded"}


@solution_design_bp.route("/<int:solution_id>/decisions", methods=["GET"])
@login_required
def list_decisions(solution_id: int):
    """List architecture decisions for a solution."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    from app.models.architecture_decision import ArchitectureDecision

    status_filter = request.args.get("status")
    type_filter = request.args.get("decision_type")

    query = ArchitectureDecision.query.filter_by(solution_id=solution_id)
    if status_filter and status_filter in VALID_DECISION_STATUSES:
        query = query.filter_by(status=status_filter)
    if type_filter and type_filter in VALID_DECISION_TYPES:
        query = query.filter_by(decision_type=type_filter)

    decisions = query.order_by(ArchitectureDecision.created_at.desc()).all()
    return jsonify({"success": True, "data": [d.to_dict() for d in decisions]})


@solution_design_bp.route("/<int:solution_id>/decisions", methods=["POST"])
@login_required
def create_decision(solution_id: int):
    """Create a new architecture decision record."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    try:
        from app.models.architecture_decision import ArchitectureDecision

        body = request.get_json(silent=True) or {}
        title = (body.get("title") or "").strip()
        if not title:
            return jsonify({"success": False, "error": "title is required"}), 400

        decision_type = body.get("decision_type")
        if decision_type and decision_type not in VALID_DECISION_TYPES:
            return jsonify({"success": False, "error": f"Invalid decision_type. Must be one of: {', '.join(sorted(VALID_DECISION_TYPES))}"}), 400

        # Generate decision_id — DB column is NOT NULL (pre-dates nullable migration)
        import time as _time
        adr_id_str = f"ADR-S{solution_id}-{int(_time.time() * 1000) % 1000000:06d}"

        adr = ArchitectureDecision(
            solution_id=solution_id,
            decision_id=adr_id_str,
            title=title,
            status="proposed",
            decision_type=decision_type,
            context=body.get("context"),
            decision=body.get("decision"),
            rationale=body.get("rationale"),
            alternatives=body.get("alternatives"),
            constraints=body.get("constraints"),
            consequences=body.get("consequences"),
            related_element_ids=body.get("related_element_ids"),
            decided_by_id=current_user.id,
            decided_at=datetime.utcnow(),
        )
        # Explicitly set organization_id in case before_flush event is not wired
        if not getattr(adr, "organization_id", None):
            org_id = getattr(g, "current_org_id", None) or getattr(current_user, "organization_id", None)
            if org_id:
                adr.organization_id = org_id
        db.session.add(adr)
        db.session.commit()
        logger.info("ADR created: id=%s solution=%s title=%s", adr.id, solution_id, title)
        return jsonify({"success": True, "data": adr.to_dict()}), 201
    except Exception as _adr_err:
        db.session.rollback()
        err_msg = str(_adr_err)
        logger.error("create_decision failed for solution %s: %s", solution_id, err_msg, exc_info=True)
        return jsonify({"success": False, "error": "Failed to create decision", "detail": err_msg}), 500


@solution_design_bp.route("/<int:solution_id>/decisions/<int:decision_id>", methods=["PUT"])
@login_required
def update_decision(solution_id: int, decision_id: int):
    """Update an architecture decision record (only while proposed)."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    from app.models.architecture_decision import ArchitectureDecision

    adr = ArchitectureDecision.query.filter_by(id=decision_id, solution_id=solution_id).first()
    if not adr:
        return jsonify({"success": False, "error": "Decision not found"}), 404

    if adr.status not in ("proposed", "rejected"):
        return jsonify({"success": False, "error": f"Cannot edit a decision with status '{adr.status}'"}), 409

    body = request.get_json(silent=True) or {}

    if "title" in body:
        title = (body["title"] or "").strip()
        if not title:
            return jsonify({"success": False, "error": "title cannot be empty"}), 400
        adr.title = title

    if "decision_type" in body:
        if body["decision_type"] and body["decision_type"] not in VALID_DECISION_TYPES:
            return jsonify({"success": False, "error": f"Invalid decision_type. Must be one of: {', '.join(sorted(VALID_DECISION_TYPES))}"}), 400
        adr.decision_type = body["decision_type"]

    for field in ("context", "decision", "rationale", "consequences"):
        if field in body:
            setattr(adr, field, body[field])

    for json_field in ("alternatives", "constraints", "related_element_ids"):
        if json_field in body:
            setattr(adr, json_field, body[json_field])

    db.session.commit()
    logger.info("ADR updated: id=%s solution=%s", decision_id, solution_id)
    return jsonify({"success": True, "data": adr.to_dict()})


@solution_design_bp.route("/<int:solution_id>/decisions/<int:decision_id>/approve", methods=["POST"])
@login_required
def approve_decision(solution_id: int, decision_id: int):
    """Approve an architecture decision (ARB workflow)."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    from app.models.architecture_decision import ArchitectureDecision

    adr = ArchitectureDecision.query.filter_by(id=decision_id, solution_id=solution_id).first()
    if not adr:
        return jsonify({"success": False, "error": "Decision not found"}), 404

    if adr.status != "proposed":
        return jsonify({"success": False, "error": f"Only proposed decisions can be approved (current: {adr.status})"}), 409

    adr.status = "approved"
    adr.approved_by_id = current_user.id
    adr.approved_at = datetime.utcnow()
    adr.rejection_reason = None
    db.session.commit()
    logger.info("ADR approved: id=%s solution=%s by=%s", decision_id, solution_id, current_user.id)
    return jsonify({"success": True, "data": adr.to_dict()})


@solution_design_bp.route("/<int:solution_id>/decisions/<int:decision_id>/reject", methods=["POST"])
@login_required
def reject_decision(solution_id: int, decision_id: int):
    """Reject an architecture decision with a reason."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    from app.models.architecture_decision import ArchitectureDecision

    adr = ArchitectureDecision.query.filter_by(id=decision_id, solution_id=solution_id).first()
    if not adr:
        return jsonify({"success": False, "error": "Decision not found"}), 404

    if adr.status != "proposed":
        return jsonify({"success": False, "error": f"Only proposed decisions can be rejected (current: {adr.status})"}), 409

    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "reason is required when rejecting"}), 400

    adr.status = "rejected"
    adr.rejection_reason = reason
    db.session.commit()
    logger.info("ADR rejected: id=%s solution=%s reason=%s", decision_id, solution_id, reason[:80])
    return jsonify({"success": True, "data": adr.to_dict()})


# =============================================================================
# API SPEC REGISTRY — Publish, discover, and consume API contracts (CODEGEN-05)
# =============================================================================


def _bump_version(version_str):
    """Increment the patch component of a semver string (e.g. 1.0.2 → 1.0.3)."""
    parts = (version_str or "0.0.0").split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        parts[2] = str(int(parts[2]) + 1)
    except ValueError:
        parts[2] = "1"
    return ".".join(parts[:3])


@solution_design_bp.route("/<int:solution_id>/api-specs/publish", methods=["POST"])
@login_required
def api_publish_spec(solution_id):
    """Publish current OpenAPI spec to the enterprise API registry.

    Generates the spec from the solution blueprint, auto-increments the version,
    and stores it in the published_api_specs table for cross-team discovery.
    """
    import hashlib

    from app.models.published_api_spec import PublishedAPISpec

    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)

    # Generate the current spec
    try:
        from app.modules.solutions_strategic.v2.services.spec_generator import SolutionSpecGenerator
        generator = SolutionSpecGenerator(solution_id)
        bundle = generator.generate()
    except Exception as e:
        db.session.rollback()
        logger.error("Spec publish failed — generation error for solution %s: %s", solution_id, e)
        return jsonify({"success": False, "error": str(e)}), 500

    if not bundle.get("success", True):
        return jsonify({"success": False, "error": "Spec generation failed", "details": bundle.get("errors")}), 422

    openapi_spec = bundle.get("openapi")
    if not openapi_spec:
        return jsonify({"success": False, "error": "No OpenAPI spec was generated"}), 422

    # Determine next version
    latest = (
        PublishedAPISpec.query
        .filter_by(solution_id=solution_id, spec_type="openapi")
        .order_by(PublishedAPISpec.published_at.desc())
        .first()
    )
    next_version = _bump_version(latest.spec_version if latest else None)

    # Compute content hash for dedup / drift detection
    spec_json = json.dumps(openapi_spec, sort_keys=True)
    spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()

    # Skip publish if identical to latest
    if latest and latest.spec_hash == spec_hash:
        return jsonify({
            "success": True,
            "spec_id": latest.id,
            "version": latest.spec_version,
            "message": "Spec unchanged — latest version is identical",
            "duplicate": True,
        })

    # Count endpoints
    paths = openapi_spec.get("paths", {})
    endpoint_count = sum(
        len([m for m in methods if m.lower() in ("get", "post", "put", "patch", "delete")])
        for methods in paths.values()
        if isinstance(methods, dict)
    )

    record = PublishedAPISpec(
        solution_id=solution_id,
        spec_type="openapi",
        spec_version=next_version,
        spec_hash=spec_hash,
        spec_content=openapi_spec,
        title=openapi_spec.get("info", {}).get("title", solution.solution_name),
        description=openapi_spec.get("info", {}).get("description", ""),
        endpoint_count=endpoint_count,
        published_by_id=current_user.id,
        published_at=datetime.utcnow(),
        status="published",
    )
    db.session.add(record)

    # Also publish AsyncAPI if present
    asyncapi_spec = bundle.get("asyncapi")
    async_record = None
    if asyncapi_spec:
        async_json = json.dumps(asyncapi_spec, sort_keys=True)
        async_hash = hashlib.sha256(async_json.encode("utf-8")).hexdigest()
        channels = asyncapi_spec.get("channels", {})

        async_latest = (
            PublishedAPISpec.query
            .filter_by(solution_id=solution_id, spec_type="asyncapi")
            .order_by(PublishedAPISpec.published_at.desc())
            .first()
        )
        async_version = _bump_version(async_latest.spec_version if async_latest else None)

        if not (async_latest and async_latest.spec_hash == async_hash):
            async_record = PublishedAPISpec(
                solution_id=solution_id,
                spec_type="asyncapi",
                spec_version=async_version,
                spec_hash=async_hash,
                spec_content=asyncapi_spec,
                title=asyncapi_spec.get("info", {}).get("title", f"{solution.solution_name} Events"),
                description=asyncapi_spec.get("info", {}).get("description", ""),
                endpoint_count=len(channels),
                published_by_id=current_user.id,
                published_at=datetime.utcnow(),
                status="published",
            )
            db.session.add(async_record)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("Spec publish failed — DB error for solution %s: %s", solution_id, e)
        return jsonify({"success": False, "error": "Database error while publishing"}), 500

    logger.info(
        "API spec published: solution=%s version=%s endpoints=%d hash=%s",
        solution_id, next_version, endpoint_count, spec_hash[:12],
    )
    result = {
        "success": True,
        "spec_id": record.id,
        "version": next_version,
        "endpoint_count": endpoint_count,
    }
    if async_record:
        result["asyncapi_spec_id"] = async_record.id
        result["asyncapi_version"] = async_record.spec_version
    return jsonify(result), 201


@solution_design_bp.route("/<int:solution_id>/api-specs/latest", methods=["GET"])
@login_required
def api_get_latest_spec(solution_id):
    """Get the latest published API spec for a solution.

    Query params:
        type — openapi (default) or asyncapi
    """
    from app.models.published_api_spec import PublishedAPISpec

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        abort(403)

    spec_type = request.args.get("type", "openapi")
    if spec_type not in ("openapi", "asyncapi"):
        return jsonify({"success": False, "error": "type must be openapi or asyncapi"}), 400

    latest = (
        PublishedAPISpec.query
        .filter_by(solution_id=solution_id, spec_type=spec_type, status="published")
        .order_by(PublishedAPISpec.published_at.desc())
        .first()
    )
    if not latest:
        return jsonify({"success": False, "error": "No published spec found"}), 404

    data = latest.to_dict()
    data["spec_content"] = latest.spec_content
    data["solution_name"] = solution.solution_name
    return jsonify({"success": True, "data": data})


@solution_design_bp.route("/api/registry/specs", methods=["GET"])
@login_required
def api_registry_list_specs():
    """Enterprise-wide API spec registry — list all published specs across solutions.

    Query params:
        status   — filter by status (draft, published, deprecated). Default: published
        type     — filter by spec_type (openapi, asyncapi). Optional.
        page     — pagination page (default 1)
        per_page — items per page (default 50, max 200)
    """
    from app.models.published_api_spec import PublishedAPISpec

    status_filter = request.args.get("status", "published")
    type_filter = request.args.get("type")
    page = safe_int_arg('page', 1, minimum=1)
    per_page = min(safe_int_arg('per_page', 50, minimum=1, maximum=500), 200)

    query = (
        db.session.query(PublishedAPISpec, Solution.name)
        .join(Solution, PublishedAPISpec.solution_id == Solution.id)
    )

    if status_filter:
        query = query.filter(PublishedAPISpec.status == status_filter)
    if type_filter:
        query = query.filter(PublishedAPISpec.spec_type == type_filter)

    query = query.order_by(PublishedAPISpec.published_at.desc())

    total = query.count()
    results = query.offset((page - 1) * per_page).limit(per_page).all()

    specs = []
    for spec, solution_name in results:
        d = spec.to_dict()
        d["solution_name"] = solution_name
        specs.append(d)

    return jsonify({
        "success": True,
        "data": specs,
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@solution_design_bp.route("/api/registry/specs/<int:spec_id>", methods=["GET"])
@login_required
def api_registry_get_spec(spec_id):
    """Get a single published API spec by ID.

    GET /solutions/api/registry/specs/<spec_id>
    """
    from app.models.published_api_spec import PublishedAPISpec

    spec = PublishedAPISpec.query.get(spec_id)
    if not spec:
        return jsonify({"success": False, "error": "Spec not found"}), 404

    data = spec.to_dict()
    data["solution_name"] = spec.solution.solution_name if spec.solution else None
    data["spec_content"] = spec.spec_content  # include full content for detail view
    return jsonify({"success": True, "data": data})


@solution_design_bp.route("/api/registry/specs/<int:spec_id>/openapi", methods=["GET"])
@login_required
def api_registry_download_openapi(spec_id):
    """Download the raw OpenAPI JSON for a published spec.

    GET /solutions/api/registry/specs/<spec_id>/openapi
    Returns the spec_content JSON as a downloadable file.
    """
    from app.models.published_api_spec import PublishedAPISpec
    from flask import Response
    import json as json_mod

    spec = PublishedAPISpec.query.get(spec_id)
    if not spec:
        return jsonify({"success": False, "error": "Spec not found"}), 404

    filename = f"openapi-solution-{spec.solution_id}-v{spec.spec_version}.json"
    content = json_mod.dumps(spec.spec_content, indent=2)
    return Response(
        content,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},  # raw-html-ok: filename is built from spec.solution_id (int) + spec.spec_version, never free text
    )


@solution_design_bp.route("/api-registry", methods=["GET"])
@login_required
def api_registry_page():
    """Render the enterprise-wide API registry catalog page.

    GET /solutions/api-registry
    """
    return render_template("api_registry.html")


# =============================================================================
# RUNTIME COMPLIANCE WEBHOOK (CODEGEN-06)
# =============================================================================


@solution_design_bp.route("/api/runtime/health-report", methods=["POST"])
# csrf.exempt: webhook receiver — external services cannot include CSRF tokens
@csrf.exempt
def runtime_health_report():
    """Receive a runtime health report from a deployed service.

    POST /solutions/api/runtime/health-report
    CSRF-exempt: external services cannot include CSRF tokens.

    Body: {
        "solution_id": int,
        "service_name": str,
        "spec_hash": str,
        "endpoints_active": [str],
        "endpoints_erroring": [str],
        "avg_latency_ms": float,
        "error_rate_pct": float,
        "uptime_pct": float
    }
    """
    import hmac

    from app.models.solution_models import Solution
    from app.models.published_api_spec import PublishedAPISpec
    from app.models.compliance_check import RuntimeComplianceCheck

    # Authenticate the reporter. Being CSRF-exempt removes the browser-origin
    # check, and there was nothing behind it: any caller could POST a report for
    # any solution_id, in any tenant, and have it written as a compliance record.
    # For a governance product, forged compliance evidence is not a small thing -
    # the records are the deliverable.
    #
    # Fails closed when unconfigured rather than accepting anonymous reports,
    # which is affordable here because nothing posts to this endpoint yet: no
    # client, generated service, test or document references it. An operator
    # enabling it sets the token on both ends at once.
    expected = current_app.config.get("RUNTIME_REPORT_TOKEN") or os.environ.get("RUNTIME_REPORT_TOKEN")
    if not expected:
        return jsonify({
            "received": False,
            "error": "Runtime reporting is not enabled. Set RUNTIME_REPORT_TOKEN to enable it.",
        }), 503
    supplied = request.headers.get("X-Runtime-Report-Token", "")
    if not supplied or not hmac.compare_digest(supplied, expected):
        current_app.logger.warning(
            "Rejected runtime health report with bad/missing token from IP %s",
            request.remote_addr,
        )
        return jsonify({"received": False, "error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}

    solution_id = body.get("solution_id")
    if not solution_id or not isinstance(solution_id, int):
        return jsonify({"received": False, "error": "solution_id is required and must be an integer"}), 400

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"received": False, "error": f"Solution {solution_id} not found"}), 404

    spec_hash = body.get("spec_hash", "")
    service_name = body.get("service_name", "unknown")

    # Find matching spec by solution_id and spec_hash
    published_spec = None
    if spec_hash:
        published_spec = (
            PublishedAPISpec.query
            .filter_by(solution_id=solution_id, spec_hash=spec_hash, status="published")
            .order_by(PublishedAPISpec.published_at.desc())
            .first()
        )

    # Fall back to latest published spec for this solution
    latest_spec = (
        PublishedAPISpec.query
        .filter_by(solution_id=solution_id, status="published")
        .order_by(PublishedAPISpec.published_at.desc())
        .first()
    )

    spec_in_sync = published_spec is not None
    target_spec = published_spec or latest_spec

    if not target_spec:
        return jsonify({
            "received": True,
            "spec_in_sync": False,
            "sla_compliant": False,
            "error": "No published spec found for this solution",
        }), 200

    # SLA compliance: latency < 500ms, error_rate < 5%, uptime > 99%
    try:
        avg_latency_ms = float(body.get("avg_latency_ms") or 0)
        error_rate_pct = float(body.get("error_rate_pct") or 0)
        uptime_pct = float(body.get("uptime_pct") or 100)
    except (ValueError, TypeError):
        return jsonify({"received": False, "error": "avg_latency_ms, error_rate_pct and uptime_pct must be numbers"}), 400

    sla_violations = []
    if avg_latency_ms >= 500:
        sla_violations.append({
            "type": "latency",
            "threshold_ms": 500,
            "actual_ms": avg_latency_ms,
        })
    if error_rate_pct >= 5:
        sla_violations.append({
            "type": "error_rate",
            "threshold_pct": 5,
            "actual_pct": error_rate_pct,
        })
    if uptime_pct < 99:
        sla_violations.append({
            "type": "uptime",
            "threshold_pct": 99,
            "actual_pct": uptime_pct,
        })

    sla_compliant = len(sla_violations) == 0

    endpoints_active = body.get("endpoints_active") or []
    endpoints_erroring = body.get("endpoints_erroring") or []

    # Determine endpoints in spec vs active
    spec_endpoints = []
    if target_spec.spec_content and isinstance(target_spec.spec_content, dict):
        paths = target_spec.spec_content.get("paths", {})
        for path, methods in paths.items():
            for method in methods:
                if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    spec_endpoints.append(f"{method.upper()} {path}")

    active_set = set(endpoints_active)
    spec_set = set(spec_endpoints)
    missing_endpoints = list(spec_set - active_set)
    extra_endpoints = list(active_set - spec_set)

    # Compliance score: fraction of spec endpoints that are active and not erroring
    erroring_set = set(endpoints_erroring)
    if spec_set:
        healthy = len(spec_set & active_set - erroring_set)
        compliance_score = round(healthy / len(spec_set), 4)
    else:
        compliance_score = 1.0 if sla_compliant else 0.0

    # Determine overall status
    if not endpoints_active:
        status = "down"
    elif sla_violations or missing_endpoints or endpoints_erroring:
        status = "drifted"
    else:
        status = "passed"

    check = RuntimeComplianceCheck(
        solution_id=solution_id,
        published_spec_id=target_spec.id,
        service_url=service_name,
        status=status,
        compliance_score=compliance_score,
        missing_endpoints=missing_endpoints,
        extra_endpoints=extra_endpoints,
        schema_mismatches=[],
        sla_violations=sla_violations,
    )
    db.session.add(check)
    db.session.commit()

    logger.info(
        "Runtime health report received: solution=%s service=%s spec_in_sync=%s sla_compliant=%s score=%.2f",
        solution_id, service_name, spec_in_sync, sla_compliant, compliance_score,
    )

    return jsonify({
        "received": True,
        "spec_in_sync": spec_in_sync,
        "sla_compliant": sla_compliant,
        "compliance_score": compliance_score,
        "status": status,
    }), 200


@solution_design_bp.route("/api/runtime/status/<int:solution_id>", methods=["GET"])
@login_required
def runtime_status(solution_id):
    """Return the latest runtime compliance status for a solution.

    GET /solutions/api/runtime/status/<solution_id>
    """
    from app.models.solution_models import Solution
    from app.models.compliance_check import RuntimeComplianceCheck

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    check = (
        RuntimeComplianceCheck.query
        .filter_by(solution_id=solution_id)
        .order_by(RuntimeComplianceCheck.checked_at.desc())
        .first()
    )

    if not check:
        return jsonify({
            "solution_id": solution_id,
            "service_name": None,
            "spec_in_sync": None,
            "sla_compliant": None,
            "last_report": None,
            "compliance_score": None,
            "status": "unknown",
        })

    sla_compliant = not bool(check.sla_violations)
    spec_in_sync = check.status not in ("drifted",)

    return jsonify({
        "solution_id": solution_id,
        "service_name": check.service_url,
        "spec_in_sync": spec_in_sync,
        "sla_compliant": sla_compliant,
        "last_report": check.checked_at.isoformat() if check.checked_at else None,
        "compliance_score": check.compliance_score,
        "status": check.status if check.status in ("passed", "drifted", "down") else "unknown",
    })


# =============================================================================
# EXPORT BLUEPRINT AS PDF (browser print-to-PDF)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/export/blueprint", methods=["GET"])
@login_required
def export_solution_blueprint(solution_id: int):
    """Export Solution Architecture Blueprint as a print-optimized HTML page.

    Opens in a new tab with Ctrl+P / Save as PDF. Standalone document with
    cover page, TOC, all 14 viewpoint sections, element tables, narratives,
    completeness scores, and ARB readiness assessment.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        abort(403)

    ctx = _build_blueprint_context(solution)

    # Build section elements by viewpoint for the export
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.archimate_core import ArchiMateElement as ArchElement

        links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        element_ids = [link.element_id for link in links]
        elements = ArchElement.query.filter(ArchElement.id.in_(element_ids)).all() if element_ids else []
        role_map = {link.element_id: getattr(link, "element_role", "supporting") for link in links}

        # Group by section based on element type → viewpoint mapping
        section_elements = {}
        type_to_section = {}
        for sec_id, sec_def in ctx["section_definitions"].items():
            for req_type in sec_def.get("required_types", []):
                type_to_section.setdefault(req_type, sec_id)

        for el in elements:
            el_type = getattr(el, "type", "")
            sec = type_to_section.get(el_type, "executive_summary")
            section_elements.setdefault(sec, []).append({
                "name": el.name,
                "type": el_type,
                "element_type": el_type,
                "layer": getattr(el, "layer", ""),
                "description": getattr(el, "description", "") or "",
                "role": role_map.get(el.id, "supporting"),
            })
        ctx["section_elements"] = section_elements
    except Exception as e:
        logger.warning("Blueprint export: could not group elements by section: %s", e)
        ctx.setdefault("section_elements", {})

    ctx["narratives"] = solution.section_narratives or {}
    ctx["export_timestamp"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    return render_template("solutions/export_blueprint.html", **ctx)


# =============================================================================
# EXPORT SOLUTION AS SAD PDF (REQ-SAD-001)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/export/sad", methods=["GET"])
@login_required
def export_solution_sad(solution_id: int):
    """Export Solution Architecture Document as a print-optimized HTML page (REQ-SAD-001).

    Opens in a new tab with print-to-PDF controls. Uses the same data context
    as the detail page but renders a standalone, styled document.
    """
    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)

    ctx = _build_solution_detail_context(solution)

    # Load risks, metrics, TCO for the export
    try:
        from app.models.solution_lifecycle_models import SolutionRisk, SolutionMetric, SolutionTCOItem
        ctx["risks"] = SolutionRisk.query.filter_by(solution_id=solution_id).all()
        ctx["metrics"] = SolutionMetric.query.filter_by(solution_id=solution_id).all()
        ctx["tco_items"] = SolutionTCOItem.query.filter_by(solution_id=solution_id).all()
    except Exception as e:
        logger.debug(f"Could not load lifecycle entities for SAD export: {e}")
        ctx.setdefault("risks", [])
        ctx.setdefault("metrics", [])
        ctx.setdefault("tco_items", [])

    # Load ArchiMate elements linked to this solution
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.archimate_core import ArchiMateElement as ArchElement
        links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        element_ids = [link.element_id for link in links]
        elements = ArchElement.query.filter(ArchElement.id.in_(element_ids)).all() if element_ids else []
        arch_data = []
        role_map = {link.element_id: getattr(link, "element_role", "supporting") for link in links}
        for el in elements:
            arch_data.append({
                "name": el.name,
                "element_type": getattr(el, "element_type", getattr(el, "type", "—")),
                "layer": getattr(el, "layer", "—"),
                "role": role_map.get(el.id, "supporting"),
            })
        ctx["archimate_elements"] = arch_data
    except Exception as e:
        logger.debug(f"Could not load ArchiMate elements for SAD export: {e}")
        ctx.setdefault("archimate_elements", [])

    # Maturity score
    ctx["maturity_score"] = ctx.get("document_maturity_score", ctx.get("maturity_score", 0))

    ctx["export_timestamp"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    return render_template("solutions/export_sad.html", **ctx)


# =============================================================================
# EXPORT SOLUTION AS MARKDOWN (ENT-011)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/export-markdown", methods=["GET"])
@solution_design_bp.route("/<int:solution_id>/export.md", methods=["GET"])
@login_required
def export_solution_markdown(solution_id: int):
    """Export solution as Markdown document (ENT-011)."""
    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)
    # Build markdown
    lines = [
        f"# {solution.name}",
        "",
        f"**Type:** {solution.solution_type or 'N/A'}  ",
        f"**Domain:** {solution.business_domain or 'N/A'}  ",
        f"**Status:** {solution.status or 'N/A'}  ",
        f"**ADM Phase:** {solution.adm_phase or 'A'}  ",
        f"**Governance:** {solution.governance_status or 'draft'}  ",
        "",
        "## Description",
        "",
        solution.description or "_No description provided._",
        "",
    ]
    # Add drivers/goals/constraints/requirements from DB
    try:
        from app.models.solution_architect_models import (
            SolutionAnalysisSession, SolutionProblemDefinition,
            SolutionDriver, SolutionGoal, SolutionConstraint, SolutionRequirement,
        )
        sessions = SolutionAnalysisSession.query.filter_by(solution_id=solution_id).all()
        pd_ids = []
        for s in sessions:
            pds = SolutionProblemDefinition.query.filter_by(session_id=s.id).all()
            pd_ids.extend([p.id for p in pds])
        if pd_ids:
            drivers = SolutionDriver.query.filter(SolutionDriver.problem_id.in_(pd_ids)).all()
            goals = SolutionGoal.query.filter(SolutionGoal.problem_id.in_(pd_ids)).all()
            constraints = SolutionConstraint.query.filter(SolutionConstraint.problem_id.in_(pd_ids)).all()
            requirements = SolutionRequirement.query.filter(SolutionRequirement.problem_id.in_(pd_ids)).all()
            if drivers:
                lines += ["## Phase A: Drivers", ""]
                for d in drivers:
                    lines.append(f"- **{d.name}** ({d.driver_type.value if d.driver_type else 'internal'}): {d.description or ''}")
                lines.append("")
            if goals:
                lines += ["## Phase A: Goals", ""]
                for g in goals:
                    lines.append(f"- **{g.name}**: {g.description or ''}")
                lines.append("")
            if constraints:
                lines += ["## Phase A: Constraints", ""]
                for c in constraints:
                    lines.append(f"- **{c.name}** ({c.constraint_type.value if c.constraint_type else 'general'}): {c.description or ''}")
                lines.append("")
            if requirements:
                lines += ["## Phase B-D: Requirements", ""]
                for r in requirements:
                    lines.append(f"- **[{r.requirement_type or 'functional'}]** {r.name}: {r.description or ''}")
                lines.append("")
    except Exception as e:
        logger.debug(f"Could not load architect entities for export: {e}")
    # Risks, Metrics, TCO
    try:
        from app.models.solution_lifecycle_models import SolutionRisk, SolutionMetric, SolutionTCOItem
        risks = SolutionRisk.query.filter_by(solution_id=solution_id).all()
        metrics = SolutionMetric.query.filter_by(solution_id=solution_id).all()
        tco = SolutionTCOItem.query.filter_by(solution_id=solution_id).all()
        if risks:
            lines += ["## Risks", ""]
            for r in risks:
                lines.append(f"- **[{r.impact}/{r.probability}]** {r.risk_description}: {r.mitigation or 'No mitigation'}")
            lines.append("")
        if metrics:
            lines += ["## Success Metrics", ""]
            for m in metrics:
                lines.append(f"- **{m.name}**: baseline={m.baseline_value}, target={m.target_value}")
            lines.append("")
        if tco:
            total = sum(float(t.amount or 0) for t in tco)
            lines += [f"## TCO (Total: {total:,.0f})", ""]
            for t in tco:
                recurring = "recurring" if t.is_recurring else "one-time"
                lines.append(f"- {t.cost_category} ({recurring}, yr{t.year}): {t.amount:,.0f}")
            lines.append("")
    except Exception as e:
        logger.debug(f"Could not load lifecycle entities for export: {e}")
    lines += [
        "---",
        "_Exported from Entelim Enterprise Architecture Platform_",
    ]
    content = "\n".join(lines)
    from flask import Response
    filename = f"{solution.name.replace(' ', '_').replace('/', '-')}_architecture.md"
    return Response(
        content,
        mimetype="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# =============================================================================
# PLT-011: SOLUTION COMMENTS API
# =============================================================================

@solution_design_bp.route("/<int:solution_id>/comments", methods=["GET"])
@login_required
def list_solution_comments(solution_id: int):
    """PLT-011: Return comments for a solution, optionally filtered by section."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        abort(403)
    section = request.args.get("section")
    try:
        from app.models.solution_models import SolutionComment
        q = SolutionComment.query.filter_by(solution_id=solution_id, parent_comment_id=None)
        if section:
            q = q.filter_by(section_name=section)
        comments = q.order_by(SolutionComment.created_at.asc()).all()
        result = []
        for c in comments:
            d = c.to_dict()
            d["replies"] = [r.to_dict() for r in c.replies.order_by(SolutionComment.created_at.asc()).all()]
            result.append(d)
        return jsonify({"comments": result, "total": len(result)})
    except Exception as e:
        logger.error("PLT-011 list_solution_comments error: %s", e)
        return jsonify({"error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/comments", methods=["POST"])
@login_required
def create_solution_comment(solution_id: int):
    """PLT-011: Create a new section-level comment on a solution."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        abort(403)
    data = request.get_json(silent=True) or {}
    section = (data.get("section") or "").strip()
    content = (data.get("content") or "").strip()
    parent_id = data.get("parent_comment_id")
    if not section or not content:
        return jsonify({"error": "section and content are required"}), 400
    try:
        from app.models.solution_models import SolutionComment
        if section not in SolutionComment.VALID_SECTIONS:
            return jsonify({"error": f"Invalid section. Valid: {SolutionComment.VALID_SECTIONS}"}), 400
        comment = SolutionComment(
            solution_id=solution_id,
            section_name=section,
            author_id=current_user.id,
            author_name=current_user.full_name() or current_user.email,
            content=content,
            parent_comment_id=int(parent_id) if parent_id else None,
        )
        db.session.add(comment)
        db.session.commit()
        return jsonify({"comment": comment.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        logger.error("PLT-011 create_solution_comment error: %s", e)
        return jsonify({"error": str(e)}), 500
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/export/archimate-oef", methods=["GET"])
@login_required
def export_archimate_oef(solution_id):
    """Export solution ArchiMate elements as Open Exchange Format XML (ARC-E01)."""
    from flask import Response

    from app.services.archimate_oef_export_service import archimate_oef_export_service

    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)

    try:
        xml_content = archimate_oef_export_service.export_solution(solution_id)
    except ValueError:
        abort(404)

    filename = f"{solution.name.replace(' ', '_').replace('/', '-')}_archimate.xml"
    return Response(
        xml_content,
        mimetype="application/xml",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@solution_design_bp.route(
    "/<int:solution_id>/versions/<int:version_id>/conditions/<int:condition_idx>/address",
    methods=["POST"],
)
@login_required
@require_roles("admin", "enterprise_architect", "architect")
def address_condition(solution_id: int, version_id: int, condition_idx: int):
    """PLT-016: Mark an approval condition as addressed by the architect."""
    from app.models.solution_governance import SolutionVersion

    Solution.query.get_or_404(solution_id)
    ver = SolutionVersion.query.filter_by(id=version_id, solution_id=solution_id).first()
    if not ver:
        return jsonify({"error": "Version not found"}), 404

    conditions = _normalize_approval_conditions(solution_id, version_id, ver.approval_conditions)
    if condition_idx < 0 or condition_idx >= len(conditions):
        return jsonify({"error": "Condition index out of range"}), 404

    conditions[condition_idx]["status"] = "addressed"
    conditions[condition_idx]["addressed_by_id"] = current_user.id
    conditions[condition_idx]["addressed_at"] = datetime.utcnow().isoformat()
    conditions[condition_idx]["addressed_by_name"] = _condition_actor_name(current_user)
    ver.approval_conditions = conditions
    db.session.flag_modified(ver, "approval_conditions")
    db.session.commit()
    return jsonify({"success": True, "conditions": conditions})


@solution_design_bp.route(
    "/<int:solution_id>/versions/<int:version_id>/conditions/<int:condition_idx>/verify",
    methods=["POST"],
)
@login_required
@require_roles("admin", "enterprise_architect")
def verify_condition(solution_id: int, version_id: int, condition_idx: int):
    """PLT-016: Mark an approval condition as verified by an ARB member."""
    from app.models.solution_governance import SolutionVersion

    Solution.query.get_or_404(solution_id)
    ver = SolutionVersion.query.filter_by(id=version_id, solution_id=solution_id).first()
    if not ver:
        return jsonify({"error": "Version not found"}), 404

    conditions = _normalize_approval_conditions(solution_id, version_id, ver.approval_conditions)
    if condition_idx < 0 or condition_idx >= len(conditions):
        return jsonify({"error": "Condition index out of range"}), 404

    conditions[condition_idx]["status"] = "verified"
    conditions[condition_idx]["verified_by_id"] = current_user.id
    conditions[condition_idx]["verified_at"] = datetime.utcnow().isoformat()
    conditions[condition_idx]["verified_by_name"] = _condition_actor_name(current_user)
    ver.approval_conditions = conditions
    db.session.flag_modified(ver, "approval_conditions")
    db.session.commit()
    return jsonify({"success": True, "conditions": conditions})


@solution_design_bp.route(
    "/<int:solution_id>/versions/<int:version_id>/conditions/<int:condition_idx>/comment",
    methods=["POST"],
)
@login_required
@require_roles("admin", "enterprise_architect", "architect")
def comment_on_condition(solution_id: int, version_id: int, condition_idx: int):
    """PLT-016: Add a comment to an approval condition thread."""
    from app.models.solution_governance import SolutionVersion
    from app.models.solution_models import SolutionComment

    Solution.query.get_or_404(solution_id)
    ver = SolutionVersion.query.filter_by(id=version_id, solution_id=solution_id).first()
    if not ver:
        return jsonify({"error": "Version not found"}), 404

    conditions = _normalize_approval_conditions(solution_id, version_id, ver.approval_conditions)
    if condition_idx < 0 or condition_idx >= len(conditions):
        return jsonify({"error": "Condition index out of range"}), 404

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Comment content is required"}), 400

    author_name = _condition_actor_name(current_user)
    if "comments" not in conditions[condition_idx]:
        conditions[condition_idx]["comments"] = []
    conditions[condition_idx]["comments"].append({
        "author_id": current_user.id,
        "author_name": author_name,
        "content": content,
        "created_at": datetime.utcnow().isoformat(),
    })
    db.session.add(
        SolutionComment(
            solution_id=solution_id,
            section_name=_condition_comment_section_name(version_id, condition_idx),
            author_id=current_user.id,
            author_name=author_name,
            content=content,
        )
    )
    ver.approval_conditions = conditions
    db.session.flag_modified(ver, "approval_conditions")
    db.session.commit()
    return jsonify({
        "success": True,
        "conditions": _normalize_approval_conditions(solution_id, version_id, conditions),
    })


@solution_design_bp.route("/<int:solution_id>/save-as-template", methods=["POST"])
@login_required
def save_solution_as_template(solution_id: int):
    """Save solution entities as a reusable template (ENT-016)."""
    from app.models.solution_governance import SolutionTemplate
    from app.models.solution_architect_models import (
        SolutionAnalysisSession, SolutionProblemDefinition,
        SolutionDriver, SolutionGoal, SolutionConstraint, SolutionRequirement,
    )
    from app.models.solution_lifecycle_models import SolutionRisk, SolutionMetric, SolutionTCOItem, SolutionPlateau
    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)
    data = request.get_json() or {}
    name = (data.get("name") or solution.name or "Untitled Template").strip()[:255]
    description = (data.get("description") or solution.description or "")[:2000]
    domain = (data.get("domain") or solution.business_domain or "")[:100]
    from app.models.solution_sad_models import (
        SolutionQualityAttribute, SolutionSLA,
        SolutionStakeholderSAD, SolutionBusinessElement,
        SolutionAppElement, SolutionTechElement,
    )
    from app.models.solution_models import SolutionArchiMateElement

    entities = {
        "drivers": [], "goals": [], "constraints": [], "requirements": [],
        "risks": [], "metrics": [], "tco_items": [], "plateaus": [],
        "quality_attributes": [], "slas": [], "stakeholders": [],
        "business_elements": [], "app_elements": [], "tech_elements": [],
        "archimate_elements": [],
    }
    pd_ids = []
    if solution.analysis_session_id:
        session = SolutionAnalysisSession.query.get(solution.analysis_session_id)
        if session:
            pds = SolutionProblemDefinition.query.filter_by(session_id=session.id).all()
            pd_ids = [p.id for p in pds]
    if pd_ids:
        for d in SolutionDriver.query.filter(SolutionDriver.problem_id.in_(pd_ids)).all():
            entities["drivers"].append({"name": d.name, "description": d.description or "", "driver_type": getattr(d.driver_type, "value", "internal"), "impact_level": d.impact_level, "urgency": d.urgency, "source": d.source or ""})
        for g in SolutionGoal.query.filter(SolutionGoal.problem_id.in_(pd_ids)).all():
            entities["goals"].append({"name": g.name, "description": g.description or "", "priority": g.priority, "measurement_criteria": g.measurement_criteria or ""})
        for c in SolutionConstraint.query.filter(SolutionConstraint.problem_id.in_(pd_ids)).all():
            entities["constraints"].append({"name": c.name, "description": c.description or "", "constraint_type": getattr(c.constraint_type, "value", "technical"), "value": c.value or "", "severity": c.severity, "source": c.source or ""})
        for r in SolutionRequirement.query.filter(SolutionRequirement.problem_id.in_(pd_ids)).all():
            entities["requirements"].append({"name": r.name, "description": r.description or "", "requirement_type": getattr(r.requirement_type, "value", "functional"), "priority": r.priority, "is_mandatory": r.is_mandatory, "source": r.source or "", "rationale": r.rationale or "", "acceptance_criteria": r.acceptance_criteria or ""})
    for r in SolutionRisk.query.filter_by(solution_id=solution_id).all():
        entities["risks"].append({"risk_description": r.risk_description, "impact": r.impact, "probability": r.probability, "mitigation": r.mitigation or "", "owner": getattr(r, "owner", "") or ""})
    for m in SolutionMetric.query.filter_by(solution_id=solution_id).all():
        entities["metrics"].append({"name": m.name, "unit": m.unit or "", "baseline_value": m.baseline_value or "", "target_value": m.target_value or "", "notes": m.notes or ""})
    for t in SolutionTCOItem.query.filter_by(solution_id=solution_id).all():
        entities["tco_items"].append({"option_label": t.option_label or "Option A", "cost_category": t.cost_category, "is_recurring": t.is_recurring, "year": t.year or 1, "amount": float(t.amount or 0), "notes": t.notes or ""})
    for p in SolutionPlateau.query.filter_by(solution_id=solution_id).all():
        entities["plateaus"].append({"name": p.name, "description": p.description or "", "order": getattr(p, "order", 0)})
    # SAD layer elements
    for q in SolutionQualityAttribute.query.filter_by(solution_id=solution_id).all():
        entities["quality_attributes"].append({"attribute_name": q.attribute_name, "attribute_type": q.attribute_type or "performance", "target_value": q.target_value or "", "verification_method": q.verification_method or "", "notes": q.notes or ""})
    for s in SolutionSLA.query.filter_by(solution_id=solution_id).all():
        entities["slas"].append({"sla_name": s.sla_name, "availability_target": float(s.availability_target) if s.availability_target else None, "response_time_ms": s.response_time_ms, "throughput_tps": s.throughput_tps, "rto_hours": s.rto_hours, "rpo_hours": s.rpo_hours, "support_hours": s.support_hours or "", "notes": ""})
    for s in SolutionStakeholderSAD.query.filter_by(solution_id=solution_id).all():
        entities["stakeholders"].append({"name": s.name, "role": s.role, "organization": s.organization or "", "influence_level": s.influence_level or "medium", "interest_level": s.interest_level or "medium", "engagement_strategy": s.engagement_strategy or "", "notes": s.notes or ""})
    for b in SolutionBusinessElement.query.filter_by(solution_id=solution_id).all():
        entities["business_elements"].append({"element_type": b.element_type, "name": b.name, "description": b.description or "", "owner": b.owner or "", "notes": b.notes or ""})
    for a in SolutionAppElement.query.filter_by(solution_id=solution_id).all():
        entities["app_elements"].append({"element_type": a.element_type, "name": a.name, "description": a.description or "", "technology": a.technology or "", "notes": a.notes or ""})
    for t in SolutionTechElement.query.filter_by(solution_id=solution_id).all():
        entities["tech_elements"].append({"element_type": t.element_type, "name": t.name, "description": t.description or "", "specification": t.specification or "", "notes": t.notes or ""})
    # ArchiMate element references (capture name/layer metadata, not the FK ids)
    for e in SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all():
        if e.element_name:
            entities["archimate_elements"].append({"layer_type": e.layer_type, "element_name": e.element_name, "element_table": e.element_table, "relationship_type": e.relationship_type or "", "notes": e.notes or ""})
    import json
    template = SolutionTemplate(
        name=name,
        description=description,
        domain=domain,
        template_json=json.dumps(entities),
        source_solution_id=solution_id,
        created_by_id=current_user.id,
    )
    db.session.add(template)
    db.session.commit()
    return jsonify({"success": True, "template_id": template.id, "template": template.to_dict()})


# =============================================================================
# EDIT SOLUTION
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/edit", methods=["GET", "POST"])
@login_required
@audit_log("edit_solution")
def edit_solution(solution_id: int):
    """Edit an existing solution."""
    solution = Solution.query.get_or_404(solution_id)

    # Verify ownership
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        flash("You don't have permission to edit this solution", "error")
        abort(403)

    if request.method == "POST":
        try:
            data = request.get_json(silent=True) or request.form.to_dict()

            # PLT-014: Capture old values for change detection
            old_status = solution.status
            old_owner = solution.solution_owner

            # Update basic information
            new_name = data.get("name", "").strip()
            if new_name:
                solution.name = new_name
            solution.description = data.get("description", solution.description)
            solution.solution_type = data.get("solution_type", solution.solution_type)
            solution.business_domain = data.get("business_domain", solution.business_domain)
            solution.status = data.get("status", solution.status)
            solution.complexity_level = data.get("complexity_level", solution.complexity_level)
            solution.business_value = data.get(
                "value_proposition",
                data.get("business_value", solution.business_value),
            )

            # Scope and boundaries (SOL-025)
            if "scope_description" in data:
                solution.scope_description = (
                    data.get("scope_description") or ""
                ).strip() or None

            if "in_scope_applications" in data:
                raw = (data.get("in_scope_applications") or "").strip()
                solution.in_scope_applications = (
                    [x.strip() for x in raw.split(",") if x.strip()] if raw else []
                )
            if "out_of_scope_applications" in data:
                raw = (data.get("out_of_scope_applications") or "").strip()
                solution.out_of_scope_applications = (
                    [x.strip() for x in raw.split(",") if x.strip()] if raw else []
                )

            # Governance & ownership fields
            if "solution_owner" in data:
                solution.solution_owner = (data.get("solution_owner") or "").strip() or None
            if "business_sponsor" in data:
                solution.business_sponsor = (data.get("business_sponsor") or "").strip() or None
            if "technical_lead" in data:
                solution.technical_lead = (data.get("technical_lead") or "").strip() or None
            if "architecture_lead" in data and "technical_lead" not in data:
                solution.technical_lead = (data.get("architecture_lead") or "").strip() or None

            # Delivery date
            if "planned_end_date" in data or "expected_delivery_date" in data:
                raw_date = data.get("planned_end_date") or data.get("expected_delivery_date") or ""
                raw_date = raw_date.strip() if raw_date else ""
                if raw_date:
                    try:
                        solution.planned_end_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
                    except ValueError:
                        logger.exception("Failed to compute solution.planned_end_date")
                        pass
                else:
                    solution.planned_end_date = None

            # Parse list fields (JSON array from AJAX, comma-separated from form)
            if "target_outcomes" in data:
                outcomes = data.get("target_outcomes")
                if isinstance(outcomes, str):
                    try:
                        solution.target_outcomes = json.loads(outcomes)
                    except (json.JSONDecodeError, ValueError):
                        solution.target_outcomes = [x.strip() for x in outcomes.split(",") if x.strip()]
                elif outcomes is not None:
                    solution.target_outcomes = outcomes

            if "success_metrics" in data:
                metrics = data.get("success_metrics")
                if isinstance(metrics, str):
                    try:
                        solution.success_metrics = json.loads(metrics)
                    except (json.JSONDecodeError, ValueError):
                        solution.success_metrics = [x.strip() for x in metrics.split(",") if x.strip()]
                elif metrics is not None:
                    solution.success_metrics = metrics

            # Financial information (allowlisted Decimal fields only)
            for numeric_field in ("estimated_cost", "roi_percentage"):
                if numeric_field in data:
                    raw = data.get(numeric_field)
                    if raw is not None and str(raw).strip():
                        try:
                            setattr(solution, numeric_field, Decimal(str(raw)))
                        except InvalidOperation:
                            logger.exception("Failed to operation")
                            pass
                    else:
                        setattr(solution, numeric_field, None)

            # Timeline dates (allowlisted only)
            for date_field in (
                "planned_start_date",
                "target_completion_date",
            ):
                if date_field in data:
                    raw_date = (data.get(date_field) or "").strip()
                    if raw_date:
                        try:
                            setattr(solution, date_field, datetime.strptime(raw_date, "%Y-%m-%d").date())
                        except ValueError:
                            logger.exception("Failed to operation")
                            pass
                    else:
                        setattr(solution, date_field, None)

            # Extended governance fields
            if "security_lead" in data:
                solution.security_lead = (data.get("security_lead") or "").strip() or None
            if "data_protection_officer" in data:
                solution.data_protection_officer = (data.get("data_protection_officer") or "").strip() or None
            if "deployment_status" in data:
                solution.deployment_status = (data.get("deployment_status") or "").strip() or None
            # Affected systems (stored as Text, comma-separated)
            if "affected_systems" in data:
                solution.affected_systems = (data.get("affected_systems") or "").strip() or None

            solution.updated_at = datetime.utcnow()

            # PLT-014: Notify on status change
            new_status = solution.status
            if old_status and new_status and old_status != new_status and solution.created_by_id:
                _create_notification(
                    user_id=solution.created_by_id,
                    notification_type="solution_update",
                    message=(
                        f"Solution '{solution.name}' status changed "
                        f"from '{old_status}' to '{new_status}'."
                    ),
                    solution_id=solution.id,
                )

            # PLT-014: Notify on owner change
            new_owner = solution.solution_owner
            if old_owner != new_owner and new_owner and solution.created_by_id:
                _create_notification(
                    user_id=solution.created_by_id,
                    notification_type="assignment",
                    message=f"Solution '{solution.name}' owner changed to '{new_owner}'.",
                    solution_id=solution.id,
                )

            db.session.commit()

            # Re-sync ArchiMate elements after edit
            try:
                from app.services.solution_archimate_sync_service import sync_all_for_solution
                sync_all_for_solution(solution.id)
                db.session.commit()
            except Exception as sync_err:
                logger.warning(f"ArchiMate sync failed for solution {solution.id}: {sync_err}")

            flash(f'Solution "{solution.name}" updated successfully!', "success")
            if request.is_json:
                return jsonify(
                    {
                        "success": True,
                        "message": "Solution updated successfully",
                        "redirect_url": url_for(
                            "solution_design.view_solution", solution_id=solution.id
                        ),
                    }
                )
            return redirect(url_for("solution_design.view_solution", solution_id=solution.id))

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating solution: {str(e)}")
            if request.is_json:
                return jsonify({"success": False, "error": "An internal error occurred"}), 500
            flash("Error updating solution. Please try again.", "error")
            return redirect(url_for("solution_design.edit_solution", solution_id=solution_id))

    # GET - Show edit form with full context
    try:
        ctx = _build_solution_detail_context(solution)
    except Exception as e:
        logger.error(f"Error loading edit context for solution {solution_id}: {e}")
        # Provide safe defaults so the template can always render
        ctx = {
            "solution": solution,
            "applications": [], "processes": [],
            "analysis_data": {}, "arb_reviews": [], "arb_conditions": [],
            "risks": [], "metrics": [], "tco_items": [],
            "currency_symbol": "£", "plateaus": [],
            "lifecycle_json": {"risks": [], "metrics": [], "tcoItems": [], "plateaus": [],
                               "drivers": [], "goals": [], "constraints": [],
                               "requirements": [], "recommendations": [], "sad": {}},
            "archimate_elements": [], "phase_gate": {"valid": True, "errors": [], "warnings": []},
            "vendor_products": [], "adr_list": [], "sad_data": {},
            "analysis_origin": None,
            "applications_json": [], "vendor_products_json": [],
            "has_subscription_vendors": False,
            "adr_list_json": [], "processes_json": [],
            "capabilities_json": [],
            "maturity_score": 0,
            "reasoning_state": None, "llm_available": False,
        }
    return render_template("solutions/edit.html", **ctx)


# =============================================================================
# DELETE SOLUTION
# =============================================================================


def _safe_model_delete(model_class, solution_id):
    """Delete solution child rows using a savepoint so a missing table doesn't abort the outer tx."""
    _sp = db.session.begin_nested()
    try:
        db.session.query(model_class).filter_by(solution_id=solution_id).delete(synchronize_session=False)
        _sp.commit()
    except Exception:
        _sp.rollback()
        logger.debug("cascade skip %s", model_class, exc_info=True)


def _engine_archimate_cleanup(solution_ids):
    """Delete ALL archimate/architecture child records for a set of solutions.

    Runs via db.engine.begin() in its OWN committed transaction BEFORE any
    ORM session work.  This avoids two classes of failures:

    1. Cross-solution FK violation: archimate_relationships.source_id / target_id
       may point to elements that belong to OTHER solutions.  The old cleanup only
       deleted relationships WHERE architecture_id IN (target_models), silently
       leaving 200+ cross-solution rows behind → FK block on archimate_elements.

    2. Lock contention: opening a second db.engine connection WHILE the SQLAlchemy
       session held row/page locks from its own savepoints caused a lock-wait that
       lasted until the gunicorn worker was killed (120 s timeout).

    By committing this engine transaction first (before any session savepoints),
    all archimate data is cleanly gone before the session tries to delete solutions.
    """
    if not solution_ids:
        return
    from sqlalchemy import text

    sids = [int(i) for i in solution_ids]
    sids_str = ",".join(str(i) for i in sids)

    with db.engine.begin() as conn:
        # Prevent indefinite lock waits that block worker threads.
        conn.execute(text("SET LOCAL lock_timeout = '10s'"))
        conn.execute(text("SET LOCAL statement_timeout = '60s'"))

        def _sp_exe(sql):
            sp = conn.begin_nested()
            try:
                conn.execute(text(sql))
                sp.commit()
            except Exception as _e:
                sp.rollback()
                logger.debug("archimate cleanup skip: %s … %s", sql[:80], _e)

        # ── Collect target architecture_model and element IDs ──────────────
        # tenancy-ok: sids come from Solution.query.get_or_404() /
        # Solution.query.filter(Solution.id.in_(...)) in the three callers —
        # Solution is a TenantMixin model, so do_orm_execute has already
        # org-filtered them — plus a created_by_id/is_admin ownership check, and
        # they are cast with int() above. Every statement below is keyed off
        # these ids, so the whole cascade is scoped by its input.
        mids = [r[0] for r in conn.execute(
            text(f"SELECT id FROM architecture_models WHERE solution_id IN ({sids_str})")
        ).fetchall()]
        if not mids:
            return
        mids_str = ",".join(str(i) for i in mids)

        # tenancy-ok: mids derive from the org-scoped solution ids above.
        eids = [r[0] for r in conn.execute(
            text(f"SELECT id FROM archimate_elements WHERE architecture_id IN ({mids_str})")
        ).fetchall()]

        if eids:
            eids_str = ",".join(str(i) for i in eids)

            # ── Clear back-references that would block element deletion ─────
            _sp_exe(f"UPDATE solutions SET archimate_element_id = NULL "
                    f"WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"UPDATE archimate_elements SET parent_id = NULL "
                    f"WHERE parent_id IN ({eids_str})")

            # ── Relationships: cover source_id/target_id cross-solution refs ─
            # This is the primary FK that was blocking the delete.
            _sp_exe(f"DELETE FROM archimate_relationships "
                    f"WHERE source_id IN ({eids_str}) "
                    f"   OR target_id IN ({eids_str}) "
                    f"   OR architecture_id IN ({mids_str})")
            _sp_exe(f"DELETE FROM other_relationships "
                    f"WHERE source_id IN ({eids_str}) OR target_id IN ({eids_str})")

            # ── Multi-column FK tables ──────────────────────────────────────
            _sp_exe(f"DELETE FROM data_lineage_flows "
                    f"WHERE source_element_id IN ({eids_str}) "
                    f"   OR target_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM traceability_links "
                    f"WHERE source_archimate_element_id IN ({eids_str}) "
                    f"   OR target_archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM composite_structures "
                    f"WHERE child_element_id IN ({eids_str}) "
                    f"   OR parent_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM relationship_suggestions "
                    f"WHERE source_element_id IN ({eids_str}) "
                    f"   OR target_element_id IN ({eids_str})")

            # ── Tables with cross-refs among themselves (must go first) ──────
            _sp_exe(f"DELETE FROM solution_quality_attributes "
                    f"WHERE constraint_id IN ({eids_str}) OR principle_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM solution_governance_exceptions "
                    f"WHERE principle_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM requirements "
                    f"WHERE archimate_element_id IN ({eids_str}) "
                    f"   OR goal_id           IN ({eids_str}) "
                    f"   OR source_element_id IN ({eids_str}) "
                    f"   OR stakeholder_id    IN ({eids_str}) "
                    f"   OR driver_id         IN ({eids_str})")
            _sp_exe(f"DELETE FROM risk_assessments "
                    f"WHERE archimate_element_id IN ({eids_str}) "
                    f"   OR goal_id IN ({eids_str}) OR driver_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM outcomes "
                    f"WHERE archimate_element_id IN ({eids_str}) "
                    f"   OR goal_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM constraints "
                    f"WHERE archimate_element_id IN ({eids_str}) "
                    f"   OR goal_id IN ({eids_str})")

            # ── System tables (cross-referencing element IDs) ───────────────
            _sp_exe(f"DELETE FROM system_dependencies "
                    f"WHERE target_system_id IN ({eids_str}) "
                    f"   OR source_system_id IN ({eids_str}) "
                    f"   OR interface_id     IN ({eids_str})")
            _sp_exe(f"DELETE FROM system_hierarchies "
                    f"WHERE parent_system_id IN ({eids_str}) "
                    f"   OR child_system_id  IN ({eids_str})")
            _sp_exe(f"DELETE FROM system_interfaces "
                    f"WHERE target_system_id    IN ({eids_str}) "
                    f"   OR source_system_id    IN ({eids_str}) "
                    f"   OR archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM system_lifecycles "
                    f"WHERE system_id              IN ({eids_str}) "
                    f"   OR replacement_system_id  IN ({eids_str})")
            _sp_exe(f"DELETE FROM system_deployments    WHERE system_id              IN ({eids_str})")
            _sp_exe(f"DELETE FROM system_boundaries     WHERE archimate_element_id   IN ({eids_str})")
            _sp_exe(f"DELETE FROM interface_consumer    WHERE consumer_application_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM data_object_storage   WHERE application_component_id IN ({eids_str})")

            # ── Application layer ───────────────────────────────────────────
            _sp_exe(f"DELETE FROM application_capability          WHERE archimate_application_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM application_collaborations      WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM application_data_objects        WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM application_events              WHERE archimate_element_id IN ({eids_str}) OR publisher_application_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM application_functions           WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM application_interactions        WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM application_interface_metadata  WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM application_interfaces          WHERE archimate_element_id IN ({eids_str}) OR provider_application_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM application_processes           WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM application_services            WHERE archimate_element_id IN ({eids_str})")

            # ── Business layer ──────────────────────────────────────────────
            _sp_exe(f"DELETE FROM business_actors        WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_capability    WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_collaborations WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_events        WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_function      WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_interactions  WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_interfaces    WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_objects       WHERE archimate_element_id IN ({eids_str}) OR master_system_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_processes     WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_roles         WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM business_services      WHERE archimate_element_id IN ({eids_str})")

            # ── Technology layer ────────────────────────────────────────────
            _sp_exe(f"DELETE FROM technology_artifacts             WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_collaborations        WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_collaborations_full   WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_communication_networks WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_devices               WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_events                WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_functions             WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_interactions          WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_interfaces            WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_nodes                 WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_paths                 WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_processes             WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_services              WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM technology_system_software       WHERE archimate_element_id IN ({eids_str})")

            # ── Motivation / data ───────────────────────────────────────────
            _sp_exe(f"DELETE FROM drivers             WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM goals               WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM principles          WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM stakeholders        WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM assessments         WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM capability_archimate_classifications WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM code_artifacts      WHERE source_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM compliance_requirements WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM conceptual_data_models WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM courses_of_action   WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM data_catalogs       WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM data_domains        WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM data_entities       WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM data_lineage        WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM data_transformations WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM design_patterns     WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM equipment           WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM functional_requirement WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM logical_data_models WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM manufacturing_plants WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM meanings            WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM missing_business_collaborations WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM missing_business_interactions   WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM missing_business_interfaces     WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM motivation_drivers   WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM motivation_goals     WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM motivation_principles WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM non_functional_requirement WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM physical_data_models          WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM physical_distribution_networks WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM physical_equipment            WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM physical_facilities           WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM physical_materials            WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM portfolio_initiatives         WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM production_lines              WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM products                      WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM project_constraints           WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM quality_attributes            WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM representations               WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM saved_diagram_elements        WHERE element_id           IN ({eids_str})")
            _sp_exe(f"DELETE FROM software_dependencies         WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM software_modules              WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM solution_compliance_mappings  WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM solution_contracts_model      WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM solution_patterns             WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM strategy_resources            WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM structural_groupings          WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM structural_junctions          WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM structural_locations          WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM uml_elements                  WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM unified_capabilities          WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM values                        WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM architecture_review_findings  WHERE element_id           IN ({eids_str})")
            _sp_exe(f"DELETE FROM archimate_contracts           WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM archimate_representations     WHERE archimate_element_id IN ({eids_str})")
            _sp_exe(f"DELETE FROM archimate_resources           WHERE archimate_element_id IN ({eids_str})")

            # ── Now safe to delete archimate_elements ───────────────────────
            # tenancy-ok: mids derive from the org-scoped solution ids above.
            conn.execute(text(f"DELETE FROM archimate_elements WHERE architecture_id IN ({mids_str})"))

        # ── Architecture model child tables (model_id / architecture_id) ───
        _sp_exe(f"UPDATE arb_review_items SET architecture_model_id = NULL "
                f"WHERE architecture_model_id IN ({mids_str})")
        _sp_exe(f"UPDATE viewpoint_views SET architecture_model_id = NULL "
                f"WHERE architecture_model_id IN ({mids_str})")
        _sp_exe(f"DELETE FROM archimate_contracts      WHERE model_id           IN ({mids_str})")
        _sp_exe(f"DELETE FROM archimate_representations WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM archimate_resources       WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM architecture_decision_records WHERE architecture_model_id IN ({mids_str})")
        _sp_exe(f"DELETE FROM business_collaborations   WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM business_interactions     WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM business_interfaces       WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM business_processes        WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM code_artifacts            WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM constraints               WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM data_domains              WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM data_entities             WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM drivers                   WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM generation_pipelines      WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM git_repositories          WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM goals                     WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM implementation_gaps       WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM implementation_plateaus   WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM implementation_work_packages WHERE architecture_id IN ({mids_str})")
        _sp_exe(f"DELETE FROM jira_issues               WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM jira_projects             WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM motivation_assessments    WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM motivation_constraints    WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM motivation_meanings       WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM motivation_outcomes       WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM motivation_stakeholders   WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM motivation_values         WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM outcomes                  WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM planning_deliverables     WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM principles                WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM representations           WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM requirements              WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM risk_assessments          WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM salesforce_object         WHERE architecture_model_id IN ({mids_str})")
        _sp_exe(f"DELETE FROM technology_collaborations_full WHERE model_id     IN ({mids_str})")
        _sp_exe(f"DELETE FROM technology_events         WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM technology_functions      WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM technology_interactions   WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM technology_processes      WHERE model_id          IN ({mids_str})")
        _sp_exe(f"DELETE FROM uml_models                WHERE architecture_id   IN ({mids_str})")
        _sp_exe(f"DELETE FROM workflow_pipelines        WHERE architecture_id   IN ({mids_str})")

        # ── Now safe to delete architecture_models ──────────────────────────
        # tenancy-ok: mids derive from the org-scoped solution ids above.
        conn.execute(text(f"DELETE FROM architecture_models WHERE id IN ({mids_str})"))

    # engine transaction committed — all archimate data is cleanly gone


def _cascade_delete_solutions_batch(solution_ids):
    """Batch cascade delete child records for multiple solutions in one pass.

    Uses IN(...) queries — 1 query per model type instead of N×30 queries,
    which makes bulk deletes of 100+ solutions feasible within a single request.
    NOTE: archimate/architecture cleanup is handled by _engine_archimate_cleanup
    which must be called first (before any session savepoints) to avoid lock
    contention and cross-solution FK violations.
    """
    if not solution_ids:
        return
    sid_list = list(solution_ids)

    def _batch_delete(model_class):
        _sp = db.session.begin_nested()
        try:
            db.session.query(model_class).filter(
                model_class.solution_id.in_(sid_list)
            ).delete(synchronize_session=False)
            _sp.commit()
        except Exception:
            _sp.rollback()
            logger.debug("batch cascade skip %s", model_class, exc_info=True)

    try:
        from app.models.solution_lifecycle_models import (
            SolutionRisk, SolutionTCOItem, SolutionMetric, SolutionPlateau,
        )
        for _m in [SolutionRisk, SolutionTCOItem, SolutionMetric, SolutionPlateau]:
            _batch_delete(_m)
    except Exception:
        logger.debug("lifecycle batch skip", exc_info=True)

    try:
        from app.models.solution_sad_models import (
            SolutionIntegrationFlow, SolutionComposition, RiskSnapshot,
            SolutionQualityAttribute, SolutionSLA, MigrationDependency,
            SolutionInvestmentPhase, SolutionGovernanceException, SolutionComplianceMapping,
            SolutionChangeRequest, SolutionFeasibilityReview, SolutionBenefitRealization,
            SolutionOrgImpact, SolutionLessonLearned, SolutionPrincipleSAD,
            SolutionAssessmentSAD, SolutionStakeholderSAD, SolutionBusinessElement,
            SolutionAppElement, SolutionTechElement, SolutionADRDirect, SolutionAPQCProcess,
        )
        for _m in [
            SolutionIntegrationFlow, SolutionComposition, RiskSnapshot,
            SolutionQualityAttribute, SolutionSLA, MigrationDependency,
            SolutionInvestmentPhase, SolutionGovernanceException, SolutionComplianceMapping,
            SolutionChangeRequest, SolutionFeasibilityReview, SolutionBenefitRealization,
            SolutionOrgImpact, SolutionLessonLearned, SolutionPrincipleSAD,
            SolutionAssessmentSAD, SolutionStakeholderSAD, SolutionBusinessElement,
            SolutionAppElement, SolutionTechElement, SolutionADRDirect, SolutionAPQCProcess,
        ]:
            _batch_delete(_m)
    except Exception:
        logger.debug("sad batch skip", exc_info=True)

    try:
        from app.models.solution_governance import (
            SolutionVersion, SolutionExecutionTracking, SolutionIssue,
            SolutionARBReview, SolutionOutcomeTracking, SolutionAIBacktesting,
            SolutionNotification,
        )
        for _m in [
            SolutionVersion, SolutionExecutionTracking, SolutionIssue,
            SolutionARBReview, SolutionOutcomeTracking, SolutionAIBacktesting,
            SolutionNotification,
        ]:
            _batch_delete(_m)
    except Exception:
        logger.debug("governance batch skip", exc_info=True)

    try:
        from app.models.solution_outcomes import SolutionOutcome
        from app.models.solution_outcomes import SolutionBenefitRealization as SolutionBenefitRealizationOutcome
        for _m in [SolutionOutcome, SolutionBenefitRealizationOutcome]:
            _batch_delete(_m)
    except Exception:
        logger.debug("outcomes batch skip", exc_info=True)

    try:
        from app.models.solution_stakeholder import SolutionStakeholderMapping
        _batch_delete(SolutionStakeholderMapping)
    except Exception:
        logger.debug("stakeholder batch skip", exc_info=True)

    try:
        from app.models.solution_workflow import SolutionWorkflow
        _batch_delete(SolutionWorkflow)
    except Exception:
        logger.debug("workflow batch skip", exc_info=True)

    try:
        from app.models.solution_cost_model import SolutionCostModel
        _batch_delete(SolutionCostModel)
    except Exception:
        logger.debug("cost batch skip", exc_info=True)

    try:
        from app.models.solution_deployment import SolutionDeploymentArchitecture
        _batch_delete(SolutionDeploymentArchitecture)
    except Exception:
        logger.debug("deployment batch skip", exc_info=True)

    try:
        from app.models.solution_reasoning import SolutionAIReasoningState
        _batch_delete(SolutionAIReasoningState)
    except Exception:
        logger.debug("reasoning batch skip", exc_info=True)

    try:
        from app.models.solution_models import SolutionArchiMateElement, SolutionComment
        for _m in [SolutionArchiMateElement, SolutionComment]:
            _batch_delete(_m)
    except Exception:
        logger.debug("archimate/comment batch skip", exc_info=True)

    try:
        from app.models.vector_embeddings import SolutionEmbedding
        _sp = db.session.begin_nested()
        try:
            db.session.query(SolutionEmbedding).filter(
                SolutionEmbedding.solution_id.in_(sid_list)
            ).delete(synchronize_session=False)
            _sp.commit()
        except Exception:
            _sp.rollback()
            logger.debug("embedding batch skip", exc_info=True)
    except Exception:
        logger.debug("embedding import batch skip", exc_info=True)

    # RAW SQL batch cleanup — IN :sids instead of = :sid (one query per statement)
    from sqlalchemy import text as _sql_text, bindparam as _bindparam
    _raw_batch_stmts = [
        "UPDATE solutions SET arb_review_item_id = NULL WHERE arb_review_item_id IN "
        "(SELECT id FROM arb_review_items WHERE solution_id IN :sids)",
        "DELETE FROM arb_review_comments WHERE review_item_id IN "
        "(SELECT id FROM arb_review_items WHERE solution_id IN :sids)",
        "DELETE FROM adm_phase_approvals WHERE arb_review_item_id IN "
        "(SELECT id FROM arb_review_items WHERE solution_id IN :sids)",
        "DELETE FROM adm_rida_logs WHERE arb_review_item_id IN "
        "(SELECT id FROM arb_review_items WHERE solution_id IN :sids)",
        "DELETE FROM arb_adversarial_reviews WHERE review_item_id IN "
        "(SELECT id FROM arb_review_items WHERE solution_id IN :sids)",
        "DELETE FROM arb_capability_impacts WHERE review_item_id IN "
        "(SELECT id FROM arb_review_items WHERE solution_id IN :sids)",
        "DELETE FROM arb_compliance_checks WHERE review_item_id IN "
        "(SELECT id FROM arb_review_items WHERE solution_id IN :sids)",
        "DELETE FROM arb_conditions WHERE review_item_id IN "
        "(SELECT id FROM arb_review_items WHERE solution_id IN :sids)",
        "UPDATE kanban_cards SET arb_review_id = NULL WHERE arb_review_id IN "
        "(SELECT id FROM arb_review_items WHERE solution_id IN :sids)",
        "DELETE FROM arb_review_items WHERE solution_id IN :sids",
        "DELETE FROM solution_blueprint_proposals WHERE solution_id IN :sids",
        "DELETE FROM architecture_generation_runs WHERE solution_id IN :sids",
        "DELETE FROM codegen_data_imports WHERE solution_id IN :sids",
        "DELETE FROM codegen_test_runs WHERE solution_id IN :sids",
        "DELETE FROM codegen_workflow_designs WHERE solution_id IN :sids",
        "DELETE FROM copilot_insights WHERE solution_id IN :sids",
        "DELETE FROM published_api_specs WHERE solution_id IN :sids",
        "DELETE FROM runtime_compliance_checks WHERE solution_id IN :sids",
        "DELETE FROM solution_cost_models WHERE solution_id IN :sids",
        "DELETE FROM solution_domain_specs WHERE solution_id IN :sids",
        "DELETE FROM solution_outcomes WHERE solution_id IN :sids",
        "DELETE FROM solution_spec_generations WHERE solution_id IN :sids",
        "UPDATE solutions SET parent_solution_id = NULL WHERE parent_solution_id IN :sids",
        # archimate chain: relationships before elements before architecture_models
        "DELETE FROM archimate_relationships WHERE architecture_id IN "
        "(SELECT id FROM architecture_models WHERE solution_id IN :sids)",
        "DELETE FROM archimate_elements WHERE architecture_id IN "
        "(SELECT id FROM architecture_models WHERE solution_id IN :sids)",
    ]
    for _stmt_str in _raw_batch_stmts:
        _sp = db.session.begin_nested()
        try:
            _compiled = _sql_text(_stmt_str).bindparams(
                _bindparam("sids", expanding=True)
            )
            db.session.execute(_compiled, {"sids": sid_list})
            _sp.commit()
        except Exception:
            _sp.rollback()
            logger.debug("raw batch delete skip: %s", _stmt_str[:60], exc_info=True)

    try:
        from app.models.architecture_review_board import ARBReviewItem
        _batch_delete(ARBReviewItem)
    except Exception:
        logger.debug("arb batch skip", exc_info=True)

    try:
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
        _sp = db.session.begin_nested()
        try:
            arch_ids = db.session.query(ArchitectureModel.id).filter(
                ArchitectureModel.solution_id.in_(sid_list)
            )
            db.session.query(ArchiMateRelationship).filter(
                ArchiMateRelationship.architecture_id.in_(arch_ids)
            ).delete(synchronize_session=False)
            db.session.query(ArchiMateElement).filter(
                ArchiMateElement.architecture_id.in_(arch_ids)
            ).delete(synchronize_session=False)
            _sp.commit()
        except Exception:
            _sp.rollback()
            logger.debug("architecture_models batch cascade skip", exc_info=True)
    except ImportError:
        logger.debug("architecture model batch import skip", exc_info=True)

    try:
        from app.modules.codegen.models import (
            CrossLayerRelationship, SolutionLayerElement, ExperienceElement,
            SolutionConnector, SolutionInstance, SolutionRule,
            SystemBoundarySolution, CodegenChatMessage,
            CodegenGeneration, CodegenGenerationHistory, CodegenDriftReport,
            SolutionVersion as CodegenSolutionVersion,
        )
        _sp = db.session.begin_nested()
        try:
            gen_ids = db.session.query(CodegenGeneration.id).filter(
                CodegenGeneration.solution_id.in_(sid_list)
            )
            db.session.query(CodegenGenerationHistory).filter(
                CodegenGenerationHistory.codegen_generation_id.in_(gen_ids)
            ).delete(synchronize_session=False)
            _sp.commit()
        except Exception:
            _sp.rollback()
            logger.debug("codegen history batch cascade skip", exc_info=True)

        for _m in [
            CrossLayerRelationship, SolutionLayerElement, ExperienceElement,
            SolutionConnector, SolutionInstance, SolutionRule, SystemBoundarySolution,
            CodegenChatMessage, CodegenGeneration, CodegenDriftReport, CodegenSolutionVersion,
        ]:
            _batch_delete(_m)
    except Exception:
        logger.debug("codegen batch cascade skip", exc_info=True)


def _cascade_delete_solution(solution_id):
    """Delete all child records for a solution before ORM deletion."""
    try:
        from app.models.solution_lifecycle_models import (
            SolutionRisk, SolutionTCOItem, SolutionMetric, SolutionPlateau,
        )
        for _m in [SolutionRisk, SolutionTCOItem, SolutionMetric, SolutionPlateau]:
            _safe_model_delete(_m, solution_id)
    except Exception:
        logger.debug("lifecycle import skip", exc_info=True)

    try:
        from app.models.solution_sad_models import (
            SolutionIntegrationFlow, SolutionComposition, RiskSnapshot,
            SolutionQualityAttribute, SolutionSLA, MigrationDependency,
            SolutionInvestmentPhase, SolutionGovernanceException, SolutionComplianceMapping,
            SolutionChangeRequest, SolutionFeasibilityReview, SolutionBenefitRealization,
            SolutionOrgImpact, SolutionLessonLearned, SolutionPrincipleSAD,
            SolutionAssessmentSAD, SolutionStakeholderSAD, SolutionBusinessElement,
            SolutionAppElement, SolutionTechElement, SolutionADRDirect, SolutionAPQCProcess,
        )
        for _m in [
            SolutionIntegrationFlow, SolutionComposition, RiskSnapshot,
            SolutionQualityAttribute, SolutionSLA, MigrationDependency,
            SolutionInvestmentPhase, SolutionGovernanceException, SolutionComplianceMapping,
            SolutionChangeRequest, SolutionFeasibilityReview, SolutionBenefitRealization,
            SolutionOrgImpact, SolutionLessonLearned, SolutionPrincipleSAD,
            SolutionAssessmentSAD, SolutionStakeholderSAD, SolutionBusinessElement,
            SolutionAppElement, SolutionTechElement, SolutionADRDirect, SolutionAPQCProcess,
        ]:
            _safe_model_delete(_m, solution_id)
    except Exception:
        logger.debug("sad import skip", exc_info=True)

    try:
        from app.models.solution_governance import (
            SolutionVersion, SolutionExecutionTracking, SolutionIssue,
            SolutionARBReview, SolutionOutcomeTracking, SolutionAIBacktesting,
            SolutionNotification,
        )
        for _m in [
            SolutionVersion, SolutionExecutionTracking, SolutionIssue,
            SolutionARBReview, SolutionOutcomeTracking, SolutionAIBacktesting,
            SolutionNotification,
        ]:
            _safe_model_delete(_m, solution_id)
    except Exception:
        logger.debug("governance import skip", exc_info=True)

    try:
        from app.models.solution_outcomes import SolutionOutcome
        from app.models.solution_outcomes import SolutionBenefitRealization as SolutionBenefitRealizationOutcome
        for _m in [SolutionOutcome, SolutionBenefitRealizationOutcome]:
            _safe_model_delete(_m, solution_id)
    except Exception:
        logger.debug("outcomes import skip", exc_info=True)

    try:
        from app.models.solution_stakeholder import SolutionStakeholderMapping
        _safe_model_delete(SolutionStakeholderMapping, solution_id)
    except Exception:
        logger.debug("stakeholder import skip", exc_info=True)

    try:
        from app.models.solution_workflow import SolutionWorkflow
        _safe_model_delete(SolutionWorkflow, solution_id)
    except Exception:
        logger.debug("workflow import skip", exc_info=True)

    try:
        from app.models.solution_cost_model import SolutionCostModel
        _safe_model_delete(SolutionCostModel, solution_id)
    except Exception:
        logger.debug("cost import skip", exc_info=True)

    try:
        from app.models.solution_deployment import SolutionDeploymentArchitecture
        _safe_model_delete(SolutionDeploymentArchitecture, solution_id)
    except Exception:
        logger.debug("deployment import skip", exc_info=True)

    try:
        from app.models.solution_reasoning import SolutionAIReasoningState
        _safe_model_delete(SolutionAIReasoningState, solution_id)
    except Exception:
        logger.debug("reasoning import skip", exc_info=True)

    try:
        from app.models.solution_models import SolutionArchiMateElement, SolutionComment
        for _m in [SolutionArchiMateElement, SolutionComment]:
            _safe_model_delete(_m, solution_id)
    except Exception:
        logger.debug("archimate/comment import skip", exc_info=True)

    try:
        from app.models.vector_embeddings import SolutionEmbedding
        db.session.query(SolutionEmbedding).filter_by(solution_id=solution_id).delete(synchronize_session=False)
    except Exception:
        logger.debug("embedding cascade skip", exc_info=True)

    # --- RAW SQL CLEANUP: all NO-ACTION FK tables not covered above ---
    # Each block uses a savepoint so failures don't abort the outer transaction.
    # Order matters: delete grandchildren before children before solutions.
    _raw_deletes = [
        # --- arb_review_items and ALL its NO-ACTION child tables ---
        # First NULL-out circular reference: solutions.arb_review_item_id
        ("UPDATE solutions SET arb_review_item_id = NULL WHERE arb_review_item_id IN "
         "(SELECT id FROM arb_review_items WHERE solution_id = :sid)", {}),
        # Delete all NO-ACTION children of arb_review_items
        ("DELETE FROM arb_review_comments WHERE review_item_id IN "
         "(SELECT id FROM arb_review_items WHERE solution_id = :sid)", {}),
        ("DELETE FROM adm_phase_approvals WHERE arb_review_item_id IN "
         "(SELECT id FROM arb_review_items WHERE solution_id = :sid)", {}),
        ("DELETE FROM adm_rida_logs WHERE arb_review_item_id IN "
         "(SELECT id FROM arb_review_items WHERE solution_id = :sid)", {}),
        ("DELETE FROM arb_adversarial_reviews WHERE review_item_id IN "
         "(SELECT id FROM arb_review_items WHERE solution_id = :sid)", {}),
        ("DELETE FROM arb_capability_impacts WHERE review_item_id IN "
         "(SELECT id FROM arb_review_items WHERE solution_id = :sid)", {}),
        ("DELETE FROM arb_compliance_checks WHERE review_item_id IN "
         "(SELECT id FROM arb_review_items WHERE solution_id = :sid)", {}),
        ("DELETE FROM arb_conditions WHERE review_item_id IN "
         "(SELECT id FROM arb_review_items WHERE solution_id = :sid)", {}),
        ("UPDATE kanban_cards SET arb_review_id = NULL WHERE arb_review_id IN "
         "(SELECT id FROM arb_review_items WHERE solution_id = :sid)", {}),
        # Now safe to delete arb_review_items
        ("DELETE FROM arb_review_items WHERE solution_id = :sid", {}),
        # solution_blueprint_proposals
        ("DELETE FROM solution_blueprint_proposals WHERE solution_id = :sid", {}),
        # other NO-ACTION FK tables missing from ORM cascade above
        ("DELETE FROM architecture_generation_runs WHERE solution_id = :sid", {}),
        ("DELETE FROM codegen_data_imports WHERE solution_id = :sid", {}),
        ("DELETE FROM codegen_test_runs WHERE solution_id = :sid", {}),
        ("DELETE FROM codegen_workflow_designs WHERE solution_id = :sid", {}),
        ("DELETE FROM copilot_insights WHERE solution_id = :sid", {}),
        ("DELETE FROM published_api_specs WHERE solution_id = :sid", {}),
        ("DELETE FROM runtime_compliance_checks WHERE solution_id = :sid", {}),
        ("DELETE FROM solution_cost_models WHERE solution_id = :sid", {}),
        ("DELETE FROM solution_domain_specs WHERE solution_id = :sid", {}),
        ("DELETE FROM solution_outcomes WHERE solution_id = :sid", {}),
        ("DELETE FROM solution_spec_generations WHERE solution_id = :sid", {}),
        # self-referential: NULL-out parent_solution_id before delete
        ("UPDATE solutions SET parent_solution_id = NULL WHERE parent_solution_id = :sid", {}),
    ]
    from sqlalchemy import text as _sql_text
    for _stmt, _params in _raw_deletes:
        _sp2 = db.session.begin_nested()
        try:
            db.session.execute(_sql_text(_stmt), {"sid": solution_id, **_params})
            _sp2.commit()
        except Exception:
            _sp2.rollback()
            logger.debug("raw delete skip: %s", _stmt[:60], exc_info=True)

    try:
        from app.models.architecture_review_board import ARBReviewItem
        _safe_model_delete(ARBReviewItem, solution_id)
    except Exception:
        logger.debug("arb import skip", exc_info=True)

    # architecture_models → archimate_elements chain.
    # solutions.architecture_models has ON DELETE CASCADE at DB level, but
    # architecture_models.archimate_elements has ON DELETE NO ACTION, so we must
    # delete archimate_elements (and archimate_relationships) first.
    try:
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
        _sp = db.session.begin_nested()
        try:
            arch_ids = db.session.query(ArchitectureModel.id).filter_by(solution_id=solution_id)
            db.session.query(ArchiMateRelationship).filter(
                ArchiMateRelationship.architecture_id.in_(arch_ids)
            ).delete(synchronize_session=False)
            db.session.query(ArchiMateElement).filter(
                ArchiMateElement.architecture_id.in_(arch_ids)
            ).delete(synchronize_session=False)
            _sp.commit()
        except Exception:
            _sp.rollback()
            logger.debug("architecture_models child cascade skip", exc_info=True)
    except ImportError:
        logger.debug("architecture model import skip", exc_info=True)

    try:
        from app.modules.codegen.models import (
            CrossLayerRelationship, SolutionLayerElement, ExperienceElement,
            SolutionConnector, SolutionInstance, SolutionRule,
            SystemBoundarySolution, CodegenChatMessage,
            CodegenGeneration, CodegenGenerationHistory, CodegenDriftReport,
            SolutionVersion as CodegenSolutionVersion,
        )
        # CodegenGenerationHistory references codegen_generations.id — delete before CodegenGeneration
        _sp = db.session.begin_nested()
        try:
            gen_ids = db.session.query(CodegenGeneration.id).filter_by(solution_id=solution_id)
            db.session.query(CodegenGenerationHistory).filter(
                CodegenGenerationHistory.codegen_generation_id.in_(gen_ids)
            ).delete(synchronize_session=False)
            _sp.commit()
        except Exception:
            _sp.rollback()
            logger.debug("codegen history cascade skip", exc_info=True)

        # CrossLayerRelationship must go before SolutionLayerElement/ExperienceElement
        for _m in [
            CrossLayerRelationship, SolutionLayerElement, ExperienceElement,
            SolutionConnector, SolutionInstance, SolutionRule, SystemBoundarySolution,
            CodegenChatMessage, CodegenGeneration, CodegenDriftReport, CodegenSolutionVersion,
        ]:
            _safe_model_delete(_m, solution_id)
    except Exception:
        logger.debug("codegen cascade skip", exc_info=True)


@solution_design_bp.route("/<int:solution_id>/delete", methods=["POST"])
@login_required
@audit_log("delete_solution")
def delete_solution(solution_id: int):
    """Delete a solution."""
    solution = Solution.query.get_or_404(solution_id)

    # Verify ownership
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        return jsonify({"success": False, "error": "Permission denied"}), 403

    try:
        solution_name = solution.name

        # Get applications associated with solution
        try:
            solution_app_table = db.metadata.tables.get("solution_applications")
            if solution_app_table is not None:
                apps = (
                    db.session.query(ApplicationComponent)
                    .join(
                        solution_app_table,
                        ApplicationComponent.id == solution_app_table.c.application_component_id,
                    )
                    .filter(solution_app_table.c.solution_id == solution_id)
                    .all()
                )

                # Batch delete associated process mappings
                app_ids = [a.id for a in apps]
                if app_ids:
                    db.session.query(ProcessApplicationMapping).filter(
                        ProcessApplicationMapping.application_id.in_(app_ids)
                    ).delete(synchronize_session=False)
        except Exception as e:
            logger.warning(f"Could not delete process mappings: {e}")

        # Engine-level archimate cleanup must run before session savepoints
        try:
            _engine_archimate_cleanup([solution_id])
        except Exception:
            logger.warning("engine archimate cleanup failed for solution %s", solution_id, exc_info=True)
            db.session.rollback()

        _cascade_delete_solution(solution_id)
        db.session.query(Solution).filter_by(id=solution_id).delete(synchronize_session=False)
        db.session.commit()

        flash(f'Solution "{solution_name}" deleted successfully!', "success")
        if request.is_json:
            return jsonify(
                {
                    "success": True,
                    "message": "Solution deleted successfully",
                    "redirect_url": url_for("solution_design.list_solutions"),
                }
            )
        return redirect(url_for("solution_design.list_solutions"))

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting solution: {str(e)}")
        if request.is_json:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500
        flash("Error deleting solution. Please try again.", "error")
        return redirect(url_for("solution_design.view_solution", solution_id=solution_id))


@solution_design_bp.route("/<int:solution_id>/sync-archimate", methods=["POST"])
@login_required
def sync_archimate(solution_id: int):
    """Trigger ArchiMate repository sync for a solution."""
    solution = Solution.query.get_or_404(solution_id)
    try:
        from app.services.solution_archimate_sync_service import sync_all_for_solution
        counts = sync_all_for_solution(solution.id)
        db.session.commit()
        return jsonify({"success": True, "counts": counts})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Sync failed for solution {solution_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/advance-phase", methods=["POST"])
@login_required
@audit_log("advance_adm_phase")
def advance_phase(solution_id: int):
    """Advance ADM phase with gate validation (ENT-064).

    Checks phase gate requirements before allowing advancement.
    Pass ``force: true`` in the JSON body to bypass warnings (but NOT critical failures).
    """
    from app.modules.solutions_strategic.v2.services.solution_phase_gate_service import (
        SolutionPhaseGateService,
    )

    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    force = data.get("force", False)

    current_phase = solution.adm_phase or "A"

    # ── ENT-064: Phase gate validation ────────────────────────────────────
    gate_svc = SolutionPhaseGateService()
    gate_result = gate_svc.check_gate(solution_id, current_phase)

    # Critical failures always block (even with force=True)
    if gate_result["critical_failures"]:
        return jsonify({
            "success": False,
            "valid": False,
            "errors": [
                f"{item['label']}: found {item['actual']}, need {item['required']}"
                for item in gate_result["critical_failures"]
            ],
            "warnings": [
                f"{item['label']}: found {item['actual']}, need {item['required']}"
                for item in gate_result["warnings"]
            ],
            "gate": gate_result,
        }), 400

    # Warnings block unless force=True
    if gate_result["warnings"] and not force:
        return jsonify({
            "success": False,
            "valid": False,
            "errors": [],
            "warnings": [
                f"{item['label']}: found {item['actual']}, need {item['required']}"
                for item in gate_result["warnings"]
            ],
            "gate": gate_result,
        }), 400

    # Gate passed — delegate to model method (force=True because we already validated)
    result = solution.complete_adm_phase(current_phase, force=True)
    if result.get("completed") or result.get("valid"):
        # Notify solution owner of phase advance (ENT-012)
        if solution.created_by_id:
            phase_name = result.get("phase", solution.adm_phase or "A")
            notif = SolutionNotification(
                solution_id=solution.id,
                user_id=solution.created_by_id,
                type="phase_advance",
                message=f"Solution '{solution.name}' advanced to phase {phase_name}.",
            )
            db.session.add(notif)
        db.session.commit()
        return jsonify({"success": True, "gate": gate_result, **result})
    else:
        return jsonify({"success": False, **result}), 400


@solution_design_bp.route("/bulk-delete", methods=["POST"])
@login_required
@audit_log("bulk_delete_solutions")
def bulk_delete_solutions():
    """Bulk delete multiple solutions.

    Accepts either:
    - { solution_ids: [1,2,3] }  — explicit list of IDs
    - { select_all_filter: true, ws_filter: "needs_setup" }  — delete all matching a worklist bucket
    """
    try:
        data = request.get_json() or {}

        # ── select-all-across-pages mode ──────────────────────────────────────
        if data.get("select_all_filter"):
            ws_filter = (data.get("ws_filter") or "").strip()
            if ws_filter not in _WORKLIST_BUCKETS:
                return jsonify({"success": False, "error": "Invalid filter"}), 400
            _can_see_all = (
                (hasattr(current_user, 'is_admin') and current_user.is_admin())
                or (hasattr(current_user, 'can_vote_arb') and current_user.can_vote_arb())
                or (hasattr(current_user, 'can_manage_portfolio') and current_user.can_manage_portfolio())
                or getattr(current_user, 'enterprise_role', None) in ('enterprise_architect', 'cto', 'platform_admin')
            )
            _q = Solution.query if _can_see_all else Solution.query.filter_by(created_by_id=current_user.id)
            _all = _q.order_by(Solution.id).all()
            try:
                _summaries, _ = _build_solution_worklist_summaries(_all)
            except Exception:
                _summaries = {}
            solution_ids = [
                s.id for s in _all
                if _summaries.get(s.id, {}).get("work_bucket") == ws_filter
            ]
            if not solution_ids:
                return jsonify({"success": True, "deleted_count": 0, "message": "No matching solutions found"}), 200
        else:
            solution_ids = data.get("solution_ids", [])

        if not solution_ids:
            return jsonify({"success": False, "error": "No solutions selected"}), 400

        deleted_count = 0
        errors = []

        # Batch prefetch all requested solutions
        solutions_map = {}
        if solution_ids:
            fetched = Solution.query.filter(Solution.id.in_(solution_ids)).all()
            solutions_map = {s.id: s for s in fetched}

        # Determine which solutions can be deleted (exist + owned by user)
        deletable_ids = []
        for solution_id in solution_ids:
            solution = solutions_map.get(solution_id)
            if not solution:
                errors.append(f"Solution {solution_id} not found")
                continue
            if solution.created_by_id != current_user.id and not current_user.is_admin():
                errors.append(f"Permission denied for solution {solution_id}")
                continue
            deletable_ids.append(solution_id)

        if deletable_ids:
            # ── Step 1: engine-level archimate/architecture cleanup ──────────
            # Must run FIRST (before session savepoints) to avoid FK violations.
            # Non-fatal: no DB-level FK constraints exist on the solutions table,
            # so a cleanup failure just leaves orphaned archimate rows.
            try:
                _engine_archimate_cleanup(deletable_ids)
            except Exception:
                logger.warning(
                    "engine archimate cleanup failed (continuing with delete) — "
                    "orphaned archimate rows may remain",
                    exc_info=True,
                )
                db.session.rollback()

            # ── Step 2: ORM-based cascade for all other solution child tables ─
            _cascade_delete_solutions_batch(deletable_ids)

            # ── Step 3: Capability mappings (problem_id FK) ──────────────────
            try:
                from app.models.solution_models import SolutionCapabilityMapping
                SolutionCapabilityMapping.query.filter(
                    SolutionCapabilityMapping.problem_id.in_(deletable_ids)
                ).delete(synchronize_session=False)
            except Exception:
                logger.debug("Failed to batch delete capability mappings", exc_info=True)

            # ── Step 4: Delete solutions (archimate already clean, no FK block) ─
            db.session.expunge_all()
            deleted_count = (
                db.session.query(Solution)
                .filter(Solution.id.in_(deletable_ids))
                .delete(synchronize_session=False)
            )

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Deleted {deleted_count} solution(s)",
                "deleted_count": deleted_count,
                "errors": errors if errors else None,
            }
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in bulk delete: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# JSON API ENDPOINTS
# =============================================================================


@solution_design_bp.route("/list-json", methods=["GET"])
@login_required
def api_list_solutions():
    """Get solutions as JSON — admins and review-role personas see all; others see own."""
    _can_see_all = (
        (hasattr(current_user, 'is_admin') and current_user.is_admin())
        or (hasattr(current_user, 'can_vote_arb') and current_user.can_vote_arb())
        or (hasattr(current_user, 'can_manage_portfolio') and current_user.can_manage_portfolio())
        or getattr(current_user, 'enterprise_role', None) in ('enterprise_architect', 'cto', 'platform_admin')
    )
    if _can_see_all:
        solutions = Solution.query.order_by(Solution.created_at.desc()).all()
    else:
        solutions = Solution.query.filter_by(created_by_id=current_user.id).order_by(Solution.created_at.desc()).all()

    return jsonify(
        {
            "success": True,
            "solutions": [
                {
                    "id": s.id,
                    "name": s.name,
                    "status": s.status,
                    "business_domain": s.business_domain,
                    "complexity_level": s.complexity_level,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
                for s in solutions
            ],
        }
    )


# =============================================================================
# SOLUTION LIFECYCLE NOTIFICATIONS (ENT-012)
# =============================================================================

# =============================================================================
# SOLUTION TEMPLATE LIBRARY (ENT-016)
# =============================================================================


@solution_design_bp.route("/templates", methods=["GET"])
@login_required
def list_solution_templates():
    """List available solution templates ordered by usage.

    Returns rendered HTML for browser requests and JSON for API clients.
    Displays SolutionTemplate records from the database plus the built-in
    RequirementTemplate catalogue grouped by architecture layer.
    """
    from types import SimpleNamespace

    # --- 1. Load SolutionTemplate records (may fail if table not yet created) ---
    solution_templates: list = []
    try:
        from app.models.solution_governance import SolutionTemplate
        solution_templates = SolutionTemplate.query.order_by(
            SolutionTemplate.usage_count.desc()
        ).all()
    except Exception as exc:
        logger.warning("SolutionTemplate table unavailable: %s", exc)

    # --- 2. Build RequirementTemplate catalogue from static seed data ---
    from app.models.requirement_template import SYSTEM_TEMPLATES

    layer_descriptions = {
        "Business": "Business-layer scaffolds covering roles, processes, SLAs, and compliance rules.",
        "Application": "Application-layer scaffolds for APIs, data objects, search, notifications, and workflows.",
        "Technology": "Technology-layer scaffolds for hosting, scalability, DR, monitoring, and security.",
        "CrossCutting": "Cross-cutting scaffolds for performance, accessibility, privacy, i18n, and cost.",
    }

    # Group seed templates by layer
    layer_groups: dict[str, list] = {}
    for tpl in SYSTEM_TEMPLATES:
        layer_groups.setdefault(tpl["layer"], []).append(tpl)

    # Create display objects that match the shape expected by templates.html
    # (attributes: id, name, description, element_count)
    requirement_catalogue = []
    for layer_name, items in sorted(layer_groups.items()):
        requirement_catalogue.append(SimpleNamespace(
            id=f"req-{layer_name.lower()}",
            name=f"{layer_name} Layer Templates",
            description=layer_descriptions.get(layer_name, f"{layer_name} architecture requirement templates."),
            element_count=len(items),
            layer=layer_name,
            items=items,
        ))

    # --- 3. Combine: SolutionTemplate records first, then catalogue cards ---
    display_templates = []
    for st in solution_templates:
        tpl_data = st.template_data if hasattr(st, "template_data") else {}
        element_count = sum(len(v) for v in tpl_data.values() if isinstance(v, list))
        display_templates.append(SimpleNamespace(
            id=st.id,
            name=st.name,
            description=st.description or "Saved solution template.",
            element_count=element_count,
        ))
    display_templates.extend(requirement_catalogue)

    # --- 4. JSON response for API consumers ---
    if request.accept_mimetypes.best == "application/json":
        return jsonify({
            "templates": [t.to_dict() for t in solution_templates],
            "requirement_catalogue": [
                {"layer": c.layer, "name": c.name, "count": c.element_count, "items": c.items}
                for c in requirement_catalogue
            ],
        })

    # --- 5. Render HTML page ---
    return render_template(
        "solutions/templates.html",
        templates=display_templates,
        requirement_catalogue=requirement_catalogue,
    )


@solution_design_bp.route("/create-from-template", methods=["POST"])
@login_required
def create_solution_from_template():
    """Create a new solution from a template and populate entities."""
    from app.models.solution_governance import SolutionTemplate
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
    data = request.get_json() or {}
    template_id = data.get("template_id")
    if not template_id:
        return jsonify({"success": False, "error": "template_id is required"}), 400
    template = SolutionTemplate.query.get(template_id)
    if not template:
        return jsonify({"success": False, "error": "Template not found"}), 404
    try:
        import json
        entities = json.loads(template.template_json) if isinstance(template.template_json, str) else template.template_json
    except Exception as e:
        logger.warning("Invalid template_json: %s", e)
        return jsonify({"success": False, "error": "Invalid template data"}), 400
    name = (data.get("solution_name") or data.get("name") or template.name or "From template").strip()[:255]
    solution = Solution(
        name=name,
        description=template.description or "",
        business_domain=template.domain,
        created_by_id=current_user.id,
        status="draft",
    )
    db.session.add(solution)
    db.session.flush()
    try:
        orchestrator = SolutionAIOrchestrator()
        orchestrator._create_entities_from_draft(solution, entities, current_user.id)
    except Exception as e:
        db.session.rollback()
        logger.error("create_from_template entity creation failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500
    template.usage_count = (template.usage_count or 0) + 1
    db.session.commit()
    return jsonify({"success": True, "solution_id": solution.id, "redirect": url_for("solution_design.view_solution", solution_id=solution.id)})


@solution_design_bp.route("/from-template/<int:template_id>", methods=["POST"])
@login_required
def create_solution_from_template_by_id(template_id: int):
    """Create a new solution pre-populated from a template (ENT-016 AC: POST /solutions/from-template/<template_id>)."""
    from app.models.solution_governance import SolutionTemplate
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
    template = SolutionTemplate.query.get_or_404(template_id)
    data = request.get_json() or {}
    try:
        import json as _json
        entities = _json.loads(template.template_json) if isinstance(template.template_json, str) else (template.template_json or {})
    except Exception as e:
        logger.warning("Invalid template_json in from-template/%s: %s", template_id, e)
        return jsonify({"success": False, "error": "Invalid template data"}), 400
    name = (data.get("name") or template.name or "From template").strip()[:255]
    solution = Solution(
        name=name,
        description=template.description or "",
        business_domain=template.domain,
        created_by_id=current_user.id,
        status="draft",
    )
    db.session.add(solution)
    db.session.flush()
    try:
        orchestrator = SolutionAIOrchestrator()
        orchestrator._create_entities_from_draft(solution, entities, current_user.id)
    except Exception as e:
        db.session.rollback()
        logger.error("from-template/%s entity creation failed: %s", template_id, e)
        return jsonify({"success": False, "error": str(e)}), 500
    template.usage_count = (template.usage_count or 0) + 1
    db.session.commit()
    return jsonify({
        "success": True,
        "solution_id": solution.id,
        "name": solution.name,
        "redirect": url_for("solution_design.view_solution", solution_id=solution.id),
    })


@solution_design_bp.route("/<int:solution_id>/json", methods=["GET"])
@login_required
def api_get_solution(solution_id: int):
    """Get solution details as JSON."""
    solution = Solution.query.get_or_404(solution_id)

    # Verify ownership
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        return jsonify({"success": False, "error": "Permission denied"}), 403

    return jsonify(
        {
            "success": True,
            "solution": {
                "id": solution.id,
                "name": solution.name,
                "description": solution.description,
                "status": solution.status,
                "business_domain": solution.business_domain,
                "solution_type": solution.solution_type,
                "complexity_level": solution.complexity_level,
                "business_value": solution.business_value,
                "created_at": solution.created_at.isoformat() if solution.created_at else None,
            },
        }
    )


@solution_design_bp.route("/<int:solution_id>/gantt-data", methods=["GET"])
@login_required
def api_solution_gantt_data(solution_id: int):
    """
    Gantt timeline data for a solution's implementation roadmap.

    Returns the generic Gantt contract consumed by the ganttTimeline Alpine component.
    Transforms RoadmapWorkPackage records into groups/tasks/milestones.
    """
    try:
        from app.models.roadmap_models import RoadmapDeliverable, RoadmapWorkPackage

        solution = Solution.query.get_or_404(solution_id)

        # Query work packages for this solution
        work_packages = RoadmapWorkPackage.query.filter_by(
            source_id=solution_id, source_type="solution"
        ).all()

        # Group by business_capability
        seen_groups = {}
        groups = []
        tasks = []

        for wp in work_packages:
            # Build group
            group_key = (wp.business_capability or "Ungrouped").strip()
            group_id = group_key.lower().replace(" ", "-")
            if group_id not in seen_groups:
                seen_groups[group_id] = True
                groups.append({
                    "id": group_id,
                    "label": group_key,
                    "collapsed": False,
                })

            # Map dependencies (M2M relationship)
            dep_ids = []
            try:
                dep_ids = [str(dep.id) for dep in wp.dependencies] if wp.dependencies else []
            except Exception:
                dep_ids = []

            tasks.append({
                "id": str(wp.id),
                "name": wp.name,
                "group": group_id,
                "start_date": wp.start_date.isoformat() if wp.start_date else None,
                "end_date": wp.end_date.isoformat() if wp.end_date else None,
                "status": wp.status or "planned",
                "progress": wp.progress_percentage or 0,
                "dependencies": dep_ids,
                "meta": {
                    "priority": wp.priority or "medium",
                    "assigned_to": wp.assigned_to or "Unassigned",
                    "estimated_cost": f"£{wp.estimated_cost:,.0f}" if wp.estimated_cost else "—",
                    "risk_level": wp.risk_level or "medium",
                    "description": wp.description or "",
                },
            })

        # Milestones from deliverables with due dates
        milestones = []
        deliverable_ids = [wp.id for wp in work_packages]
        if deliverable_ids:
            deliverables = RoadmapDeliverable.query.filter(
                RoadmapDeliverable.work_package_id.in_(deliverable_ids),
                RoadmapDeliverable.due_date.isnot(None),
            ).all()
            for d in deliverables:
                # Find which group this deliverable's WP belongs to
                ms_group = None
                for wp in work_packages:
                    if wp.id == d.work_package_id:
                        ms_group = (wp.business_capability or "Ungrouped").strip().lower().replace(" ", "-")
                        break
                milestones.append({
                    "id": f"ms-{d.id}",
                    "date": d.due_date.isoformat() if d.due_date else None,
                    "label": d.name,
                    "group": ms_group,
                })

        # ADM Phase milestones (SDX-015)
        phase_names = {
            "a": "Architecture Vision",
            "b": "Business Architecture",
            "c": "Information Systems Architecture",
            "d": "Technology Architecture",
            "e": "Opportunities & Solutions",
            "f": "Migration Planning",
            "g": "Implementation Governance",
            "h": "Architecture Change Management",
        }
        adm_group_id = "adm-phases"
        has_adm_milestones = False
        for letter in "abcdefgh":
            completed_at = getattr(solution, f"adm_phase_{letter}_completed_at", None)
            if completed_at:
                if not has_adm_milestones:
                    groups.insert(0, {
                        "id": adm_group_id,
                        "label": "ADM Phases",
                        "collapsed": False,
                    })
                    has_adm_milestones = True
                milestones.append({
                    "id": f"adm-{letter}",
                    "date": completed_at.isoformat() if completed_at else None,
                    "label": f"Phase {letter.upper()} — {phase_names.get(letter, '')}",
                    "group": adm_group_id,
                    "type": "adm_milestone",
                })

        # Compute timeline bounds
        from datetime import datetime
        all_dates = []
        for t in tasks:
            if t["start_date"]:
                all_dates.append(t["start_date"])
            if t["end_date"]:
                all_dates.append(t["end_date"])
        for m in milestones:
            if m["date"]:
                all_dates.append(m["date"])
        now = datetime.now()
        timeline_start = min(all_dates) if all_dates else f"{now.year}-01-01"
        timeline_end = max(all_dates) if all_dates else f"{now.year + 2}-12-31"

        return jsonify({
            "success": True,
            "gantt": {
                "timelineStart": timeline_start,
                "timelineEnd": timeline_end,
                "groups": groups,
                "tasks": tasks,
                "milestones": milestones,
            },
            "config": {
                "groupLabel": "Work Stream",
                "statusColors": {
                    "planned":     {"fill": "#9ca3af", "bg": "#f3f1ee", "text": "#4b5563"},
                    "identified":  {"fill": "#9ca3af", "bg": "#f3f1ee", "text": "#4b5563"},
                    "in_progress": {"fill": "#ea6a47", "bg": "#fef0ec", "text": "#9a3412"},
                    "approved":    {"fill": "#22c55e", "bg": "#f0fdf4", "text": "#15803d"},
                    "completed":   {"fill": "#22c55e", "bg": "#f0fdf4", "text": "#15803d"},
                },
                "detailFields": [
                    {"key": "meta.priority", "label": "Priority"},
                    {"key": "meta.assigned_to", "label": "Assigned To"},
                    {"key": "meta.estimated_cost", "label": "Est. Cost"},
                    {"key": "meta.risk_level", "label": "Risk"},
                ],
                "features": {
                    "dependencies": True,
                    "milestones": True,
                    "progress": True,
                    "detailPanel": True,
                    "todayMarker": True,
                    "export": ["csv"],
                },
            },
        })

    except HTTPException:
        raise
    except Exception as e:
        from flask import current_app
        current_app.logger.error(f"Error getting solution gantt data: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# WORK PACKAGE CRUD (for Gantt component)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/api/work-packages", methods=["POST"])
@solution_design_bp.route(
    "/<int:solution_id>/api/work-packages/<int:wp_id>", methods=["PUT", "DELETE"]
)
@login_required
@audit_log("solution_work_package_crud")
def api_solution_work_packages(solution_id, wp_id=None):
    """CRUD for solution work packages (used by Gantt component)."""
    from app.models.roadmap_models import RoadmapWorkPackage

    solution = Solution.query.get_or_404(solution_id)

    if solution.created_by_id != current_user.id and not current_user.is_admin:
        return jsonify({"success": False, "error": "Permission denied"}), 403

    try:
        if request.method == "POST":
            data = request.get_json() or {}
            if not data.get("name"):
                return jsonify({"success": False, "error": "name is required"}), 400

            wp = RoadmapWorkPackage(
                name=data["name"],
                status=data.get("status", "planned"),
                priority=data.get("priority", "medium"),
                description=data.get("description", ""),
                source_type="solution",
                source_id=solution_id,
                business_capability=data.get("group", "General"),
            )
            if data.get("start_date"):
                wp.start_date = datetime.strptime(data["start_date"], "%Y-%m-%d")
            if data.get("end_date"):
                wp.end_date = datetime.strptime(data["end_date"], "%Y-%m-%d")

            db.session.add(wp)
            db.session.commit()

            # Sync new work package to ArchiMate WorkPackage
            try:
                from app.services.solution_archimate_sync_service import sync_work_packages
                sync_work_packages(solution_id)
                db.session.commit()
            except Exception as sync_err:
                logger.warning(f"Work package ArchiMate sync failed: {sync_err}")

            return jsonify({"success": True, "id": wp.id}), 201

        elif request.method == "PUT":
            wp = RoadmapWorkPackage.query.get_or_404(wp_id)
            data = request.get_json() or {}
            for field in ("name", "status", "priority", "description"):
                if field in data:
                    setattr(wp, field, data[field])
            if "start_date" in data:
                wp.start_date = (
                    datetime.strptime(data["start_date"], "%Y-%m-%d")
                    if data["start_date"]
                    else None
                )
            if "end_date" in data:
                wp.end_date = (
                    datetime.strptime(data["end_date"], "%Y-%m-%d")
                    if data["end_date"]
                    else None
                )
            db.session.commit()

            # Re-sync work packages after update
            try:
                from app.services.solution_archimate_sync_service import sync_work_packages
                sync_work_packages(solution_id)
                db.session.commit()
            except Exception as sync_err:
                logger.warning(f"Work package ArchiMate sync failed: {sync_err}")

            return jsonify({"success": True})

        elif request.method == "DELETE":
            wp = RoadmapWorkPackage.query.get_or_404(wp_id)
            db.session.delete(wp)
            db.session.commit()
            return jsonify({"success": True})

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in solution work package CRUD: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/update-json", methods=["PUT"])
@login_required
@audit_log("update_solution_json")
def api_update_solution(solution_id: int):
    """Update solution via JSON API."""
    solution = Solution.query.get_or_404(solution_id)

    # Verify ownership
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        return jsonify({"success": False, "error": "Permission denied"}), 403

    data = request.get_json()

    if not data:
        return jsonify({"success": False, "error": "No JSON body provided"}), 400

    unknown_keys = set(data.keys()) - _EDITABLE_FIELDS
    if unknown_keys:
        return jsonify({"success": False, "error": f"Unknown fields: {sorted(unknown_keys)}"}), 422

    # Validate every submitted field *before* touching the model, so a bad value
    # anywhere in the payload is a single 400 rather than a partial write followed
    # by a DB-level failure on commit (which the broad except below turned into an
    # opaque 500 — see api_update_solution history).
    updates: dict = {}
    for field in _EDITABLE_FIELDS:
        if field not in data:
            continue
        value = data[field]

        if field == "name" and not (value and str(value).strip()):
            return jsonify({"success": False, "error": "name cannot be empty"}), 400

        if field in _STRING_FIELD_MAX_LENGTHS and value is not None:
            if not isinstance(value, str):
                return jsonify({"success": False, "error": f"{field} must be a string"}), 400
            max_len = _STRING_FIELD_MAX_LENGTHS[field]
            if len(value) > max_len:
                return jsonify(
                    {"success": False, "error": f"{field} must be {max_len} characters or fewer"}
                ), 400

        if field in _DATE_FIELDS and value is not None:
            try:
                value = datetime.strptime(value, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return jsonify(
                    {"success": False, "error": f"{field} must be a date in YYYY-MM-DD format"}
                ), 400

        if field in _DECIMAL_FIELDS and value is not None:
            try:
                value = Decimal(str(value)) if field == "estimated_cost" else float(value)
            except (InvalidOperation, TypeError, ValueError):
                return jsonify({"success": False, "error": f"{field} must be a number"}), 400

        updates[field] = value

    # PLT-014: Capture old values for change detection
    old_status = solution.status
    old_owner = solution.solution_owner

    try:
        for field, value in updates.items():
            setattr(solution, field, value)

        # PLT-014: Notify on status change
        if old_status and solution.status and old_status != solution.status and solution.created_by_id:
            _create_notification(
                user_id=solution.created_by_id,
                notification_type="solution_update",
                message=(
                    f"Solution '{solution.name}' status changed "
                    f"from '{old_status}' to '{solution.status}'."
                ),
                solution_id=solution.id,
            )

        # PLT-014: Notify on owner change
        if old_owner != solution.solution_owner and solution.solution_owner and solution.created_by_id:
            _create_notification(
                user_id=solution.created_by_id,
                notification_type="assignment",
                message=f"Solution '{solution.name}' owner changed to '{solution.solution_owner}'.",
                solution_id=solution.id,
            )

        solution.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": "Solution updated successfully",
                "solution_id": solution.id,
            }
        )
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/delete-json", methods=["DELETE"])
@login_required
@audit_log("delete_solution_json")
def api_delete_solution(solution_id: int):
    """Delete solution via JSON API."""
    solution = Solution.query.get_or_404(solution_id)

    # Verify ownership
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        return jsonify({"success": False, "error": "Permission denied"}), 403

    try:
        # Run engine-level archimate/architecture cleanup first (before session savepoints)
        # to handle cross-solution FK references that _cascade_delete_solution misses.
        try:
            _engine_archimate_cleanup([solution_id])
        except Exception:
            logger.warning("engine archimate cleanup failed for solution %s", solution_id, exc_info=True)
            db.session.rollback()

        _cascade_delete_solution(solution_id)
        solution = Solution.query.get(solution_id)
        if solution:
            db.session.delete(solution)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Solution deleted successfully",
            "redirect_url": url_for("solution_design.list_solutions"),
        })
    except Exception as e:
        db.session.rollback()
        logger.error("delete_solution failed for id=%s: %s", solution_id, e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# CAPABILITY MAPPING ENDPOINTS
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/capabilities", methods=["GET"])
@login_required
def get_solution_capabilities(solution_id: int):
    """Get all capabilities mapped to a solution."""
    solution = Solution.query.get_or_404(solution_id)

    try:
        capabilities = _get_solution_capabilities_payload(solution)

        return jsonify(
            {
                "success": True,
                "capabilities": capabilities,
                "counts": {
                    "required": len([c for c in capabilities if c["category"] == "required"]),
                    "optional": len([c for c in capabilities if c["category"] == "optional"]),
                    "future": len([c for c in capabilities if c["category"] == "future"]),
                },
            }
        )
    except Exception as e:
        logger.error(f"Error getting solution capabilities: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/motivation-elements", methods=["GET"])
@login_required
def get_motivation_elements(solution_id):
    """Return Assessments, Principles, Outcomes, Values for a solution."""
    solution = Solution.query.get_or_404(solution_id)
    elements = []
    try:
        from app.models.solution_architect_models import (
            SolutionAssessment, SolutionPrinciple,
            SolutionAnalysisSession, SolutionProblemDefinition,
        )
        if solution.analysis_session_id:
            session = db.session.get(SolutionAnalysisSession, solution.analysis_session_id)
            if session:
                pd = SolutionProblemDefinition.query.filter_by(session_id=session.id).first()
                if pd:
                    for a in SolutionAssessment.query.filter_by(problem_id=pd.id).all():
                        elements.append({
                            'id': a.id, 'name': getattr(a, 'aspect', '') or getattr(a, 'name', ''),
                            'type': 'Assessment', 'description': getattr(a, 'current_state', '') or getattr(a, 'gap_analysis', '') or '',
                        })
                    for p in SolutionPrinciple.query.filter_by(problem_id=pd.id).all():
                        elements.append({
                            'id': p.id, 'name': p.name,
                            'type': 'Principle', 'description': p.statement or p.rationale or '',
                        })
    except Exception as e:
        logger.debug(f"Error loading motivation elements: {e}")
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement as SAE
        from app.models.archimate_core import ArchiMateElement as AE
        links = SAE.query.filter_by(solution_id=solution_id, layer_type='motivation').all()
        elem_ids = [item.element_id for item in links if item.element_id]
        if elem_ids:
            aes = AE.query.filter(AE.id.in_(elem_ids), AE.type.in_(['Outcome', 'Value'])).all()
            for ae in aes:
                elements.append({'id': ae.id, 'name': ae.name, 'type': ae.type, 'description': ae.description or ''})
    except Exception as e:
        logger.debug(f"Error loading ArchiMate motivation elements: {e}")
    return jsonify({'data': elements})


@solution_design_bp.route("/<int:solution_id>/entities/<int:entity_id>", methods=["PATCH"])
@login_required
def patch_motivation_entity(solution_id, entity_id):
    """Inline edit a motivation element."""
    Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "No data"}), 400
    from app.models.solution_architect_models import (
        SolutionDriver, SolutionGoal, SolutionRequirement,
        SolutionConstraint, SolutionPrinciple, SolutionAssessment,
    )
    entity = None
    for Model in [SolutionDriver, SolutionGoal, SolutionRequirement,
                  SolutionConstraint, SolutionPrinciple, SolutionAssessment]:
        entity = db.session.get(Model, entity_id)
        if entity:
            break
    if not entity:
        return jsonify({"error": "Entity not found"}), 404
    for field in ['name', 'description', 'impact_level', 'urgency', 'priority', 'measurement_criteria']:
        if field in data and hasattr(entity, field):
            setattr(entity, field, data[field])
    try:
        db.session.commit()
        return jsonify({"success": True, "id": entity.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/capabilities", methods=["POST"])
@login_required
@audit_log("update_solution_capabilities")
def update_solution_capabilities(solution_id: int):
    """Add or update capability mappings for a solution."""
    solution = Solution.query.get_or_404(solution_id)

    try:
        from app.models.solution_models import SolutionCapabilityMapping
        from app.models.solution_architect_models import SolutionProblemDefinition

        # Resolve problem_id via analysis session
        problem_id = None
        if solution.analysis_session_id:
            pd = SolutionProblemDefinition.query.filter_by(
                session_id=solution.analysis_session_id
            ).first()
            if pd:
                problem_id = pd.id

        data = request.get_json()

        # Support single capability_id from journey's selectCapability()
        single_cap_id = data.get("capability_id")
        if single_cap_id:
            capabilities = [{"capability_id": single_cap_id}]
        else:
            capabilities = data.get("capabilities", [])

        # Clear existing mappings if replace mode
        if data.get("replace", False):
            SolutionCapabilityMapping.query.filter_by(problem_id=problem_id).delete()

        added = 0
        updated = 0

        # Batch prefetch existing mappings for this solution
        incoming_cap_ids = [c.get("capability_id") for c in capabilities if c.get("capability_id")]
        existing_map = {}
        if incoming_cap_ids:
            existing_mappings = SolutionCapabilityMapping.query.filter(
                SolutionCapabilityMapping.solution_id == solution_id,
                SolutionCapabilityMapping.capability_id.in_(incoming_cap_ids),
            ).all()
            existing_map = {m.capability_id: m for m in existing_mappings}

        for cap_data in capabilities:
            capability_id = cap_data.get("capability_id")
            if not capability_id:
                continue

            # Check if mapping exists using prefetched map
            existing = existing_map.get(capability_id)

            if existing:
                # Update existing
                existing.support_level = cap_data.get("category", existing.support_level)
                existing.notes = cap_data.get("notes", existing.notes)
                existing.priority = cap_data.get("priority", existing.priority)
                existing.rationale = cap_data.get("rationale", existing.rationale)
                updated += 1
            else:
                # Create new mapping
                mapping = SolutionCapabilityMapping(
                    problem_id=problem_id,
                    solution_id=solution_id,
                    capability_id=capability_id,
                    support_level=cap_data.get("category", "required"),
                    notes=cap_data.get("notes"),
                    priority=cap_data.get("priority", 0),
                    rationale=cap_data.get("rationale"),
                    created_by_id=current_user.id,
                )
                db.session.add(mapping)
                added += 1

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Added {added}, updated {updated} capabilities",
                "added": added,
                "updated": updated,
            }
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating solution capabilities: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/capabilities/<int:mapping_id>", methods=["DELETE"])
@login_required
@audit_log("delete_solution_capability")
def delete_solution_capability(solution_id: int, mapping_id: int):
    """Remove a capability mapping from a solution."""
    try:
        from app.models.solution_models import SolutionCapabilityMapping

        mapping = SolutionCapabilityMapping.query.filter_by(
            id=mapping_id
        ).first()
        if not mapping:
            return jsonify({"success": False, "error": "Capability mapping not found"}), 404

        db.session.delete(mapping)
        db.session.commit()

        return jsonify({"success": True, "message": "Capability mapping removed"})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting capability mapping: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# ARCHIMATE ELEMENT MAPPING ENDPOINTS
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/archimate-elements", methods=["GET"])
@login_required
def get_solution_archimate_elements(solution_id: int):
    """Get all ArchiMate elements mapped to a solution, grouped by layer."""
    Solution.query.get_or_404(solution_id)

    try:
        from app.models.solution_models import SolutionArchiMateElement

        elements = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()

        # Group by layer
        by_layer = {
            "motivation": [],
            "strategy": [],
            "business": [],
            "application": [],
            "technology": [],
            "implementation": [],
        }

        for elem in elements:
            layer = elem.layer_type
            if layer in by_layer:
                by_layer[layer].append(
                    {
                        "mapping_id": elem.id,
                        "element_id": elem.element_id,
                        "element_table": elem.element_table,
                        "element_name": elem.element_name,
                        "relationship_type": elem.relationship_type,
                        "notes": elem.notes,
                        "is_new_element": elem.is_new_element,
                        "color": SolutionArchiMateElement.get_layer_color(layer),
                    }
                )

        return jsonify(
            {
                "success": True,
                "elements": by_layer,
                "counts": {layer: len(items) for layer, items in by_layer.items()},
                "total": len(elements),
            }
        )
    except Exception as e:
        logger.error(f"Error getting solution ArchiMate elements: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/archimate-elements", methods=["POST"])
@login_required
@audit_log("update_solution_archimate")
def update_solution_archimate_elements(solution_id: int):
    """Add or update ArchiMate element mappings for a solution."""
    Solution.query.get_or_404(solution_id)

    try:
        from app.models.solution_models import SolutionArchiMateElement

        data = request.get_json()
        elements = data.get("elements", [])
        layer_type = data.get("layer_type")  # Optional: filter to specific layer

        # Clear existing mappings if replace mode
        if data.get("replace", False):
            query = SolutionArchiMateElement.query.filter_by(solution_id=solution_id)
            if layer_type:
                query = query.filter_by(layer_type=layer_type)
            query.delete()

        added = 0
        updated = 0

        # Batch prefetch existing mappings for this solution
        existing_elems = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id
        ).all()
        existing_map = {
            (e.layer_type, e.element_table, e.element_id): e for e in existing_elems
        }

        for elem_data in elements:
            element_id = elem_data.get("element_id")
            element_table = elem_data.get("element_table")
            elem_layer = elem_data.get("layer_type", layer_type)

            if not all([element_id, element_table, elem_layer]):
                continue

            # Check if mapping exists using prefetched map
            existing = existing_map.get((elem_layer, element_table, element_id))

            if existing:
                # Update existing
                existing.element_name = elem_data.get("element_name", existing.element_name)
                existing.relationship_type = elem_data.get(
                    "relationship_type", existing.relationship_type
                )
                existing.notes = elem_data.get("notes", existing.notes)
                updated += 1
            else:
                # Create new mapping
                mapping = SolutionArchiMateElement(
                    solution_id=solution_id,
                    layer_type=elem_layer,
                    element_id=element_id,
                    element_table=element_table,
                    element_name=elem_data.get("element_name"),
                    relationship_type=elem_data.get("relationship_type"),
                    notes=elem_data.get("notes"),
                    is_new_element=elem_data.get("is_new_element", False),
                    created_by_id=current_user.id,
                )
                db.session.add(mapping)
                added += 1

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Added {added}, updated {updated} elements",
                "added": added,
                "updated": updated,
            }
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating solution ArchiMate elements: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route(
    "/<int:solution_id>/archimate-elements/<int:mapping_id>", methods=["DELETE"]
)
@login_required
def delete_solution_archimate_element(solution_id: int, mapping_id: int):
    """Remove an ArchiMate element mapping from a solution."""
    try:
        from app.models.solution_models import SolutionArchiMateElement

        mapping = SolutionArchiMateElement.query.filter_by(
            id=mapping_id, solution_id=solution_id
        ).first_or_404()

        db.session.delete(mapping)
        db.session.commit()

        return jsonify({"success": True, "message": "ArchiMate element mapping removed"})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting ArchiMate element mapping: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# ARCHIMATE LAYER ELEMENTS LOOKUP
# =============================================================================


@solution_design_bp.route("/archimate/<layer>/elements", methods=["GET"])
@login_required
def get_archimate_layer_elements(layer: str):
    """Get ArchiMate elements for a specific layer from the archimate_elements repository."""
    LAYER_COLORS = {
        "motivation": "#B3A2C7", "strategy": "#F5D742", "business": "#FFFFB5",
        "application": "#B5E3FF", "technology": "#C9E6B5", "implementation": "#FFB5B5",
    }
    LAYER_ALIASES = {
        "motivation": ["motivation", "Motivation"],
        "strategy": ["strategy", "Strategy"],
        "business": ["business", "Business"],
        "application": ["application", "Application"],
        "technology": ["technology", "Technology"],
        "implementation": ["implementation", "Implementation"],
    }
    if layer.lower() not in LAYER_ALIASES:
        return jsonify({"success": False, "error": f"Invalid layer: {layer}"}), 400

    try:
        search = request.args.get("search", "").strip()
        element_type = request.args.get("element_type", "").strip()

        layers = LAYER_ALIASES[layer.lower()]
        placeholders = ", ".join([f":layer_{i}" for i in range(len(layers))])
        params = {f"layer_{i}": v for i, v in enumerate(layers)}
        conditions = [f"layer IN ({placeholders})"]
        if element_type:
            conditions.append("type = :etype")
            params["etype"] = element_type
        if search:
            conditions.append("name ILIKE :search")
            params["search"] = f"%{search}%"
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        if _org is not None:
            conditions.append("organization_id = :org")
            params["org"] = _org
        where_clause = "WHERE " + " AND ".join(conditions)
        sql = db.text(f"""
            SELECT id, name, type, layer, description, status
            FROM archimate_elements {where_clause}
            ORDER BY name LIMIT 200
        """)
        rows = db.session.execute(sql, params).fetchall()

        color = LAYER_COLORS.get(layer.lower(), "#9CA3AF")
        elements = []
        for row in rows:
            elements.append({
                "element_id": row[0],
                "element_table": "archimate_elements",
                "element_type": row[2],
                "id": row[0],
                "name": row[1],
                "type": row[2],
                "layer": layer.lower(),
                "description": row[4] or "",
                "color": color,
                "layer_color": color,
                "status": row[5],
            })

        return jsonify({"success": True, "layer": layer.lower(), "color": color, "elements": elements, "count": len(elements)})
    except Exception as e:
        logger.error(f"Error getting ArchiMate layer elements: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/capabilities/search", methods=["GET"])
@login_required
def search_business_capabilities():
    """Search business or technical capabilities for the solution wizard capability picker."""
    try:
        cap_type = request.args.get("type", "business").strip().lower()
        q = request.args.get("q", "").strip()
        domain = request.args.get("domain", "").strip()
        level = request.args.get("level", type=int)
        limit = min(safe_int_arg('limit', 20, minimum=1, maximum=500), 50)

        if cap_type == "technical":
            from app.models.technical_capability import TechnicalCapability
            query = TechnicalCapability.query
            if q:
                query = query.filter(TechnicalCapability.name.ilike(f"%{q}%"))
            if domain:
                query = query.filter(TechnicalCapability.acm_domain.ilike(f"%{domain}%"))
            if level:
                query = query.filter(TechnicalCapability.level == f"L{level}")
            caps = query.order_by(TechnicalCapability.level.asc(), TechnicalCapability.name.asc()).limit(limit).all()
            return jsonify({
                "success": True,
                "capabilities": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "code": getattr(c, "code", None),
                        "level": getattr(c, "level_number", None),
                        "business_domain": getattr(c, "acm_domain", None),
                        "acm_domain": getattr(c, "acm_domain", None),
                        "is_differentiating": getattr(c, "is_differentiating", False),
                        "industry_maturity": getattr(c, "industry_maturity", None),
                        "current_maturity": None,
                        "target_maturity": None,
                    }
                    for c in caps
                ],
            })
        else:
            from app.models.business_capabilities import BusinessCapability
            query = BusinessCapability.query
            if q:
                query = query.filter(BusinessCapability.name.ilike(f"%{q}%"))
            if domain:
                query = query.filter(BusinessCapability.business_domain.ilike(f"%{domain}%"))
            if level:
                query = query.filter(BusinessCapability.level == level)
            total_count = query.count()
            caps = query.order_by(BusinessCapability.level.asc(), BusinessCapability.name.asc()).limit(limit).all()
            return jsonify({
                "success": True,
                "total": total_count,
                "capabilities": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "code": getattr(c, "code", None),
                        "level": getattr(c, "level", None),
                        "business_domain": getattr(c, "business_domain", None),
                        "strategic_importance": getattr(c, "strategic_importance", None),
                        "current_maturity": getattr(c, "current_maturity_level", None),
                        "target_maturity": getattr(c, "target_maturity_level", None),
                    }
                    for c in caps
                ],
            })
    except Exception as e:
        logger.error(f"Error searching capabilities: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/capabilities/tree", methods=["GET"])
@login_required
def get_capabilities_tree():
    """Return all business capabilities with hierarchy info for tree picker (CAP-016)."""
    try:
        from app.models.business_capabilities import BusinessCapability

        domain = request.args.get("domain", "").strip()
        query = BusinessCapability.query
        if domain:
            query = query.filter(BusinessCapability.business_domain.ilike(f"%{domain}%"))
        caps = query.order_by(
            BusinessCapability.level.asc(), BusinessCapability.name.asc()
        ).all()

        # Collect unique domains for the domain filter dropdown
        domains = sorted(
            set(
                c.business_domain
                for c in caps
                if getattr(c, "business_domain", None)
            )
        )

        return jsonify({
            "success": True,
            "domains": domains,
            "capabilities": [
                {
                    "id": c.id,
                    "name": c.name,
                    "code": getattr(c, "code", None),
                    "level": getattr(c, "level", None),
                    "parent_capability_id": getattr(c, "parent_capability_id", None),
                    "business_domain": getattr(c, "business_domain", None),
                    "strategic_importance": getattr(c, "strategic_importance", None),
                    "current_maturity": getattr(c, "current_maturity_level", None),
                    "target_maturity": getattr(c, "target_maturity_level", None),
                }
                for c in caps
            ],
        })
    except Exception as e:
        logger.error(f"Error loading capabilities tree: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500



def _solution_has_ai_generated_content(solution):
    """Return True if solution has AI-generated content requiring human approval before ARB submit (ENH-005)."""
    if not solution.analysis_session_id:
        return False
    try:
        from app.models.solution_architect_models import (
            SolutionConstraint,
            SolutionDriver,
            SolutionGoal,
            SolutionProblemDefinition,
        )
        problem = SolutionProblemDefinition.query.filter_by(
            session_id=solution.analysis_session_id
        ).first()
        if not problem:
            return False
        for model in (SolutionDriver, SolutionGoal, SolutionConstraint):
            if model.query.filter_by(problem_id=problem.id).filter(
                model.ai_generated.is_(True)
            ).limit(1).count() > 0:
                return True
        from app.models.solution_architect_models import SolutionRecommendation
        if SolutionRecommendation.query.filter_by(
            session_id=solution.analysis_session_id
        ).limit(1).count() > 0:
            return True
        return False
    except Exception:
        return False


def _validate_solution_recommended_option(solution):
    """Validate that the solution's recommended option references real vendor products (ENH-007). Returns (True, None) or (False, error_message)."""
    if not solution.analysis_session_id:
        return True, None
    try:
        from app.models.solution_architect_models import SolutionRecommendation
        from app.models.vendor.vendor_organization import VendorProduct

        rec = (
            SolutionRecommendation.query.filter_by(session_id=solution.analysis_session_id)
            .order_by(SolutionRecommendation.rank.asc().nullslast(), SolutionRecommendation.id.asc())
            .first()
        )
        if not rec:
            return True, None
        option_type = getattr(rec.option_type, "value", str(rec.option_type)) if hasattr(rec, "option_type") else str(rec.option_type)
        if option_type in ("build", "reuse"):
            return True, None
        vendor_ids = rec.vendor_products if isinstance(rec.vendor_products, list) else []
        if not vendor_ids:
            return True, None
        for vid in vendor_ids:
            try:
                v_id = int(vid)
            except (TypeError, ValueError):
                return False, "option_invalid"
            if VendorProduct.query.get(v_id) is None:
                return False, "vendor_not_found"
        return True, None
    except Exception:
        return True, None


@solution_design_bp.route("/<int:solution_id>/submit-for-arb", methods=["POST"])
@login_required
@audit_log("submit_solution_for_arb")
def submit_solution_for_arb(solution_id: int):
    """Submit a solution for Architecture Review Board approval."""
    data = request.get_json(silent=True) or {}
    from app.modules.transformation_room.arb_submission_adapter import (
        TypedARBSubmissionAdapter,
    )

    result = TypedARBSubmissionAdapter.submit_solution_from_request(
        solution_id=solution_id,
        payload=data,
    )
    if not result.success:
        return jsonify({
            "success": False,
            "reason_codes": result.reason_codes,
            "missing_evidence": result.missing_evidence,
        }), result.http_status
    return jsonify({
        "success": True,
        "message": "Solution submitted for ARB review",
        "review_item_id": result.review_item_id,
        "review_number": result.review_number,
        "snapshot_id": result.snapshot_id,
        "idempotent": result.idempotent,
        "review_cycle_id": result.review_cycle_id,
        "canonical_url": result.canonical_url,
    })


@solution_design_bp.route("/<int:solution_id>/governance-gates/check", methods=["GET"])
@login_required
def check_governance_gates(solution_id: int):
    """GOV-03: Check if a solution passes governance gates.

    Returns gate check result with pass/fail and specific failures.
    Query param: gate (default "arb_submission").
    """
    solution = Solution.query.get_or_404(solution_id)  # noqa: F841 — validates existence
    gate_name = request.args.get("gate", "arb_submission")

    try:
        from app.modules.solutions_strategic.v2.services.governance_gate_service import check_gate

        result = check_gate(solution_id, gate_name)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error("Governance gate check failed for solution %s: %s", solution_id, e)
        return jsonify({"success": False, "error": "Failed to check governance gate"}), 500


@solution_design_bp.route("/<int:solution_id>/governance-status", methods=["GET"])
@login_required
def get_solution_governance_status(solution_id: int):
    """Get the governance status of a solution."""
    solution = Solution.query.get_or_404(solution_id)

    try:
        # Build readiness checks with section anchors for Fix links (SDX-007)
        readiness = solution.arb_readiness
        readiness_checks = []
        anchor_map = {
            "Problem statement defined": "phase-a",
            "Solution owner assigned": "phase-a",
            "Business sponsor assigned": "phase-a",
            "Technical lead assigned": "phase-a",
            "Business capabilities mapped": "phase-bcd",
            "Solution option recommended": "phase-e",
            "Risks documented": "phase-risks",
            "Security lead assigned": "phase-a",
        }
        for check in readiness.get("checks", []):
            readiness_checks.append({
                "label": check["label"],
                "passed": check["passed"],
                "required": check["required"],
                "section_anchor": anchor_map.get(check["label"], "phase-g"),
            })

        # ENH-005: Expose so frontend can show approval step when submitting to ARB
        has_ai_generated_content = _solution_has_ai_generated_content(solution)
        # ENH-009: Expose so frontend can show second reviewer selector when required
        require_second_review = current_app.config.get("FLASK_ARB_REQUIRE_SECOND_REVIEW", False)

        return jsonify(
            {
                "success": True,
                "governance_status": solution.governance_status,
                "has_ai_generated_content": has_ai_generated_content,
                "require_second_review": require_second_review,
                "arb_submission_date": solution.arb_submission_date.isoformat()
                if solution.arb_submission_date
                else None,
                "arb_approval_date": solution.arb_approval_date.isoformat()
                if solution.arb_approval_date
                else None,
                "arb_review_item_id": solution.arb_review_item_id,
                "arb_rejection_reason": solution.arb_rejection_reason,
                "can_submit": solution.governance_status in ["draft", "rejected"],
                "is_approved": solution.governance_status == "approved",
                "readiness_checks": readiness_checks,
                "readiness_passed": readiness.get("passed", 0),
                "readiness_total": readiness.get("total", 0),
                "can_submit_arb": readiness.get("can_submit", False),
            }
        )
    except Exception as e:
        logger.error(f"Error getting governance status: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# ARB CONDITION TRACKING (SDX-004)
# =============================================================================


@solution_design_bp.route(
    "/<int:solution_id>/arb-condition/<int:condition_index>/toggle", methods=["POST"]
)
@login_required
def toggle_arb_condition(solution_id: int, condition_index: int):
    """Toggle the completed status of an ARB condition."""
    solution = Solution.query.get_or_404(solution_id)

    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    if TypedARBDecisionAdapter.solution_has_typed_cycle(solution_id):
        return jsonify({
            "success": False,
            "reason_codes": ["typed_condition_toggle_not_supported"],
        }), 409

    try:
        from app.models.architecture_review_board import ARBReviewItem

        # Find the latest review with conditions for this solution
        review = (
            ARBReviewItem.query.filter_by(solution_id=solution.id)
            .filter(ARBReviewItem.decision == "approved_with_conditions")
            .order_by(ARBReviewItem.id.desc())
            .first()
        )
        if not review or not review.conditions:
            return jsonify({"success": False, "error": "No conditions found"}), 404

        conditions = list(review.conditions)
        if condition_index < 0 or condition_index >= len(conditions):
            return jsonify({"success": False, "error": "Invalid condition index"}), 400

        # Toggle the completed flag
        cond = conditions[condition_index]
        if isinstance(cond, dict):
            cond["completed"] = not cond.get("completed", False)
        else:
            # If condition is a plain string, convert to dict
            conditions[condition_index] = {"text": cond, "completed": True}

        review.conditions = conditions
        db.session.flag_modified(review, "conditions")
        db.session.commit()

        all_met = all(
            (c.get("completed", False) if isinstance(c, dict) else False) for c in conditions
        )
        return jsonify({
            "success": True,
            "conditions": conditions,
            "all_conditions_met": all_met,
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error toggling ARB condition: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/archimate-elements/search", methods=["GET"])
@login_required
def search_archimate_elements():
    """Search ArchiMate elements by type, layer, and name — used by the composer search panel."""
    from app.models.models import ArchiMateElement

    query = ArchiMateElement.query

    elem_type = request.args.get("type", "").strip()
    if elem_type:
        query = query.filter(ArchiMateElement.type == elem_type)

    layer = request.args.get("layer", "").strip()
    if layer:
        query = query.filter(ArchiMateElement.layer.ilike(layer))

    search_q = request.args.get("q", "").strip()
    if search_q:
        query = query.filter(ArchiMateElement.name.ilike(f"%{search_q}%"))

    try:
        limit = min(safe_int_arg('limit', 30, minimum=1, maximum=500), 200)
    except (ValueError, TypeError):
        limit = 30

    elements = query.order_by(ArchiMateElement.name).limit(limit).all()
    return jsonify({
        "data": [
            {
                "id": el.id,
                "name": el.name,
                "type": el.type,
                "layer": el.layer or "",
                "description": el.description or "",
            }
            for el in elements
        ]
    })


@solution_design_bp.route("/api/archimate-all-elements", methods=["GET"])
@login_required
def api_archimate_all_elements():
    """Get ALL ArchiMate 3.2 elements from the repository (720+ elements), optionally filtered."""
    try:
        layer_filter = request.args.get("layer", "all").lower()
        element_type_filter = request.args.get("element_type", "").strip()
        search_term = request.args.get("search", "").strip()

        LAYER_COLORS = {
            "motivation": "#B3A2C7", "strategy": "#F5D742", "business": "#FFFFB5",
            "application": "#B5E3FF", "technology": "#C9E6B5", "implementation": "#FFB5B5",
        }
        LAYER_ALIASES = {
            "motivation": ["motivation", "Motivation"],
            "strategy": ["strategy", "Strategy"],
            "business": ["business", "Business"],
            "application": ["application", "Application"],
            "technology": ["technology", "Technology"],
            "implementation": ["implementation", "Implementation"],
        }

        # Use raw SQL to avoid ORM model/DB schema mismatch (some model columns don't exist in DB)
        conditions = []
        params = {}
        if layer_filter != "all" and layer_filter in LAYER_ALIASES:
            aliases = LAYER_ALIASES[layer_filter]
            placeholders = ", ".join([f":layer_{i}" for i in range(len(aliases))])
            conditions.append(f"layer IN ({placeholders})")
            for i, alias in enumerate(aliases):
                params[f"layer_{i}"] = alias
        if element_type_filter:
            conditions.append("type = :etype")
            params["etype"] = element_type_filter
        if search_term:
            conditions.append("name ILIKE :search")
            params["search"] = f"%{search_term}%"

        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        if _org is not None:
            conditions.append("organization_id = :org")
            params["org"] = _org
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = db.text(f"""
            SELECT id, name, type, layer, description, status,
                   strategic_alignment_score, business_value_score
            FROM archimate_elements
            {where_clause}
            ORDER BY layer, type, name
            LIMIT 1000
        """)
        rows = db.session.execute(sql, params).fetchall()

        elements = []
        for row in rows:
            norm_layer = (row[3] or "").lower()
            elements.append({
                "id": row[0],
                "element_id": row[0],
                "element_table": "archimate_elements",
                "name": row[1],
                "type": row[2],
                "element_type": row[2],
                "layer": norm_layer,
                "layer_color": LAYER_COLORS.get(norm_layer, "#9CA3AF"),
                "description": row[4] or "",
                "status": row[5],
                "strategic_alignment_score": row[6],
                "business_value_score": row[7],
            })

        by_layer = {}
        for el in elements:
            by_layer.setdefault(el["layer"], []).append(el)

        return jsonify({
            "success": True,
            "elements": elements,
            "by_layer": by_layer,
            "count": len(elements),
            "layers": list(by_layer.keys()),
            "layer_colors": LAYER_COLORS,
        })
    except Exception as e:
        logger.error(f"Error fetching ArchiMate elements: {e}")
        return jsonify({"success": False, "error": "An internal error occurred", "elements": []}), 500


# =============================================================================
# CAPABILITIES BY TYPE API
# =============================================================================


@solution_design_bp.route("/api/capabilities-by-type", methods=["GET"])
@login_required
def api_capabilities_by_type():
    """
    Get capabilities filtered by specialization type and level.

    Query Parameters:
        type: BUSINESS, TECHNICAL, MANUFACTURING, APPLICATION (default: BUSINESS)
        level: 0, 1, 2, 3 (optional)
        search: Search term for name filtering (optional)

    Returns:
        JSON with capabilities list
    """
    cap_type = request.args.get("type", "BUSINESS").upper()
    level = request.args.get("level", "")
    search = request.args.get("search", "").strip().lower()

    try:
        capabilities = []

        if cap_type == "TECHNICAL":
            from app.models.technical_capability import TechnicalCapability

            query = TechnicalCapability.query
            if level:
                query = query.filter(TechnicalCapability.level == f"L{level}")
            if search:
                query = query.filter(TechnicalCapability.name.ilike(f"%{search}%"))
            items = query.limit(200).all()
            capabilities = [
                {
                    "id": c.id,
                    "name": c.name,
                    "type": "TECHNICAL",
                    "level": c.level,
                    "acm_domain": getattr(c, "acm_domain", ""),
                    "description": c.description or "",
                }
                for c in items
            ]

        elif cap_type == "APPLICATION":
            from app.models.missing_capability_models import ApplicationCapability

            query = ApplicationCapability.query
            if level:
                query = query.filter(ApplicationCapability.level == int(level))
            if search:
                query = query.filter(ApplicationCapability.name.ilike(f"%{search}%"))
            items = query.limit(200).all()
            capabilities = [
                {
                    "id": c.id,
                    "name": c.name,
                    "type": "APPLICATION",
                    "level": getattr(c, "level", 1),
                    "domain": getattr(c, "domain", ""),
                    "description": getattr(c, "description", "") or "",
                }
                for c in items
            ]

        elif cap_type == "MANUFACTURING":
            from app.models.manufacturing_capability import ManufacturingCapability

            query = ManufacturingCapability.query
            if level:
                query = query.filter(ManufacturingCapability.level == int(level))
            if search:
                query = query.filter(ManufacturingCapability.name.ilike(f"%{search}%"))
            items = query.limit(200).all()
            capabilities = [
                {
                    "id": c.id,
                    "name": c.name,
                    "type": "MANUFACTURING",
                    "level": getattr(c, "level", 1),
                    "category": getattr(c, "category", ""),
                    "description": getattr(c, "description", "") or "",
                }
                for c in items
            ]

        else:  # BUSINESS (default)
            from app.models.unified_capability import UnifiedCapability

            query = UnifiedCapability.query.filter(
                UnifiedCapability.specialization_type == "BUSINESS"
            )
            if level:
                query = query.filter(UnifiedCapability.level == int(level))
            if search:
                query = query.filter(UnifiedCapability.name.ilike(f"%{search}%"))
            items = query.limit(200).all()
            capabilities = [
                {
                    "id": c.id,
                    "name": c.name,
                    "type": "BUSINESS",
                    "level": getattr(c, "level", 1),
                    "domain_id": getattr(c, "domain_id", None),
                    "description": getattr(c, "description", "") or "",
                }
                for c in items
            ]

        return jsonify(
            {
                "success": True,
                "type": cap_type,
                "capabilities": capabilities,
                "count": len(capabilities),
            }
        )

    except Exception as e:
        logger.error(f"Error fetching capabilities by type: {e}")
        return jsonify({"success": False, "error": "An internal error occurred", "capabilities": []}), 500


# =============================================================================
# ROADMAP ITEM CRUD
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/update-roadmap-item", methods=["POST"])
@login_required
@audit_log("update_roadmap_item")
def update_roadmap_item(solution_id: int):
    """Update a single roadmap item in a solution."""
    solution = Solution.query.get_or_404(solution_id)

    if solution.created_by_id != current_user.id and not current_user.is_admin:
        return jsonify({"success": False, "error": "Permission denied"}), 403

    try:
        data = request.get_json()
        index = data.get("index")
        item_data = data.get("item", {})

        if index is None:
            return jsonify({"success": False, "error": "No index provided"}), 400

        current_items = solution.roadmap_items_json or []

        if index < 0 or index >= len(current_items):
            return jsonify({"success": False, "error": "Invalid index"}), 400

        # Update the item at the specified index
        current_items[index].update(
            {
                "name": item_data.get("name", current_items[index].get("name")),
                "description": item_data.get(
                    "description", current_items[index].get("description")
                ),
                "phase": item_data.get("phase", current_items[index].get("phase")),
                "priority": item_data.get("priority", current_items[index].get("priority")),
                "status": item_data.get(
                    "status", current_items[index].get("status", "not_started")
                ),
                "duration_weeks": int(
                    item_data.get("duration_weeks", current_items[index].get("duration_weeks", 1))
                ),
                "percent_complete": int(
                    item_data.get(
                        "percent_complete", current_items[index].get("percent_complete", 0)
                    )
                ),
            }
        )

        solution.roadmap_items_json = current_items
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": "Roadmap item updated successfully",
                "item": current_items[index],
            }
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating roadmap item: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@solution_design_bp.route("/<int:solution_id>/delete-roadmap-items", methods=["POST"])
@login_required
@audit_log("delete_roadmap_items")
def delete_roadmap_items(solution_id: int):
    """Delete selected roadmap items from a solution."""
    solution = Solution.query.get_or_404(solution_id)

    if solution.created_by_id != current_user.id and not current_user.is_admin:
        return jsonify({"success": False, "error": "Permission denied"}), 403

    try:
        data = request.get_json()
        indices_to_delete = set(data.get("indices", []))

        if not indices_to_delete:
            return jsonify({"success": False, "error": "No roadmap items selected"}), 400

        current_items = solution.roadmap_items_json or []
        # Filter out the selected indices
        updated_items = [
            item for idx, item in enumerate(current_items) if idx not in indices_to_delete
        ]

        solution.roadmap_items_json = updated_items
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Deleted {len(indices_to_delete)} roadmap item(s)",
                "remaining_count": len(updated_items),
            }
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting roadmap items: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

# =============================================================================
# CEV-001: TRACEABILITY CHAIN  Solution → Applications → Capabilities → Domain
# =============================================================================

@solution_design_bp.route("/<int:solution_id>/traceability/chain", methods=["GET"])
@login_required
def api_solution_traceability(solution_id: int):
    """
    TOGAF traceability chain: Solution → ApplicationComponents → BusinessCapabilities → Domain.

    Joins: solution_applications → application_components → application_capability_mapping
           → business_capability.
    Returns a list of application nodes, each with their mapped capabilities and domain.
    """
    try:
        from app.models.application_capability import ApplicationCapabilityMapping
        from app.models.business_capabilities import BusinessCapability
        from app.models.solution_models import Solution, solution_applications

        solution = Solution.query.get_or_404(solution_id)

        # Fetch linked applications via the solution_applications junction table
        apps = (
            db.session.query(ApplicationComponent)
            .join(solution_applications,
                  solution_applications.c.application_component_id == ApplicationComponent.id)
            .filter(solution_applications.c.solution_id == solution_id)
            .order_by(ApplicationComponent.name)
            .all()
        )

        chain = []
        for app in apps:
            # Fetch capabilities mapped to this application
            cap_mappings = (
                db.session.query(
                    BusinessCapability.id,
                    BusinessCapability.name,
                    BusinessCapability.business_domain,
                    ApplicationCapabilityMapping.support_level,
                    ApplicationCapabilityMapping.coverage_percentage,
                )
                .join(ApplicationCapabilityMapping,
                      ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id)
                .filter(ApplicationCapabilityMapping.application_component_id == app.id)
                .order_by(BusinessCapability.name)
                .all()
            )

            capabilities = []
            seen_domains = set()
            for cap in cap_mappings:
                capabilities.append({
                    "id": cap.id,
                    "name": cap.name,
                    "domain": cap.business_domain or "Unclassified",
                    "support_level": cap.support_level or "partial",
                    "coverage_pct": cap.coverage_percentage or 0,
                })
                if cap.business_domain:
                    seen_domains.add(cap.business_domain)

            chain.append({
                "app_id": app.id,
                "app_name": app.name,
                "component_type": app.component_type or "Application",
                "lifecycle_status": app.lifecycle_status or "active",
                "deployment_model": app.deployment_model or "",
                "business_domain": app.business_domain or "Unclassified",
                "capabilities": capabilities,
                "domains": sorted(seen_domains),
            })

        return jsonify({
            "solution_id": solution_id,
            "solution_name": solution.name,
            "chain": chain,
            "summary": {
                "app_count": len(chain),
                "capability_count": sum(len(n["capabilities"]) for n in chain),
                "domain_count": len({d for n in chain for d in n["domains"]}),
            },
        })
    except HTTPException as e:
        return jsonify({"error": e.description}), e.code
    except Exception as e:
        logger.error(f"api_solution_traceability error: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


# =============================================================================
# ARCH-004: Viewpoint generation — solution-scoped (phase-filtered elements)
# =============================================================================

# ADM phase → relevant ArchiMate layers
_PHASE_LAYERS = {
    "C": ["application"],
    "D": ["application", "technology"],
    "E": ["strategy", "business", "application"],
}


@solution_design_bp.route("/<int:solution_id>/generate-viewpoint/<phase>", methods=["GET"])
@login_required
def generate_solution_viewpoint(solution_id: int, phase: str):
    """Return the solution's linked ArchiMate elements filtered by ADM phase layers."""
    Solution.query.get_or_404(solution_id)
    phase_upper = phase.upper()
    relevant_layers = _PHASE_LAYERS.get(phase_upper)
    if not relevant_layers:
        return jsonify({"success": False, "error": f"Viewpoint not defined for phase {phase_upper}"}), 400

    try:
        from app.models.solution_models import SolutionArchiMateElement
        elements = SolutionArchiMateElement.query.filter(
            SolutionArchiMateElement.solution_id == solution_id,
            SolutionArchiMateElement.layer_type.in_(relevant_layers),
        ).all()
        viewpoint_name = {
            "C": "Application Cooperation",
            "D": "Technology Usage",
            "E": "Capability Map",
        }.get(phase_upper, f"Phase {phase_upper} Viewpoint")
        grouped_elements = {}
        serialized_elements = []
        for element in elements:
            layer_key = (element.layer_type or "unknown").lower()
            item = {
                "mapping_id": element.id,
                "element_id": element.element_id,
                "element_name": element.element_name,
                "layer": layer_key,
                "relationship_type": element.relationship_type,
                "notes": element.notes,
            }
            serialized_elements.append(item)
            grouped_elements.setdefault(layer_key, []).append(item)

        empty_reason = None
        if not serialized_elements:
            layer_labels = ", ".join(layer.title() for layer in relevant_layers)
            empty_reason = (
                f"No linked ArchiMate elements are currently mapped to the {phase_upper} viewpoint "
                f"layers ({layer_labels})."
            )

        return jsonify({
            "success": True,
            "viewpoint_name": viewpoint_name,
            "phase": phase_upper,
            "layers": relevant_layers,
            "grouped_elements": grouped_elements,
            "elements": serialized_elements,
            "count": len(serialized_elements),
            "empty_reason": empty_reason,
        })
    except Exception as e:
        logger.error(f"Error generating viewpoint for solution {solution_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# BIZBOK × ArchiMate: Viewpoint-filtered elements for ComposerRenderer diagrams
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/viewpoint-elements", methods=["GET"])
@login_required
def get_solution_viewpoint_elements(solution_id: int):
    """Return ArchiMate elements + relationships for a solution filtered by viewpoint.

    Query params:
        viewpoint: one of the ArchiMate 3.2 standard viewpoint IDs
                   (motivation, strategy, solution_architecture, implementation_migration,
                    layered, stakeholder, goal_realization, capability_map, etc.)

    Returns JSON compatible with ComposerRenderer.loadElements():
        {elements: [{id, name, type, layer}], relationships: [{id, source_id, target_id, type}]}
    """
    Solution.query.get_or_404(solution_id)
    viewpoint_id = request.args.get("viewpoint", "layered")

    try:
        from app.models.models import ArchiMateElement, ArchiMateRelationship
        from app.models.solution_models import SolutionArchiMateElement
        from app.modules.architecture.services.archimate_viewpoint_service import (
            ArchiMateViewpointService,
        )

        vp_service = ArchiMateViewpointService()
        vp_def = vp_service.get_viewpoint_definition(viewpoint_id)
        if not vp_def:
            return jsonify({"success": False, "error": f"Unknown viewpoint: {viewpoint_id}"}), 400

        allowed_types = vp_def.get("element_types")  # None means all types (layered)
        allowed_rel_types = vp_def.get("relationship_types")  # None means all

        # Get ArchiMate element IDs linked to this solution from both junction tables
        junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        archimate_ids = set()
        for j in junctions:
            if j.element_table == "archimate_elements" and j.element_id:
                archimate_ids.add(j.element_id)

        # Also check SolutionElement (used by backfill pipeline)
        from app.models.solution_element import SolutionElement
        se_rows = SolutionElement.query.filter_by(solution_id=solution_id).all()
        for se in se_rows:
            if se.archimate_element_id:
                archimate_ids.add(se.archimate_element_id)

        if not archimate_ids:
            return jsonify({
                "success": True,
                "viewpoint": viewpoint_id,
                "viewpoint_name": vp_def["name"],
                "elements": [],
                "relationships": [],
                "count": 0,
                "empty_reason": "No ArchiMate elements are linked to this solution yet.",
            })

        # Load actual ArchiMate elements and filter by viewpoint types
        query = ArchiMateElement.query.filter(ArchiMateElement.id.in_(archimate_ids))
        if allowed_types:
            query = query.filter(ArchiMateElement.type.in_(allowed_types))
        elements = query.all()

        element_ids = {e.id for e in elements}

        # Load relationships between the filtered elements
        rel_query = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id.in_(element_ids),
            ArchiMateRelationship.target_id.in_(element_ids),
        )
        if allowed_rel_types:
            rel_query = rel_query.filter(ArchiMateRelationship.type.in_(allowed_rel_types))
        relationships = rel_query.all()

        # Serialize for ComposerRenderer
        elements_json = [
            {
                "id": e.id,
                "name": e.name,
                "type": e.type or "ApplicationComponent",
                "layer": (e.layer or "application").lower(),
                "description": e.description or "",
            }
            for e in elements
        ]

        relationships_json = [
            {
                "id": r.id,
                "source_id": r.source_id,
                "target_id": r.target_id,
                "type": r.type or "Association",
            }
            for r in relationships
        ]

        return jsonify({
            "success": True,
            "viewpoint": viewpoint_id,
            "viewpoint_name": vp_def["name"],
            "elements": elements_json,
            "relationships": relationships_json,
            "count": len(elements_json),
            "relationship_count": len(relationships_json),
            "empty_reason": None if elements_json else "No elements match this viewpoint filter.",
        })

    except Exception as e:
        logger.error(f"Error loading viewpoint elements for solution {solution_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# ARCH-005: Relationship generation from linked ArchiMate elements
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/generate-relationships", methods=["POST"])
@login_required
def generate_solution_relationships(solution_id: int):
    """
    Generate ArchiMate relationships between the solution's linked elements.
    Uses ArchiMateRelationshipGenerator (3-pass algorithm).
    """
    solution = Solution.query.get_or_404(solution_id)
    try:
        from app.models.solution_models import SolutionArchiMateElement
        from app.modules.architecture.services.archimate_relationship_generator import (
            ArchiMateRelationshipGenerator,
        )

        elements = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        if not elements:
            return jsonify({"success": False, "error": "No ArchiMate elements linked to this solution yet"}), 400

        element_dicts = [
            {
                "name": e.element_name or f"Element {e.element_id}",
                "type": e.relationship_type or "unknown",
                "layer": e.layer_type or "unknown",
                "description": e.notes or "",
                "id": e.element_id,
            }
            for e in elements
        ]

        generator = ArchiMateRelationshipGenerator()
        relationships = generator.generate_relationships(element_dicts, solution.name or str(solution_id))

        # Persist as reasoning state for review
        try:
            from app.models.solution_reasoning import SolutionAIReasoningState
            state = SolutionAIReasoningState(
                solution_id=solution_id,
                suggestions={"relationships": relationships},
                reasoning_context={"source": "generate_relationships", "element_count": len(elements)},
                adm_phase="G",
                confidence_score=0.7,
            )
            db.session.add(state)
            db.session.commit()
            state_id = state.id
        except Exception as persist_err:
            logger.warning(f"Could not persist relationships reasoning state: {persist_err}")
            state_id = None

        return jsonify({
            "success": True,
            "relationships": relationships,
            "count": len(relationships),
            "reasoning_state_id": state_id,
            "element_count": len(elements),
        })
    except Exception as e:
        logger.error(f"Error generating relationships for solution {solution_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

# Register route extensions — these modules decorate solution_design_bp with additional routes
from . import solution_phase_routes, solution_link_routes, solution_ai_routes, solution_import_routes, solution_wizard_routes, programme_routes, solution_csv_junction_import_routes, solution_vendor_eval_routes  # noqa: E402, F401 # dead-code-ok


# =============================================================================
# SA-004 — Solution Architecture Document (SAD) narrative generation
# =============================================================================

@solution_design_bp.route("/<int:solution_id>/sad", methods=["GET"],
                           endpoint="solution_sad_document")
@login_required
def solution_sad_document(solution_id: int):
    """Render the SAD document page for a solution."""
    from app.models.solution_models import Solution
    from app.services.solution_narrative_service import generate_sad

    solution = db.session.get(Solution, solution_id)
    if not solution:
        abort(404)

    sad = generate_sad(solution_id)
    return render_template(
        "solutions/sad_document.html",
        solution=solution,
        sad=sad,
        standalone=False,
    )


@solution_design_bp.route("/<int:solution_id>/sad/download", methods=["GET"],
                           endpoint="solution_sad_download")
@login_required
def solution_sad_download(solution_id: int):
    """Return SAD as a downloadable HTML file."""
    from app.models.solution_models import Solution
    from app.services.solution_narrative_service import get_sad_html
    from flask import Response

    solution = db.session.get(Solution, solution_id)
    if not solution:
        abort(404)

    html_content = get_sad_html(solution_id)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in solution.name)[:60]
    filename = f"SAD-{safe_name}.html"
    return Response(
        html_content,
        mimetype="text/html",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# =============================================================================
# ENH-022: Portfolio View — solutions grouped by domain with risk and roadmap
# =============================================================================


@solution_design_bp.route("/portfolio", methods=["GET"])
@login_required
def portfolio_view():
    """ENH-022: Portfolio view — solutions grouped by business domain or roadmap initiative."""
    domain_filter = request.args.get("domain", "").strip()
    status_filter = request.args.get("status", "").strip()
    group_by = request.args.get("group_by", "domain").strip()

    if hasattr(current_user, "is_admin") and current_user.is_admin():
        query = Solution.query
    else:
        query = Solution.query.filter_by(created_by_id=current_user.id)

    if domain_filter:
        query = query.filter(Solution.business_domain == domain_filter)
    if status_filter:
        query = query.filter(Solution.status == status_filter)

    solutions = query.order_by(Solution.business_domain, Solution.name).all()

    from collections import defaultdict

    # ENH-022: Support grouping by roadmap initiative as well as domain
    by_domain = defaultdict(list)
    if group_by == "roadmap":
        try:
            from app.models.implementation_migration import TechnologyRoadmapInitiative
            sol_ids = [s.id for s in solutions]
            sol_initiative_map = {}
            if sol_ids:
                initiatives = TechnologyRoadmapInitiative.query.filter(
                    TechnologyRoadmapInitiative.solution_id.in_(sol_ids)
                ).all()
                for ini in initiatives:
                    sol_initiative_map.setdefault(ini.solution_id, []).append(ini.name)
            for sol in solutions:
                initiative_names = sol_initiative_map.get(sol.id, [])
                if initiative_names:
                    for name in initiative_names:
                        by_domain[name].append(sol)
                else:
                    by_domain["No Initiative"].append(sol)
        except Exception:
            for sol in solutions:
                domain = sol.business_domain or "Uncategorised"
                by_domain[domain].append(sol)
    else:
        for sol in solutions:
            domain = sol.business_domain or "Uncategorised"
            by_domain[domain].append(sol)

    domains = sorted(by_domain.keys())

    # Distinct domain/status for filter dropdowns
    all_domains = [
        d[0] for d in db.session.query(Solution.business_domain).distinct().all() if d[0]
    ]
    all_statuses = [
        s[0] for s in db.session.query(Solution.status).distinct().all() if s[0]
    ]

    return render_template(
        "solutions/portfolio.html",
        by_domain=dict(by_domain),
        domains=domains,
        all_domains=sorted(all_domains),
        all_statuses=sorted(all_statuses),
        selected_domain=domain_filter,
        selected_status=status_filter,
        group_by=group_by,
        total_count=len(solutions),
    )


@solution_design_bp.route("/api/portfolio", methods=["GET"])
@login_required
def portfolio_api():
    """ENH-022: Portfolio API — solutions grouped by domain or roadmap, with status counts."""
    group_by = request.args.get("group_by", "domain").strip()

    if hasattr(current_user, "is_admin") and current_user.is_admin():
        solutions = Solution.query.order_by(Solution.name).all()
    else:
        solutions = Solution.query.filter_by(created_by_id=current_user.id).order_by(Solution.name).all()

    from collections import defaultdict
    groups = defaultdict(list)

    if group_by == "roadmap":
        try:
            from app.models.implementation_migration import TechnologyRoadmapInitiative
            sol_ids = [s.id for s in solutions]
            sol_initiative_map = {}
            if sol_ids:
                initiatives = TechnologyRoadmapInitiative.query.filter(
                    TechnologyRoadmapInitiative.solution_id.in_(sol_ids)
                ).all()
                for ini in initiatives:
                    sol_initiative_map.setdefault(ini.solution_id, []).append(ini.name)
            for sol in solutions:
                names = sol_initiative_map.get(sol.id, ["No Initiative"])
                for name in names:
                    groups[name].append(sol)
        except Exception:
            for sol in solutions:
                groups[sol.business_domain or "Uncategorised"].append(sol)
    else:
        for sol in solutions:
            groups[sol.business_domain or "Uncategorised"].append(sol)

    result = []
    for group_name in sorted(groups.keys()):
        sols = groups[group_name]
        status_counts = defaultdict(int)
        for s in sols:
            status_counts[s.status or "planned"] += 1
        result.append({
            "group": group_name,
            "count": len(sols),
            "status_counts": dict(status_counts),
            "solutions": [
                {
                    "id": s.id,
                    "name": s.name,
                    "status": s.status or "planned",
                    "business_domain": s.business_domain,
                    "complexity_level": getattr(s, "complexity_level", None),
                    "governance_status": getattr(s, "governance_status", None),
                }
                for s in sols
            ],
        })

    return jsonify({"success": True, "group_by": group_by, "groups": result, "total": len(solutions)})


# =============================================================================
# ENH-023: Program/Dependency View — solutions as nodes with dependency arrows
# =============================================================================


def _ensure_solution_dependencies_table():
    """ENH-023: Idempotent DDL for solution_dependencies junction table."""
    try:
        db.session.execute(db.text("""
            CREATE TABLE IF NOT EXISTS solution_dependencies (
                solution_id INTEGER NOT NULL
                    REFERENCES solutions(id) ON DELETE CASCADE,
                depends_on_solution_id INTEGER NOT NULL
                    REFERENCES solutions(id) ON DELETE CASCADE,
                dependency_type VARCHAR(50) DEFAULT 'technical',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (solution_id, depends_on_solution_id)
            )
        """))
        db.session.commit()
    except Exception:
        db.session.rollback()


@solution_design_bp.route("/program", methods=["GET"])
@login_required
def program_view():
    """ENH-023: Program view — all solutions as nodes with dependency relationships."""
    _ensure_solution_dependencies_table()

    if hasattr(current_user, "is_admin") and current_user.is_admin():
        solutions = Solution.query.order_by(Solution.name).all()
    else:
        solutions = Solution.query.filter_by(created_by_id=current_user.id).order_by(Solution.name).all()

    # Load dependency links
    solution_ids = [s.id for s in solutions]
    dependencies = []
    if solution_ids:
        try:
            rows = db.session.execute(db.text(  # tenant-exempt: scoped via solution FK
                "SELECT solution_id, depends_on_solution_id, dependency_type, notes "
                "FROM solution_dependencies "
                "WHERE solution_id = ANY(:ids) OR depends_on_solution_id = ANY(:ids)"
            ), {"ids": solution_ids}).fetchall()
            dependencies = [
                {"source": r[0], "target": r[1], "type": r[2], "notes": r[3]}
                for r in rows
            ]
        except Exception:
            # ANY(:ids) is PostgreSQL-specific; try in-list fallback
            try:
                in_list = ",".join(str(i) for i in solution_ids)
                rows = db.session.execute(db.text(  # tenant-exempt: scoped via solution FK
                    f"SELECT solution_id, depends_on_solution_id, dependency_type, notes "
                    f"FROM solution_dependencies "
                    f"WHERE solution_id IN ({in_list}) OR depends_on_solution_id IN ({in_list})"
                )).fetchall()
                dependencies = [
                    {"source": r[0], "target": r[1], "type": r[2], "notes": r[3]}
                    for r in rows
                ]
            except Exception:
                dependencies = []

    solutions_json = [
        {"id": s.id, "name": s.name, "status": s.status or "planned",
         "domain": s.business_domain or "", "solution_type": s.solution_type or ""}
        for s in solutions
    ]

    return render_template(
        "solutions/program_view.html",
        solutions=solutions,
        solutions_json=solutions_json,
        dependencies=dependencies,
    )


@solution_design_bp.route("/program/dependencies", methods=["POST"])
@login_required
def add_solution_dependency():
    """ENH-023: Add a dependency between two solutions."""
    _ensure_solution_dependencies_table()
    data = request.get_json(silent=True) or {}
    solution_id = data.get("solution_id")
    depends_on = data.get("depends_on_solution_id")
    if not solution_id or not depends_on:
        return jsonify({"success": False, "error": "solution_id and depends_on_solution_id required"}), 400
    try:
        db.session.execute(db.text(  # tenant-exempt: scoped via solution FK
            "INSERT INTO solution_dependencies (solution_id, depends_on_solution_id, dependency_type, notes) "
            "VALUES (:s, :d, :t, :n) ON CONFLICT DO NOTHING"
        ), {
            "s": int(solution_id), "d": int(depends_on),
            "t": data.get("dependency_type", "technical"),
            "n": data.get("notes", ""),
        })
        db.session.commit()
        return jsonify({"success": True})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 500


# =============================================================================
# PRD-009: Linked Vendor Products API (returns only products linked to solution)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/linked-vendor-products", methods=["GET"])
@login_required
def linked_vendor_products(solution_id):
    """Return vendor products currently linked to this solution."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.vendor.vendor_organization import VendorProduct
        tbl = db.metadata.tables.get("solution_vendor_products")
        if tbl is None:
            return jsonify({"products": []})
        rows = db.session.execute(  # tenant-exempt: scoped via solution FK
            tbl.select().where(tbl.c.solution_id == solution_id)
        ).fetchall()
        vp_ids = [r.vendor_product_id for r in rows]
        if not vp_ids:
            return jsonify({"products": []})
        products = VendorProduct.query.filter(VendorProduct.id.in_(vp_ids)).all()
        return jsonify({
            "products": [
                _serialize_solution_vendor_product(v) for v in products
            ]
        })
    except Exception as e:
        logger.exception(f"Error fetching linked vendor products: {e}")
        return jsonify(
            {"success": False, "error": "Could not load linked vendor products"}
        ), 500


# =============================================================================
# PRD-010: Linked APQC Processes API (returns only processes linked to solution)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/linked-apqc-processes", methods=["GET"])
@login_required
def linked_apqc_processes(solution_id):
    """Return APQC processes currently linked to this solution."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.apqc_process import APQCProcess as APQCModel
        from app.models.solution_sad_models import SolutionAPQCProcess
        links = SolutionAPQCProcess.query.filter_by(solution_id=solution_id).all()
        if not links:
            return jsonify({"processes": []})
        proc_ids = [lnk.apqc_process_id for lnk in links]
        procs = APQCModel.query.filter(APQCModel.id.in_(proc_ids)).all()
        return jsonify({
            "processes": [
                {
                    "id": p.id,
                    "process_id": p.id,
                    "process_name": p.process_name,
                    "process_code": getattr(p, "process_code", "") or "",
                    "name": p.process_name,
                }
                for p in procs
            ]
        })
    except Exception as e:
        logger.exception(f"Error fetching linked APQC processes: {e}")
        return jsonify(
            {"success": False, "error": "Could not load linked APQC processes"}
        ), 500


# =============================================================================
# PRD-012: Linked Applications API (returns only apps linked to solution)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/linked-applications", methods=["GET"])
@login_required
def linked_applications_api(solution_id):
    """Return applications currently linked to this solution."""
    Solution.query.get_or_404(solution_id)
    try:
        apps = _get_solution_applications(solution_id)
        return jsonify({
            "applications": [
                _serialize_solution_application(a) for a in apps
            ]
        })
    except Exception as e:
        logger.exception(f"Error fetching linked applications: {e}")
        return jsonify(
            {"success": False, "error": "Could not load linked applications"}
        ), 500


# =============================================================================
# A95-004: Start with AI — create solution and immediately generate a draft
# =============================================================================


@solution_design_bp.route("/create-with-draft", methods=["POST"])
@login_required
def create_with_draft():
    """Create a new solution and immediately generate a draft architecture from a brief."""
    data = request.get_json(silent=True) or {}
    brief = (data.get("brief") or "").strip()
    title = (data.get("title") or "AI-Generated Solution").strip() or "AI-Generated Solution"

    # User.full_name is a method, not a property — call it when callable, otherwise
    # getattr returns the bound method and the insert fails with "can't adapt type 'method'".
    owner = getattr(current_user, "full_name", None)
    if callable(owner):
        owner = owner()
    owner = owner or getattr(current_user, "email", "")

    solution = Solution(
        name=title[:255],
        description=brief[:2000] or None,
        status="planned",
        deployment_status="design",
        governance_status="draft",
        adm_phase="A",
        solution_owner=owner,
        created_by_id=current_user.id,
    )
    db.session.add(solution)
    db.session.flush()

    try:
        from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import SolutionAIOrchestrator
        orchestrator = SolutionAIOrchestrator()
        # user_id is required by the orchestrator; omitting it raised TypeError.
        orchestrator.generate_draft_architecture(
            solution.id, brief=brief, user_id=current_user.id
        )
    except Exception as e:
        current_app.logger.warning(f"A95-004 generate_draft failed: {e}")

    db.session.commit()

    if request.is_json:
        return jsonify({
            "status": "created",
            "solution_id": solution.id,
            "redirect": url_for("solution_design.view_solution", solution_id=solution.id),
        })
    return redirect(url_for("solution_design.view_solution", solution_id=solution.id))


@solution_design_bp.route("/<int:solution_id>/comments", methods=["GET"])
@login_required
def get_comments(solution_id):
    """PLT-011: List comments for a solution, optionally filtered by section."""
    from app.models.solution_models import SolutionComment

    section = request.args.get("section")
    query = SolutionComment.query.filter_by(solution_id=solution_id)
    if section:
        if section not in SolutionComment.VALID_SECTIONS:
            return jsonify({"error": f"Invalid section. Must be one of: {', '.join(SolutionComment.VALID_SECTIONS)}"}), 400
        query = query.filter_by(section_name=section)
    comments = query.order_by(SolutionComment.created_at.asc()).all()

    # Build threaded structure: top-level + replies nested
    top_level = []
    replies_map = {}
    for c in comments:
        d = c.to_dict()
        d["replies"] = []
        if c.parent_comment_id:
            replies_map.setdefault(c.parent_comment_id, []).append(d)
        else:
            top_level.append(d)
    for t in top_level:
        t["replies"] = replies_map.get(t["id"], [])

    return jsonify({"comments": top_level, "total": len(comments)})


@solution_design_bp.route("/<int:solution_id>/comments", methods=["POST"])
@login_required
def create_comment(solution_id):
    """PLT-011: Add a comment to a solution section."""
    from flask_login import current_user
    from app.models.solution_models import Solution, SolutionComment

    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    section_name = data.get("section_name", "").strip()
    content = data.get("content", "").strip()
    parent_comment_id = data.get("parent_comment_id")

    if not section_name or section_name not in SolutionComment.VALID_SECTIONS:
        return jsonify({"error": f"Invalid section_name. Must be one of: {', '.join(SolutionComment.VALID_SECTIONS)}"}), 400
    if not content:
        return jsonify({"error": "Content is required"}), 400
    if parent_comment_id:
        parent = SolutionComment.query.get(parent_comment_id)
        if not parent or parent.solution_id != solution_id:
            return jsonify({"error": "Invalid parent comment"}), 400

    author_name = current_user.display_name if hasattr(current_user, "display_name") else current_user.username
    comment = SolutionComment(
        solution_id=solution.id,
        section_name=section_name,
        author_id=current_user.id,
        author_name=author_name,
        content=content,
        parent_comment_id=parent_comment_id,
    )
    db.session.add(comment)
    db.session.commit()

    # PLT-012: Parse @mentions and create notifications for mentioned users
    import re
    from sqlalchemy import or_
    from app.models.solution_governance import SolutionNotification
    mention_pattern = re.compile(r"@([\w][\w ]*?)(?=\s@|\s*$|[^\w ])")
    mentioned_names = mention_pattern.findall(content)
    if mentioned_names:
        from app.models.user import User
        mentioned_notified = set()
        for name in mentioned_names:
            name_stripped = name.strip()
            if not name_stripped:
                continue
            parts = name_stripped.split()
            matched_user = None
            if len(parts) >= 2:
                matched_user = User.query.filter(
                    User.organization_id == g.current_org_id,
                    User.first_name.ilike(parts[0]),
                    User.last_name.ilike(parts[-1]),
                ).first()
            if not matched_user:
                term = f"%{name_stripped}%"
                matched_user = User.query.filter(
                    User.organization_id == g.current_org_id,
                    or_(
                        User.email.ilike(term),
                        User.first_name.ilike(term),
                        User.last_name.ilike(term),
                    )
                ).first()
            if matched_user and matched_user.id != current_user.id and matched_user.id not in mentioned_notified:
                mentioned_notified.add(matched_user.id)
                try:
                    n = SolutionNotification(
                        solution_id=solution.id,
                        user_id=matched_user.id,
                        type="comment_mention",
                        message=f"{author_name} mentioned you in a comment on '{solution.name}' ({section_name}).",
                    )
                    db.session.add(n)
                except Exception as e:
                    logger.debug("Could not create mention notification: %s", e)
        if mentioned_notified:
            db.session.commit()

    return jsonify({"success": True, "comment": comment.to_dict()}), 201


@solution_design_bp.route("/<int:solution_id>/versions", methods=["GET"])
@login_required
def get_versions(solution_id):
    """PLT-023: List all versions for a solution."""
    from app.models.solution_governance import SolutionVersion

    solution = Solution.query.get_or_404(solution_id)
    versions = (
        SolutionVersion.query.filter_by(solution_id=solution.id)
        .order_by(SolutionVersion.version_number.desc())
        .all()
    )
    return jsonify({
        "solution_id": solution.id,
        "versions": [v.to_dict() for v in versions],
        "total": len(versions),
    })


@solution_design_bp.route("/<int:solution_id>/versions/<int:v1>/diff/<int:v2>", methods=["GET"])
@login_required
def diff_versions(solution_id, v1, v2):
    """PLT-023: Compute diff between two solution version snapshots."""
    import logging
    from app.models.solution_governance import SolutionVersion

    logger = logging.getLogger(__name__)
    solution = Solution.query.get_or_404(solution_id)

    ver1 = SolutionVersion.query.filter_by(solution_id=solution.id, version_number=v1).first()
    ver2 = SolutionVersion.query.filter_by(solution_id=solution.id, version_number=v2).first()

    if not ver1 or not ver2:
        return jsonify({"error": "Version not found"}), 404

    snap1 = ver1.solution_snapshot or {}
    snap2 = ver2.solution_snapshot or {}

    diff = _compute_json_diff(snap1, snap2)
    logger.debug("PLT-023 diff v%d vs v%d for solution %d: %d changes", v1, v2, solution.id, len(diff))

    return jsonify({
        "solution_id": solution.id,
        "version_a": v1,
        "version_b": v2,
        "changes": diff,
    })


@solution_design_bp.route(
    "/<int:solution_id>/versions/<int:version_id>/restore",
    methods=["POST"],
)
@login_required
@require_roles("admin", "enterprise_architect", "architect")
def restore_solution_version(solution_id: int, version_id: int):
    """PLT-027: Restore a solution to a historical version snapshot.

    1. Load the target version and the current solution.
    2. Snapshot current state as a new SolutionVersion (safety backup).
    3. Apply scalar fields from the target snapshot back onto the Solution row.
    4. Commit.
    5. Notify the solution owner.
    """
    from app.models.solution_governance import SolutionVersion

    solution = Solution.query.get_or_404(solution_id)
    target_version = SolutionVersion.query.filter_by(
        id=version_id, solution_id=solution_id
    ).first()
    if not target_version:
        return jsonify({"error": "Version not found"}), 404

    target_snapshot = getattr(target_version, "solution_snapshot", None) or {}  # model-safety-ok

    # Snapshot the current state before overwriting (safety backup)
    latest = (
        SolutionVersion.query.filter_by(solution_id=solution_id)
        .order_by(SolutionVersion.version_number.desc())
        .first()
    )
    next_version_number = (latest.version_number + 1) if latest else 1

    current_snapshot = _build_solution_snapshot(solution)
    backup_version = SolutionVersion(
        solution_id=solution_id,
        version_number=next_version_number,
        created_by_id=current_user.id,
        change_summary=(
            f"Auto-backup before restoring to v{target_version.version_number} "
            f"(requested by {getattr(current_user, 'full_name', None) or current_user.email})"  # model-safety-ok
        ),
        change_delta={},
        solution_snapshot=current_snapshot,
        approval_status="pending",
    )
    db.session.add(backup_version)

    # Apply scalar fields from the target snapshot onto the solution row
    _RESTORABLE_FIELDS = [
        "name",
        "description",
        "solution_type",
        "business_domain",
        "complexity_level",
        "business_value",
        "target_outcomes",
        "success_metrics",
        "scope_description",
        "in_scope_applications",
        "out_of_scope_applications",
        "status",
        "deployment_status",
        "solution_owner",
        "business_sponsor",
        "technical_lead",
        "governance_status",
        "adm_phase",
        "scope_in",
        "scope_out",
        "affected_systems",
        "security_lead",
        "data_protection_officer",
    ]
    for field in _RESTORABLE_FIELDS:
        if field in target_snapshot:
            try:
                setattr(solution, field, target_snapshot[field])
            except Exception as exc:
                logger.warning(
                    "PLT-027: Could not restore field %s for solution %d: %s",
                    field, solution_id, exc,
                )

    solution.updated_at = datetime.utcnow()

    # Notify the solution owner / creator
    owner_id = getattr(solution, "created_by_id", None)  # model-safety-ok
    if owner_id:
        _create_notification(
            user_id=owner_id,
            notification_type="version_restored",
            message=(
                f"Solution '{solution.name}' was restored to "
                f"v{target_version.version_number} by "
                f"{getattr(current_user, 'full_name', None) or current_user.email}."  # model-safety-ok
            ),
            solution_id=solution_id,
        )

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error("PLT-027: Restore commit failed for solution %d: %s", solution_id, exc)
        return jsonify({"error": "Restore failed — database error."}), 500

    logger.info(
        "PLT-027: Solution %d restored to v%d by user %d (backup saved as v%d)",
        solution_id,
        target_version.version_number,
        current_user.id,
        next_version_number,
    )
    return jsonify({
        "success": True,
        "restored_to_version": target_version.version_number,
        "backup_version": next_version_number,
        "message": (
            f"Solution restored to v{target_version.version_number}. "
            f"Current state saved as v{next_version_number}."
        ),
    })


def _build_solution_snapshot(solution: Solution) -> dict:
    """PLT-027: Build a scalar-field snapshot dict from a Solution instance."""
    snapshot_fields = [
        "name", "description", "solution_type", "business_domain",
        "complexity_level", "business_value", "target_outcomes", "success_metrics",
        "scope_description", "in_scope_applications", "out_of_scope_applications",
        "status", "deployment_status", "solution_owner", "business_sponsor",
        "technical_lead", "governance_status", "adm_phase", "scope_in",
        "scope_out", "affected_systems", "security_lead", "data_protection_officer",
    ]
    snap = {}
    for field in snapshot_fields:
        val = getattr(solution, field, None)  # model-safety-ok
        if val is not None:
            snap[field] = val
    return snap


def _compute_json_diff(old, new):
    """PLT-023: Compute structured diff between two JSON snapshots.

    Returns list of {field, type (added/removed/changed), old_value, new_value}.
    """
    changes = []
    all_keys = set(list(old.keys()) + list(new.keys()))

    for key in sorted(all_keys):
        old_val = old.get(key)
        new_val = new.get(key)

        if key not in old:
            changes.append({"field": key, "type": "added", "old_value": None, "new_value": new_val})
        elif key not in new:
            changes.append({"field": key, "type": "removed", "old_value": old_val, "new_value": None})
        elif old_val != new_val:
            changes.append({"field": key, "type": "changed", "old_value": old_val, "new_value": new_val})

    return changes


# ---------------------------------------------------------------------------
# PLT-008: Suggest Connections — keyword-based entity recommendations
# ---------------------------------------------------------------------------


def _tokenize_text(text):
    """Split text into lowercase keyword tokens for fuzzy matching.

    Reuses the same logic as capabilities/mapping_routes._tokenize:
    split on non-alphanumeric, lowercase, drop tokens with <= 2 chars.
    """
    import re

    if not text:
        return set()
    return {w for w in re.split(r"[^a-zA-Z0-9]+", text.lower()) if len(w) > 2}


def _jaccard_score(tokens_a, tokens_b):
    """Compute Jaccard similarity between two token sets."""
    if not tokens_a or not tokens_b:
        return 0.0
    overlap = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return overlap / union if union else 0.0


@solution_design_bp.route("/<int:solution_id>/suggest-connections")
@login_required
def suggest_connections(solution_id: int):
    """PLT-008: Return keyword-matched entity suggestions for a solution.

    Tokenizes the solution's name, description, and business_domain into
    keywords, then computes Jaccard similarity against all entities of four
    types (applications, capabilities, vendors, ArchiMate elements).
    Excludes entities already linked to this solution.
    Returns top 5 per category with score > 0.1.
    """
    try:
        from app.models.application_portfolio import ApplicationComponent
        from app.models.archimate_core import ArchiMateElement
        from app.models.business_capabilities import BusinessCapability
        from app.models.solution_models import (
            SolutionArchiMateElement,
            SolutionCapabilityMapping,
        )
        from app.models.vendor.vendor_organization import VendorProduct

        solution = Solution.query.get(solution_id)
        if not solution:
            return jsonify({"error": "Solution not found"}), 404

        # Build solution token set from name, description, business_domain
        sol_tokens = (
            _tokenize_text(solution.name)
            | _tokenize_text(solution.description)
            | _tokenize_text(getattr(solution, "business_domain", None))
        )

        if not sol_tokens:
            return jsonify({
                "suggestions": {
                    "applications": [],
                    "capabilities": [],
                    "vendors": [],
                    "archimate": [],
                },
            })

        min_score = 0.1
        top_n = 5

        # --- Applications ---
        existing_app_ids = set()
        sa_table = db.metadata.tables.get("solution_applications")
        if sa_table is not None:
            rows = (
                db.session.query(sa_table.c.application_component_id)
                .filter(sa_table.c.solution_id == solution_id)
                .all()
            )
            existing_app_ids = {r[0] for r in rows}

        app_suggestions = []
        _app_rows = (
            db.session.query(
                ApplicationComponent.id,
                ApplicationComponent.name,
                ApplicationComponent.description,
                ApplicationComponent.business_domain,
            )
            .limit(2000)
            .all()
        )
        for app_id, app_name, app_desc, app_domain in _app_rows:
            if app_id in existing_app_ids:
                continue
            app_tokens = (
                _tokenize_text(app_name)
                | _tokenize_text(app_desc)
                | _tokenize_text(app_domain)
            )
            score = _jaccard_score(sol_tokens, app_tokens)
            if score > min_score:
                app_suggestions.append({
                    "id": app_id,
                    "name": app_name,
                    "score": round(score, 4),
                    "entity_type": "application",
                })
        app_suggestions.sort(key=lambda s: s["score"], reverse=True)
        app_suggestions = app_suggestions[:top_n]

        # --- Capabilities ---
        existing_cap_ids = set()
        cap_rows = (
            db.session.query(SolutionCapabilityMapping.capability_id)
            .filter(
                (SolutionCapabilityMapping.solution_id == solution_id)
            )
            .all()
        )
        existing_cap_ids = {r[0] for r in cap_rows}

        cap_suggestions = []
        _cap_rows = (
            db.session.query(
                BusinessCapability.id,
                BusinessCapability.name,
                BusinessCapability.description,
                BusinessCapability.business_domain,
                BusinessCapability.category,
            )
            .limit(2000)
            .all()
        )
        for cap_id, cap_name, cap_desc, cap_domain, cap_cat in _cap_rows:
            if cap_id in existing_cap_ids:
                continue
            cap_tokens = (
                _tokenize_text(cap_name)
                | _tokenize_text(cap_desc)
                | _tokenize_text(cap_domain)
                | _tokenize_text(cap_cat)
            )
            score = _jaccard_score(sol_tokens, cap_tokens)
            if score > min_score:
                cap_suggestions.append({
                    "id": cap_id,
                    "name": cap_name,
                    "score": round(score, 4),
                    "entity_type": "capability",
                })
        cap_suggestions.sort(key=lambda s: s["score"], reverse=True)
        cap_suggestions = cap_suggestions[:top_n]

        # --- Vendor Products ---
        existing_vp_ids = set()
        svp_table = db.metadata.tables.get("solution_vendor_products")
        if svp_table is not None:
            vp_rows = (
                db.session.query(svp_table.c.vendor_product_id)
                .filter(svp_table.c.solution_id == solution_id)
                .all()
            )
            existing_vp_ids = {r[0] for r in vp_rows}

        vendor_suggestions = []
        for vp in VendorProduct.query.limit(2000).all():
            if vp.id in existing_vp_ids:
                continue
            vp_tokens = (
                _tokenize_text(vp.name)
                | _tokenize_text(getattr(vp, "functional_scope", None))
                | _tokenize_text(getattr(vp, "product_family_name", None))
            )
            score = _jaccard_score(sol_tokens, vp_tokens)
            if score > min_score:
                vendor_name = ""
                if getattr(vp, "vendor_organization", None):
                    vendor_name = vp.vendor_organization.name
                vendor_suggestions.append({
                    "id": vp.id,
                    "name": vp.name,
                    "score": round(score, 4),
                    "entity_type": "vendor_product",
                    "vendor_name": vendor_name,
                })
        vendor_suggestions.sort(key=lambda s: s["score"], reverse=True)
        vendor_suggestions = vendor_suggestions[:top_n]

        # --- ArchiMate Elements ---
        existing_am_ids = set()
        am_rows = (
            db.session.query(SolutionArchiMateElement.element_id)
            .filter(SolutionArchiMateElement.solution_id == solution_id)
            .all()
        )
        existing_am_ids = {r[0] for r in am_rows}

        archimate_suggestions = []
        _am_rows = (
            db.session.query(
                ArchiMateElement.id,
                ArchiMateElement.name,
                ArchiMateElement.description,
                ArchiMateElement.type,
                ArchiMateElement.layer,
            )
            .limit(5000)
            .all()
        )
        for am_id, am_name, am_desc, am_type, am_layer in _am_rows:
            if am_id in existing_am_ids:
                continue
            elem_tokens = (
                _tokenize_text(am_name)
                | _tokenize_text(am_desc)
                | _tokenize_text(am_type)
            )
            score = _jaccard_score(sol_tokens, elem_tokens)
            if score > min_score:
                archimate_suggestions.append({
                    "id": am_id,
                    "name": am_name,
                    "score": round(score, 4),
                    "entity_type": "archimate",
                    "layer": am_layer or "",
                    "element_type": am_type or "",
                })
        archimate_suggestions.sort(key=lambda s: s["score"], reverse=True)
        archimate_suggestions = archimate_suggestions[:top_n]

        return jsonify({
            "suggestions": {
                "applications": app_suggestions,
                "capabilities": cap_suggestions,
                "vendors": vendor_suggestions,
                "archimate": archimate_suggestions,
            },
        })

    except Exception as e:
        logger.error("PLT-008 suggest-connections error for solution %s: %s", solution_id, e)
        return jsonify({"error": "Failed to generate suggestions"}), 500


@solution_design_bp.route("/<int:solution_id>/link-capability", methods=["POST"])
@login_required
def link_capability(solution_id):
    """PLT-008: Link a single capability to a solution via solution_id (no analysis session required)."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_models import SolutionCapabilityMapping

    Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    cap_id = data.get("capability_id")
    if not cap_id:
        return jsonify({"success": False, "error": "capability_id required"}), 400
    cap = BusinessCapability.query.get(cap_id)
    if not cap:
        return jsonify({"success": False, "error": "Capability not found"}), 404
    try:
        existing = SolutionCapabilityMapping.query.filter_by(
            solution_id=solution_id, capability_id=cap_id
        ).first()
        if existing:
            return jsonify({"success": False, "error": "Already linked"}), 409
        mapping = SolutionCapabilityMapping(
            solution_id=solution_id,
            capability_id=cap_id,
        )
        db.session.add(mapping)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error("Error linking capability to solution %s: %s", solution_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/generate-adr", methods=["GET"])
@login_required
def generate_adr(solution_id):
    """ARC-E03: Generate an Architecture Decision Record (ADR) markdown document
    from a solution's options analysis, drivers, goals, constraints, and risks."""
    from app.models.solution_architect_models import (
        SolutionConstraint,
        SolutionDriver,
        SolutionGoal,
        SolutionProblemDefinition,
        SolutionRecommendation,
    )
    from app.models.solution_lifecycle_models import SolutionRisk

    solution = Solution.query.get_or_404(solution_id)

    # Gather drivers, goals, constraints via analysis session's problem definition
    drivers = []
    goals = []
    constraints = []
    recommendations = []
    problem_description = ""

    if solution.analysis_session_id:
        problem_def = SolutionProblemDefinition.query.filter_by(
            session_id=solution.analysis_session_id
        ).first()
        if problem_def:
            problem_description = problem_def.problem_description or ""
            drivers = SolutionDriver.query.filter_by(problem_id=problem_def.id).all()
            goals = SolutionGoal.query.filter_by(problem_id=problem_def.id).all()
            constraints = SolutionConstraint.query.filter_by(problem_id=problem_def.id).all()

        recommendations = (
            SolutionRecommendation.query
            .filter_by(session_id=solution.analysis_session_id)
            .order_by(SolutionRecommendation.rank.asc())
            .all()
        )

    # Risks are linked directly to the solution
    risks = SolutionRisk.query.filter_by(solution_id=solution_id).all()

    # Capability mappings
    capabilities_payload = _get_solution_capabilities_payload(solution)

    # Determine the top-ranked recommendation (rank 1 = selected)
    selected = recommendations[0] if recommendations else None
    alternatives = recommendations[1:] if len(recommendations) > 1 else []

    # Build the ADR markdown
    lines = []
    lines.append(f"# {solution.name} — Architecture Decision Record")
    lines.append("")
    lines.append("## Status")
    lines.append("Accepted")
    lines.append("")
    lines.append("## Date")
    lines.append(datetime.utcnow().strftime("%Y-%m-%d"))
    lines.append("")

    # Context section
    lines.append("## Context")
    if problem_description:
        lines.append("")
        lines.append(problem_description)
    lines.append("")

    if drivers:
        lines.append("### Business Drivers")
        lines.append("")
        for d in drivers:
            desc = f" — {d.description}" if d.description else ""
            impact = f" (Impact: {d.impact_level}/5)" if d.impact_level else ""
            lines.append(f"- **{d.name}**{desc}{impact}")
        lines.append("")

    if goals:
        lines.append("### Goals")
        lines.append("")
        for g in goals:
            desc = f" — {g.description}" if g.description else ""
            priority = f" (Priority: {g.priority}/5)" if g.priority else ""
            lines.append(f"- **{g.name}**{desc}{priority}")
        lines.append("")

    if constraints:
        lines.append("### Constraints")
        lines.append("")
        for c in constraints:
            value_str = f" [{c.value}]" if c.value else ""
            lines.append(f"- **{c.name}**{value_str} — {c.description}")
        lines.append("")

    # Decision section
    lines.append("## Decision")
    lines.append("")
    if selected:
        option_label = selected.option_type.value if selected.option_type else "Unknown"
        lines.append(f"We have selected **{option_label.title()}** (Rank 1, Score: {selected.score}).")
        lines.append("")
        if selected.justification:
            lines.append(selected.justification)
            lines.append("")
        if selected.pros:
            lines.append("**Advantages:**")
            for pro in selected.pros:
                lines.append(f"- {pro}")
            lines.append("")
        if selected.cons:
            lines.append("**Disadvantages:**")
            for con in selected.cons:
                lines.append(f"- {con}")
            lines.append("")
        if selected.estimated_cost_min or selected.estimated_cost_max:
            currency = selected.cost_currency or "GBP"
            cost_min = f"{selected.estimated_cost_min:,.0f}" if selected.estimated_cost_min else "N/A"
            cost_max = f"{selected.estimated_cost_max:,.0f}" if selected.estimated_cost_max else "N/A"
            lines.append(f"**Estimated Cost:** {currency} {cost_min} - {cost_max}")
            lines.append("")
        if selected.timeline_months:
            lines.append(f"**Estimated Timeline:** {selected.timeline_months} months")
            lines.append("")
    else:
        lines.append("No recommendation has been recorded for this solution yet.")
        lines.append("")

    # Alternatives section
    if alternatives:
        lines.append("## Alternatives Considered")
        lines.append("")
        for alt in alternatives:
            alt_label = alt.option_type.value if alt.option_type else "Unknown"
            rank_str = f"Rank {alt.rank}" if alt.rank else ""
            score_str = f", Score: {alt.score}" if alt.score else ""
            lines.append(f"### {alt_label.title()} ({rank_str}{score_str})")
            lines.append("")
            if alt.justification:
                lines.append(alt.justification)
                lines.append("")
            if alt.pros:
                lines.append("**Advantages:**")
                for pro in alt.pros:
                    lines.append(f"- {pro}")
                lines.append("")
            if alt.cons:
                lines.append("**Disadvantages:**")
                for con in alt.cons:
                    lines.append(f"- {con}")
                lines.append("")

    # Consequences section
    lines.append("## Consequences")
    lines.append("")

    if selected and selected.pros:
        lines.append("### Positive")
        lines.append("")
        for pro in selected.pros:
            lines.append(f"- {pro}")
        lines.append("")

    if risks:
        lines.append("### Risks")
        lines.append("")
        for r in risks:
            severity = f"Impact: {r.impact}, Probability: {r.probability}"
            mitigation_str = f" Mitigation: {r.mitigation}" if r.mitigation else ""
            lines.append(f"- **{r.risk_description}** ({severity}).{mitigation_str}")
        lines.append("")
    elif selected and selected.risks:
        lines.append("### Risks")
        lines.append("")
        for risk_item in selected.risks:
            if isinstance(risk_item, dict):
                desc = risk_item.get("description", risk_item.get("risk", str(risk_item)))
                sev = risk_item.get("severity", "")
                sev_str = f" (Severity: {sev})" if sev else ""
                lines.append(f"- {desc}{sev_str}")
            else:
                lines.append(f"- {risk_item}")
        lines.append("")

    if capabilities_payload:
        lines.append("### Capability Impact")
        lines.append("")
        for cap in capabilities_payload:
            gap_info = ""
            if cap.get("gap_severity") and cap["gap_severity"] != "none":
                gap_info = f" — maturity gap: {cap['gap_severity']}"
            lines.append(f"- **{cap['capability_name']}**{gap_info}")
        lines.append("")

    # Next steps from selected recommendation
    if selected and selected.next_steps:
        lines.append("## Next Steps")
        lines.append("")
        for step in selected.next_steps:
            lines.append(f"- {step}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by Entelim on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append("")

    markdown_content = "\n".join(lines)

    # Return as downloadable markdown file
    safe_name = "".join(c if c.isalnum() or c in ("-", "_", " ") else "" for c in solution.name)
    safe_name = safe_name.strip().replace(" ", "-")[:80] or "adr"
    filename = f"ADR-{solution_id}-{safe_name}.md"

    response = make_response(markdown_content)
    response.headers["Content-Type"] = "text/markdown; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'  # raw-html-ok: filename built from safe_name, already alnum/dash/underscore-filtered above
    return response


# ---------------------------------------------------------------------------
# Architect Scratchpad — staging area for AI suggestions (ARC-E04)
# ---------------------------------------------------------------------------


@solution_design_bp.route("/<int:solution_id>/scratchpad", methods=["GET"])
@login_required
def get_scratchpad(solution_id):
    """Return all scratchpad items for a solution."""
    solution = db.session.get(Solution, solution_id)
    if not solution:
        abort(404)
    return jsonify({"items": solution.scratchpad_items or []})


@solution_design_bp.route("/<int:solution_id>/scratchpad", methods=["POST"])
@login_required
def add_scratchpad_item(solution_id):
    """Add an item to the architect scratchpad."""
    solution = db.session.get(Solution, solution_id)
    if not solution:
        abort(404)

    data = request.get_json()
    import uuid

    item = {
        "id": str(uuid.uuid4()),
        "type": data.get("type", "general"),
        "data": data.get("data", {}),
        "source": data.get("source", "manual"),
        "created_at": datetime.utcnow().isoformat(),
    }

    items = list(solution.scratchpad_items or [])
    items.append(item)
    solution.scratchpad_items = items
    db.session.commit()

    return jsonify({"success": True, "item": item}), 201


@solution_design_bp.route(
    "/<int:solution_id>/scratchpad/<item_id>", methods=["DELETE"]
)
@login_required
def discard_scratchpad_item(solution_id, item_id):
    """Remove a single item from the scratchpad."""
    solution = db.session.get(Solution, solution_id)
    if not solution:
        abort(404)

    items = [i for i in (solution.scratchpad_items or []) if i.get("id") != item_id]
    if len(items) == len(solution.scratchpad_items or []):
        return jsonify({"error": "Item not found"}), 404

    solution.scratchpad_items = items
    db.session.commit()

    return jsonify({"success": True})


@solution_design_bp.route(
    "/<int:solution_id>/scratchpad/<item_id>/promote", methods=["POST"]
)
@login_required
def promote_scratchpad_item(solution_id, item_id):
    """Promote a scratchpad item — removes it and returns data for entity creation."""
    solution = db.session.get(Solution, solution_id)
    if not solution:
        abort(404)

    items = solution.scratchpad_items or []
    item = next((i for i in items if i.get("id") == item_id), None)
    if not item:
        return jsonify({"error": "Item not found"}), 404

    solution.scratchpad_items = [i for i in items if i.get("id") != item_id]
    db.session.commit()

    return jsonify({
        "success": True,
        "promoted": item,
        "message": "Item promoted. Use the appropriate creation API to persist the entity.",
    })


# =============================================================================
# Capability-Driven Design Analysis (ARC-F04)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/capability-analysis", methods=["GET"])
@login_required
def get_capability_analysis(solution_id):
    """Return composed capability analysis for solution design (ARC-F04)."""
    from app.services.capability_design_composition_service import (
        CapabilityDesignCompositionService,
    )

    Solution.query.get_or_404(solution_id)
    service = CapabilityDesignCompositionService()
    result = service.analyze(solution_id)
    return jsonify(result)


@solution_design_bp.route(
    "/<int:solution_id>/capabilities/discover", methods=["POST"]
)
@login_required
def discover_capabilities(solution_id):
    """Discover capabilities from problem description via AI (ARC-F04/I01)."""
    from app.services.capability_design_composition_service import (
        CapabilityDesignCompositionService,
    )

    Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    problem_text = data.get("problem_description", "").strip()
    if not problem_text:
        return jsonify({"error": "problem_description is required"}), 400

    service = CapabilityDesignCompositionService()
    suggestions = service.discover_capabilities_from_problem(problem_text)
    return jsonify({"suggestions": suggestions, "count": len(suggestions)})


@solution_design_bp.route("/<int:solution_id>/related-solutions", methods=["GET"])
@login_required
def get_related_solutions(solution_id):
    """Find solutions with overlapping capabilities, apps, or business domain (SAD-019)."""
    solution = db.session.get(Solution, solution_id)
    if not solution:
        abort(404)

    from app.models.solution_models import SolutionCapabilityMapping

    # 1. Find solutions sharing capabilities
    my_cap_ids = [
        m.capability_id
        for m in SolutionCapabilityMapping.query.filter_by(solution_id=solution_id).all()
        if m.capability_id
    ]
    cap_overlap = {}
    if my_cap_ids:
        other_mappings = (
            SolutionCapabilityMapping.query
            .filter(
                SolutionCapabilityMapping.capability_id.in_(my_cap_ids),
                SolutionCapabilityMapping.solution_id != solution_id,
            )
            .all()
        )
        for m in other_mappings:
            cap_overlap.setdefault(m.solution_id, set()).add(m.capability_id)

    # 2. Find solutions sharing applications
    from sqlalchemy import text

    my_app_rows = db.session.execute(  # tenant-exempt: scoped via solution FK
        text("SELECT application_component_id FROM solution_applications WHERE solution_id = :sid"),  # tenant-exempt
        {"sid": solution_id},
    ).fetchall()
    my_app_ids = [r[0] for r in my_app_rows]
    app_overlap = {}
    if my_app_ids:
        other_app_rows = db.session.execute(  # tenant-exempt: scoped via solution FK
            text(
                "SELECT solution_id, application_component_id FROM solution_applications "
                "WHERE application_component_id IN :aids AND solution_id != :sid"
            ),
            {"aids": tuple(my_app_ids), "sid": solution_id},
        ).fetchall()
        for r in other_app_rows:
            app_overlap.setdefault(r[0], set()).add(r[1])

    # 3. Find solutions in same business domain
    domain_ids = set()
    if solution.business_domain:
        domain_solutions = (
            Solution.query
            .filter(
                Solution.business_domain == solution.business_domain,
                Solution.id != solution_id,
            )
            .limit(20)
            .all()
        )
        domain_ids = {s.id for s in domain_solutions}

    # Merge all related solution IDs
    all_related_ids = set(cap_overlap.keys()) | set(app_overlap.keys()) | domain_ids
    if not all_related_ids:
        return jsonify({"related": [], "count": 0})

    related_solutions = Solution.query.filter(Solution.id.in_(all_related_ids)).all()
    results = []
    for s in related_solutions:
        shared_caps = len(cap_overlap.get(s.id, set()))
        shared_apps = len(app_overlap.get(s.id, set()))
        same_domain = s.id in domain_ids
        overlap_score = shared_caps * 3 + shared_apps * 2 + (1 if same_domain else 0)
        results.append({
            "id": s.id,
            "name": s.name,
            "business_domain": s.business_domain,
            "solution_type": s.solution_type,
            "governance_status": s.governance_status,
            "shared_capabilities": shared_caps,
            "shared_applications": shared_apps,
            "same_domain": same_domain,
            "overlap_score": overlap_score,
        })

    results.sort(key=lambda x: x["overlap_score"], reverse=True)
    return jsonify({"related": results[:10], "count": len(results)})


# ============================================================================
# Blueprint Pre-Population API (BPP-010)
# ============================================================================


@solution_design_bp.route(
    "/<int:solution_id>/api/suggest-elements", methods=["POST"]
)
@login_required
def api_suggest_elements(solution_id):
    """Trigger the pre-population pipeline for a solution.

    Request JSON:
        phases: list of phase letters (e.g. ["A", "B", "C"])

    Returns per-phase suggestions with existing_elements and new_elements.
    """
    solution = Solution.query.get_or_404(solution_id)

    # V-PP-001: must be draft
    gov_status = getattr(solution, "governance_status", "draft")
    if gov_status not in ("draft", None, ""):
        return jsonify({
            "error": "Suggestions are only available for solutions in Draft status"
        }), 400

    # V-PP-002: must have at least one linked entity or business domain
    has_apps = db.session.execute(db.text(  # tenant-exempt: scoped via solution FK
        "SELECT 1 FROM solution_applications WHERE solution_id = :sid LIMIT 1"
    ), {"sid": solution_id}).fetchone() is not None

    has_caps = db.session.execute(db.text(  # tenant-exempt: scoped via solution FK
        "SELECT 1 FROM solution_capability_mappings WHERE solution_id = :sid LIMIT 1"
    ), {"sid": solution_id}).fetchone() is not None

    has_domain = bool(getattr(solution, "business_domain", None))

    if not (has_apps or has_caps or has_domain):
        return jsonify({
            "error": (
                "Link at least one application or capability, "
                "or set a business domain, to enable suggestions"
            )
        }), 400

    data = request.get_json(silent=True) or {}
    target_phases = data.get("phases", ["A", "B", "C", "D", "E", "F"])

    try:
        from app.modules.solutions_strategic.v2.services.solution_context_assembler import (
            SolutionContextAssembler,
        )
        from app.modules.solutions_strategic.v2.services.solution_suggestion_generator import (
            SolutionSuggestionGenerator,
        )

        assembler = SolutionContextAssembler()
        context = assembler.assemble(solution_id)

        generator = SolutionSuggestionGenerator()
        suggestions = generator.generate_suggestions(context, target_phases)

        return jsonify({"suggestions": suggestions})

    except Exception as e:
        logger.error("Error generating suggestions for solution %d: %s", solution_id, e)
        return jsonify({"error": str(e)}), 500


@solution_design_bp.route(
    "/<int:solution_id>/api/accept-suggestions", methods=["POST"]
)
@login_required
def api_accept_suggestions(solution_id):
    """Persist accepted element suggestions to junction tables.

    Request JSON:
        accepted: [{element_id, phase, relationship_type}, ...]
        new_elements: [{name, type, layer, phase}, ...]

    Returns counts of accepted and created elements.
    """
    from app.models.archimate_core import ArchiMateElement

    Solution.query.get_or_404(solution_id)

    data = request.get_json(silent=True) or {}
    accepted = data.get("accepted", [])
    new_elements = data.get("new_elements", [])

    accepted_count = 0
    created_count = 0
    junction_rows = 0

    try:
        # Process existing element acceptances
        for item in accepted:
            element_id = item.get("element_id")
            if not element_id:
                continue

            # Check element exists
            element = ArchiMateElement.query.get(element_id)
            if element is None:
                continue

            # Create junction record (skip if exists)
            existing = db.session.execute(db.text(  # tenant-exempt: scoped via solution FK
                "SELECT 1 FROM solution_archimate_elements "
                "WHERE solution_id = :sid AND element_id = :eid LIMIT 1"
            ), {"sid": solution_id, "eid": element_id}).fetchone()

            if existing is None:
                db.session.execute(db.text(  # tenant-exempt: scoped via solution FK
                    "INSERT INTO solution_archimate_elements "
                    "(solution_id, element_id, element_role, created_at) "
                    "VALUES (:sid, :eid, 'ai_derived', CURRENT_TIMESTAMP)"
                ), {"sid": solution_id, "eid": element_id})
                junction_rows += 1

            accepted_count += 1

        # Process new element creations
        for item in new_elements:
            name = item.get("name", "").strip()
            el_type = item.get("type", "")
            layer = item.get("layer", "")

            if not name or not el_type:
                continue

            new_el = ArchiMateElement(name=name, type=el_type, layer=layer)
            db.session.add(new_el)
            db.session.flush()  # get the ID

            # Create junction
            db.session.execute(db.text(  # tenant-exempt: scoped via solution FK
                "INSERT INTO solution_archimate_elements "
                "(solution_id, element_id, element_role, created_at) "
                "VALUES (:sid, :eid, 'ai_derived', CURRENT_TIMESTAMP)"
            ), {"sid": solution_id, "eid": new_el.id})

            created_count += 1
            junction_rows += 1

        db.session.commit()

        # Enrichment feedback: promote confirmed relationships to enterprise graph
        try:
            from app.modules.solutions_strategic.v2.services.solution_enrichment_feedback import (
                SolutionEnrichmentFeedback,
            )
            feedback = SolutionEnrichmentFeedback()
            feedback.on_suggestions_accepted(solution_id, accepted)
        except Exception as fb_err:
            logger.warning("Enrichment feedback failed (non-blocking): %s", fb_err)

        return jsonify({
            "accepted_count": accepted_count,
            "created_count": created_count,
            "junction_rows_created": junction_rows,
        })

    except Exception as e:
        db.session.rollback()
        logger.error("Error accepting suggestions for solution %d: %s", solution_id, e)
        return jsonify({"error": str(e)}), 500


# =============================================================================
# BLUEPRINT PAGE API ENDPOINTS (2026-03-21)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/api/roadmap-tasks", methods=["GET"])
@login_required
def api_get_roadmap_tasks(solution_id):
    """Work packages linked to the solution, shaped for the gantt component.

    blueprint.html's Transition Roadmap gantt has pointed at this URL since it
    was added, but the route never existed - every page view logged a 404 and
    the gantt showed a permanent error state.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403

    from app.models.implementation_migration import WorkPackage
    from app.models.solution_models import solution_work_packages

    wps = (
        WorkPackage.query
        .join(solution_work_packages, solution_work_packages.c.work_package_id == WorkPackage.id)
        .filter(solution_work_packages.c.solution_id == solution_id)
        .order_by(WorkPackage.start_date.asc().nulls_last(), WorkPackage.name)
        .all()
    )
    tasks = [
        {
            "id": wp.id,
            "name": wp.name,
            "status": wp.status,
            "start_date": wp.start_date.isoformat() if wp.start_date else None,
            "end_date": wp.target_date.isoformat() if wp.target_date else None,
        }
        for wp in wps
    ]
    return jsonify({"success": True, "tasks": tasks})


@solution_design_bp.route("/<int:solution_id>/api/section-narratives", methods=["GET"])
@login_required
def api_get_section_narratives(solution_id):
    """Return all section narratives for a solution."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403
    narratives = solution.section_narratives or {}
    return jsonify({"success": True, "data": {"narratives": narratives}})


@solution_design_bp.route("/<int:solution_id>/api/section-narratives/<section_id>", methods=["GET"])
@login_required
def api_get_section_narrative(solution_id, section_id):
    """Return one section's narrative.

    The decisions modal loads its narrative from this URL on every open;
    without a GET here it received 405 on each page view and warned the
    user the saved narrative could not be loaded.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403
    narratives = solution.section_narratives or {}
    text = narratives.get(section_id, "")
    return jsonify({"success": True, "data": {"section_id": section_id, "narrative": text}})


@solution_design_bp.route("/<int:solution_id>/api/section-narratives/<section_id>", methods=["PUT"])
@login_required
def api_put_section_narrative(solution_id, section_id):
    """Save/update narrative text for one section."""
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
    )

    # Validate section_id
    if section_id not in BlueprintCompletenessService.SECTION_DEFINITIONS:
        return jsonify({
            "success": False,
            "error": "Invalid section ID",
            "error_code": "INVALID_SECTION",
        }), 400

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403
    body = request.get_json(silent=True) or {}
    narrative_text = body.get("narrative", "")

    if not isinstance(narrative_text, str):
        return jsonify({
            "success": False,
            "error": "narrative must be a string",
            "error_code": "INVALID_PAYLOAD",
        }), 400

    # Update narratives JSON column
    narratives = dict(solution.section_narratives or {})
    narratives[section_id] = narrative_text
    solution.section_narratives = narratives
    solution.blueprint_updated_at = datetime.utcnow()
    solution.blueprint_updated_by_id = current_user.id

    db.session.commit()

    return jsonify({
        "success": True,
        "data": {
            "section_id": section_id,
            "narrative": narrative_text,
            "word_count": len(narrative_text.split()) if narrative_text.strip() else 0,
            "updated_at": solution.blueprint_updated_at.isoformat(),
        },
    })


def _extract_problem_statement(solution):
    """Extract a clean problem statement string from solution.problem_clarification."""
    if not solution.problem_clarification:
        return ""
    pc = solution.problem_clarification
    if isinstance(pc, str):
        try:
            import json as _json
            parsed = _json.loads(pc)
            if isinstance(parsed, dict):
                return parsed.get("summary", parsed.get("problem_statement", pc))[:500]
        except Exception as exc:
            logger.debug("suppressed error in _extract_problem_statement (app/modules/solutions_strategic/v2/routes/solution_design_routes.py): %s", exc)
        return pc[:500]
    if isinstance(pc, dict):
        return pc.get("summary", pc.get("problem_statement", ""))[:500]
    return ""


def _build_spec_data_context(solution_id, section_id, element_ids):
    """Build architecture spec context block from SolutionArchiMateElement.spec_data.

    Pulls component specs, integration contracts, and deployment specs from junction
    rows and formats them as structured context for the LLM prompt.

    Returns a non-empty string block, or "" if no spec_data is populated.
    """
    from app.models.solution_archimate_element import SolutionArchiMateElement

    if not element_ids:
        return ""

    junctions = (
        SolutionArchiMateElement.query
        .filter_by(solution_id=solution_id)
        .filter(SolutionArchiMateElement.element_id.in_(element_ids))
        .all()
    )

    # Sections that benefit from component specs (fields + api_contract + business_rules)
    COMPONENT_SPEC_SECTIONS = {
        "application_cooperation", "data_information", "executive_summary",
        "vision_motivation", "nfr_satisfaction", "requirements_traceability",
    }
    # Sections that benefit from integration contracts
    INTEGRATION_SECTIONS = {
        "application_cooperation", "network_communication", "data_information",
    }
    # Sections that benefit from deployment specs
    DEPLOYMENT_SECTIONS = {
        "deployment_view", "network_communication", "work_packages",
        "transition_roadmap", "gap_analysis",
    }

    lines = []

    for j in junctions:
        if not j.spec_data:
            continue
        sd = j.spec_data
        name = j.element_name or f"element-{j.element_id}"

        # Component fields (data model)
        if section_id in COMPONENT_SPEC_SECTIONS and sd.get("fields"):
            fields = sd["fields"]
            field_names = [f.get("name", "") for f in fields[:8] if f.get("name")]
            if field_names:
                lines.append(
                    f"  Component '{name}' data model: {', '.join(field_names)}"
                    + (f" (+{len(fields) - 8} more)" if len(fields) > 8 else "")
                )

        # API contract
        if section_id in COMPONENT_SPEC_SECTIONS and sd.get("api_contract"):
            ac = sd["api_contract"]
            if isinstance(ac, dict):
                endpoints = ac.get("endpoints", [])
                ep_summary = ", ".join(
                    f"{ep.get('method','?')} {ep.get('path','?')}"
                    for ep in endpoints[:4]
                )
                if ep_summary:
                    lines.append(f"  Component '{name}' API endpoints: {ep_summary}")
                base_url = ac.get("base_url") or ac.get("baseUrl")
                if base_url:
                    lines.append(f"  Component '{name}' base URL: {base_url}")

        # Business rules
        if section_id in COMPONENT_SPEC_SECTIONS and sd.get("business_rules"):
            rules = sd["business_rules"]
            if isinstance(rules, list) and rules:
                rule_summaries = [
                    r.get("description", r.get("rule", ""))[:80]
                    for r in rules[:3]
                    if isinstance(r, dict)
                ]
                rule_summaries = [r for r in rule_summaries if r]
                if rule_summaries:
                    lines.append(
                        f"  Component '{name}' business rules: "
                        + "; ".join(rule_summaries)
                    )

        # Integration contracts
        if section_id in INTEGRATION_SECTIONS and sd.get("integrations"):
            integrations = sd["integrations"]
            if isinstance(integrations, dict):
                for target_key, contract in list(integrations.items())[:3]:
                    if not isinstance(contract, dict):
                        continue
                    protocol = contract.get("protocol") or contract.get("communication_type", "")
                    pattern = contract.get("pattern") or contract.get("integration_pattern", "")
                    target_name = contract.get("target_name", f"element-{target_key}")
                    detail = " → ".join(filter(None, [protocol, pattern]))
                    lines.append(
                        f"  Integration '{name}' → '{target_name}': {detail or 'contract defined'}"
                    )

        # Deployment spec
        if section_id in DEPLOYMENT_SECTIONS and sd.get("deployment"):
            dep = sd["deployment"]
            if isinstance(dep, dict):
                platform = dep.get("platform") or dep.get("cloud_provider", "")
                replicas = dep.get("replicas") or dep.get("min_replicas")
                env = dep.get("environment") or dep.get("target_environment", "")
                tech = dep.get("technology_stack") or dep.get("runtime", "")
                dep_parts = []
                if env:
                    dep_parts.append(f"env={env}")
                if platform:
                    dep_parts.append(f"platform={platform}")
                if tech:
                    dep_parts.append(f"runtime={tech}")
                if replicas is not None:
                    dep_parts.append(f"replicas={replicas}")
                if dep_parts:
                    lines.append(f"  Deployment '{name}': {', '.join(dep_parts)}")

    if not lines:
        return ""

    return "Specification context from architecture data:\n" + "\n".join(lines)


def _build_solution_meta_context(solution):
    """Build solution-level metadata context block for the LLM prompt."""
    lines = []
    if solution.business_domain:
        lines.append(f"Business domain: {solution.business_domain}")
    if solution.complexity_level:
        lines.append(f"Complexity: {solution.complexity_level}")
    if solution.solution_type:
        lines.append(f"Solution type: {solution.solution_type}")
    if solution.business_value:
        lines.append(f"Business value: {solution.business_value[:300]}")
    if solution.target_outcomes:
        outcomes = solution.target_outcomes
        if isinstance(outcomes, list):
            lines.append("Target outcomes: " + "; ".join(str(o) for o in outcomes[:4]))
        elif isinstance(outcomes, str):
            lines.append(f"Target outcomes: {outcomes[:200]}")
    if solution.scope_description:
        lines.append(f"Scope: {solution.scope_description[:300]}")
    return "\n".join(lines)


def _build_section_narrative_prompt(
    solution, section_id, section_title, viewpoint, required_types, elements, spec_context,
    roadmap_phase_context=""
):
    """Build the enriched LLM prompt for a blueprint section narrative."""
    sol_name = solution.name or "Unnamed Solution"
    sol_desc = (solution.description or "").strip()[:500]
    problem = _extract_problem_statement(solution)
    meta = _build_solution_meta_context(solution)

    element_lines = "\n".join(
        f"  - [{e.type}] {e.name}: {(e.description or '').strip()[:120]}"
        for e in elements
    ) or "  (no elements linked yet)"

    # Section-specific instruction additions
    section_hints = {
        "executive_summary": (
            "Summarise the strategic intent, key architectural decisions, and expected outcomes. "
            "Reference specific components and integrations from the architecture context."
        ),
        "vision_motivation": (
            "Articulate the business drivers, stakeholder goals, and architectural requirements "
            "that justify this solution. Connect each driver to a measurable outcome."
        ),
        "application_cooperation": (
            "Describe the application components, their interfaces, and the integration patterns "
            "between them. Reference specific API endpoints and protocols from the specification context."
        ),
        "data_information": (
            "Describe the data objects, their structure, and how they flow between components. "
            "Reference specific field definitions and data contracts from the specification context."
        ),
        "deployment_view": (
            "Describe the deployment topology: infrastructure nodes, cloud platforms, replica counts, "
            "and runtime environments. Reference specific deployment specs from the specification context."
        ),
        "network_communication": (
            "Describe the communication paths, protocols, and integration contracts between nodes. "
            "Reference specific integration patterns and protocols from the specification context."
        ),
        "work_packages": (
            "Describe the implementation work packages, deliverables, and sequencing. "
            "Connect each package to the deployment and component specs it delivers."
        ),
        "gap_analysis": (
            "Identify the gaps between the current and target architecture plateaus. "
            "Reference specific components, integrations, or deployment capabilities missing today."
        ),
        "transition_roadmap": (
            "Describe the phased transition from current-state to target architecture. "
            "For each phase: name the plateau/milestone, list the key work packages, "
            "state the entry and exit criteria, identify the implementation events, and "
            "quantify the gap closures achieved. Reference specific element names from the "
            "architecture context. Use TOGAF ADM Phase E/F terminology: plateau, gap, "
            "implementation event, migration planning."
        ),
        "security_viewpoint": (
            "Describe the security constraints, authentication/authorisation patterns, and compliance "
            "requirements. Reference specific API auth methods and business rules from the spec context."
        ),
    }
    section_hint = section_hints.get(section_id, (
        "Focus on the architectural decisions, rationale, and implications specific to this viewpoint."
    ))

    prompt_parts = [
        "You are a senior enterprise architect writing a Solution Architecture Document (SAD) "
        "for a real production system. Your output will be reviewed by an Architecture Review Board.",
        "",
        f"Solution: {sol_name}",
    ]
    if meta:
        prompt_parts.append(meta)
    if sol_desc:
        prompt_parts.append(f"Description: {sol_desc}")
    if problem:
        prompt_parts.append(f"Problem being solved: {problem}")

    prompt_parts += [
        "",
        f"Section to generate: '{section_title}' ({viewpoint} viewpoint)",
        f"Relevant ArchiMate element types: {', '.join(required_types) or 'All'}",
        "",
        "Architecture elements linked to this section:",
        element_lines,
    ]

    if spec_context:
        prompt_parts += ["", spec_context]

    if roadmap_phase_context:
        prompt_parts += ["", roadmap_phase_context]

    prompt_parts += [
        "",
        "Instructions:",
        f"- {section_hint}",
        "- Write 150-250 words of precise, architecture-grade narrative",
        "- Reference specific component names, integration patterns, and deployment targets from above",
        "- Avoid generic phrases like 'This solution aims to...' or 'The system will...'",
        "- Use TOGAF ADM and ArchiMate 3.2 terminology appropriate for a " + viewpoint + " viewpoint",
        "- Output plain text only — no markdown headers, no bullet points, no lists",
        "- Every sentence should contain a specific architectural fact, decision, or rationale",
    ]

    return "\n".join(prompt_parts)


class NarrativeGenerationError(Exception):
    """Raised by `generate_section_narrative` on any handled failure.

    Carries `error_code` so callers (the HTTP route, the AI-chat tool
    executor) can map it to their own response shape without re-deriving it.
    """

    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


def generate_section_narrative(solution_id, section_id, user_id):
    """Generate and persist AI narrative text for one blueprint section.

    Uses the section's linked ArchiMate elements + spec_data context as prompt.
    Saves the result to section_narratives[section_id] and returns a result dict.

    Takes `user_id` rather than relying on flask_login's `current_user`, so it
    is callable from contexts with no HTTP request — notably
    `app.modules.ai_chat.tools.executor.ToolExecutor._tool_generate_blueprint_narrative`,
    which runs in-process against the AI-chat conversation's own user_id.
    Raises `NarrativeGenerationError` on any handled failure.
    """
    from app.models.user import User
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
        SECTION_TITLES,
    )

    if section_id not in BlueprintCompletenessService.SECTION_DEFINITIONS:
        raise NarrativeGenerationError("Invalid section ID", "INVALID_SECTION")

    solution = Solution.query.get(solution_id)
    if solution is None:
        raise NarrativeGenerationError("Solution not found", "NOT_FOUND")

    # Looked up by primary key with no org filter because cross-org access is
    # closed by _check_solution_access() immediately below, which checks this
    # user against the *solution's* organization (creator/admin/stakeholder-
    # email match) — an org filter here would only duplicate that check.
    user = User.query.get(user_id)  # tenant-scoping-ok: access closed by _check_solution_access() below
    if user is None or not _check_solution_access(solution, user=user):
        raise NarrativeGenerationError("Forbidden", "FORBIDDEN")

    # Gather context
    svc = BlueprintCompletenessService()
    defn = BlueprintCompletenessService.SECTION_DEFINITIONS[section_id]
    section_title = SECTION_TITLES.get(section_id, section_id.replace("_", " ").title())
    viewpoint = defn.get("viewpoint") or "General"
    required_types = defn.get("required_types", [])

    elements = svc._get_section_elements(solution_id, section_id)
    element_ids = [e.id for e in elements]
    spec_context = _build_spec_data_context(solution_id, section_id, element_ids)

    # Inject real roadmap phase data for transition_roadmap section.
    # JourneyOrchestrator.get_roadmap_data() derives phases from SolutionBlueprintProposal —
    # SolutionRoadmapPhase does not exist in this codebase.
    roadmap_phase_context = ""
    if section_id == "transition_roadmap":
        try:
            from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator as _JO
            _roadmap = _JO(solution_id).get_roadmap_data()
            _phases = _roadmap.get("phases", [])
            if _phases:
                _lines = ["Roadmap phases (derived from accepted architecture elements):"]
                for _ph in _phases:
                    # Sanitize element names: strip non-printable chars (blocks newline injection),
                    # truncate to 80 chars. "\n".isprintable() is False — newlines are stripped.
                    _elem_names = ", ".join(
                        "".join(c for c in (e.get("name", "") or "")[:80] if c.isprintable())
                        for e in _ph.get("elements", [])[:8]
                    )
                    _suffix = "..." if len(_ph.get("elements", [])) > 8 else ""
                    _lines.append(
                        f"  {_ph['name']}: {len(_ph.get('elements', []))} elements "
                        f"({_elem_names}{_suffix})"
                    )
                roadmap_phase_context = "\n".join(_lines)
                logger.debug(
                    "Roadmap phase context built: %d phases, %d total elements for sol %d",
                    len(_phases), _roadmap.get("total_elements", 0), solution_id,
                )
        except Exception as _rme:
            logger.warning(
                "Roadmap phase context failed for section %s sol %d: %s",
                section_id, solution_id, _rme,
            )

    prompt = _build_section_narrative_prompt(
        solution, section_id, section_title, viewpoint, required_types, elements, spec_context,
        roadmap_phase_context=roadmap_phase_context,
    )

    try:
        from app.services.llm_service import LLMService
        narrative = LLMService.generate_from_prompt(prompt, use_cache=False)
    except ValueError:
        raise NarrativeGenerationError(
            "No LLM provider configured. Add an API key in Admin → API Settings.", "NO_LLM"
        )
    except Exception as e:
        logger.error("Blueprint narrative generation failed for solution %s section %s: %s", solution_id, section_id, e)
        raise NarrativeGenerationError("LLM generation failed. Try again.", "LLM_ERROR")

    # Save to section_narratives
    narratives = dict(solution.section_narratives or {})
    narratives[section_id] = narrative
    solution.section_narratives = narratives
    solution.blueprint_updated_at = datetime.utcnow()
    solution.blueprint_updated_by_id = user_id
    db.session.commit()

    return {
        "narrative": narrative,
        "section_id": section_id,
        "word_count": len(narrative.split()),
    }


_NARRATIVE_ERROR_STATUS = {
    "INVALID_SECTION": 400,
    "NOT_FOUND": 404,
    "FORBIDDEN": 403,
    "NO_LLM": 503,
    "LLM_ERROR": 500,
}


@solution_design_bp.route("/<int:solution_id>/api/blueprint/<section_id>/generate", methods=["POST"])
@login_required
def api_generate_section_narrative(solution_id, section_id):
    """HTTP wrapper around `generate_section_narrative` for the blueprint editor UI."""
    try:
        result = generate_section_narrative(solution_id, section_id, current_user.id)
    except NarrativeGenerationError as e:
        status = _NARRATIVE_ERROR_STATUS.get(e.error_code, 500)
        return jsonify({"success": False, "error": str(e), "error_code": e.error_code}), status

    return jsonify({"success": True, **result})


@solution_design_bp.route("/<int:solution_id>/api/blueprint/<section_id>/codegen", methods=["POST"])
@login_required
def api_blueprint_codegen(solution_id, section_id):
    """Generate infrastructure/API code from blueprint section elements.

    POST body: {"target": "terraform|helm|openapi|api_stub|cicd|security_policy"}
    Returns: {"success": true, "code": "...", "filename": "main.tf", "language": "hcl"}
    """
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
        SECTION_TITLES,
    )

    CODEGEN_TARGETS = {
        "deployment_view":       ["terraform", "helm"],
        "application_cooperation": ["openapi", "api_stub"],
        "network_communication": ["terraform"],
        "work_packages":         ["cicd"],
        "security_viewpoint":    ["security_policy"],
        "nfr_satisfaction":      ["openapi"],
    }

    TARGET_META = {
        "terraform":       {"language": "hcl",        "filename": "main.tf"},
        "helm":            {"language": "yaml",       "filename": "values.yaml"},
        "openapi":         {"language": "yaml",       "filename": "openapi.yaml"},
        "api_stub":        {"language": "python",     "filename": "api.py"},
        "cicd":            {"language": "yaml",       "filename": ".github/workflows/deploy.yaml"},
        "security_policy": {"language": "hcl",        "filename": "security.tf"},
    }

    if section_id not in BlueprintCompletenessService.SECTION_DEFINITIONS:
        return jsonify({"success": False, "error": "Invalid section ID", "error_code": "INVALID_SECTION"}), 400

    allowed = CODEGEN_TARGETS.get(section_id, [])
    if not allowed:
        return jsonify({"success": False, "error": "Codegen not supported for this section", "error_code": "UNSUPPORTED_SECTION"}), 400

    body = request.get_json(silent=True) or {}
    target = body.get("target", allowed[0])
    if target not in allowed:
        return jsonify({"success": False, "error": f"Target '{target}' not valid for section '{section_id}'. Valid: {allowed}", "error_code": "INVALID_TARGET"}), 400

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403

    svc = BlueprintCompletenessService()
    elements = svc._get_section_elements(solution_id, section_id)
    element_lines = "\n".join(
        f"  - [{e.type}] {e.name}: {(e.description or '').strip()[:120]}"
        for e in elements
    ) or "  (no elements linked yet — generate from available context)"

    sol_name = solution.name or "Unnamed Solution"
    sol_desc = (solution.description or "").strip()[:400]
    section_title = SECTION_TITLES.get(section_id, section_id.replace("_", " ").title())
    meta = TARGET_META[target]

    TARGET_PROMPTS = {
        "terraform": (
            f"You are a senior DevOps engineer. Generate a complete Terraform configuration (HCl) "
            f"for the following solution architecture.\n\n"
            f"Solution: {sol_name}\nDescription: {sol_desc or 'Not provided'}\n\n"
            f"Architecture elements from the '{section_title}' viewpoint:\n{element_lines}\n\n"
            f"Requirements:\n"
            f"- Create resources for each Node, Device, SystemSoftware, and ApplicationComponent element\n"
            f"- Use sensible defaults (AWS provider unless context suggests otherwise)\n"
            f"- Include provider block, variables block, and outputs block\n"
            f"- Add comments explaining each resource's role in the architecture\n"
            f"- Output complete, valid HCl — no TODOs, no placeholders\n"
        ),
        "helm": (
            f"You are a senior Kubernetes engineer. Generate a complete Helm values.yaml "
            f"for the following solution architecture.\n\n"
            f"Solution: {sol_name}\nDescription: {sol_desc or 'Not provided'}\n\n"
            f"Architecture elements from the '{section_title}' viewpoint:\n{element_lines}\n\n"
            f"Requirements:\n"
            f"- Create a Helm values file covering all ApplicationComponent and Node elements\n"
            f"- Include replicaCount, image, resources, service, ingress sections per component\n"
            f"- Add environment variables and config maps where relevant\n"
            f"- Output complete, valid YAML — no TODOs, no placeholders\n"
        ),
        "openapi": (
            f"You are a senior API architect. Generate a complete OpenAPI 3.0 specification "
            f"for the following solution architecture.\n\n"
            f"Solution: {sol_name}\nDescription: {sol_desc or 'Not provided'}\n\n"
            f"Architecture elements from the '{section_title}' viewpoint:\n{element_lines}\n\n"
            f"Requirements:\n"
            f"- Create paths for each ApplicationService and ApplicationInterface element\n"
            f"- Include GET, POST, PUT, DELETE where semantically appropriate\n"
            f"- Define request/response schemas under components/schemas\n"
            f"- Add security schemes (Bearer JWT)\n"
            f"- Output complete, valid OpenAPI 3.0 YAML — no TODOs, no placeholders\n"
        ),
        "api_stub": (
            f"You are a senior backend engineer. Generate complete Python/Flask API stub code "
            f"for the following solution architecture.\n\n"
            f"Solution: {sol_name}\nDescription: {sol_desc or 'Not provided'}\n\n"
            f"Architecture elements from the '{section_title}' viewpoint:\n{element_lines}\n\n"
            f"Requirements:\n"
            f"- Create Flask blueprints for each ApplicationComponent element\n"
            f"- Add route handlers for each ApplicationService and ApplicationInterface\n"
            f"- Include request validation, error handling, and logging\n"
            f"- Add SQLAlchemy model stubs where DataObject elements are present\n"
            f"- Output complete Python code — no TODOs, no placeholders\n"
        ),
        "cicd": (
            f"You are a senior DevOps engineer. Generate a complete GitHub Actions CI/CD pipeline "
            f"for the following solution architecture.\n\n"
            f"Solution: {sol_name}\nDescription: {sol_desc or 'Not provided'}\n\n"
            f"Architecture elements from the '{section_title}' viewpoint:\n{element_lines}\n\n"
            f"Requirements:\n"
            f"- Create jobs for each WorkPackage or Deliverable element (build, test, deploy stages)\n"
            f"- Include environment-specific deployment steps (staging, production)\n"
            f"- Add secret references for credentials\n"
            f"- Include approval gates for production deployments\n"
            f"- Output complete, valid GitHub Actions YAML — no TODOs, no placeholders\n"
        ),
        "security_policy": (
            f"You are a senior security engineer. Generate a complete security policy configuration "
            f"(Terraform/HCl) for the following solution architecture.\n\n"
            f"Solution: {sol_name}\nDescription: {sol_desc or 'Not provided'}\n\n"
            f"Architecture elements from the '{section_title}' viewpoint:\n{element_lines}\n\n"
            f"Requirements:\n"
            f"- Create IAM policies, security groups, and WAF rules for each Constraint/Requirement\n"
            f"- Include network ACLs and encryption settings\n"
            f"- Add audit logging and monitoring resources\n"
            f"- Output complete, valid HCl — no TODOs, no placeholders\n"
        ),
    }

    prompt = TARGET_PROMPTS[target]

    try:
        from app.services.llm_service import LLMService
        code = LLMService.generate_from_prompt(prompt, use_cache=False)
        # Strip markdown fences if present (handles ```hcl\n...\n``` wrapping)
        import re as _re
        fence_match = _re.search(r"```(?:\w+)?\n(.*?)\n```", code, _re.DOTALL)
        if fence_match:
            code = fence_match.group(1)
        # Guard against empty/whitespace-only responses before declaring success
        if not code or not code.strip():
            logger.warning(
                "Blueprint codegen returned empty content for solution %s section %s target %s",
                solution_id, section_id, target,
            )
            return jsonify({
                "success": False,
                "error": "The LLM returned an empty response. Please try again — if this persists, verify your LLM API key is valid in Admin → API Settings.",
                "error_code": "EMPTY_RESPONSE",
            }), 500
    except ValueError:
        return jsonify({
            "success": False,
            "error": "No LLM provider configured. Add an API key in Admin → API Settings.",
            "error_code": "NO_LLM",
        }), 503
    except Exception as e:
        logger.error("Blueprint codegen failed for solution %s section %s target %s: %s", solution_id, section_id, target, e)
        return jsonify({"success": False, "error": "Code generation failed. Try again.", "error_code": "LLM_ERROR"}), 500

    return jsonify({
        "success": True,
        "code": code,
        "filename": meta["filename"],
        "language": meta["language"],
        "target": target,
        "section_id": section_id,
        "element_count": len(elements),
    })


@solution_design_bp.route("/<int:solution_id>/generate-draft", methods=["POST"])
@login_required
def api_generate_draft(solution_id):
    """Bulk-generate narratives for all 14 blueprint sections.

    Uses solution description as the problem statement seed.
    Generates sections sequentially to avoid LLM rate limits.
    """
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
        SECTION_TITLES,
    )

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403

    try:
        from app.services.llm_service import LLMService
        LLMService._get_configured_provider()  # fail fast if no LLM configured
    except ValueError:
        return jsonify({
            "success": False,
            "error": "No LLM provider configured. Add an API key in Admin → API Settings.",
            "error_code": "NO_LLM",
        }), 503

    svc = BlueprintCompletenessService()

    narratives = dict(solution.section_narratives or {})
    generated = []
    errors = []

    for section_id, defn in BlueprintCompletenessService.SECTION_DEFINITIONS.items():
        # Skip sections that already have content
        if narratives.get(section_id, "").strip():
            continue

        section_title = SECTION_TITLES.get(section_id, section_id.replace("_", " ").title())
        viewpoint = defn.get("viewpoint") or "General"
        required_types = defn.get("required_types", [])

        elements = svc._get_section_elements(solution_id, section_id)
        element_ids = [e.id for e in elements]
        spec_context = _build_spec_data_context(solution_id, section_id, element_ids)

        prompt = _build_section_narrative_prompt(
            solution, section_id, section_title, viewpoint, required_types, elements, spec_context
        )

        try:
            from app.services.llm_service import LLMService
            narrative = LLMService.generate_from_prompt(prompt, use_cache=False)
            narratives[section_id] = narrative
            generated.append(section_id)
        except Exception as e:
            logger.error("generate-draft: section %s failed: %s", section_id, e)
            errors.append(section_id)

    if generated:
        solution.section_narratives = narratives
        solution.blueprint_updated_at = datetime.utcnow()
        solution.blueprint_updated_by_id = current_user.id
        db.session.commit()

    return jsonify({
        "success": True,
        "total": len(generated),
        "generated": generated,
        "skipped_errors": errors,
    })


@solution_design_bp.route("/<int:solution_id>/api/blueprint-scores", methods=["GET"])
@login_required
def api_get_blueprint_scores(solution_id):
    """Return all section scores + overall + next actions + arb_ready."""
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
    )

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403
    svc = BlueprintCompletenessService()

    # Use cached scores if available, otherwise compute
    section_scores = solution.section_scores
    if not section_scores:
        section_scores = svc.score_all(solution_id)

    # Compute overall average
    if section_scores:
        overall = round(
            sum(s.get("overall", 0) for s in section_scores.values()) / len(section_scores)
        )
    else:
        overall = 0

    next_actions = svc.get_next_actions(solution_id, precomputed_scores=section_scores)
    arb_ready = svc.check_arb_ready(solution_id, precomputed_scores=section_scores)

    return jsonify({
        "success": True,
        "data": {
            "sections": section_scores,
            "overall": overall,
            "next_actions": next_actions,
            "arb_ready": arb_ready,
            "blueprint_version": solution.blueprint_version or 1,
            "updated_at": solution.blueprint_updated_at.isoformat() if solution.blueprint_updated_at else None,
        },
    })


@solution_design_bp.route("/<int:solution_id>/api/blueprint-scores/recalculate", methods=["POST"])
@login_required
def api_recalculate_blueprint_scores(solution_id):
    """Force full recalculation of all section scores."""
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
    )

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403
    svc = BlueprintCompletenessService()
    section_scores = svc.score_all(solution_id)

    # Refresh solution to get updated fields
    db.session.refresh(solution)

    if section_scores:
        overall = round(
            sum(s.get("overall", 0) for s in section_scores.values()) / len(section_scores)
        )
    else:
        overall = 0

    next_actions = svc.get_next_actions(solution_id, precomputed_scores=section_scores)
    arb_ready = svc.check_arb_ready(solution_id, precomputed_scores=section_scores)

    return jsonify({
        "success": True,
        "data": {
            "sections": section_scores,
            "overall": overall,
            "next_actions": next_actions,
            "arb_ready": arb_ready,
            "blueprint_version": solution.blueprint_version or 1,
            "updated_at": solution.blueprint_updated_at.isoformat() if solution.blueprint_updated_at else None,
        },
    })


def _viewpoint_elements_for_section(solution_id, section_id):
    """Return serialised element dicts for a viewpoint section (reused by generate endpoint)."""
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
    )
    svc = BlueprintCompletenessService()
    elements = svc._get_section_elements(solution_id, section_id)
    return [
        {
            "id": e.id,
            "name": e.name,
            "type": e.type,
            "layer": e.layer,
            "description": e.description or "",
            "status": "linked",
        }
        for e in elements
    ]


@solution_design_bp.route("/<int:solution_id>/api/viewpoint/<section_id>/elements", methods=["GET"])
@login_required
def api_get_viewpoint_elements(solution_id, section_id):
    """Return elements filtered by viewpoint for a section."""
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
    )

    # Validate section_id
    if section_id not in BlueprintCompletenessService.SECTION_DEFINITIONS:
        return jsonify({
            "success": False,
            "error": "Invalid section ID",
            "error_code": "INVALID_SECTION",
        }), 400

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403
    svc = BlueprintCompletenessService()
    elements = svc._get_section_elements(solution_id, section_id)

    # Build app-context lookup for ApplicationComponent elements (contextual intelligence)
    app_context = {}
    try:
        from app.models.models import Application
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from sqlalchemy import func as _func
        app_names = [
            e.name for e in elements
            if getattr(e, "type", "") in ("ApplicationComponent", "ApplicationService")
        ]
        if app_names:
            apps = Application.query.filter(Application.name.in_(app_names)).all()
            # Count solutions that reference each app by name
            ref_counts = {}
            for app in apps:
                count = (
                    SolutionArchiMateElement.query
                    .filter(
                        SolutionArchiMateElement.element_name == app.name,
                        SolutionArchiMateElement.solution_id != solution_id,
                    )
                    .with_entities(_func.count())
                    .scalar()
                ) or 0
                ref_counts[app.name] = count

            for app in apps:
                lifecycle = getattr(app, "lifecycle_status", None) or getattr(app, "status", None) or ""
                rat_score = getattr(app, "rationalization_score", None)
                app_context[app.name] = {
                    "lifecycle": lifecycle,
                    "other_solutions": ref_counts.get(app.name, 0),
                    "flag": "decommission" if "decommission" in (lifecycle or "").lower() or "retired" in (lifecycle or "").lower() else (
                        "low_score" if rat_score and rat_score < 40 else None
                    ),
                }
    except Exception as _ctx_err:
        logger.debug("Contextual intelligence lookup skipped: %s", _ctx_err)

    element_data = []
    for elem in elements:
        ctx = app_context.get(elem.name, {})
        element_data.append({
            "id": elem.id,
            "name": elem.name,
            "type": elem.type,
            "layer": elem.layer,
            "description": elem.description or "",
            "scope": elem.scope,
            "building_block_type": getattr(elem, "building_block_type", None),
            "plateau": elem.plateau,
            "status": "linked",
            # Contextual intelligence fields (empty for non-app elements)
            "lifecycle": ctx.get("lifecycle", ""),
            "other_solutions": ctx.get("other_solutions", 0),
            "context_flag": ctx.get("flag", None),
        })

    # Fetch relationships: include 1-hop neighbours within the solution
    from app.models.archimate_core import ArchiMateRelationship, ArchiMateElement
    from app.models.solution_archimate_element import SolutionArchiMateElement as _SAE
    elem_ids = [e.id for e in elements]
    elem_ids_set = set(elem_ids)

    # All element IDs belonging to this solution (for scoping neighbour lookup)
    sol_elem_ids = [
        r[0] for r in _SAE.query.filter_by(solution_id=solution_id)
        .with_entities(_SAE.element_id).all()
        if r[0] is not None
    ]
    set(sol_elem_ids)

    if elem_ids:
        # Relationships where section element is source and target is anywhere in solution
        rels_out = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id.in_(elem_ids),
            ArchiMateRelationship.target_id.in_(sol_elem_ids),
        ).limit(50).all()
        # Relationships where section element is target and source is anywhere in solution
        rels_in = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.target_id.in_(elem_ids),
            ArchiMateRelationship.source_id.in_(sol_elem_ids),
        ).limit(50).all()
        all_rels = list({r.id: r for r in rels_out + rels_in}.values())[:60]
    else:
        all_rels = []

    # Collect neighbour element IDs (in solution but not already in section)
    neighbour_ids = set()
    for r in all_rels:
        if r.source_id not in elem_ids_set:
            neighbour_ids.add(r.source_id)
        if r.target_id not in elem_ids_set:
            neighbour_ids.add(r.target_id)

    # Fetch and append neighbour elements (marked is_context=True)
    if neighbour_ids:
        neighbour_elems = ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(neighbour_ids)
        ).all()
        for elem in neighbour_elems:
            element_data.append({
                "id": elem.id,
                "name": elem.name,
                "type": elem.type or "",
                "layer": getattr(elem, "layer", ""),
                "description": getattr(elem, "description", "") or "",
                "scope": getattr(elem, "scope", None),
                "building_block_type": getattr(elem, "building_block_type", None),
                "plateau": getattr(elem, "plateau", None),
                "status": "context",
                "lifecycle": "",
                "other_solutions": 0,
                "context_flag": None,
                "is_context": True,
            })

    rel_data = [
        {
            "id": r.id,
            "source_id": r.source_id,
            "target_id": r.target_id,
            "type": r.type or "association",
            "description": r.description or "",
        }
        for r in all_rels
    ]

    defn = BlueprintCompletenessService.SECTION_DEFINITIONS[section_id]
    return jsonify({
        "success": True,
        "data": {
            "section_id": section_id,
            "viewpoint": defn.get("viewpoint"),
            "required_types": defn.get("required_types", []),
            "elements": element_data,
            "relationships": rel_data,
            "count": len(element_data),
        },
    })


@solution_design_bp.route("/<int:solution_id>/api/viewpoint/<section_id>/diagram-data", methods=["GET"])
@login_required
def api_get_viewpoint_diagram_data(solution_id, section_id):
    """Return JointJS-compatible graph data (elements + relationships between them)."""
    from app.models.archimate_core import ArchiMateRelationship
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
    )

    # Validate section_id
    if section_id not in BlueprintCompletenessService.SECTION_DEFINITIONS:
        return jsonify({
            "success": False,
            "error": "Invalid section ID",
            "error_code": "INVALID_SECTION",
        }), 400

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403
    svc = BlueprintCompletenessService()
    elements = svc._get_section_elements(solution_id, section_id)

    if not elements:
        return jsonify({
            "success": True,
            "data": {"cells": [], "element_count": 0, "relationship_count": 0},
        })

    elem_ids = [e.id for e in elements]
    elem_map = {e.id: e for e in elements}

    # Fetch relationships between these elements
    relationships = ArchiMateRelationship.query.filter(
        ArchiMateRelationship.source_id.in_(elem_ids),
        ArchiMateRelationship.target_id.in_(elem_ids),
    ).all()

    # Build JointJS cells array
    cells = []

    # Layer-to-color mapping for visual grouping
    layer_colors = {
        "Business": "#FFFFB5",
        "Application": "#B5FFFF",
        "Technology": "#C9E7B7",
        "Strategy": "#F5DEAA",
        "Motivation": "#CCCCFF",
        "Implementation": "#FFE0D0",
        "Physical": "#C9E7B7",
    }

    # Grid layout: arrange elements in rows by layer
    x_offset = 50
    y_offset = 50
    row_elements = {}

    for elem in elements:
        layer = elem.layer or "Other"
        if layer not in row_elements:
            row_elements[layer] = []
        row_elements[layer].append(elem)

    y = y_offset
    for layer, layer_elems in row_elements.items():
        x = x_offset
        for elem in layer_elems:
            fill_color = layer_colors.get(layer, "#FFFFFF")
            cells.append({
                "type": "archimate.Element",
                "id": f"elem-{elem.id}",
                "position": {"x": x, "y": y},
                "size": {"width": 160, "height": 80},
                "attrs": {
                    "label": {"text": elem.name or ""},
                    "body": {"fill": fill_color, "stroke": "#333333"},
                },
                "archimate": {
                    "id": elem.id,
                    "type": elem.type,
                    "layer": elem.layer,
                    "description": elem.description or "",
                },
            })
            x += 200
        y += 120

    # Add relationship links
    for rel in relationships:
        if rel.source_id in elem_map and rel.target_id in elem_map:
            cells.append({
                "type": "archimate.Relationship",
                "id": f"rel-{rel.id}",
                "source": {"id": f"elem-{rel.source_id}"},
                "target": {"id": f"elem-{rel.target_id}"},
                "labels": [{"attrs": {"text": {"text": rel.type or ""}}}],
                "archimate": {
                    "id": rel.id,
                    "type": rel.type,
                    "description": rel.description or "",
                },
            })

    return jsonify({
        "success": True,
        "data": {
            "cells": cells,
            "element_count": len(elements),
            "relationship_count": len(relationships),
        },
    })


@solution_design_bp.route("/<int:solution_id>/api/traceability-matrix", methods=["GET"])
@login_required
def api_get_traceability_matrix(solution_id):
    """Return requirement x element x work package grid with coverage dots.

    Rows: Requirements linked to this solution
    Columns: Business Services, Application Components, Nodes, Work Packages
    Cells: relationship type if a relationship exists between the row and column element
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_archimate_element import SolutionArchiMateElement

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden", "error_code": "FORBIDDEN"}), 403

    # Get all elements linked to this solution
    element_ids_q = (
        db.session.query(SolutionArchiMateElement.element_id)
        .filter_by(solution_id=solution_id)
        .all()
    )
    if not element_ids_q:
        return jsonify({
            "success": True,
            "data": {
                "requirements": [],
                "columns": [],
                "matrix": [],
                "coverage_pct": 0,
            },
        })

    elem_ids = [eid for (eid,) in element_ids_q]
    elements = ArchiMateElement.query.filter(ArchiMateElement.id.in_(elem_ids)).all()

    # Separate by role
    requirements = [e for e in elements if e.type in ("Requirement", "Constraint", "Goal")]
    column_types = ("BusinessService", "ApplicationComponent", "Node", "WorkPackage", "Deliverable")
    column_elements = [e for e in elements if e.type in column_types]

    if not requirements:
        return jsonify({
            "success": True,
            "data": {
                "requirements": [],
                "columns": [{"id": c.id, "name": c.name, "type": c.type} for c in column_elements],
                "matrix": [],
                "coverage_pct": 0,
            },
        })

    # Build relationship lookup: (source_id, target_id) -> relationship type
    req_ids = [r.id for r in requirements]
    col_ids = [c.id for c in column_elements]
    all_relevant_ids = req_ids + col_ids

    rels = ArchiMateRelationship.query.filter(
        ArchiMateRelationship.source_id.in_(all_relevant_ids),
        ArchiMateRelationship.target_id.in_(all_relevant_ids),
    ).all()

    rel_lookup = {}
    for rel in rels:
        rel_lookup[(rel.source_id, rel.target_id)] = rel.type
        # Also check reverse direction
        rel_lookup[(rel.target_id, rel.source_id)] = rel.type

    # Build matrix rows
    matrix = []
    total_cells = len(requirements) * len(column_elements) if column_elements else 0
    covered_cells = 0

    for req in requirements:
        row = {
            "requirement": {"id": req.id, "name": req.name, "type": req.type},
            "cells": [],
        }
        for col_elem in column_elements:
            rel_type = rel_lookup.get((req.id, col_elem.id))
            cell = {
                "element_id": col_elem.id,
                "covered": rel_type is not None,
                "relationship_type": rel_type,
            }
            row["cells"].append(cell)
            if rel_type is not None:
                covered_cells += 1
        matrix.append(row)

    coverage_pct = round((covered_cells / total_cells) * 100) if total_cells > 0 else 0

    return jsonify({
        "success": True,
        "data": {
            "requirements": [{"id": r.id, "name": r.name, "type": r.type} for r in requirements],
            "columns": [{"id": c.id, "name": c.name, "type": c.type} for c in column_elements],
            "matrix": matrix,
            "coverage_pct": coverage_pct,
            "total_cells": total_cells,
            "covered_cells": covered_cells,
        },
    })


# =============================================================================
# COMPONENT SPECIFICATION API ENDPOINTS (ACM-001)
# =============================================================================


def _find_junction(solution_id, element_id):
    """Find the SolutionArchiMateElement junction for a solution+element pair."""
    from app.models.solution_archimate_element import SolutionArchiMateElement
    return SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id,
        element_id=element_id,
    ).first()


@solution_design_bp.route("/<int:solution_id>/api/component-specs/<int:element_id>", methods=["GET"])
@login_required
def api_get_component_spec(solution_id, element_id):
    """Return component spec (fields, api_contract, business_rules) for an element."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": False, "error": "Element not linked to solution"}), 404

    from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
    svc = ComponentSpecService()
    spec = svc.get_component_spec(junction.id)

    return jsonify({"success": True, "data": {"spec_data": spec, "junction_id": junction.id}})


@solution_design_bp.route("/<int:solution_id>/api/component-specs/<int:element_id>", methods=["PUT"])
@login_required
def api_put_component_spec(solution_id, element_id):
    """Save fields, api_contract, or business_rules for a component."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": False, "error": "Element not linked to solution"}), 404

    body = request.get_json(silent=True) or {}
    tab = body.get("tab", "fields")
    data = body.get("data", {})

    from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
    svc = ComponentSpecService()

    try:
        if tab == "fields":
            result = svc.save_fields(
                junction.id,
                data.get("fields", []),
                user_id=current_user.id,
                generated_by=data.get("generated_by", "user"),
                model_used=data.get("model_used"),
            )
        elif tab == "api_contract":
            result = svc.save_api_contract(
                junction.id,
                data.get("api_contract", {}),
                user_id=current_user.id,
                generated_by=data.get("generated_by", "user"),
                model_used=data.get("model_used"),
            )
        elif tab == "business_rules":
            result = svc.save_business_rules(
                junction.id,
                data.get("rules", []),
                user_id=current_user.id,
                generated_by=data.get("generated_by", "user"),
                model_used=data.get("model_used"),
            )
        else:
            return jsonify({"success": False, "error": f"Invalid tab: {tab}"}), 400

        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@solution_design_bp.route("/<int:solution_id>/api/component-specs/<int:element_id>/confirm", methods=["POST"])
@login_required
def api_confirm_component_spec(solution_id, element_id):
    """Confirm proposed fields, api_contract, or a business rule."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": False, "error": "Element not linked to solution"}), 404

    body = request.get_json(silent=True) or {}
    tab = body.get("tab", "fields")

    from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
    svc = ComponentSpecService()

    try:
        if tab == "fields":
            result = svc.confirm_fields(junction.id, user_id=current_user.id)
        elif tab == "api_contract":
            result = svc.confirm_api_contract(junction.id, user_id=current_user.id)
        elif tab == "business_rules":
            rule_id = body.get("rule_id")
            if not rule_id:
                return jsonify({"success": False, "error": "rule_id required for business_rules tab"}), 400
            result = svc.confirm_business_rule(junction.id, rule_id, user_id=current_user.id)
        else:
            return jsonify({"success": False, "error": f"Invalid tab: {tab}"}), 400

        # RUNTIME-08: Fire webhooks on spec confirmation
        try:
            from app.modules.solutions_product.services.drift_remediation_service import DriftRemediationService
            drift_svc = DriftRemediationService()
            drift_svc.on_spec_confirmed(solution_id, element_id)
        except Exception as drift_err:
            # Non-blocking: webhook failures should not break spec confirmation
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Drift remediation webhook failed for solution %s element %s: %s",
                solution_id, element_id, drift_err,
            )

        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@solution_design_bp.route("/<int:solution_id>/api/component-specs/<int:element_id>/history", methods=["GET"])
@login_required
def api_get_component_spec_history(solution_id, element_id):
    """Return version history for a component spec section."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": True, "data": {"history": []}}), 200

    section = request.args.get("section", "fields")

    from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
    svc = ComponentSpecService()
    history = svc.get_history(junction.id, section=section)

    return jsonify({"success": True, "data": {"history": history}})


@solution_design_bp.route("/<int:solution_id>/api/component-specs/<int:element_id>/diff", methods=["GET"])
@login_required
def api_diff_component_spec(solution_id, element_id):
    """Diff two versions of a component spec section."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": False, "error": "Element not linked"}), 404

    section = request.args.get("section", "fields")
    from_v = request.args.get("from", type=int)
    to_v = request.args.get("to", type=int)
    if from_v is None or to_v is None:
        return jsonify({"success": False, "error": "from and to version params required"}), 400

    from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
    svc = ComponentSpecService()
    diff = svc.diff_versions(junction.id, section, from_v, to_v)

    return jsonify({"success": True, "data": diff})


@solution_design_bp.route("/<int:solution_id>/api/component-specs/<int:element_id>/infer", methods=["POST"])
@login_required
def api_infer_component_spec(solution_id, element_id):
    """Run LLM inference to propose fields for a component."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": False, "error": "Element not linked to solution"}), 404

    from app.modules.solutions_strategic.v2.services.code_spec_inference import infer_code_spec
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_sad_models import SolutionIntegrationFlow, SolutionSLA

    element = ArchiMateElement.query.get(element_id)
    if not element:
        return jsonify({"success": False, "error": "Element not found"}), 404

    flows = SolutionIntegrationFlow.query.filter_by(solution_id=solution_id).all()
    slas = SolutionSLA.query.filter_by(solution_id=solution_id).all()

    requirements = []
    try:
        from app.models.solution_architect_models import SolutionRequirement
        requirements = SolutionRequirement.query.filter_by(
            solution_id=solution_id
        ).filter(SolutionRequirement.deleted_at.is_(None)).all()
    except Exception as e:
        logger.warning("Failed to load requirements for spec inference: %s", e)

    # Create a compatible element-like object for infer_code_spec
    # Note: ArchiMateElement uses 'type' but infer_code_spec expects 'element_type'
    elem_proxy = type("ElemProxy", (), {
        "name": element.name,
        "element_type": element.type,
        "description": element.description,
        "technology": getattr(element, "technology", None),
    })()

    result = infer_code_spec(elem_proxy, requirements, flows, slas, solution_id, element_id=element.id)
    if not result:
        return jsonify({"success": False, "error": "LLM inference failed"}), 500

    # Auto-save as proposed
    from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
    svc = ComponentSpecService()
    save_result = svc.save_fields(
        junction.id,
        result.get("fields", []),
        user_id=current_user.id,
        generated_by="llm",
        model_used=result.get("model"),
    )

    return jsonify({"success": True, "data": {
        "fields": result.get("fields", []),
        "confidence": result.get("confidence", 0),
        "reasoning": result.get("reasoning", ""),
        "version": save_result["version"],
        "status": "proposed",
    }})


@solution_design_bp.route("/<int:solution_id>/api/component-specs/infer-all", methods=["POST"])
@login_required
def api_infer_all_component_specs(solution_id):
    """Run LLM inference for all ApplicationComponent elements in the solution."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    from app.models.solution_archimate_element import SolutionArchiMateElement

    junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    app_junctions = [j for j in junctions if hasattr(j, "layer_type") and j.layer_type == "application"]

    # If no layer_type, check the linked element type
    if not app_junctions:
        from app.models.archimate_core import ArchiMateElement
        for j in junctions:
            elem = ArchiMateElement.query.get(j.element_id)
            if elem and elem.type in ("ApplicationComponent", "ApplicationService"):
                app_junctions.append(j)

    results = {}
    for j in app_junctions:
        # Trigger inference for each element via the single-element endpoint logic
        try:
            from app.models.archimate_core import ArchiMateElement
            from app.modules.solutions_strategic.v2.services.code_spec_inference import infer_code_spec
            from app.models.solution_sad_models import SolutionIntegrationFlow, SolutionSLA

            element = ArchiMateElement.query.get(j.element_id)
            if not element:
                continue

            flows = SolutionIntegrationFlow.query.filter_by(solution_id=solution_id).all()
            slas = SolutionSLA.query.filter_by(solution_id=solution_id).all()
            requirements = []
            try:
                from app.models.solution_architect_models import SolutionRequirement
                requirements = SolutionRequirement.query.filter_by(
                    solution_id=solution_id
                ).filter(SolutionRequirement.deleted_at.is_(None)).all()
            except Exception as e:
                logger.warning("Failed to load requirements for batch inference: %s", e)

            # Note: ArchiMateElement uses 'type' but infer_code_spec expects 'element_type'
            elem_proxy = type("ElemProxy", (), {
                "name": element.name,
                "element_type": element.type,
                "description": element.description,
                "technology": getattr(element, "technology", None),
            })()

            result = infer_code_spec(elem_proxy, requirements, flows, slas, solution_id, element_id=element.id)
            if result and result.get("fields"):
                from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
                svc = ComponentSpecService()
                svc.save_fields(j.id, result["fields"], user_id=current_user.id, generated_by="llm", model_used=result.get("model"))
                results[j.element_id] = {"status": "proposed", "field_count": len(result["fields"])}
            else:
                results[j.element_id] = {"status": "failed"}
        except Exception as e:
            logger.error("Inference failed for junction %d: %s", j.id, e)
            results[j.element_id] = {"status": "error", "message": str(e)}

    return jsonify({"success": True, "data": {"results": results, "total": len(app_junctions)}})


@solution_design_bp.route("/<int:solution_id>/api/component-specs/<int:element_id>/validate", methods=["POST"])
@login_required
def api_validate_component_spec(solution_id, element_id):
    """Validate fields against schema rules."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    body = request.get_json(silent=True) or {}
    fields = body.get("fields", [])

    from app.modules.solutions_strategic.v2.services.spec_validators import validate_fields_schema
    errors = validate_fields_schema(fields)

    return jsonify({"success": True, "data": {"errors": errors, "valid": len(errors) == 0}})


# --- Business Rules API ---

@solution_design_bp.route("/<int:solution_id>/api/business-rules/<int:element_id>", methods=["GET"])
@login_required
def api_get_business_rules(solution_id, element_id):
    """Return business rules for a component."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": True, "data": {"rules": []}})

    from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
    svc = ComponentSpecService()
    result = svc.get_business_rules(junction.id)

    return jsonify({"success": True, "data": result or {"rules": []}})


@solution_design_bp.route("/<int:solution_id>/api/business-rules/<int:element_id>", methods=["PUT"])
@login_required
def api_put_business_rules(solution_id, element_id):
    """Save business rules for a component."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": False, "error": "Element not linked to solution"}), 404

    body = request.get_json(silent=True) or {}
    rules = body.get("rules", [])

    from app.modules.solutions_strategic.v2.services.component_spec_service import ComponentSpecService
    svc = ComponentSpecService()

    try:
        result = svc.save_business_rules(
            junction.id,
            rules,
            user_id=current_user.id,
            generated_by=body.get("generated_by", "user"),
            model_used=body.get("model_used"),
        )
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@solution_design_bp.route("/<int:solution_id>/api/business-rules/<int:element_id>/suggest", methods=["POST"])
@login_required
def api_suggest_business_rules(solution_id, element_id):
    """LLM-suggest business rules from component requirements and capabilities."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    # Business rule suggestion returns empty when no LLM is configured
    return jsonify({"success": True, "data": {"rules": [], "message": "Business rule suggestion requires LLM configuration"}})


@solution_design_bp.route("/<int:solution_id>/api/business-rules/<int:element_id>/validate", methods=["POST"])
@login_required
def api_validate_business_rules(solution_id, element_id):
    """Validate business rules against metamodel."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    body = request.get_json(silent=True) or {}
    rules = body.get("rules", [])

    from app.modules.solutions_strategic.v2.services.spec_validators import validate_business_rules
    errors = validate_business_rules(rules)

    return jsonify({"success": True, "data": {"errors": errors, "valid": len(errors) == 0}})


# --- Integration Contracts API ---

@solution_design_bp.route("/<int:solution_id>/api/integration-contracts/<int:element_id>", methods=["GET"])
@login_required
def api_get_integration_contracts(solution_id, element_id):
    """Return all integration contracts for a source element."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": True, "data": {"contracts": {}}})

    from app.modules.solutions_strategic.v2.services.integration_contract_service import IntegrationContractService
    svc = IntegrationContractService()
    contracts = svc.get_contracts(junction.id)

    return jsonify({"success": True, "data": {"contracts": contracts}})


@solution_design_bp.route("/<int:solution_id>/api/integration-contracts/<int:element_id>", methods=["PUT"])
@login_required
def api_put_integration_contract(solution_id, element_id):
    """Save an integration contract for a target element."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": False, "error": "Element not linked to solution"}), 404

    body = request.get_json(silent=True) or {}
    target_id = body.get("target_element_id")
    contract = body.get("contract", {})

    if not target_id:
        return jsonify({"success": False, "error": "target_element_id required"}), 400

    from app.modules.solutions_strategic.v2.services.integration_contract_service import IntegrationContractService
    svc = IntegrationContractService()

    try:
        result = svc.save_contract(
            junction.id,
            str(target_id),
            contract,
            user_id=current_user.id,
            generated_by=body.get("generated_by", "user"),
            model_used=body.get("model_used"),
        )
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@solution_design_bp.route("/<int:solution_id>/api/integration-contracts/<int:element_id>/suggest", methods=["POST"])
@login_required
def api_suggest_integration_contract(solution_id, element_id):
    """Suggest an integration contract from ArchiMate relationships."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    body = request.get_json(silent=True) or {}
    target_id = body.get("target_element_id")

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": False, "error": "Element not linked"}), 404

    # Try to find existing relationship
    from app.models.archimate_core import ArchiMateRelationship
    relationship = None
    if target_id:
        relationship = ArchiMateRelationship.query.filter_by(
            source_id=element_id, target_id=target_id
        ).first()
        if not relationship:
            relationship = ArchiMateRelationship.query.filter_by(
                source_id=target_id, target_id=element_id
            ).first()

    from app.modules.solutions_strategic.v2.services.integration_contract_service import IntegrationContractService
    svc = IntegrationContractService()
    suggestion = svc.suggest_from_relationship(junction.id, relationship)

    return jsonify({"success": True, "data": {"suggestion": suggestion}})


@solution_design_bp.route("/<int:solution_id>/api/integration-contracts/<int:element_id>/validate", methods=["POST"])
@login_required
def api_validate_integration_contract(solution_id, element_id):
    """Validate an integration contract."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    body = request.get_json(silent=True) or {}
    contract = body.get("contract", {})

    from app.modules.solutions_strategic.v2.services.spec_validators import validate_integration_contract
    errors = validate_integration_contract(contract)

    return jsonify({"success": True, "data": {"errors": errors, "valid": len(errors) == 0}})


# --- Deployment Specs API ---

@solution_design_bp.route("/<int:solution_id>/api/deployment-specs/<int:element_id>", methods=["GET"])
@login_required
def api_get_deployment_spec(solution_id, element_id):
    """Return deployment spec for a Node element."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": True, "data": {"deployment": None}})

    from app.modules.solutions_strategic.v2.services.deployment_spec_service import DeploymentSpecService
    svc = DeploymentSpecService()
    result = svc.get_deployment(junction.id)

    return jsonify({"success": True, "data": result or {"deployment": None}})


@solution_design_bp.route("/<int:solution_id>/api/deployment-specs/<int:element_id>", methods=["PUT"])
@login_required
def api_put_deployment_spec(solution_id, element_id):
    """Save a deployment spec for a Node element."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    junction = _find_junction(solution_id, element_id)
    if not junction:
        return jsonify({"success": False, "error": "Element not linked to solution"}), 404

    body = request.get_json(silent=True) or {}
    deployment = body.get("deployment", {})

    from app.modules.solutions_strategic.v2.services.deployment_spec_service import DeploymentSpecService
    svc = DeploymentSpecService()

    try:
        result = svc.save_deployment(
            junction.id,
            deployment,
            user_id=current_user.id,
            generated_by=body.get("generated_by", "user"),
            model_used=body.get("model_used"),
        )
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@solution_design_bp.route("/<int:solution_id>/api/deployment-specs/<int:element_id>/suggest", methods=["POST"])
@login_required
def api_suggest_deployment_spec(solution_id, element_id):
    """Suggest a deployment spec from a Node/TechnologyService element."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    from app.models.archimate_core import ArchiMateElement
    element = ArchiMateElement.query.get(element_id)

    from app.modules.solutions_strategic.v2.services.deployment_spec_service import DeploymentSpecService
    svc = DeploymentSpecService()
    suggestion = svc.suggest_from_element(element)

    return jsonify({"success": True, "data": {"suggestion": suggestion}})


@solution_design_bp.route("/<int:solution_id>/api/deployment-specs/<int:element_id>/validate", methods=["POST"])
@login_required
def api_validate_deployment_spec(solution_id, element_id):
    """Validate a deployment spec for runtime compatibility."""
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    body = request.get_json(silent=True) or {}
    spec = body.get("deployment", body)

    from app.modules.solutions_strategic.v2.services.spec_validators import validate_deployment_spec
    errors = validate_deployment_spec(spec)

    return jsonify({"success": True, "data": {"errors": errors, "valid": len(errors) == 0}})


# ── RUNTIME-03: Identity Provider Configuration ──


@solution_design_bp.route("/<int:solution_id>/identity-provider", methods=["PUT"])
@login_required
def update_identity_provider(solution_id):
    """Configure the identity provider for generated code auth.

    PUT /solutions/<id>/identity-provider
    Body: {"type": "okta", "params": {"domain": "company"}, "client_id_env": "...", ...}

    Stores the resolved config in the solution's first application-layer element's
    spec_data["identity_provider"], making it available to the code generator.
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"success": False, "error": "Request body required"}), 400

    from app.modules.solutions_product.services.identity_provider_presets import (
        build_identity_provider_config,
    )

    try:
        config = build_identity_provider_config(body)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    # Store on the first application-layer element, or create a solution-level entry
    from app.models.solution_archimate_element import SolutionArchiMateElement

    target = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id,
        layer_type="application",
    ).first()

    if target:
        sd = target.spec_data or {}
        sd["identity_provider"] = config
        target.spec_data = sd
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(target, "spec_data")
    else:
        # No application-layer element — store on any element or use the first one
        target = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id,
        ).first()
        if target:
            sd = target.spec_data or {}
            sd["identity_provider"] = config
            target.spec_data = sd
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(target, "spec_data")
        else:
            return jsonify({
                "success": False,
                "error": "No ArchiMate elements found for this solution. "
                         "Add at least one element before configuring identity provider.",
            }), 400

    db.session.commit()

    return jsonify({
        "success": True,
        "data": {
            "identity_provider": config,
            "stored_on_element_id": target.element_id if target else None,
        },
    })


@solution_design_bp.route("/<int:solution_id>/identity-provider", methods=["GET"])
@login_required
def get_identity_provider(solution_id):
    """Get the current identity provider configuration for a solution.

    GET /solutions/<id>/identity-provider
    """
    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        return jsonify({"success": False, "error": "Forbidden"}), 403

    from app.models.solution_archimate_element import SolutionArchiMateElement

    links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
    for link in links:
        sd = link.spec_data or {}
        if sd.get("identity_provider"):
            return jsonify({
                "success": True,
                "data": {
                    "identity_provider": sd["identity_provider"],
                    "stored_on_element_id": link.element_id,
                },
            })

    return jsonify({
        "success": True,
        "data": {"identity_provider": None},
    })


@solution_design_bp.route("/api/identity-provider/presets", methods=["GET"])
@login_required
def list_identity_provider_presets():
    """List available identity provider presets.

    GET /solutions/api/identity-provider/presets
    """
    from app.modules.solutions_product.services.identity_provider_presets import (
        list_presets,
    )

    return jsonify({
        "success": True,
        "data": {"presets": list_presets()},
    })


# ===================================================================
# FRAG-030 through FRAG-039: Fragmentation remediation routes
# ===================================================================

@solution_design_bp.route("/<int:solution_id>/api/raci")
@login_required
def api_raci_matrix(solution_id):
    """FRAG-030: Get RACI matrix for a solution."""
    require_entity(Solution, solution_id, description="Solution not found")
    try:
        from app.services.raci_service import get_raci_matrix
        matrix = get_raci_matrix(solution_id)
        return jsonify({"success": True, "matrix": matrix})
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"RACI matrix error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/api/raci", methods=["PUT"])
@login_required
def api_set_raci(solution_id):
    """FRAG-030: Set RACI assignment."""
    try:
        from app.services.raci_service import set_raci_assignment
        data = request.get_json()
        result = set_raci_assignment(
            solution_id, data["stakeholder_id"], data["raci_type"], data["value"]
        )
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"RACI set error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/api/gantt/export")
@login_required
def api_gantt_export(solution_id):
    """FRAG-031: Export Gantt chart."""
    require_entity(Solution, solution_id, description="Solution not found")
    try:
        from app.services.gantt_export_service import GanttExportService
        fmt = request.args.get("format", "csv")
        service = GanttExportService()
        # Export solution phases as work packages
        phases = ['Phase A: Vision', 'Phase B: Business', 'Phase C: Info Systems',
                  'Phase D: Technology', 'Phase E: Opportunities', 'Phase F: Migration',
                  'Phase G: Governance', 'Phase H: Change Mgmt']
        wp_dicts = [{"name": p, "start_date": "", "end_date": "",
                     "status": "planned", "progress": 0} for p in phases]
        if fmt == "csv":
            output = service.export_to_csv(wp_dicts)
            resp = make_response(output)
            resp.headers["Content-Type"] = "text/csv"
            resp.headers["Content-Disposition"] = f"attachment; filename=gantt_{solution_id}.csv"
            return resp
        elif fmt == "excel":
            output = service.export_to_excel(wp_dicts)
            resp = make_response(output)
            resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            resp.headers["Content-Disposition"] = f"attachment; filename=gantt_{solution_id}.xlsx"
            return resp
        elif fmt == "pdf":
            output = service.export_to_pdf(wp_dicts)
            resp = make_response(output)
            resp.headers["Content-Type"] = "application/pdf"
            resp.headers["Content-Disposition"] = f"attachment; filename=gantt_{solution_id}.pdf"
            return resp
        else:
            return jsonify({"error": f"Unknown format: {fmt}"}), 400
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Gantt export error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/api/outcomes")
@login_required
def api_outcomes(solution_id):
    """FRAG-032: Get outcome tracking summary."""
    require_entity(Solution, solution_id, description="Solution not found")
    try:
        from app.services.outcome_tracking_service import OutcomeTrackingService
        service = OutcomeTrackingService()
        summary = service.get_solution_realization_summary(solution_id)
        return jsonify({"success": True, "data": summary})
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Outcomes error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/api/templates")
@login_required
def api_list_templates():
    """FRAG-033: List solution templates."""
    try:
        from app.services.solution_template_service import list_templates
        templates = list_templates()
        return jsonify({"success": True, "templates": templates})
    except Exception as e:
        current_app.logger.error(f"Templates error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/api/from-template", methods=["POST"])
@login_required
def api_create_from_template():
    """FRAG-033: Create solution from template."""
    try:
        from app.services.solution_template_service import create_solution_from_template
        data = request.get_json()
        # created_by is a required third argument; omitting it raised TypeError.
        result = create_solution_from_template(
            data["template_id"], data.get("name"), current_user.id
        )
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"Template create error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/api/market-intelligence")
@login_required
def api_market_intelligence(solution_id):
    """FRAG-039: Get market intelligence for a solution."""
    require_entity(Solution, solution_id, description="Solution not found")
    try:
        from app.modules.solutions_strategic.v2.services.market_intelligence_service import MarketIntelligenceService
        service = MarketIntelligenceService()
        trends = service.get_industry_trends("technology", limit=5)
        landscape = service.get_competitive_landscape(solution_id)
        return jsonify({"success": True, "trends": trends, "landscape": landscape})
    except HTTPException:
        raise
    except Exception as e:
        current_app.logger.error(f"Market intelligence error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/deliverables/export")
@login_required
def solution_deliverable_export(solution_id):
    """FRAG-034: Export TOGAF deliverables (vision/compliance/roadmap) as PDF.

    Query params:
        type  — vision | compliance | roadmap
        format — pdf (only pdf supported currently)
    """
    from types import SimpleNamespace

    export_type = request.args.get("type", "vision")
    fmt = request.args.get("format", "pdf")

    if fmt != "pdf":
        return jsonify({"error": "Only pdf format is supported"}), 400
    if export_type not in ("vision", "compliance", "roadmap"):
        return jsonify({"error": "type must be vision, compliance, or roadmap"}), 400

    solution = Solution.query.get_or_404(solution_id)

    try:
        from app.services.togaf_deliverable_export_service import TOGAFDeliverableExportService
        svc = TOGAFDeliverableExportService()

        if export_type == "vision":
            # Try DB first; fall back to synthetic object built from solution
            doc = None
            try:
                from app.models.workflow_artifacts import ArchitectureVisionDocument
                doc = (
                    ArchitectureVisionDocument.query
                    .order_by(ArchitectureVisionDocument.created_at.desc())
                    .first()
                )
            except Exception as _e:
                logger.debug("No ArchitectureVisionDocument found, using synthetic: %s", _e)

            if doc is None:
                doc = SimpleNamespace(
                    title=f"Architecture Vision — {solution.name}",
                    scope_summary=getattr(solution, "problem_statement", None)
                        or getattr(solution, "description", None),
                    stakeholder_concerns=[],
                    business_goals=[],
                    constraints={},
                    target_architecture_summary=getattr(solution, "proposed_solution", None),
                    architecture_principles=[],
                    status="draft",
                )
            pdf_bytes = svc.export_vision_to_pdf(doc)
            filename = f"vision_{solution_id}.pdf"

        elif export_type == "compliance":
            doc = None
            try:
                from app.models.workflow_artifacts import ComplianceScanReport
                doc = (
                    ComplianceScanReport.query
                    .order_by(ComplianceScanReport.created_at.desc())
                    .first()
                )
            except Exception as _e:
                logger.debug("No ComplianceScanReport found, using synthetic: %s", _e)

            if doc is None:
                doc = SimpleNamespace(
                    title=f"Compliance Report — {solution.name}",
                    total_violations=0,
                    policies_evaluated=0,
                    applications_scanned=0,
                    auto_remediated_count=0,
                    violations_by_severity={},
                    content={},
                    status="draft",
                )
            pdf_bytes = svc.export_compliance_to_pdf(doc)
            filename = f"compliance_{solution_id}.pdf"

        else:  # roadmap
            doc = None
            try:
                from app.models.workflow_artifacts import MigrationPlanDocument
                doc = (
                    MigrationPlanDocument.query
                    .order_by(MigrationPlanDocument.created_at.desc())
                    .first()
                )
            except Exception as _e:
                logger.debug("No MigrationPlanDocument found, using synthetic: %s", _e)

            if doc is None:
                doc = SimpleNamespace(
                    title=f"Architecture Roadmap — {solution.name}",
                    adm_phase="F",
                    consolidated_gaps=[],
                    prioritized_projects=[],
                    transition_architectures=[],
                    status="draft",
                )
            pdf_bytes = svc.export_roadmap_to_pdf(doc)
            filename = f"roadmap_{solution_id}.pdf"

        response = make_response(pdf_bytes)
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'  # raw-html-ok: filename built from solution_id, a route int, never free text
        return response

    except ImportError as e:
        current_app.logger.error(f"TOGAF export import error: {e}")
        return jsonify({"error": "PDF export dependencies not available"}), 503
    except Exception as e:
        current_app.logger.error(f"TOGAF export error for solution {solution_id}: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# EXPORT OEF XML (ArchiMate Open Exchange Format)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/export-oef", methods=["GET"])
@login_required
def export_solution_oef(solution_id: int):
    """Export solution architecture as ArchiMate OEF (Open Exchange Format) XML.

    Generates a standards-compliant ArchiMate 3.0 OEF XML file for import into
    ArchiMate-compatible tools (Archi, Sparx EA, BizzDesign, etc.).

    Data sources (in priority order):
    1. SolutionArchiMateElement rows linked to this solution (via archimate_elements join)
    2. SolutionBlueprintProposal rows with status in [promoted, accepted] as fallback
    Returns 404 JSON if no elements found in either source.
    """
    import re
    import xml.etree.ElementTree as ET

    solution = Solution.query.get_or_404(solution_id)
    if not _check_solution_access(solution):
        abort(403)

    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

    # ── 1. Collect elements ───────────────────────────────────────────────────
    # Primary: solution_archimate_elements joined to archimate_elements
    junction_rows = (
        SolutionArchiMateElement.query
        .filter_by(solution_id=solution_id)
        .all()
    )

    elements_data = []        # list of dicts: {id, name, archimate_type, description}
    element_ids_used = set()  # archimate_elements.id values included

    for jrow in junction_rows:
        ae = ArchiMateElement.query.get(jrow.element_id)
        if ae:
            archimate_type = _oef_type(ae.type or jrow.layer_type or "")
            elements_data.append({
                "id": ae.id,
                "name": ae.name or jrow.element_name or f"Element {ae.id}",
                "archimate_type": archimate_type,
                "description": ae.description or "",
            })
            element_ids_used.add(ae.id)
        elif jrow.element_name:
            # Linked element deleted from master table — use junction data only
            archimate_type = _oef_type(jrow.layer_type or "")
            elements_data.append({
                "id": jrow.element_id,
                "name": jrow.element_name,
                "archimate_type": archimate_type,
                "description": "",
            })
            element_ids_used.add(jrow.element_id)

    # Fallback: blueprint proposals (promoted or accepted)
    if not elements_data:
        proposals = (
            SolutionBlueprintProposal.query
            .filter(
                SolutionBlueprintProposal.solution_id == solution_id,
                SolutionBlueprintProposal.status.in_(["promoted", "accepted"]),
            )
            .all()
        )
        for p in proposals:
            archimate_type = _oef_type(p.archimate_type or "")
            elements_data.append({
                "id": p.id,
                "name": p.name or f"Proposal {p.id}",
                "archimate_type": archimate_type,
                "description": p.description or "",
            })

    if not elements_data:
        return jsonify({"error": "No architecture elements found for this solution"}), 404

    # ── 2. Collect relationships between included elements ────────────────────
    relationships_data = []
    if element_ids_used:
        rels = (
            ArchiMateRelationship.query
            .filter(
                ArchiMateRelationship.source_id.in_(element_ids_used),
                ArchiMateRelationship.target_id.in_(element_ids_used),
            )
            .all()
        )
        for rel in rels:
            rel_type = _oef_rel_type(rel.type or "Association")
            relationships_data.append({
                "id": rel.id,
                "type": rel_type,
                "source_id": rel.source_id,
                "target_id": rel.target_id,
            })

    # ── 3. Build OEF XML ──────────────────────────────────────────────────────
    OEF_NS = "http://www.opengroup.org/xsd/archimate/3.0/"
    XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
    SCHEMA_LOC = (
        "http://www.opengroup.org/xsd/archimate/3.0/ "
        "http://www.opengroup.org/xsd/archimate/3.0/archimate3_Diagram.xsd"
    )

    ET.register_namespace("", OEF_NS)
    ET.register_namespace("xsi", XSI_NS)

    model_elem = ET.Element(f"{{{OEF_NS}}}model")
    model_elem.set(f"{{{XSI_NS}}}schemaLocation", SCHEMA_LOC)
    model_elem.set("identifier", f"id-{solution_id}")
    model_elem.set("name", solution.name or f"Solution {solution_id}")

    # <elements>
    elements_tag = ET.SubElement(model_elem, f"{{{OEF_NS}}}elements")
    for el in elements_data:
        el_tag = ET.SubElement(elements_tag, f"{{{OEF_NS}}}element")
        el_tag.set("identifier", f"id-{el['id']}")
        el_tag.set(f"{{{XSI_NS}}}type", el["archimate_type"])
        el_tag.set("name", el["name"])
        if el.get("description"):
            doc_tag = ET.SubElement(el_tag, f"{{{OEF_NS}}}documentation")
            doc_tag.text = el["description"]

    # <relationships>
    if relationships_data:
        rels_tag = ET.SubElement(model_elem, f"{{{OEF_NS}}}relationships")
        for rel in relationships_data:
            rel_tag = ET.SubElement(rels_tag, f"{{{OEF_NS}}}relationship")
            rel_tag.set("identifier", f"id-rel-{rel['id']}")
            rel_tag.set(f"{{{XSI_NS}}}type", rel["type"])
            rel_tag.set("source", f"id-{rel['source_id']}")
            rel_tag.set("target", f"id-{rel['target_id']}")

    # Serialize with XML declaration
    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        model_elem, encoding="unicode"
    ).encode("utf-8")

    # Build filename from solution name
    slug = re.sub(
        r"[^a-z0-9]+", "-",
        (solution.name or f"solution-{solution_id}").lower()
    ).strip("-")
    filename = f"{slug}-architecture.xml"

    response = make_response(xml_bytes)
    response.headers["Content-Type"] = "application/xml; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'  # raw-html-ok: filename built from slug, already regex-sanitized to [a-z0-9-] above
    return response


def _oef_type(raw_type: str) -> str:
    """Map internal ArchiMate type strings to OEF xsi:type values.

    OEF types use the element name exactly as in the ArchiMate 3.0 spec,
    e.g. 'BusinessProcess', 'ApplicationComponent', 'TechnologyNode'.
    Unknown or layer-only strings fall back to 'BusinessObject'.
    """
    import re

    if not raw_type:
        return "BusinessObject"

    # Already looks like a proper CamelCase OEF type — return as-is
    if raw_type[0].isupper() and " " not in raw_type and "_" not in raw_type:
        return raw_type

    # Normalize: title-case each word, strip separators
    normalized = "".join(
        w.capitalize() for w in re.split(r"[\s_\-]+", raw_type.strip())
    )

    # Layer-only strings — map to a safe per-layer default
    _LAYER_DEFAULTS = {
        "Business": "BusinessObject",
        "Application": "ApplicationComponent",
        "Technology": "TechnologyNode",
        "Motivation": "Goal",
        "Strategy": "Capability",
        "Implementation": "WorkPackage",
        "Physical": "Equipment",
    }
    if normalized in _LAYER_DEFAULTS:
        return _LAYER_DEFAULTS[normalized]

    return normalized if normalized else "BusinessObject"


def _oef_rel_type(raw_type: str) -> str:
    """Map internal relationship type strings to OEF xsi:type values."""
    if not raw_type:
        return "AssociationRelationship"
    clean = raw_type.strip()
    if clean.endswith("Relationship"):
        return clean
    return clean[0].upper() + clean[1:] + "Relationship"
