"""
Integration Workflow Routes - UI Controller for Intelligent Integration

Provides web interface for managing and monitoring EA workflows powered by
the EAWorkflowEngine service.

Features:
- Workflow dashboard with all available workflows
- Active workflow instance monitoring
- Workflow execution controls (start, resume, approve)
- Step-by-step workflow progress visualization
"""

from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException

from app import db
from app.decorators import audit_log
from app.services.ea_workflow_engine import EAWorkflowEngine
from app.utils.pagination import safe_int_arg

integration_bp = Blueprint("integration", __name__, url_prefix="/integration")


# ============================================================================
# DASHBOARD & LIST VIEWS
# ============================================================================


@integration_bp.route("/")
@login_required
def workflow_dashboard():
    """Main workflow dashboard showing available workflows and active instances."""
    try:
        engine = EAWorkflowEngine()

        # Get all workflow definitions
        definitions = engine.list_workflow_definitions(active_only=True)

        # Get active instances
        from app.models.workflow_models import EAWorkflowInstance

        active_instances = (
            EAWorkflowInstance.query.filter(
                EAWorkflowInstance.status.in_(["running", "paused", "waiting_approval"])
            )
            .order_by(EAWorkflowInstance.started_at.desc())
            .limit(20)
            .all()
        )

        # Get statistics
        total_definitions = len(definitions)
        active_count = len(active_instances)
        completed_count = EAWorkflowInstance.query.filter_by(status="completed").count()
        failed_count = EAWorkflowInstance.query.filter_by(status="failed").count()

        return render_template(
            "integration/dashboard.html",
            definitions=definitions,
            active_instances=active_instances,
            stats={
                "total_definitions": total_definitions,
                "active": active_count,
                "completed": completed_count,
                "failed": failed_count,
            },
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error loading workflow dashboard: %s", e)
        flash(f"Error loading workflow dashboard: {str(e)}", "error")
        # stats=None so the cards render an em dash; {} left them blank, which
        # is just as misleading as a zero once the flash has faded.
        return render_template(
            "integration/dashboard.html",
            definitions=[],
            active_instances=[],
            stats=None,
            load_error="Workflow statistics could not be read.",
        )


@integration_bp.route("/workflows")
@login_required
def list_workflows():
    """List all available workflow definitions."""
    try:
        engine = EAWorkflowEngine()
        category = request.args.get("category")

        definitions = engine.list_workflow_definitions(
            category=category, active_only=True
        )

        # Group by category
        workflows_by_category = {}
        for definition in definitions:
            cat = definition.workflow_category or "uncategorized"
            if cat not in workflows_by_category:
                workflows_by_category[cat] = []
            workflows_by_category[cat].append(definition)

        # Every category a filter can choose, so the toolbar keeps its options
        # even while one category is selected.
        from app.models.workflow_models import EAWorkflowDefinition

        categories = [
            row[0]
            for row in db.session.query(EAWorkflowDefinition.workflow_category)
            .filter(
                EAWorkflowDefinition.is_active.is_(True),
                EAWorkflowDefinition.workflow_category.isnot(None),
            )
            .distinct()
            .order_by(EAWorkflowDefinition.workflow_category)
        ]

        return render_template(
            "integration/workflow_list.html",
            workflows_by_category=workflows_by_category,
            categories=categories,
            selected_category=category,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error loading workflows: %s", e)
        flash(f"Error loading workflows: {str(e)}", "error")
        return render_template(
            "integration/workflow_list.html",
            workflows_by_category={},
            categories=[],
            selected_category=None,
            load_error="The workflow catalog could not be read.",
        )


@integration_bp.route("/instances")
@login_required
def list_instances():
    """List workflow instances with filtering."""
    try:
        from app.models.workflow_models import EAWorkflowInstance

        status = request.args.get("status", "all")
        workflow_code = request.args.get("workflow_code")

        query = EAWorkflowInstance.query

        if status != "all":
            query = query.filter_by(status=status)
        if workflow_code:
            # workflow_code lives on EAWorkflowDefinition, not EAWorkflowInstance —
            # resolve it to the definition id and filter by the real FK column.
            from app.models.workflow_models import EAWorkflowDefinition

            _defn = EAWorkflowDefinition.query.filter_by(workflow_code=workflow_code).first()
            query = query.filter_by(workflow_definition_id=_defn.id if _defn else -1)

        instances = query.order_by(EAWorkflowInstance.started_at.desc()).limit(50).all()

        # Get workflow definitions for filter dropdown
        engine = EAWorkflowEngine()
        definitions = engine.list_workflow_definitions(active_only=True)

        return render_template(
            "integration/instance_list.html",
            instances=instances,
            definitions=definitions,
            selected_status=status,
            selected_workflow=workflow_code,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error loading workflow instances: %s", e)
        flash(f"Error loading workflow instances: {str(e)}", "error")
        return render_template(
            "integration/instance_list.html",
            instances=[],
            definitions=[],
            selected_status="all",
            selected_workflow=None,
            load_error="Workflow instances could not be read.",
        )


# ============================================================================
# WORKFLOW DETAIL & EXECUTION
# ============================================================================


@integration_bp.route("/workflow/<string:workflow_code>")
@login_required
def workflow_detail(workflow_code):
    """Show details of a specific workflow definition."""
    try:
        engine = EAWorkflowEngine()
        definition = engine.get_workflow_definition(workflow_code)

        if not definition:
            flash(f"Workflow '{workflow_code}' not found", "error")
            return redirect(url_for("integration.list_workflows"))

        # Get recent instances of this workflow
        from app.models.workflow_models import EAWorkflowInstance

        recent_instances = (
            EAWorkflowInstance.query.filter_by(workflow_definition_id=definition.id)
            .order_by(EAWorkflowInstance.started_at.desc())
            .limit(10)
            .all()
        )

        return render_template(
            "integration/workflow_detail.html",
            workflow=definition,
            recent_instances=recent_instances,
        )
    except Exception as e:
        flash(f"Error loading workflow details: {str(e)}", "error")
        return redirect(url_for("integration.list_workflows"))


@integration_bp.route("/instance/<int:instance_id>")
@login_required
def instance_detail(instance_id):
    """Show detailed status of a workflow instance."""
    try:
        from app.models.workflow_models import (
            EAWorkflowInstance,
            EAWorkflowStepExecution,
        )

        instance = EAWorkflowInstance.query.get_or_404(instance_id)

        # Get step executions
        step_executions = (
            EAWorkflowStepExecution.query.filter_by(instance_id=instance_id)
            .order_by(EAWorkflowStepExecution.step_index)
            .all()
        )

        return render_template(
            "integration/instance_detail.html",
            instance=instance,
            step_executions=step_executions,
        )
    except Exception as e:
        flash(f"Error loading instance details: {str(e)}", "error")
        return redirect(url_for("integration.list_instances"))


# ============================================================================
# WORKFLOW ACTIONS
# ============================================================================


@integration_bp.route("/workflow/<string:workflow_code>/start", methods=["POST"])
@login_required
@audit_log("workflow_start")
def start_workflow(workflow_code):
    """Start a new workflow instance."""
    try:
        engine = EAWorkflowEngine()

        # Get context from form data
        context = {}
        for key, value in request.form.items():
            if key.startswith("context."):
                context_key = key[8:]  # Remove 'context.' prefix
                context[context_key] = value

        # Start the workflow
        instance = engine.start_workflow(
            workflow_code=workflow_code,
            context=context,
            triggered_by="manual",
            user_id=current_user.id,
        )

        flash(
            f"Workflow '{workflow_code}' started successfully (ID: {instance.id})",
            "success",
        )
        return redirect(url_for("integration.instance_detail", instance_id=instance.id))

    except Exception as e:
        flash(f"Error starting workflow: {str(e)}", "error")
        return redirect(
            url_for("integration.workflow_detail", workflow_code=workflow_code)
        )


@integration_bp.route("/instance/<int:instance_id>/resume", methods=["POST"])
@login_required
@audit_log("workflow_resume")
def resume_workflow(instance_id):
    """Resume a paused workflow instance."""
    try:
        engine = EAWorkflowEngine()
        engine.resume_workflow(instance_id)

        flash("Workflow resumed successfully", "success")
        return redirect(url_for("integration.instance_detail", instance_id=instance_id))

    except Exception as e:
        flash(f"Error resuming workflow: {str(e)}", "error")
        return redirect(url_for("integration.instance_detail", instance_id=instance_id))


@integration_bp.route("/instance/<int:instance_id>/approve", methods=["POST"])
@login_required
@audit_log("workflow_step_approve")
def approve_workflow_step(instance_id):
    """Approve a workflow step waiting for approval."""
    try:
        from app.models.workflow_models import EAWorkflowInstance

        instance = EAWorkflowInstance.query.get_or_404(instance_id)

        # Add approval to context
        approval_data = {
            "approved_by": current_user.id,
            "approved_at": datetime.utcnow().isoformat(),
            "comments": request.form.get("comments", ""),
        }

        if not instance.context:
            instance.context = {}
        instance.context["last_approval"] = approval_data
        db.session.commit()

        # Resume the workflow
        engine = EAWorkflowEngine()
        engine.resume_workflow(instance_id)

        flash("Step approved and workflow resumed", "success")
        return redirect(url_for("integration.instance_detail", instance_id=instance_id))

    except Exception as e:
        flash(f"Error approving workflow step: {str(e)}", "error")
        return redirect(url_for("integration.instance_detail", instance_id=instance_id))


@integration_bp.route("/instance/<int:instance_id>/cancel", methods=["POST"])
@login_required
@audit_log("workflow_cancel")
def cancel_workflow(instance_id):
    """Cancel a running workflow instance."""
    try:
        from app.models.workflow_models import EAWorkflowInstance

        instance = EAWorkflowInstance.query.get_or_404(instance_id)
        instance.status = "cancelled"
        instance.completed_at = datetime.utcnow()
        db.session.commit()

        flash("Workflow cancelled", "success")
        return redirect(url_for("integration.list_instances"))

    except Exception as e:
        flash(f"Error cancelling workflow: {str(e)}", "error")
        return redirect(url_for("integration.instance_detail", instance_id=instance_id))


# ============================================================================
# API ENDPOINTS
# ============================================================================


@integration_bp.route("/api/workflows")
@login_required
def api_list_workflows():
    """API: List all workflow definitions."""
    try:
        engine = EAWorkflowEngine()
        category = request.args.get("category")

        definitions = engine.list_workflow_definitions(
            category=category, active_only=True
        )

        return jsonify(
            {
                "success": True,
                "workflows": [
                    {
                        "code": d.workflow_code,
                        "name": d.workflow_name,
                        "category": d.workflow_category,
                        "description": d.workflow_description,
                        "type": d.workflow_type,
                        "automation_level": d.automation_level,
                    }
                    for d in definitions
                ],
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@integration_bp.route("/api/instances")
@login_required
def api_list_instances():
    """API: List workflow instances."""
    try:
        from app.models.workflow_models import EAWorkflowInstance

        status = request.args.get("status")
        limit = safe_int_arg('limit', 50, minimum=1, maximum=500)

        query = EAWorkflowInstance.query
        if status:
            query = query.filter_by(status=status)

        instances = (
            query.order_by(EAWorkflowInstance.started_at.desc()).limit(limit).all()
        )

        return jsonify(
            {
                "success": True,
                "instances": [
                    {
                        "id": i.id,
                        "workflow_code": i.definition.workflow_code if i.definition else None,
                        "status": i.status,
                        "started_at": i.started_at.isoformat()
                        if i.started_at
                        else None,
                        "completed_at": i.completed_at.isoformat()
                        if i.completed_at
                        else None,
                        "current_step": i.current_step_id,
                        "progress_percent": i.progress_percent,
                    }
                    for i in instances
                ],
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@integration_bp.route("/api/instance/<int:instance_id>/status")
@login_required
def api_instance_status(instance_id):
    """API: Get detailed status of a workflow instance."""
    from app.models.workflow_models import EAWorkflowInstance
    from app.utils.route_guards import require_entity

    require_entity(EAWorkflowInstance, instance_id, description="Workflow instance not found")

    try:
        engine = EAWorkflowEngine()
        status = engine.get_instance_status(instance_id)

        return jsonify({"success": True, "status": status})
    except HTTPException:
        raise
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@integration_bp.route("/api/instance/<int:instance_id>/start", methods=["POST"])
@login_required
@audit_log("workflow_api_start")
def api_start_workflow_instance(instance_id):
    """API: Start a workflow (alternative endpoint for AJAX calls)."""
    try:
        data = request.get_json() or {}
        workflow_code = data.get("workflow_code")
        context = data.get("context", {})

        if not workflow_code:
            return jsonify(
                {"success": False, "error": "workflow_code is required"}
            ), 400

        engine = EAWorkflowEngine()
        instance = engine.start_workflow(
            workflow_code=workflow_code,
            context=context,
            triggered_by="api",
            user_id=current_user.id,
        )

        return jsonify(
            {
                "success": True,
                "instance_id": instance.id,
                "status": instance.status,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
