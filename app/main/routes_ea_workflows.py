"""
Enterprise Architecture Workflow Routes

Provides routes for managing and executing automated EA workflows.

Features:
- Workflow definition management
- Workflow instance execution and monitoring
- Schedule management for automated workflows
- Approval handling for manual intervention steps
"""

from flask import current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.middleware.tenant_decorators import platform_admin_required
from app.utils.pagination import safe_int_arg


def _sort_iteration_keys(keys):
    """Sort iteration keys, placing None last to ensure None-safe ordering.

    When grouping workflow instances by iteration_number, some real data may
    have None as a key. Python's sorted() cannot compare None with integers,
    so we use a custom key function: (k is None, k) converts None to (True, None)
    and integers to (False, n), ensuring None sorts after all numeric keys
    while preserving numeric ordering.

    Args:
        keys: An iterable of iteration number keys (may include None)

    Returns:
        A sorted list with None values (if any) at the end
    """
    return sorted(keys, key=lambda k: (k is None, k))


def _build_arch_review_report(exec_steps, instance_data):
    """Build a consolidated Architecture Review Report from step outputs.

    Handles both old step IDs (validate_completeness, suggest_relationships,
    derive_links, calculate_quality) and new step IDs (resolve_context,
    completeness_audit, relationship_gaps, quality_assessment).
    """
    def _out(*step_ids):
        for sid in step_ids:
            step = exec_steps.get(sid)
            if step and step.get("output_data"):
                return step["output_data"]
        return {}

    resolve = _out("resolve_context")
    completeness = _out("completeness_audit", "validate_completeness")
    gaps = _out("relationship_gaps", "suggest_relationships")
    quality = _out("quality_assessment", "calculate_quality")
    committed = _out("commit_changes")

    has_data = any([completeness, gaps, quality, resolve])
    if not has_data:
        return None

    completeness_pct = completeness.get("completeness_percent", 0)
    avg_quality = quality.get("average_quality", 0)
    orphan_count = completeness.get("orphan_count", 0)
    suggestion_count = gaps.get("suggestion_count", 0)
    violation_count = gaps.get("violation_count", 0)
    undocumented = completeness.get("undocumented_count", 0)
    needs_attention = quality.get("needs_attention_count", 0)

    scores = [completeness_pct, avg_quality]
    overall = round(sum(scores) / len(scores), 1) if scores else 0
    if overall >= 75:
        health = "good"
    elif overall >= 50:
        health = "fair"
    else:
        health = "poor"

    critical = []
    if orphan_count > 0:
        critical.append(f"{orphan_count} orphan element{'s' if orphan_count != 1 else ''} with no relationships")
    if violation_count > 0:
        critical.append(f"{violation_count} metamodel violation{'s' if violation_count != 1 else ''}")
    if undocumented > 0:
        critical.append(f"{undocumented} element{'s' if undocumented != 1 else ''} missing descriptions")
    if needs_attention > 0:
        critical.append(f"{needs_attention} element{'s' if needs_attention != 1 else ''} scoring below quality threshold")

    high_gaps = gaps.get("high_priority_count", 0)
    if high_gaps > 0:
        critical.append(f"{high_gaps} high-priority relationship{'s' if high_gaps != 1 else ''} missing")

    if overall >= 75:
        recommendation = "Architecture is in good shape. Address the remaining gaps to reach excellence."
    elif overall >= 50:
        recommendation = "Architecture needs attention. Focus on orphan elements and missing relationships first."
    else:
        recommendation = "Architecture has significant gaps. Prioritize completeness and standards compliance immediately."

    naming_violations = []
    for el in quality.get("elements", []):
        criteria = el.get("criteria", {})
        naming = criteria.get("naming_convention", {})
        if naming and naming.get("score", 0) < naming.get("max", 10):
            naming_violations.append(el)

    return {
        "application": resolve,
        "completeness": completeness,
        "relationship_gaps": gaps,
        "quality": quality,
        "committed": committed,
        "naming_violations": naming_violations,
        "summary": {
            "overall_health": health,
            "overall_score": overall,
            "completeness_pct": completeness_pct,
            "avg_quality": avg_quality,
            "orphan_count": orphan_count,
            "suggestion_count": suggestion_count,
            "violation_count": violation_count,
            "undocumented": undocumented,
            "needs_attention": needs_attention,
            "critical_findings": critical,
            "recommendation": recommendation,
        },
    }


def register_ea_workflow_routes(main_blueprint):
    """Register EA workflow routes with the main blueprint."""

    @main_blueprint.route("/ea-workflows")
    @login_required
    def ea_workflows_dashboard():
        """EA Workflows dashboard showing all available workflows and recent executions."""
        # Lazy imports to avoid circular dependencies
        from app.services.ea_workflow_engine import EAWorkflowEngine

        try:
            engine = EAWorkflowEngine()
            definitions = engine.list_workflow_definitions()

            # Get recent instances for each workflow
            from app.models.workflow_models import EAWorkflowInstance

            recent_instances = (
                EAWorkflowInstance.query.order_by(EAWorkflowInstance.created_at.desc())
                .limit(20)
                .all()
            )

            # Group by status
            status_counts = {"running": 0, "waiting_approval": 0, "completed": 0, "failed": 0}
            for inst in recent_instances:
                if inst.status in status_counts:
                    status_counts[inst.status] += 1

            applications = []
            try:
                from app.models.application_portfolio import ApplicationComponent

                applications = (
                    ApplicationComponent.query
                    .with_entities(ApplicationComponent.id, ApplicationComponent.name)
                    .order_by(ApplicationComponent.name)
                    .limit(500)
                    .all()
                )
            except Exception as _app_err:
                import logging
                logging.getLogger(__name__).warning(
                    "Workflow dashboard applications unavailable: %s",
                    _app_err,
                )

            # Linkable instances for "Link to Prior Phase" dropdowns
            from app.models.workflow_models import EAWorkflowDefinition
            linkable_instances = []
            try:
                linkable_instances = (
                    EAWorkflowInstance.query
                    .join(EAWorkflowDefinition)
                    .filter(EAWorkflowInstance.status.in_(["completed", "running", "waiting_approval"]))
                    .filter(EAWorkflowDefinition.workflow_code.like("ADM_%"))
                    .order_by(EAWorkflowInstance.created_at.desc())
                    .limit(50)
                    .all()
                )
            except Exception as _link_err:
                import logging
                logging.getLogger(__name__).debug("Linkable instances unavailable: %s", _link_err)

            compliance_posture = None
            try:
                from app.models.workflow_artifacts import ComplianceScanReport
                latest_scan = (
                    ComplianceScanReport.query
                    .order_by(ComplianceScanReport.created_at.desc())
                    .first()
                )
                if latest_scan:
                    compliance_posture = {
                        "total_violations": latest_scan.total_violations or 0,
                        "by_severity": latest_scan.violations_by_severity or {},
                        "last_scan": latest_scan.created_at.isoformat() if latest_scan.created_at else None,
                        "applications_scanned": (latest_scan.content or {}).get("applications_scanned", 0),
                        "policies_evaluated": latest_scan.policies_evaluated or 0,
                    }
            except Exception as _e:
                import logging
                logging.getLogger(__name__).debug("Compliance posture unavailable: %s", _e)

            phase_counts = engine.get_phase_counts()

            # T3-2: Featured TOGAF workflow gallery
            FEATURED_CODES = [
                "ARCH_REVIEW",
                "ADM_PHASE_A_VISION",
                "VENDOR_SELECTION",
                "ADM_PHASE_F_MIGRATION",
            ]
            featured_definitions = [
                d for d in definitions
                if (d.get("workflow_code") if isinstance(d, dict) else d.workflow_code) in FEATURED_CODES
            ]
            featured_definitions.sort(
                key=lambda d: FEATURED_CODES.index(
                    d.get("workflow_code") if isinstance(d, dict) else d.workflow_code
                ) if (d.get("workflow_code") if isinstance(d, dict) else d.workflow_code) in FEATURED_CODES else 99
            )

            # WFT-061: Group definitions by category for the dashboard
            _GROUP_ORDER = [
                ("portfolio_management", "Transformation Programme Workflows",
                 "Active digital transformation work — SAP, Salesforce, cloud migrations, ARB submissions"),
                ("togaf_adm", "TOGAF ADM Phases",
                 "Architecture Development Method phase-by-phase documentation and governance"),
                ("vendor_management", "Operations",
                 "Vendor selection, gap analysis, and architecture review workflows"),
                ("gap_analysis", None, None),
                ("architecture_review", None, None),
            ]
            _cat_to_group: dict = {}
            for cat, label, desc in _GROUP_ORDER:
                _cat_to_group[cat] = (label, desc)
            _cat_to_group["vendor_management"] = ("Operations", "Vendor selection, gap analysis, and architecture review workflows")
            _cat_to_group["gap_analysis"] = ("Operations", None)
            _cat_to_group["architecture_review"] = ("Operations", None)

            workflow_groups: list = []
            _seen_labels: dict = {}
            _label_order = [
                ("Transformation Programme Workflows",
                 "Active digital transformation work — SAP, Salesforce, cloud migrations, ARB submissions",
                 ["portfolio_management"]),
                ("TOGAF ADM Phases",
                 "Architecture Development Method phase-by-phase documentation and governance",
                 ["togaf_adm"]),
                ("Operations",
                 "Vendor selection, gap analysis, and architecture review workflows",
                 ["vendor_management", "gap_analysis", "architecture_review"]),
            ]
            _defn_by_cat: dict = {}
            for _defn in definitions:
                _cat = _defn.get("workflow_category") if isinstance(_defn, dict) else getattr(_defn, "workflow_category", "")
                _defn_by_cat.setdefault(_cat, []).append(_defn)
            for _label, _desc, _cats in _label_order:
                _group_defns = []
                for _cat in _cats:
                    _group_defns.extend(_defn_by_cat.get(_cat, []))
                if _group_defns:
                    workflow_groups.append({"label": _label, "description": _desc, "definitions": _group_defns})

            # Resolve the real page URL per phase, or None.
            #
            # This used to test `"phase_d" in current_app.blueprints`, which is
            # true whenever the blueprint loads -- but those blueprints register
            # only JSON endpoints under /api/ea/phase-d/*. There is no page at
            # /ea-workflows/phase-d, so the guard passed, the template rendered a
            # hardcoded href, and the product linked to its own 404. Asking the
            # url_map for a rule that actually serves the path cannot make that
            # mistake, and a phase page added later lights up on its own.
            phase_links = {}
            for _letter in ("d", "e", "f", "g", "h"):
                _path = f"/ea-workflows/phase-{_letter}"
                _match = any(
                    str(_rule) == _path and "GET" in (_rule.methods or set())
                    for _rule in current_app.url_map.iter_rules()
                )
                phase_links[f"phase_{_letter}"] = _path if _match else None

            return render_template(
                "ea_workflows/dashboard.html",
                definitions=definitions,
                workflow_groups=workflow_groups,
                featured_definitions=featured_definitions,
                recent_instances=recent_instances,
                status_counts=status_counts,
                applications=applications,
                linkable_instances=linkable_instances,
                compliance_posture=compliance_posture,
                phase_counts=phase_counts,
                togaf_phases=engine.TOGAF_PHASES,
                phase_links=phase_links,
            )
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception("Error loading EA workflow dashboard: %s", e)
            # status_counts=None, not {}: `{{ status_counts.running or 0 }}`
            # turned an empty dict into a confident "0 running workflows".
            return render_template(
                "ea_workflows/dashboard.html",
                definitions=[],
                workflow_groups=[],
                featured_definitions=[],
                recent_instances=[],
                status_counts=None,
                applications=[],
                linkable_instances=[],
                phase_counts=None,
                togaf_phases=[],
                phase_links={"phase_d": None, "phase_e": None, "phase_f": None, "phase_g": None, "phase_h": None},
                error=str(e),
                load_error="Workflow status counts could not be read.",
            )

    @main_blueprint.route("/ea-workflows/definitions")
    @login_required
    def ea_workflows_definitions():
        """List all workflow definitions, optionally filtered by category or TOGAF phase."""
        try:
            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()

            category = request.args.get("category")
            phase = request.args.get("phase")
            definitions = engine.list_workflow_definitions(category=category, phase=phase)

            # Get unique categories
            categories = set()
            for defn in engine.list_workflow_definitions():
                if defn.workflow_category:
                    categories.add(defn.workflow_category)

            # Phase counts for TOGAF phase tabs
            phase_counts = engine.get_phase_counts()

            return render_template(
                "ea_workflows/definitions.html",
                definitions=definitions,
                categories=sorted(categories),
                selected_category=category,
                selected_phase=phase,
                phase_counts=phase_counts,
                togaf_phases=engine.TOGAF_PHASES,
            )
        except Exception:
            return jsonify({"error": "An internal error occurred"}), 500

    @main_blueprint.route("/ea-workflows/definitions/<workflow_code>")
    @login_required
    def ea_workflow_definition_detail(workflow_code):
        """Detail view for a workflow definition."""
        try:
            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            definition = engine.get_workflow_definition(workflow_code)

            if not definition:
                return render_template("errors/404.html"), 404

            # Get execution history
            from app.models.workflow_models import EAWorkflowInstance

            instances = (
                EAWorkflowInstance.query.filter_by(workflow_definition_id=definition.id)
                .order_by(EAWorkflowInstance.created_at.desc())
                .limit(10)
                .all()
            )

            applications = []
            if workflow_code == "ADM_PHASE_A_VISION":
                try:
                    from app.models.application_portfolio import ApplicationComponent

                    applications = (
                        ApplicationComponent.query.with_entities(
                            ApplicationComponent.id,
                            ApplicationComponent.name,
                        )
                        .order_by(ApplicationComponent.name)
                        .limit(500)
                        .all()
                    )
                except Exception as _app_err:
                    current_app.logger.debug(
                        "Phase A deliverable launcher applications unavailable: %s",
                        _app_err,
                    )

            return render_template(
                "ea_workflows/definition_detail.html",
                definition=definition,
                instances=instances,
                applications=applications,
            )
        except Exception:
            return jsonify({"error": "An internal error occurred"}), 500

    @main_blueprint.route("/ea-workflows/deliverables/vision")
    @login_required
    def ea_workflow_vision_deliverable_editor():
        """Render the Phase A Architecture Vision deliverable editor shell."""
        try:
            from app.models.application_portfolio import ApplicationComponent
            from app.services.ea_workflow_engine import EAWorkflowEngine
            from app.services.togaf_deliverable_handoff_service import (
                togaf_deliverable_handoff_service,
            )
            from app.services.togaf_deliverable_prefill_service import (
                togaf_deliverable_prefill_service,
            )
            from app.services.togaf_deliverable_readiness_service import (
                togaf_deliverable_readiness_service,
            )
            from app.services.togaf_deliverable_traceability_service import (
                togaf_deliverable_traceability_service,
            )

            workflow_code = request.args.get("workflow_code", "ADM_PHASE_A_VISION")
            project_name = (request.args.get("project_name") or "").strip()
            application_id = request.args.get("application_id", type=int)

            engine = EAWorkflowEngine()
            definition = engine.get_workflow_definition(workflow_code)
            if not definition or workflow_code != "ADM_PHASE_A_VISION":
                return render_template("errors/404.html"), 404

            selected_application = None
            if application_id:
                selected_application = db.session.get(ApplicationComponent, application_id)

            content = togaf_deliverable_prefill_service.build_architecture_vision_content(
                workflow_definition=definition,
                application=selected_application,
            )
            traceability_view = togaf_deliverable_traceability_service.build_traceability_view(
                content
            )
            readiness_summary = togaf_deliverable_readiness_service.build_readiness_view(
                content,
                traceability_view=traceability_view,
            )
            handoff_view = togaf_deliverable_handoff_service.build_handoff_view(
                content,
                readiness_view=readiness_summary,
            )

            deliverable_title = project_name or (
                f"{selected_application.name} Architecture Vision"
                if selected_application
                else "Architecture Vision"
            )

            return render_template(
                "ea_workflows/deliverable_editor.html",
                definition=definition,
                deliverable_title=deliverable_title,
                project_name=project_name,
                selected_application=selected_application,
                template_catalog=togaf_deliverable_prefill_service.list_templates(),
                template_content=content,
                readiness_summary=readiness_summary,
                traceability_view=traceability_view,
                handoff_view=handoff_view,
            )
        except Exception as e:
            current_app.logger.exception("Vision deliverable editor failed: %s", e)
            return jsonify({"error": "An internal error occurred"}), 500

    @main_blueprint.route("/ea-workflows/instance/<int:instance_id>")
    @login_required
    def ea_workflow_instance_detail(instance_id):
        """Detail view for a workflow instance."""
        try:
            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            status = engine.get_instance_status(instance_id)

            if status.get("error"):
                return render_template("errors/404.html"), 404

            instance_data = status["instance"]

            definition = None
            exec_steps = {}
            if instance_data.get("workflow_code"):
                definition = engine.get_workflow_definition(instance_data["workflow_code"])
            for step in status["steps"]:
                exec_steps[step["step_id"]] = step

            application = None
            context = instance_data.get("context") or {}
            app_id = context.get("application_id")
            if app_id:
                try:
                    from app.models.application_layer import ApplicationComponent
                    application = db.session.get(ApplicationComponent, int(app_id))
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug("Application lookup failed: %s", _e)

            review_report = None
            if instance_data.get("workflow_code") == "ARCH_REVIEW":
                review_report = _build_arch_review_report(exec_steps, instance_data)

            compliance_report = None
            if instance_data.get("workflow_code") == "COMPLIANCE_SCAN":
                try:
                    from app.models.workflow_artifacts import ComplianceScanReport

                    compliance_report = (
                        ComplianceScanReport.query.filter_by(
                            workflow_instance_id=instance_data.get("id")
                        )
                        .order_by(ComplianceScanReport.created_at.desc())
                        .first()
                    )
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug(
                        "Compliance report lookup failed: %s", _e
                    )

            vendor_report = None
            if instance_data.get("workflow_code") == "VENDOR_SELECTION":
                try:
                    from app.models.workflow_artifacts import VendorSelectionReport

                    vendor_report = (
                        VendorSelectionReport.query.filter_by(
                            workflow_instance_id=instance_data.get("id")
                        )
                        .order_by(VendorSelectionReport.created_at.desc())
                        .first()
                    )
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug(
                        "Vendor report lookup failed: %s", _e
                    )

            vision_doc = None
            if instance_data.get("workflow_code") == "ADM_PHASE_A_VISION":
                try:
                    from app.models.workflow_artifacts import ArchitectureVisionDocument

                    vision_doc = (
                        ArchitectureVisionDocument.query.filter_by(
                            workflow_instance_id=instance_data.get("id")
                        )
                        .order_by(ArchitectureVisionDocument.created_at.desc())
                        .first()
                    )
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug(
                        "Vision document lookup failed: %s", _e
                    )

            gap_report = None
            if instance_data.get("workflow_code") == "GAP_REMEDIATION":
                try:
                    from app.models.workflow_artifacts import GapRemediationReport

                    gap_report = (
                        GapRemediationReport.query.filter_by(
                            workflow_instance_id=instance_data.get("id")
                        )
                        .order_by(GapRemediationReport.created_at.desc())
                        .first()
                    )
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug(
                        "Gap remediation report lookup failed: %s", _e
                    )

            disposition_record = None
            if instance_data.get("workflow_code") == "APP_DISPOSITION":
                try:
                    from app.models.workflow_artifacts import ApplicationDispositionRecord
                    disposition_record = (
                        ApplicationDispositionRecord.query.filter_by(
                            workflow_instance_id=instance_data.get("id")
                        )
                        .order_by(ApplicationDispositionRecord.created_at.desc())
                        .first()
                    )
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug("Disposition record lookup failed: %s", _e)

            migration_scope = None
            if instance_data.get("workflow_code") == "PLATFORM_MIGRATION_SCOPING":
                try:
                    from app.models.workflow_artifacts import PlatformMigrationScope
                    migration_scope = (
                        PlatformMigrationScope.query.filter_by(
                            workflow_instance_id=instance_data.get("id")
                        )
                        .order_by(PlatformMigrationScope.created_at.desc())
                        .first()
                    )
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug("Migration scope lookup failed: %s", _e)

            arb_pack = None
            if instance_data.get("workflow_code") == "ARB_PACK_GENERATION":
                try:
                    from app.models.workflow_artifacts import ARBSubmissionPack
                    arb_pack = (
                        ARBSubmissionPack.query.filter_by(
                            workflow_instance_id=instance_data.get("id")
                        )
                        .order_by(ARBSubmissionPack.created_at.desc())
                        .first()
                    )
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug("ARB pack lookup failed: %s", _e)

            investment_plan = None
            if instance_data.get("workflow_code") == "CAPABILITY_INVESTMENT_PLANNING":
                try:
                    from app.models.workflow_artifacts import CapabilityInvestmentPlan
                    investment_plan = (
                        CapabilityInvestmentPlan.query.filter_by(
                            workflow_instance_id=instance_data.get("id")
                        )
                        .order_by(CapabilityInvestmentPlan.created_at.desc())
                        .first()
                    )
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug("Investment plan lookup failed: %s", _e)

            impact_register = None
            if instance_data.get("workflow_code") == "INTEGRATION_IMPACT_ASSESSMENT":
                try:
                    from app.models.workflow_artifacts import IntegrationImpactRegister
                    impact_register = (
                        IntegrationImpactRegister.query.filter_by(
                            workflow_instance_id=instance_data.get("id")
                        )
                        .order_by(IntegrationImpactRegister.created_at.desc())
                        .first()
                    )
                except Exception as _e:
                    import logging
                    logging.getLogger(__name__).debug("Impact register lookup failed: %s", _e)

            from app.models.workflow_models import WorkflowRunWatcher
            is_watched = WorkflowRunWatcher.query.filter_by(
                workflow_instance_id=instance_id, user_id=current_user.id
            ).first() is not None

            # EAW-004: Fetch live ArchiMate elements produced/consumed by this instance
            archimate_elements = []
            try:
                from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
                archimate_elements = WorkflowArchiMateContextService().get_instance_elements(instance_id)
            except Exception as _ae:
                import logging
                logging.getLogger(__name__).debug("ArchiMate elements lookup failed: %s", _ae)

            return render_template(
                "ea_workflows/instance_detail.html",
                instance=instance_data,
                steps=status["steps"],
                definition=definition,
                exec_steps=exec_steps,
                application=application,
                review_report=review_report,
                compliance_report=compliance_report,
                vendor_report=vendor_report,
                gap_report=gap_report,
                vision_doc=vision_doc,
                disposition_record=disposition_record,
                migration_scope=migration_scope,
                arb_pack=arb_pack,
                investment_plan=investment_plan,
                impact_register=impact_register,
                is_watched=is_watched,
                archimate_elements=archimate_elements,
            )
        except Exception as e:
            import traceback
            from flask import current_app
            current_app.logger.error(
                "ea_workflow_instance_detail error for instance %s: %s\n%s",
                instance_id, str(e), traceback.format_exc()
            )
            return jsonify({"error": "An internal error occurred"}), 500

    @main_blueprint.route("/ea-workflows/approvals")
    @login_required
    def ea_workflows_approvals():
        """View all workflows waiting for approval."""
        try:
            from datetime import datetime

            from sqlalchemy.orm import joinedload

            from app.models.workflow_models import EAWorkflowInstance
            from app.utils.pagination import get_pagination_params

            page, per_page = get_pagination_params()

            pagination = (
                EAWorkflowInstance.query.options(joinedload(EAWorkflowInstance.definition))
                .filter_by(status="waiting_approval")
                .order_by(EAWorkflowInstance.approval_requested_at.desc())
                .paginate(page=page, per_page=per_page, error_out=False)
            )

            return render_template(
                "ea_workflows/approvals.html",
                pending_approvals=pagination.items,
                pagination=pagination,
                per_page=per_page,
                now=datetime.utcnow(),
            )
        except Exception:
            return jsonify({"error": "An internal error occurred"}), 500

    @main_blueprint.route("/ea-workflows/schedules")
    @login_required
    def ea_workflows_schedules():
        """View all workflow schedules."""
        try:
            from sqlalchemy.orm import joinedload

            from app.models.workflow_models import EAWorkflowSchedule
            from app.services.ea_workflow_engine import EAWorkflowEngine
            from app.utils.pagination import get_pagination_params

            page, per_page = get_pagination_params()

            pagination = (
                EAWorkflowSchedule.query
                .options(joinedload(EAWorkflowSchedule.definition))
                .order_by(EAWorkflowSchedule.next_run_at)
                .paginate(page=page, per_page=per_page, error_out=False)
            )
            engine = EAWorkflowEngine()
            definitions = engine.list_workflow_definitions()

            return render_template(
                "ea_workflows/schedules.html",
                schedules=pagination.items,
                pagination=pagination,
                per_page=per_page,
                definitions=definitions,
            )
        except Exception:
            return jsonify({"error": "An internal error occurred"}), 500

    @main_blueprint.route("/ea-workflows/journeys")
    @login_required
    def ea_workflows_journeys():
        """Cross-workflow journey view grouping instances by ADM iteration cycle."""
        try:
            from sqlalchemy.orm import joinedload

            from app.models.workflow_models import EAWorkflowDefinition, EAWorkflowInstance

            instances = (
                EAWorkflowInstance.query
                .options(joinedload(EAWorkflowInstance.definition))
                .join(EAWorkflowDefinition)
                .filter(EAWorkflowDefinition.workflow_code.like("ADM_%"))
                .order_by(
                    EAWorkflowInstance.iteration_number,
                    EAWorkflowInstance.created_at,
                )
                .all()
            )

            # Group by iteration_number → list of phase records
            from collections import defaultdict
            iterations = defaultdict(list)
            for inst in instances:
                iterations[inst.iteration_number].append(inst)

            journeys = []
            for iter_num in _sort_iteration_keys(iterations.keys()):
                phases = iterations[iter_num]
                completed = sum(1 for p in phases if p.status == "completed")
                total = len(phases)
                progress_pct = round(completed / total * 100) if total else 0

                journeys.append({
                    "iteration_number": iter_num,
                    "phases": [
                        {
                            "id": p.id,
                            "workflow_code": p.definition.workflow_code if p.definition else "unknown",
                            "workflow_name": p.definition.workflow_name if p.definition else "Unknown",
                            "togaf_phase": p.definition.adm_phase if p.definition else None,
                            "status": p.status,
                            "created_at": p.created_at.isoformat() if p.created_at else None,
                            "completed_at": p.completed_at.isoformat() if p.completed_at else None,
                        }
                        for p in phases
                    ],
                    "progress_pct": progress_pct,
                    "total_phases": total,
                    "completed_phases": completed,
                })

            return jsonify({"success": True, "journeys": journeys})

        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Error loading workflow journeys: %s", e, exc_info=True)
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    # =========================================================================
    # API ENDPOINTS - WORKFLOW DEFINITIONS
    # =========================================================================

    @main_blueprint.route("/api/ea-workflows/definitions")
    @login_required
    def api_ea_workflow_definitions():
        """API: List all workflow definitions, optionally filtered by category or phase."""
        try:
            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            category = request.args.get("category")
            phase = request.args.get("phase")
            definitions = engine.list_workflow_definitions(category=category, phase=phase)

            return jsonify(
                {
                    "success": True,
                    "definitions": [d.to_dict() for d in definitions],
                    "total": len(definitions),
                }
            )
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/phase-counts")
    @login_required
    def api_ea_workflow_phase_counts():
        """API: Return count of active workflow definitions per TOGAF phase."""
        try:
            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            counts = engine.get_phase_counts()
            phases = [
                {"phase": p, "name": n, "count": counts.get(p, 0)}
                for p, n in engine.TOGAF_PHASES
            ]
            return jsonify({"success": True, "phase_counts": counts, "phases": phases})
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/definitions/<workflow_code>")
    @login_required
    def api_ea_workflow_definition(workflow_code):
        """API: Get a specific workflow definition."""
        try:
            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            definition = engine.get_workflow_definition(workflow_code)

            if not definition:
                return jsonify({"success": False, "error": "Workflow not found"}), 404

            return jsonify({"success": True, "definition": definition.to_dict()})
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/definitions", methods=["POST"])
    @login_required
    def api_create_workflow_definition():
        """API: Create a new workflow definition."""
        try:
            data = request.get_json()

            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            definition = engine.create_workflow_definition(
                workflow_code=data["workflow_code"],
                workflow_name=data["workflow_name"],
                workflow_category=data["workflow_category"],
                steps=data["steps"],
                workflow_description=data.get("workflow_description"),
                workflow_type=data.get("workflow_type", "sequential"),
                automation_level=data.get("automation_level", "assisted"),
                created_by_id=current_user.id,
            )

            return jsonify({"success": True, "definition": definition.to_dict()})
        except KeyError as e:
            return jsonify({"success": False, "error": f"Missing required field: {e}"}), 400
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/seed-defaults", methods=["POST"])
    @platform_admin_required
    def api_seed_workflow_defaults():
        """API: Seed default workflow definitions (platform admin only).

        R2-3 (PR 428 round 3): ``EAWorkflowDefinition`` carries no
        organisation column -- this writes global, platform-wide rows, so
        "admin anywhere" (every self-registered org admin) was never the
        right gate. Scoped to platform admins.
        """
        try:
            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            created = engine.seed_default_workflows()

            return jsonify(
                {
                    "success": True,
                    "created_workflows": [w.to_dict() for w in created],
                    "total_created": len(created),
                }
            )
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    # =========================================================================
    # API ENDPOINTS - WORKFLOW EXECUTION
    # =========================================================================

    @main_blueprint.route("/api/ea-workflows/start", methods=["POST"])
    @login_required
    def api_start_workflow():
        """API: Start a new workflow instance."""
        try:
            data = request.get_json() or {}

            workflow_code = data.get("workflow_code")
            context = dict(data.get("context", {}))

            if not workflow_code:
                return jsonify({"success": False, "error": "workflow_code is required"}), 400

            # Merge top-level fields into context for backward compatibility
            for key in data:
                if key not in ("workflow_code", "context"):
                    context.setdefault(key, data[key])

            # VENDOR_SELECTION: require requirements input
            if workflow_code == "VENDOR_SELECTION":
                has_doc = context.get("requirements_doc_id") is not None
                has_text = bool((context.get("requirements_text") or "").strip())
                if not (has_doc or has_text):
                    return jsonify({
                        "success": False,
                        "error": "VENDOR_SELECTION requires either requirements_doc_id or requirements_text",
                    }), 400

            # Application-scoped workflows require application_id
            application_scoped = ("APP_ONBOARDING", "ARCH_REVIEW")
            if workflow_code in application_scoped:
                app_id = context.get("application_id")
                if app_id is None or (isinstance(app_id, str) and not app_id.strip()):
                    return jsonify({
                        "success": False,
                        "error": f"{workflow_code} requires application_id",
                    }), 400

            # ADM Preliminary: require org_scope
            if workflow_code == "ADM_PRELIMINARY":
                if not (context.get("org_scope") or "").strip():
                    return jsonify({
                        "success": False,
                        "error": "ADM Preliminary requires organisation scope",
                    }), 400

            # ADM Phase A: require project_name
            if workflow_code == "ADM_PHASE_A_VISION":
                if not (context.get("project_name") or "").strip():
                    return jsonify({
                        "success": False,
                        "error": "ADM Phase A requires a project/initiative name",
                    }), 400

            # ADM Phase H: require change_trigger + change_description
            if workflow_code == "ADM_PHASE_H_CHANGE":
                if not (context.get("change_trigger") or "").strip():
                    return jsonify({
                        "success": False,
                        "error": "ADM Phase H requires a change trigger",
                    }), 400
                if not (context.get("change_description") or "").strip():
                    return jsonify({
                        "success": False,
                        "error": "ADM Phase H requires a change description",
                    }), 400

            # Requirements Management: require requirements_text
            if workflow_code == "ADM_REQUIREMENTS_MGMT":
                if not (context.get("requirements_text") or "").strip():
                    return jsonify({
                        "success": False,
                        "error": "Requirements Management requires a requirement description",
                    }), 400

            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            instance = engine.start_workflow(
                workflow_code=workflow_code,
                context=context,
                triggered_by="manual",
                user_id=current_user.id,
            )

            return jsonify({"success": True, "instance": instance.to_dict()})
        except ValueError:
            return jsonify({"success": False, "error": "Invalid request parameters"}), 400
        except Exception as e:
            import traceback
            from flask import current_app
            current_app.logger.error("api_start_workflow error: %s\n%s", str(e), traceback.format_exc())
            return jsonify({"success": False, "error": str(e)}), 500

    @main_blueprint.route("/api/ea-workflows/instances/<int:instance_id>")
    @login_required
    def api_workflow_instance_status(instance_id):
        """API: Get workflow instance status."""
        try:
            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            status = engine.get_instance_status(instance_id)

            if status.get("error"):
                return jsonify({"success": False, "error": status["error"]}), 404

            from app.models.workflow_models import WorkflowRunWatcher
            is_watched = WorkflowRunWatcher.query.filter_by(
                workflow_instance_id=instance_id, user_id=current_user.id
            ).first() is not None

            return jsonify({"success": True, "is_watched": is_watched, **status})
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances/<int:instance_id>/resume", methods=["POST"])
    @login_required
    def api_resume_workflow(instance_id):
        """API: Resume a workflow after approval."""
        try:
            data = request.get_json() or {}
            approved_items = data.get("approved_items")

            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            instance = engine.resume_workflow(
                instance_id=instance_id, approved_items=approved_items, user_id=current_user.id
            )

            return jsonify({"success": True, "instance": instance.to_dict()})
        except ValueError:
            return jsonify({"success": False, "error": "Invalid request parameters"}), 400
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances/<int:instance_id>/cancel", methods=["POST"])
    @login_required
    def api_cancel_workflow(instance_id):
        """API: Cancel a running workflow."""
        try:
            data = request.get_json() or {}
            reason = data.get("reason")

            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            instance = engine.cancel_workflow(instance_id=instance_id, reason=reason)

            return jsonify({"success": True, "instance": instance.to_dict()})
        except ValueError:
            return jsonify({"success": False, "error": "Invalid request parameters"}), 400
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances/<int:instance_id>/reject", methods=["POST"])
    @login_required
    def api_reject_workflow(instance_id):
        """API: Reject a workflow at an approval step."""
        try:
            data = request.get_json() or {}
            reason = data.get("reason", "").strip()

            if not reason:
                return jsonify({"success": False, "error": "A rejection reason is required"}), 400

            from datetime import datetime

            from app import db
            from app.models.workflow_models import EAWorkflowInstance, EAWorkflowStepExecution

            instance = db.session.get(EAWorkflowInstance, instance_id)
            if not instance:
                return jsonify({"success": False, "error": "Instance not found"}), 404

            if instance.status != "waiting_approval":
                return jsonify({"success": False, "error": "Workflow is not waiting for approval"}), 400

            if instance.pending_approval_step_id:
                pending_step = EAWorkflowStepExecution.query.filter_by(
                    instance_id=instance_id,
                    step_id=instance.pending_approval_step_id,
                ).first()
                if pending_step:
                    pending_step.status = "failed"
                    pending_step.approval_status = "rejected"
                    pending_step.approved_by_id = current_user.id
                    pending_step.approved_at = datetime.utcnow()
                    pending_step.error_message = f"Rejected: {reason}"

            instance.status = "failed"
            instance.error_message = f"Rejected by {current_user.full_name() or current_user.email}: {reason}"
            instance.pending_approval_step_id = None
            db.session.commit()

            return jsonify({"success": True, "instance": instance.to_dict()})
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances/<int:instance_id>/artifacts")
    @login_required
    def api_workflow_artifacts(instance_id):
        """API: Retrieve workflow output artifacts for an instance."""
        try:
            from app.models.workflow_models import EAWorkflowInstance
            from app.models.workflow_artifacts import (
                ArchitectureVisionDocument,
                ArchitectureReviewFinding,
                VendorSelectionReport,
                ComplianceScanReport,
                WorkflowCompletionSummary,
                GapRemediationReport,
            )

            instance = db.session.get(EAWorkflowInstance, instance_id)
            if not instance:
                return jsonify({"success": False, "error": "Instance not found"}), 404

            all_artifacts = []
            for model in (
                ArchitectureVisionDocument,
                ArchitectureReviewFinding,
                VendorSelectionReport,
                ComplianceScanReport,
                WorkflowCompletionSummary,
                GapRemediationReport,
            ):
                try:
                    q = model.query.filter_by(workflow_instance_id=instance_id)  # model-safety-ok
                    for row in q.all():  # model-safety-ok
                        all_artifacts.append(row.to_dict())
                except Exception:
                    db.session.rollback()

            all_artifacts.sort(key=lambda a: (a.get("created_at") or "")[:19])
            return jsonify({"success": True, "artifacts": all_artifacts, "total": len(all_artifacts)})
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances/<int:instance_id>/artifacts/export")
    @login_required
    def api_export_instance_artifacts(instance_id):
        """API: Export all artifacts for a workflow instance as a downloadable JSON package (T3-1).

        Returns a downloadable JSON file containing the instance metadata,
        step execution records, and all associated artifact records.
        Used by governance boards for offline review and audit trails.
        """
        import datetime as _dt
        import json
        from flask import Response
        from app.models.workflow_models import EAWorkflowInstance, EAWorkflowStepExecution

        instance = db.session.get(EAWorkflowInstance, instance_id)
        if not instance:
            return jsonify({"error": "Not found"}), 404

        steps = (
            EAWorkflowStepExecution.query
            .filter_by(instance_id=instance_id)
            .order_by(EAWorkflowStepExecution.step_index)
            .all()
        )

        artifacts = []
        try:
            from app.models.workflow_artifacts import (
                ArchitectureReviewFinding,
                ArchitectureVisionDocument,
                ChangeManagementRecord,
                ComplianceGovernanceReport,
                ComplianceScanReport,
                GapRemediationReport,
                MigrationPlanDocument,
                RequirementsTraceabilityMatrix,
                VendorSelectionReport,
                WorkflowCompletionSummary,
            )
            for Model in (
                ArchitectureVisionDocument,
                ArchitectureReviewFinding,
                VendorSelectionReport,
                ComplianceScanReport,
                MigrationPlanDocument,
                ComplianceGovernanceReport,
                ChangeManagementRecord,
                RequirementsTraceabilityMatrix,
                GapRemediationReport,
                WorkflowCompletionSummary,
            ):
                try:
                    for row in Model.query.filter_by(workflow_instance_id=instance_id).all():  # model-safety-ok
                        artifacts.append(row.to_dict())
                except Exception:
                    db.session.rollback()
        except Exception as e:
            current_app.logger.warning("Artifact collection partial: %s", e)

        payload = {
            "export_version": "1.0",
            "exported_at": _dt.datetime.utcnow().isoformat() + "Z",
            "instance": instance.to_dict(),
            "steps": [s.to_dict() for s in steps],
            "artifacts": artifacts,
        }

        filename = f"ea_workflow_{instance.instance_code}.json"
        return Response(
            json.dumps(payload, indent=2, default=str),
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


    @login_required
    def api_bulk_approve_workflows():
        """API: Bulk approve multiple workflows."""
        try:
            data = request.get_json() or {}
            instance_ids = data.get("instance_ids", [])

            if not instance_ids:
                return jsonify({"success": False, "error": "No instance IDs provided"}), 400

            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            approved_count = 0
            errors = []

            for iid in instance_ids:
                try:
                    engine.resume_workflow(
                        instance_id=iid, approved_items=None, user_id=current_user.id
                    )
                    approved_count += 1
                except Exception as e:
                    errors.append({"instance_id": iid, "error": str(e)})

            return jsonify({
                "success": True,
                "approved_count": approved_count,
                "errors": errors,
            })
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances/<int:instance_id>/retry", methods=["POST"])
    @login_required
    def api_retry_workflow(instance_id):
        """API: Retry a failed workflow from the failed step."""
        try:
            from app import db
            from app.models.workflow_models import EAWorkflowInstance

            instance = db.session.get(EAWorkflowInstance, instance_id)
            if not instance:
                return jsonify({"success": False, "error": "Instance not found"}), 404

            if instance.status != "failed":
                return jsonify({"success": False, "error": "Only failed workflows can be retried"}), 400

            instance.status = "pending"
            instance.error_message = None
            db.session.commit()

            from app.services.ea_workflow_engine import EAWorkflowEngine
            engine = EAWorkflowEngine()
            engine._execute_workflow(instance)

            return jsonify({"success": True, "instance": instance.to_dict()})
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances")
    @login_required
    def api_list_workflow_instances():
        """API: List workflow instances with filtering, sorting, and pagination."""
        try:
            from sqlalchemy.orm import joinedload

            from app.models.workflow_models import EAWorkflowDefinition, EAWorkflowInstance

            status = request.args.get("status")
            workflow_code = request.args.get("workflow_code")
            application_id = request.args.get("application_id", type=int)
            q = request.args.get("q", "").strip()
            page = safe_int_arg('page', 1, minimum=1)
            per_page = min(safe_int_arg('per_page', 20, minimum=1, maximum=500), 100)
            sort_by = request.args.get("sort_by", "created_at")
            sort_order = request.args.get("sort_order", "desc")

            query = EAWorkflowInstance.query.options(
                joinedload(EAWorkflowInstance.definition)
            )

            if status:
                query = query.filter_by(status=status)

            if application_id:
                query = query.filter(
                    EAWorkflowInstance.context['application_id'].as_integer() == application_id
                )

            if workflow_code:
                query = query.join(EAWorkflowDefinition).filter(
                    EAWorkflowDefinition.workflow_code == workflow_code
                )

            if q:
                query = query.filter(
                    EAWorkflowInstance.instance_code.ilike(f"%{q}%")
                )

            sort_col = getattr(EAWorkflowInstance, sort_by, EAWorkflowInstance.created_at)
            if sort_order == "asc":
                query = query.order_by(sort_col.asc())
            else:
                query = query.order_by(sort_col.desc())

            total = query.count()
            instances = query.offset((page - 1) * per_page).limit(per_page).all()

            return jsonify({
                "success": True,
                "instances": [i.to_dict() for i in instances],
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page,
            })
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    # =========================================================================
    # API ENDPOINTS - SCHEDULING
    # =========================================================================

    @main_blueprint.route("/api/ea-workflows/schedules", methods=["POST"])
    @login_required
    def api_create_schedule():
        """API: Create a workflow schedule."""
        try:
            data = request.get_json()

            from app.services.ea_workflow_engine import EAWorkflowEngine

            engine = EAWorkflowEngine()
            schedule = engine.create_schedule(
                workflow_code=data["workflow_code"],
                schedule_name=data["schedule_name"],
                schedule_type=data["schedule_type"],
                cron_expression=data.get("cron_expression"),
                time_of_day=data.get("time_of_day"),
                day_of_week=data.get("day_of_week"),
                day_of_month=data.get("day_of_month"),
                timezone=data.get("timezone", "UTC"),
                default_context=data.get("default_context", {}),
                created_by_id=current_user.id,
            )

            return jsonify({"success": True, "schedule": schedule.to_dict()})
        except KeyError as e:
            return jsonify({"success": False, "error": f"Missing required field: {e}"}), 400
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/schedules")
    @login_required
    def api_list_schedules():
        """API: List all workflow schedules."""
        try:
            from app.models.workflow_models import EAWorkflowSchedule

            schedules = EAWorkflowSchedule.query.order_by(EAWorkflowSchedule.next_run_at).all()

            return jsonify(
                {
                    "success": True,
                    "schedules": [s.to_dict() for s in schedules],
                    "total": len(schedules),
                }
            )
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/schedules/<int:schedule_id>/toggle", methods=["POST"])
    @login_required
    def api_toggle_schedule(schedule_id):
        """API: Enable or disable a schedule."""
        try:
            from app import db
            from app.models.workflow_models import EAWorkflowSchedule

            schedule = db.session.get(EAWorkflowSchedule, schedule_id)
            if not schedule:
                return jsonify({"success": False, "error": "Schedule not found"}), 404

            schedule.is_active = not schedule.is_active
            db.session.commit()

            return jsonify({"success": True, "schedule": schedule.to_dict()})
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    # =========================================================================
    # API ENDPOINTS - ANALYTICS
    # =========================================================================

    @main_blueprint.route("/api/ea-workflows/analytics")
    @login_required
    def api_workflow_analytics():
        """API: Get workflow execution analytics with time-series data."""
        try:
            from datetime import datetime, timedelta

            from sqlalchemy import cast, func, Date

            from app import db
            from app.models.workflow_models import (
                EAWorkflowDefinition,
                EAWorkflowInstance,
            )

            status_counts = (
                EAWorkflowInstance.query.with_entities(
                    EAWorkflowInstance.status, func.count(EAWorkflowInstance.id)
                )
                .group_by(EAWorkflowInstance.status)
                .all()
            )

            avg_durations = (
                db.session.query(
                    EAWorkflowDefinition.workflow_code,
                    func.avg(EAWorkflowInstance.duration_seconds),
                )
                .join(EAWorkflowInstance)
                .filter(EAWorkflowInstance.duration_seconds.isnot(None))
                .group_by(EAWorkflowDefinition.workflow_code)
                .all()
            )

            execution_counts = (
                db.session.query(
                    EAWorkflowDefinition.workflow_code,
                    EAWorkflowDefinition.workflow_name,
                    func.count(EAWorkflowInstance.id),
                )
                .join(EAWorkflowInstance)
                .group_by(EAWorkflowDefinition.workflow_code, EAWorkflowDefinition.workflow_name)
                .order_by(func.count(EAWorkflowInstance.id).desc())
                .limit(10)
                .all()
            )

            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            daily_counts = (
                db.session.query(
                    cast(EAWorkflowInstance.created_at, Date).label("date"),
                    func.count(EAWorkflowInstance.id),
                )
                .filter(EAWorkflowInstance.created_at >= thirty_days_ago)
                .group_by(cast(EAWorkflowInstance.created_at, Date))
                .order_by(cast(EAWorkflowInstance.created_at, Date))
                .all()
            )

            total_instances = EAWorkflowInstance.query.count()
            completed_instances = EAWorkflowInstance.query.filter_by(status="completed").count()
            success_rate = (
                round(completed_instances / total_instances * 100, 1) if total_instances > 0 else 0
            )

            avg_approval_time = None
            try:
                approval_result = (
                    db.session.query(
                        func.avg(
                            func.extract('epoch', EAWorkflowInstance.completed_at)
                            - func.extract('epoch', EAWorkflowInstance.approval_requested_at)
                        )
                    )
                    .filter(
                        EAWorkflowInstance.approval_requested_at.isnot(None),
                        EAWorkflowInstance.completed_at.isnot(None),
                    )
                    .scalar()
                )
                if approval_result:
                    avg_approval_time = round(float(approval_result) / 3600, 1)
            except Exception as _e:
                import logging
                logging.getLogger(__name__).debug("Approval time avg unavailable: %s", _e)

            return jsonify({
                "success": True,
                "analytics": {
                    "status_counts": dict(status_counts),
                    "avg_durations": {
                        code: round(float(avg), 1) if avg else 0 for code, avg in avg_durations
                    },
                    "most_executed": [
                        {"code": code, "name": name, "count": count}
                        for code, name, count in execution_counts
                    ],
                    "daily_executions": [
                        {"date": str(d), "count": c} for d, c in daily_counts
                    ],
                    "total_instances": total_instances,
                    "success_rate": success_rate,
                    "avg_approval_hours": avg_approval_time,
                },
            })
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances/<int:instance_id>/watch", methods=["POST", "DELETE"])
    @login_required
    def api_toggle_watch(instance_id):
        """Toggle watch status for a workflow run."""
        try:
            from app.models.workflow_models import WorkflowRunWatcher, EAWorkflowInstance

            instance = db.session.get(EAWorkflowInstance, instance_id)
            if not instance:
                return jsonify({"success": False, "error": "Instance not found"}), 404

            existing = WorkflowRunWatcher.query.filter_by(
                workflow_instance_id=instance_id, user_id=current_user.id
            ).first()

            if request.method == "DELETE" or (request.method == "POST" and existing):
                if existing:
                    db.session.delete(existing)
                    db.session.commit()
                return jsonify({"success": True, "watching": False})
            else:
                watcher = WorkflowRunWatcher(
                    workflow_instance_id=instance_id, user_id=current_user.id
                )
                db.session.add(watcher)
                db.session.commit()
                return jsonify({"success": True, "watching": True})
        except Exception:
            db.session.rollback()
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances/<int:instance_id>/run-history")
    @login_required
    def api_run_history(instance_id):
        """Get recent run durations for the same workflow type."""
        try:
            from app.models.workflow_models import EAWorkflowInstance

            instance = db.session.get(EAWorkflowInstance, instance_id)
            if not instance:
                return jsonify({"success": False, "error": "Instance not found"}), 404

            past_runs = (
                EAWorkflowInstance.query
                .filter(
                    EAWorkflowInstance.workflow_definition_id == instance.workflow_definition_id,
                    EAWorkflowInstance.id != instance_id,
                    EAWorkflowInstance.status == "completed",
                    EAWorkflowInstance.duration_seconds.isnot(None),
                )
                .order_by(EAWorkflowInstance.completed_at.desc())
                .limit(10)
                .all()
            )

            durations = [r.duration_seconds for r in past_runs]
            avg_duration = round(sum(durations) / len(durations)) if durations else None
            total_runs = (
                EAWorkflowInstance.query
                .filter_by(workflow_definition_id=instance.workflow_definition_id)
                .count()
            )

            return jsonify({
                "success": True,
                "durations": durations,
                "avg_duration": avg_duration,
                "total_runs": total_runs,
            })
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/instances/export")
    @login_required
    def api_export_workflow_instances():
        """API: Export workflow instances as CSV."""
        try:
            import csv
            import io

            from flask import Response
            from sqlalchemy.orm import joinedload

            from app.models.workflow_models import EAWorkflowInstance

            status = request.args.get("status")
            query = EAWorkflowInstance.query.options(
                joinedload(EAWorkflowInstance.definition)
            )
            if status:
                query = query.filter_by(status=status)
            instances = query.order_by(EAWorkflowInstance.created_at.desc()).limit(1000).all()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "Instance Code", "Workflow", "Status", "Progress %",
                "Started", "Completed", "Duration (s)", "Triggered By",
            ])
            for inst in instances:
                defn = inst.definition
                writer.writerow([
                    inst.instance_code,
                    defn.workflow_name if defn else "",
                    inst.status,
                    inst.progress_percent or 0,
                    inst.started_at.isoformat() if inst.started_at else "",
                    inst.completed_at.isoformat() if inst.completed_at else "",
                    inst.duration_seconds or "",
                    inst.triggered_by or "",
                ])

            output.seek(0)
            return Response(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment; filename=ea_workflow_instances.csv"},
            )
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/notifications")
    @login_required
    def api_workflow_notifications():
        """API: Get workflow notifications for current user."""
        try:
            from app.models.workflow_models import EAWorkflowNotification

            notifications = (
                EAWorkflowNotification.query
                .filter_by(recipient_id=current_user.id)
                .order_by(EAWorkflowNotification.sent_at.desc())
                .limit(50)
                .all()
            )
            unread_count = EAWorkflowNotification.query.filter_by(
                recipient_id=current_user.id, is_read=False
            ).count()

            return jsonify({
                "success": True,
                "notifications": [n.to_dict() for n in notifications],
                "unread_count": unread_count,
            })
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/notifications/<int:notif_id>/read", methods=["POST"])
    @login_required
    def api_mark_notification_read(notif_id):
        """API: Mark a notification as read."""
        try:
            from app import db
            from app.models.workflow_models import EAWorkflowNotification
            from datetime import datetime

            notif = db.session.get(EAWorkflowNotification, notif_id)
            if not notif or notif.recipient_id != current_user.id:
                return jsonify({"success": False, "error": "Not found"}), 404

            notif.is_read = True
            notif.read_at = datetime.utcnow()
            db.session.commit()

            return jsonify({"success": True})
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea/workflow-adm-lifecycle")
    @login_required
    def api_ea_workflow_adm_lifecycle():
        """EAW-005: Return ADM phase A-H lifecycle status for an optional architecture_id."""
        try:
            from app.services.adm_phase_gate_service import ADMPhaseGateService
            from app.models.workflow_models import EAWorkflowInstance, EAWorkflowDefinition
            from app import db
            from sqlalchemy import func

            architecture_id = request.args.get("architecture_id", type=int)
            gate_svc = ADMPhaseGateService()
            phase_summary = gate_svc.get_phase_summary(architecture_id)

            # Count completed ADM instances per phase
            phase_code_map = {
                "A": "ADM_PHASE_A_VISION",
                "B": "ADM_PHASE_B_BUSINESS",
                "C": "ADM_PHASE_C_IS",
                "D": "ADM_PHASE_D_TECH",
                "E": "ADM_PHASE_E_OPPORTUNITIES",
                "F": "ADM_PHASE_F_MIGRATION",
                "G": "ADM_PHASE_G_GOVERNANCE",
                "H": "ADM_PHASE_H_CHANGE",
            }
            instance_counts: dict = {}
            try:
                rows = (
                    db.session.query(EAWorkflowDefinition.workflow_code, func.count(EAWorkflowInstance.id))
                    .join(EAWorkflowInstance, EAWorkflowInstance.workflow_definition_id == EAWorkflowDefinition.id)
                    .filter(EAWorkflowDefinition.workflow_code.in_(list(phase_code_map.values())))
                    .group_by(EAWorkflowDefinition.workflow_code)
                    .all()
                )
                code_to_count = {code: cnt for code, cnt in rows}
                for phase, code in phase_code_map.items():
                    instance_counts[phase] = code_to_count.get(code, 0)
            except Exception as _cnt_err:
                import logging
                logging.getLogger(__name__).debug("ADM instance count query failed: %s", _cnt_err)

            phases_out = []
            for ps in phase_summary:
                phase_letter = ps.get("phase", "")
                phases_out.append({
                    **ps,
                    "workflow_code": phase_code_map.get(phase_letter, ""),
                    "instance_count": instance_counts.get(phase_letter, 0),
                })

            return jsonify({"phases": phases_out})
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("ADM lifecycle API error: %s", e)
            return jsonify({"phases": [], "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea-workflows/run-due-schedules", methods=["POST"])
    @platform_admin_required
    def api_run_due_schedules():
        """API: Manually trigger execution of all due workflow schedules (platform admin only).

        R2-3 (PR 428 round 3): this executes every organisation's due
        schedules in one batch -- "admin anywhere" let any self-registered
        org admin trigger it for organisations they have no standing in.
        Scoped to platform admins.
        """
        try:
            from app.services.ea_workflow_engine import EAWorkflowEngine
            engine = EAWorkflowEngine()
            result = engine.run_due_schedules()
            return jsonify({"success": True, **result})
        except Exception:
            return jsonify({"success": False, "error": "An internal error occurred"}), 500

    @main_blueprint.route("/api/ea/phases/archimate-summary", methods=["GET"])
    @login_required
    def api_ea_phases_archimate_summary():
        """AV-008: Return per-ADM-phase ArchiMate element counts for dashboard badges."""
        try:
            from app.models.archimate_core import ArchiMateElement
            rows = ArchiMateElement.query.with_entities(ArchiMateElement.layer).all()
            phase_layer_map = {
                "A": "motivation",
                "B": "business",
                "C": "application",
                "D": "technology",
            }
            layer_counts = {}
            for row in rows:
                layer = (row.layer or "unknown").lower()
                layer_counts[layer] = layer_counts.get(layer, 0) + 1
            phases = []
            for phase, layer in phase_layer_map.items():
                phases.append({"phase": phase, "layer": layer, "element_count": layer_counts.get(layer, 0)})
            for phase in ["E", "F", "G", "H"]:
                phases.append({"phase": phase, "layer": "", "element_count": 0})
            return jsonify({"phases": phases})
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("AV-008 archimate summary error: %s", e)
            return jsonify({"success": False, "error": "Failed to load ArchiMate phase summary"}), 500

    @main_blueprint.route("/ea-workflows/phase/<string:phase_code>/viewpoint", methods=["GET"])
    @login_required
    def ea_phase_viewpoint(phase_code: str):
        """AV-009: Render the live ArchiMate viewpoint detail page for a given ADM phase."""
        from app.services.phase_viewpoint_binding_service import PhaseViewpointBindingService
        from app.services.archimate_viewpoint_render_service import ArchimateViewpointRenderService
        from app.services.workflow_archimate_context_service import WorkflowArchiMateContextService
        _PHASE_KEY_MAP = {
            "A": "ADM_PHASE_A_VISION",
            "B": "ADM_PHASE_B_BUSINESS",
            "C": "ADM_PHASE_C_IS",
            "D": "ADM_PHASE_D_TECH",
            "E": "ADM_PHASE_E_OPPORTUNITIES",
            "F": "ADM_PHASE_F_MIGRATION",
            "G": "ADM_PHASE_G_GOVERNANCE",
            "H": "ADM_PHASE_H_CHANGE",
        }
        binding_key = _PHASE_KEY_MAP.get(phase_code.upper(), phase_code)
        try:
            binding_svc = PhaseViewpointBindingService()
            binding = binding_svc.get_binding(binding_key) or {}
            full_phase_name = binding.get("phase_name", f"Phase {phase_code.upper()} Viewpoint")
            phase_name = binding.get("viewpoint_name", full_phase_name)
            input_types = binding_svc.get_input_element_types(binding_key)
            derived_types = binding_svc.get_derived_element_types(binding_key)
            ctx_svc = WorkflowArchiMateContextService()
            phase_elements = ctx_svc.get_phase_elements(phase_code.upper())
            element_ids = [e["id"] for e in phase_elements] if phase_elements else []
            render_svc = ArchimateViewpointRenderService()
            viewpoint = render_svc.render_viewpoint(binding_key, element_ids)
            elements_by_layer = viewpoint.get("elements_by_layer", {})
            elements = [e for layer_elems in elements_by_layer.values() for e in layer_elems]
            return render_template(
                "ea_workflows/phase_viewpoint.html",
                phase_code=phase_code,
                phase_name=full_phase_name,
                viewpoint_name=phase_name,
                primary_layer=binding.get("primary_layer", ""),
                archimate_concern=binding.get("archimate_concern", ""),
                input_types=input_types or [],
                derived_types=derived_types or [],
                elements=elements,
                element_count=len(elements),
                relationship_count=viewpoint.get("relationship_count", 0),
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Error loading phase viewpoint %s", phase_code
            )
            return render_template(
                "ea_workflows/phase_viewpoint.html",
                phase_code=phase_code,
                phase_name=phase_code.replace("_", " ").title(),
                viewpoint_name="",
                primary_layer="",
                archimate_concern="",
                input_types=[],
                derived_types=[],
                elements=[],
                element_count=None,
                relationship_count=None,
                load_error="The phase viewpoint could not be read.",
            )
