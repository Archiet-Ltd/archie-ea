"""
Routes for Agentic Gap Implementation

Provides UI and API endpoints for triggering agentic implementation
of missing architecture models and services.
"""

import json
import logging
from datetime import datetime, timedelta

from flask import jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from flask_login import current_user, login_required

from app import db
from app.models.agentic_gaps import AgentConfiguration, AgentSchedule
from app.services.archimate.agentic_gap_implementation_service import (
    AgenticGapImplementationService,
)

# Import main blueprint - use relative import to avoid circular dependency
from .views import main
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)


@main.route("/agentic-gaps")
@login_required
def agentic_gaps_ui():
    """UI for Agentic Gap Implementation."""
    return render_template("main/agentic_gaps.html")


@main.route("/api/agentic-gaps/implement-all", methods=["POST"])
@login_required
def implement_all_gaps():
    """API endpoint to implement all gaps using all agents."""
    try:
        data = request.get_json() or {}
        architecture_id = data.get("architecture_id", 1)  # Default to 1 if not provided

        parallel = data.get("parallel", False)
        agent_filter = data.get("agent_filter")  # Optional list of agents to run

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )
        results = service.implement_all_gaps(
            architecture_id, parallel=parallel, agent_filter=agent_filter
        )

        return jsonify({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Agentic gap implementation failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/implement/<agent_name>", methods=["POST"])
@login_required
def implement_single_agent(agent_name):
    """API endpoint to implement gaps using a single agent."""
    try:
        data = request.get_json() or {}
        architecture_id = data.get("architecture_id", 1)

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )

        # Get agent configuration
        config = {}
        try:
            agent_config = AgentConfiguration.query.filter_by(agent_name=agent_name).first()
            if agent_config:
                config = agent_config.to_dict()
        except Exception as e:
            logger.warning(f"Could not load agent configuration: {e}")

        # Map agent names to methods
        agent_methods = {
            "system_architecture": service._run_system_architecture_agent,
            "data_governance": service._run_data_governance_agent,
            "application_lifecycle": service._run_application_lifecycle_agent,
            "software_quality": service._run_software_quality_agent,
            "solution_deployment": service._run_solution_deployment_agent,
            "viewpoint_export": service._run_viewpoint_export_agent,
        }

        if agent_name not in agent_methods:
            return jsonify({"success": False, "error": f"Unknown agent: {agent_name}"}), 400

        result = service._run_agent_with_tracking(
            agent_name, agent_methods[agent_name], architecture_id, config
        )

        return jsonify({"success": True, "agent": agent_name, "result": result})
    except Exception as e:
        logger.error(f"Agent {agent_name} failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/status", methods=["GET"])
@login_required
def get_implementation_status():
    """Get status of implemented models and services."""
    try:
        status = {
            "system_architecture": {"available": False, "models": []},
            "data_governance": {"available": False, "models": []},
            "application_lifecycle": {"available": False, "models": []},
            "software_quality": {"available": False, "models": []},
            "solution_deployment": {"available": False, "models": []},
            "viewpoint_export": {"available": False, "services": []},
        }

        # Check system architecture
        try:
            from app.models.system_architecture import (
                SystemBoundary,  # noqa: F401 — availability probe: the import IS the test
                SystemHierarchy,  # noqa: F401 — availability probe: the import IS the test
                SystemInterface,  # noqa: F401 — availability probe: the import IS the test
            )

            status["system_architecture"]["available"] = True
            status["system_architecture"]["models"] = [
                "SystemBoundary",
                "SystemHierarchy",
                "SystemInterface",
                "SystemDeployment",
                "SystemLifecycle",
            ]
        except ImportError:
            logger.exception("Failed to import system_architecture models")

        # Check data governance
        try:
            from app.models.data_governance import DataCatalog, DataQualityMetrics  # noqa: F401 — availability probe: the import IS the test

            status["data_governance"]["available"] = True
            status["data_governance"]["models"] = [
                "DataCatalog",
                "DataQualityMetrics",
                "DataGovernanceWorkflow",
                "DataAccessControl",
                "DataRetentionPolicy",
            ]
        except ImportError:
            logger.exception("Failed to import app.models.data_governance")

        # Check application lifecycle
        try:
            from app.models.application_lifecycle import ApplicationVersioning, DeploymentPipeline  # noqa: F401 — availability probe: the import IS the test

            status["application_lifecycle"]["available"] = True
            status["application_lifecycle"]["models"] = [
                "ApplicationVersioning",
                "DeploymentPipeline",
                "ApplicationPerformanceMetrics",
            ]
        except ImportError:
            logger.exception("Failed to import app.models.application_lifecycle")

        # Check software quality
        try:
            from app.models.software_quality import CodeQualityMetrics, TechnicalDebt  # noqa: F401 — availability probe: the import IS the test

            status["software_quality"]["available"] = True
            status["software_quality"]["models"] = [
                "TechnicalDebt",
                "CodeQualityMetrics",
                "RefactoringTracking",
            ]
        except ImportError:
            logger.exception("Failed to import app.models.software_quality")

        # Check solution deployment
        try:
            from app.models.solution_deployment import SolutionDeploymentArchitecture  # noqa: F401 — availability probe: the import IS the test

            status["solution_deployment"]["available"] = True
            status["solution_deployment"]["models"] = [
                "SolutionTechnologyMapping",
                "SolutionDeploymentArchitecture",
            ]
        except ImportError:
            logger.exception("Failed to import app.models.solution_deployment")

        # Check viewpoint export
        try:
            from app.services.archimate.archimate_xml_export_service import (
                ArchiMateXMLExportService,  # noqa: F401 — availability probe: the import IS the test
            )

            status["viewpoint_export"]["available"] = True
            status["viewpoint_export"]["services"] = ["ArchiMateXMLExportService"]
        except ImportError:
            logger.exception("Failed to import app.services.archimate.archimate_xml_export_service")

        return jsonify({"success": True, "status": status})
    except Exception as e:
        logger.error(f"Failed to get implementation status: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/auto-implement", methods=["POST"])
@login_required
def auto_implement_from_gaps():
    """Automatically implement gaps based on gap discovery."""
    try:
        data = request.get_json() or {}
        architecture_id = data.get("architecture_id", 1)

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )
        results = service.implement_gaps_from_discovery(architecture_id)

        return jsonify({"success": results.get("success", True), "results": results})
    except Exception as e:
        logger.error(f"Auto-implementation from gaps failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/metrics", methods=["GET"])
@login_required
def get_agent_metrics():
    """Get analytics and metrics on agent executions."""
    try:
        architecture_id = request.args.get("architecture_id", type=int)
        days = request.args.get("days", 30, type=int)

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )
        metrics = service.get_execution_metrics(architecture_id, days)

        return jsonify(metrics)
    except Exception as e:
        logger.error(f"Failed to get metrics: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/history", methods=["GET"])
@login_required
def get_execution_history():
    """Get execution history with filtering."""
    try:
        architecture_id = request.args.get("architecture_id", type=int)
        agent_name = request.args.get("agent_name")
        limit = safe_int_arg('limit', 50, minimum=1, maximum=500)

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )
        history = service.get_execution_history(architecture_id, agent_name, limit)

        return jsonify({"success": True, "history": history, "count": len(history)})
    except Exception as e:
        logger.error(f"Failed to get execution history: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/review/<int:execution_id>", methods=["POST"])
@login_required
def review_generated_code(execution_id):
    """Review and approve/reject generated code."""
    try:
        data = request.get_json() or {}
        approved = data.get("approved", False)
        reviewer_notes = data.get("notes")

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )
        result = service.review_generated_code(execution_id, approved, reviewer_notes)

        return jsonify(result)
    except Exception as e:
        logger.error(f"Code review failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/rollback/<int:execution_id>", methods=["POST"])
@login_required
def rollback_execution(execution_id):
    """Rollback changes made by an agent execution."""
    try:
        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )
        result = service.rollback_agent_execution(execution_id)

        return jsonify(result)
    except Exception as e:
        logger.error(f"Rollback failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/config/<agent_name>", methods=["GET", "POST"])
@login_required
def agent_configuration(agent_name):
    """Get or update agent configuration."""
    try:
        if request.method == "GET":
            config = AgentConfiguration.query.filter_by(agent_name=agent_name).first()
            if config:
                return jsonify({"success": True, "config": config.to_dict()})
            else:
                # Return default config
                return jsonify(
                    {
                        "success": True,
                        "config": {
                            "agent_name": agent_name,
                            "enabled": True,
                            "auto_generate": False,
                            "require_review": True,
                            "validate_models": True,
                        },
                    }
                )
        else:
            # POST - update configuration. One row per agent name serves every organisation, so
            # changing it is a platform-admin action.
            from app.middleware.tenant_decorators import is_platform_admin

            if not is_platform_admin(current_user):
                return jsonify({"success": False, "error": "Platform admin access required"}), 403

            data = request.get_json() or {}

            config = AgentConfiguration.query.filter_by(agent_name=agent_name).first()
            if not config:
                config = AgentConfiguration(agent_name=agent_name)
                db.session.add(config)

            # Update fields
            if "llm_provider" in data:
                config.llm_provider = data["llm_provider"]
            if "llm_model" in data:
                config.llm_model = data["llm_model"]
            if "temperature" in data:
                config.temperature = data["temperature"]
            if "max_tokens" in data:
                config.max_tokens = data["max_tokens"]
            if "auto_generate" in data:
                config.auto_generate = data["auto_generate"]
            if "require_review" in data:
                config.require_review = data["require_review"]
            if "validate_models" in data:
                config.validate_models = data["validate_models"]
            if "enabled" in data:
                config.enabled = data["enabled"]
            if "priority" in data:
                config.priority = data["priority"]
            if "timeout_seconds" in data:
                config.timeout_seconds = data["timeout_seconds"]
            if "depends_on" in data:
                config.depends_on = json.dumps(data["depends_on"])
            if "custom_settings" in data:
                config.custom_settings = json.dumps(data["custom_settings"])
            if "description" in data:
                config.description = data["description"]

            config.updated_at = datetime.utcnow()
            config.updated_by_id = current_user.id if current_user.is_authenticated else None

            db.session.commit()

            return jsonify(
                {
                    "success": True,
                    "config": config.to_dict(),
                    "message": "Configuration updated successfully",
                }
            )

    except Exception as e:
        logger.error(f"Configuration operation failed: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/schedule", methods=["GET", "POST"])
@login_required
def manage_schedules():
    """Get or create agent schedules."""
    try:
        if request.method == "GET":
            schedules = AgentSchedule.query.filter_by(enabled=True).all()
            return jsonify({"success": True, "schedules": [s.to_dict() for s in schedules]})
        else:
            # POST - create schedule
            data = request.get_json() or {}

            schedule = AgentSchedule(
                name=data.get("name", "Unnamed Schedule"),
                description=data.get("description"),
                agent_name=data.get("agent_name"),  # None = all agents
                architecture_id=data.get("architecture_id", 1),
                schedule_type=data.get("schedule_type", "daily"),
                schedule_config=json.dumps(data.get("schedule_config", {})),
                trigger_event=data.get("trigger_event"),
                trigger_conditions=json.dumps(data.get("trigger_conditions", {})),
                enabled=data.get("enabled", True),
                notify_on_completion=data.get("notify_on_completion", False),
                notify_emails=json.dumps(data.get("notify_emails", [])),
                created_by_id=current_user.id if current_user.is_authenticated else None,
            )

            # Calculate next run time based on schedule type
            schedule.next_run_at = _calculate_next_run(
                schedule.schedule_type, schedule.get_schedule_config()
            )

            db.session.add(schedule)
            db.session.commit()

            return jsonify(
                {
                    "success": True,
                    "schedule": schedule.to_dict(),
                    "message": "Schedule created successfully",
                }
            )

    except Exception as e:
        logger.error(f"Schedule operation failed: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def _calculate_next_run(schedule_type: str, schedule_config: dict):
    """Calculate next run time based on schedule type."""

    now = datetime.utcnow()

    if schedule_type == "daily":
        hour = schedule_config.get("hour", 0)
        minute = schedule_config.get("minute", 0)
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run
    elif schedule_type == "weekly":
        day_of_week = schedule_config.get("day_of_week", 0)  # 0 = Monday
        hour = schedule_config.get("hour", 0)
        minute = schedule_config.get("minute", 0)
        days_ahead = day_of_week - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        next_run = (now + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return next_run
    elif schedule_type == "monthly":
        day_of_month = schedule_config.get("day_of_month", 1)
        hour = schedule_config.get("hour", 0)
        minute = schedule_config.get("minute", 0)
        next_run = now.replace(day=day_of_month, hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            # Move to next month
            if now.month == 12:
                next_run = next_run.replace(year=now.year + 1, month=1)
            else:
                next_run = next_run.replace(month=now.month + 1)
        return next_run
    else:
        # Event-driven - no scheduled time
        return None


@main.route("/api/agentic-gaps/schedule/<int:schedule_id>", methods=["DELETE", "PUT"])
@login_required
def manage_single_schedule(schedule_id):
    """Delete or update a specific schedule."""
    try:
        schedule = AgentSchedule.query.get(schedule_id)
        if not schedule:
            return jsonify({"success": False, "error": "Schedule not found"}), 404

        if request.method == "DELETE":
            db.session.delete(schedule)
            db.session.commit()
            return jsonify({"success": True, "message": "Schedule deleted successfully"})
        else:
            # PUT - update schedule
            data = request.get_json() or {}

            if "name" in data:
                schedule.name = data["name"]
            if "description" in data:
                schedule.description = data["description"]
            if "enabled" in data:
                schedule.enabled = data["enabled"]
            if "schedule_config" in data:
                schedule.schedule_config = json.dumps(data["schedule_config"])
                # Recalculate next run
                schedule.next_run_at = _calculate_next_run(
                    schedule.schedule_type, data["schedule_config"]
                )

            schedule.updated_at = datetime.utcnow()
            db.session.commit()

            return jsonify(
                {
                    "success": True,
                    "schedule": schedule.to_dict(),
                    "message": "Schedule updated successfully",
                }
            )

    except Exception as e:
        logger.error(f"Schedule management failed: {e}")
        db.session.rollback()
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/recommendations", methods=["GET"])
@login_required
def get_agent_recommendations():
    """Get recommendations on which agents to run based on architecture state."""
    try:
        architecture_id = request.args.get("architecture_id", 1, type=int)

        (AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        ))

        # Analyze architecture to recommend agents
        recommendations = []

        # Check application count
        try:
            from app.models.application_layer import ApplicationComponent

            app_count = ApplicationComponent.query.count()
            if app_count > 10:
                recommendations.append(
                    {
                        "agent": "application_lifecycle",
                        "reason": f"Found {app_count} applications - lifecycle management recommended",
                        "priority": "high",
                    }
                )
        except Exception as e:
            logger.debug("Failed to check application lifecycle recommendations: %s", e)

        # Check data objects
        try:
            from app.models.business_layer import BusinessObject

            data_count = BusinessObject.query.count()
            if data_count > 5:
                recommendations.append(
                    {
                        "agent": "data_governance",
                        "reason": f"Found {data_count} data objects - data governance recommended",
                        "priority": "medium",
                    }
                )
        except Exception as e:
            logger.debug("Failed to check data governance recommendations: %s", e)

        # Check if gaps exist
        try:
            from app.services.gap_discovery_service import GapDiscoveryService

            gap_service = GapDiscoveryService()
            gaps = gap_service.discover_all_gaps(architecture_id)
            if gaps.get("summary", {}).get("total_gaps", 0) > 0:
                recommendations.append(
                    {
                        "agent": "all",
                        "reason": f"Found {gaps['summary']['total_gaps']} gaps - run all agents",
                        "priority": "high",
                    }
                )
        except Exception as e:
            logger.debug("Failed to discover gaps for recommendations: %s", e)

        return jsonify(
            {
                "success": True,
                "recommendations": recommendations,
                "architecture_id": architecture_id,
            }
        )

    except Exception as e:
        logger.error(f"Failed to get recommendations: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# LLM-Driven Gap Resolution Workflow Endpoints (PRD Implementation)
# =============================================================================


@main.route("/api/agentic-gaps/execute-full-workflow", methods=["POST"])
@login_required
def execute_full_workflow():
    """
    Execute the complete LLM-driven gap resolution workflow.

    This endpoint triggers the full PRD workflow:
    1. Gap Discovery - Identify all process/capability gaps
    2. Reuse Analysis - Search existing applications for reuse candidates
    3. Recommendation Generation - LLM-powered reuse vs build recommendations
    4. Roadmap Generation - Prioritized roadmap with action types
    5. Work Package Creation - Detailed implementation plans
    6. Stakeholder Validation - Review and approval workflow
    7. Audit Logging - Full traceability throughout

    Request Body (JSON):
        architecture_id (int): Target architecture ID (required)
        options (dict): Optional workflow configuration:
            - auto_approve (bool): Skip stakeholder validation (default: False)
            - reuse_threshold (float): Minimum similarity for reuse (default: 0.6)
            - parallel_execution (bool): Run steps in parallel (default: True)
            - dry_run (bool): Generate plan without execution (default: False)
            - gap_types (list): Gap types to process (default: all)
            - max_gaps (int): Maximum gaps to process (default: all)
            - budget_constraint (float): Maximum total budget (default: None)

    Returns:
        JSON with complete execution report including:
        - gaps_discovered, reuse_candidates, reuse_recommendations
        - roadmap_generated, work_packages_created
        - validation_status, audit_trail
    """
    try:
        data = request.get_json() or {}
        architecture_id = data.get("architecture_id", 1)
        options = data.get("options", {})

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )

        result = service.execute_llm_driven_gap_resolution(
            architecture_id=architecture_id, options=options
        )

        # Convert IDs to strings for BigInt safety per LLM_RULES.md
        if result.get("work_packages_created"):
            for wp in result["work_packages_created"]:
                if "id" in wp:
                    wp["id"] = str(wp["id"])

        return jsonify(result)

    except Exception as e:
        logger.error(f"Full workflow execution failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/reuse-candidates/<int:gap_id>", methods=["GET"])
@login_required
def get_reuse_candidates(gap_id):
    """
    Get reuse candidates for a specific gap.

    This endpoint supports the PRD requirement for discovery and reuse-first planning
    by finding existing applications that could address a capability gap.

    Args:
        gap_id: ID of the gap to find candidates for

    Query Parameters:
        threshold (float): Minimum similarity threshold (default: 0.6)
        max_candidates (int): Maximum candidates to return (default: 10)

    Returns:
        JSON with list of reuse candidates including:
        - application_id, name, similarity_score
        - reuse_type, capability_overlap
        - extension_effort_estimate, rationale
    """
    try:
        threshold = request.args.get("threshold", 0.6, type=float)
        max_candidates = request.args.get("max_candidates", 10, type=int)

        # Get the gap from database
        from app.models.roadmap_models import ImplementationGap
        from app.models.unified_capability import UnifiedCapability

        gap = ImplementationGap.query.get(gap_id)
        if not gap:
            # Try looking up as capability_id instead
            capability = UnifiedCapability.query.get(gap_id)
            if capability:
                gap_dict = {
                    "id": capability.id,
                    "capability_id": capability.id,
                    "capability_name": capability.name,
                    "gap_type": "capability",
                    "domain": capability.domain or "",
                    "severity": "medium",
                }
            else:
                return jsonify({"success": False, "error": f"Gap with ID {gap_id} not found"}), 404
        else:
            gap_dict = {
                "id": gap.id,
                "capability_id": gap.source_capability_id,
                "capability_name": gap.name,
                "gap_type": gap.gap_type,
                "severity": gap.priority,
                "gap_description": gap.description,
            }

        # Find reuse candidates
        from app.services.application_similarity_service import ApplicationSimilarityService

        similarity_service = ApplicationSimilarityService()

        candidates = similarity_service.find_reuse_candidates_for_gap(
            gap=gap_dict, threshold=threshold, max_candidates=max_candidates
        )

        # Convert IDs to strings for BigInt safety
        for candidate in candidates:
            if "application_id" in candidate:
                candidate["application_id"] = str(candidate["application_id"])

        return jsonify(
            {
                "success": True,
                "gap_id": str(gap_id),
                "candidates": candidates,
                "count": len(candidates),
                "threshold_used": threshold,
            }
        )

    except Exception as e:
        logger.error(f"Failed to get reuse candidates for gap {gap_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/reuse-recommendation", methods=["POST"])
@login_required
def get_reuse_recommendation():
    """
    Generate a reuse vs build recommendation for a gap.

    This endpoint uses LLM analysis to recommend whether to reuse, extend,
    replace, or build new to address a capability gap.

    Request Body (JSON):
        gap_id (int): ID of the gap
        threshold (float): Similarity threshold (default: 0.6)

    Returns:
        JSON with recommendation including:
        - recommendation: 'reuse' | 'extend' | 'replace' | 'build_new'
        - recommended_application_id, confidence_score
        - rationale, cost_comparison, implementation_approach
    """
    try:
        data = request.get_json() or {}
        gap_id = data.get("gap_id")
        threshold = data.get("threshold", 0.6)

        if not gap_id:
            return jsonify({"success": False, "error": "gap_id is required"}), 400

        # Get gap information
        from app.models.roadmap_models import ImplementationGap
        from app.models.unified_capability import UnifiedCapability

        gap = ImplementationGap.query.get(gap_id)
        if not gap:
            capability = UnifiedCapability.query.get(gap_id)
            if capability:
                gap_dict = {
                    "id": capability.id,
                    "capability_id": capability.id,
                    "capability_name": capability.name,
                    "gap_type": "capability",
                    "domain": capability.domain or "",
                    "severity": "medium",
                }
            else:
                return jsonify({"success": False, "error": f"Gap with ID {gap_id} not found"}), 404
        else:
            gap_dict = {
                "id": gap.id,
                "capability_id": gap.source_capability_id,
                "capability_name": gap.name,
                "gap_type": gap.gap_type,
                "severity": gap.priority,
                "gap_description": gap.description,
            }

        # Find candidates and generate recommendation
        from app.services.application_similarity_service import ApplicationSimilarityService

        similarity_service = ApplicationSimilarityService()

        candidates = similarity_service.find_reuse_candidates_for_gap(
            gap=gap_dict, threshold=threshold
        )

        recommendation = similarity_service.generate_reuse_vs_build_recommendation(
            gap=gap_dict,
            candidates=candidates,
            user_id=current_user.id if current_user.is_authenticated else None,
        )

        # Convert IDs to strings
        if recommendation.get("recommended_application_id"):
            recommendation["recommended_application_id"] = str(
                recommendation["recommended_application_id"]
            )

        return jsonify(
            {
                "success": True,
                "gap_id": str(gap_id),
                "candidates_count": len(candidates),
                "recommendation": recommendation,
            }
        )

    except Exception as e:
        logger.error(f"Failed to generate reuse recommendation: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/validate-roadmap", methods=["POST"])
@login_required
def validate_roadmap():
    """
    Create validation request for stakeholder review.

    This endpoint supports the PRD requirement for feedback and iteration
    by creating formal approval requests for roadmap items.

    Request Body (JSON):
        work_package_id (int): ID of the work package to validate
        stakeholder_ids (list): List of user IDs who should approve

    Returns:
        JSON with validation request details
    """
    try:
        data = request.get_json() or {}
        work_package_id = data.get("work_package_id")
        stakeholder_ids = data.get("stakeholder_ids", [])

        if not work_package_id:
            return jsonify({"success": False, "error": "work_package_id is required"}), 400

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )

        result = service.create_validation_request(
            roadmap_item_id=work_package_id, stakeholder_ids=stakeholder_ids
        )

        return jsonify(result)

    except Exception as e:
        logger.error(f"Failed to create validation request: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/stakeholder-feedback", methods=["POST"])
@login_required
def process_feedback():
    """
    Process stakeholder feedback on a validation request.

    This endpoint handles business owner decisions on proposed changes.

    Request Body (JSON):
        work_package_id (int): ID of the work package
        decision (str): 'approved' | 'rejected' | 'revision_requested'
        notes (str): Reviewer notes
        modifications (dict): Requested modifications (if revision_requested)

    Returns:
        JSON with processing result and next steps
    """
    try:
        data = request.get_json() or {}
        work_package_id = data.get("work_package_id")
        feedback = {
            "decision": data.get("decision"),
            "notes": data.get("notes", ""),
            "modifications": data.get("modifications", {}),
        }

        if not work_package_id:
            return jsonify({"success": False, "error": "work_package_id is required"}), 400

        if not feedback.get("decision"):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "decision is required (approved, rejected, or revision_requested)",
                    }
                ),
                400,
            )

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )

        result = service.process_stakeholder_feedback(
            validation_id=work_package_id, feedback=feedback
        )

        return jsonify(result)

    except Exception as e:
        logger.error(f"Failed to process stakeholder feedback: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/trigger-tests/<int:work_package_id>", methods=["POST"])
@login_required
def trigger_tests(work_package_id):
    """
    Trigger MCP test pipeline for a completed work package.

    This endpoint supports the PRD requirement for automated testing
    by integrating with the MCP Playwright testing pipeline.

    Args:
        work_package_id: ID of the work package to test

    Returns:
        JSON with test execution status and expected tests
    """
    try:
        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )

        result = service.trigger_validation_tests(work_package_id)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Failed to trigger tests for work package {work_package_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/audit-trail/<int:execution_id>", methods=["GET"])
@login_required
def get_audit_trail(execution_id):
    """
    Get complete audit trail for a workflow execution.

    This endpoint supports the PRD requirement for audit and traceability
    by providing full visibility into all LLM actions and decisions.

    Args:
        execution_id: ID of the workflow execution

    Returns:
        JSON with complete audit trail including:
        - All phases, decisions, and outcomes
        - Timestamps and durations
        - Error details if any
    """
    try:
        from app.models.agentic_gaps import AgentExecutionHistory
        from app.utils.route_guards import require_entity_json

        # No such execution -> 404, rather than a 200 carrying an empty trail.
        # The `main` blueprint renders an HTML 404 page, so return JSON directly.
        _execution, missing = require_entity_json(
            AgentExecutionHistory, execution_id, label="Execution"
        )
        if missing:
            return missing

        service = AgenticGapImplementationService(
            user_id=current_user.id if current_user.is_authenticated else None
        )

        result = service.get_workflow_audit_trail(execution_id)

        return jsonify(result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get audit trail for execution {execution_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@main.route("/api/agentic-gaps/decision-logs", methods=["GET"])
@login_required
def get_decision_logs():
    """
    Get LLM decision logs for audit purposes.

    This endpoint provides access to all logged LLM-driven decisions
    for traceability and review.

    Query Parameters:
        decision_type (str): Filter by decision type
        limit (int): Maximum records to return (default: 100)
        days (int): Only return decisions from last N days

    Returns:
        JSON with list of decision log entries
    """
    try:
        from app.services.llm_service import LLMService

        decision_type = request.args.get("decision_type")
        limit = safe_int_arg('limit', 100, minimum=1, maximum=500)
        days = request.args.get("days", type=int)

        since = None
        if days:
            since = datetime.utcnow() - timedelta(days=days)

        # Prompts and decisions are personal to the user who triggered them: everyone sees their
        # own, and only a platform admin sees every user's.
        from app.middleware.tenant_decorators import is_platform_admin

        logs = LLMService.get_decision_log(
            decision_type=decision_type,
            user_id=None if is_platform_admin(current_user) else current_user.id,
            limit=limit,
            since=since,
        )

        return jsonify({"success": True, "decision_logs": logs, "count": len(logs)})

    except Exception as e:
        logger.error(f"Failed to get decision logs: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500
