"""Version & Change Management routes for the Code Workbench.

All routes live under /solutions/<id>/codegen/* (submit-change-request,
version-history, rollback, apply-change, promote, compare-versions,
export-package, workflow/compile, export) and are registered on
``codegen_bp`` (defined in codegen_routes.py).  This module is imported at the
bottom of codegen_routes.py so Flask picks up the decorated handlers.
"""
import logging

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models.solution_models import Solution
from app.modules.codegen.routes.codegen_routes import codegen_bp
from app.modules.codegen.routes._helpers import _check_access

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 5: Version Management routes
# ---------------------------------------------------------------------------


@codegen_bp.route("/solutions/<int:solution_id>/codegen/submit-change-request", methods=["POST"])
@login_required
def submit_change_request(solution_id):
    """Submit a natural-language change request for impact analysis."""
    from app.modules.codegen.services.change_request_analyzer import ChangeRequestAnalyzer
    from app.modules.codegen.models import SolutionRule

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    change_text = data.get("change_text", "").strip()
    if not change_text:
        return jsonify({"success": False, "error": "change_text is required"}), 400

    # Extract model entities from solution data
    model_entities = []
    data_model = getattr(solution, "data_model", None)
    if data_model and isinstance(data_model, dict):
        model_entities = list(data_model.get("entities", {}).keys())

    # Extract existing rule names
    existing_rules = [
        r.name for r in SolutionRule.query.filter_by(
            solution_id=solution_id, is_active=True
        ).all()
    ]

    analyzer = ChangeRequestAnalyzer()
    result = analyzer.analyze(
        change_text=change_text,
        solution_id=solution_id,
        model_entities=model_entities,
        existing_rules=existing_rules,
    )

    return jsonify({"success": True, **result})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/version-history", methods=["GET"])
@login_required
def version_history(solution_id):
    """Return version history for a solution."""
    from app.modules.codegen.services.version_manager import VersionManager

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    vm = VersionManager()
    versions = vm.get_history(solution_id)

    return jsonify({
        "success": True,
        "versions": [
            {
                "id": v.id,
                "version_number": v.version_number,
                "change_summary": v.change_summary,
                "status": v.status,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "deployed_at": v.deployed_at.isoformat() if v.deployed_at else None,
                "created_by": v.created_by,
            }
            for v in versions
        ],
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/rollback", methods=["POST"])
@login_required
def rollback_version(solution_id):
    """Rollback to a previous version."""
    from app.modules.codegen.services.version_manager import VersionManager

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    target_version = data.get("target_version")
    if not target_version:
        return jsonify({"success": False, "error": "target_version is required"}), 400

    vm = VersionManager()
    try:
        version = vm.rollback(solution_id, int(target_version))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404

    return jsonify({
        "success": True,
        "version": {
            "id": version.id,
            "version_number": version.version_number,
            "status": version.status,
        },
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/workflow/compile", methods=["POST"])
@login_required
def workflow_compile(solution_id):
    """Compile a visual workflow definition into n8n workflow JSON."""
    from app.modules.codegen.services.workflow_n8n_compiler import WorkflowToN8nCompiler

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    workflow_def = data.get("workflow")
    if not workflow_def or not isinstance(workflow_def, dict):
        return jsonify({"success": False, "error": "workflow object is required"}), 400

    compiler = WorkflowToN8nCompiler()
    try:
        n8n_json = compiler.compile(workflow_def)
    except Exception as e:
        logger.exception("Workflow compilation failed for solution %s", solution_id)
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": True, "n8n_workflow": n8n_json})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/export", methods=["POST"])
@login_required
def export_solution(solution_id):
    """Export a complete solution package for customer infrastructure."""
    from app.modules.codegen.services.migration_packager import MigrationPackager

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    target_platform = data.get("target_platform", "docker-compose")

    packager = MigrationPackager()
    try:
        package = packager.export(solution_id=solution_id, target_platform=target_platform)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.exception("Export failed for solution %s", solution_id)
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": True, **package})


# ── Phase 5: Version & Change Management ──


@codegen_bp.route("/solutions/<int:solution_id>/codegen/apply-change", methods=["POST"])
@login_required
def apply_change(solution_id):
    """Analyze a change request, generate migrations, and create a new version."""
    from app.modules.codegen.services.change_request_analyzer import ChangeRequestAnalyzer
    from app.modules.codegen.services.version_manager import VersionManager
    from app.modules.codegen.models import SolutionRule

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    change_text = data.get("change_text", "").strip()
    if not change_text:
        return jsonify({"success": False, "error": "change_text is required"}), 400

    model_entities = []
    data_model = getattr(solution, "data_model", None)
    if data_model and isinstance(data_model, dict):
        model_entities = list(data_model.get("entities", {}).keys())

    existing_rules = [
        r.name for r in SolutionRule.query.filter_by(
            solution_id=solution_id, is_active=True
        ).all()
    ]

    analyzer = ChangeRequestAnalyzer()
    plan = analyzer.analyze_and_plan(
        change_text=change_text,
        solution_id=solution_id,
        model_entities=model_entities,
        existing_rules=existing_rules,
    )

    if not plan.get("success"):
        return jsonify(plan), 400

    vm = VersionManager()
    version = vm.create_version(
        solution_id=solution_id,
        change_plan={"changes": plan["changes"]},
        change_summary=plan["change_summary"],
        migration_scripts=plan["migration_scripts"],
        created_by=getattr(current_user, "email", "unknown"),
    )

    return jsonify({
        "success": True,
        "version": {
            "id": version.id,
            "version_number": version.version_number,
            "status": version.status,
        },
        "change_summary": plan["change_summary"],
        "overall_risk": plan["overall_risk"],
        "warnings": plan.get("warnings", []),
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/promote", methods=["POST"])
@login_required
def promote_version(solution_id):
    """Promote a deploying version to live."""
    from app.modules.codegen.services.version_manager import VersionManager

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    version_id = data.get("version_id")
    if not version_id:
        return jsonify({"success": False, "error": "version_id is required"}), 400

    vm = VersionManager()
    try:
        version = vm.promote(int(version_id))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404

    return jsonify({
        "success": True,
        "version": {
            "id": version.id,
            "version_number": version.version_number,
            "status": version.status,
            "deployed_at": version.deployed_at.isoformat() if version.deployed_at else None,
        },
    })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/compare-versions", methods=["GET"])
@login_required
def compare_versions(solution_id):
    """Compare two versions and return a BA-friendly diff."""
    from app.modules.codegen.services.version_manager import VersionManager

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution):
        abort(404)

    v1 = request.args.get("v1", type=int)
    v2 = request.args.get("v2", type=int)
    if not v1 or not v2:
        return jsonify({"success": False, "error": "v1 and v2 query params required"}), 400

    vm = VersionManager()
    try:
        diff = vm.compare_versions(solution_id, v1, v2)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404

    return jsonify({"success": True, **diff})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/export-package", methods=["POST"])
@login_required
def export_package(solution_id):
    """Export a complete solution package for customer infrastructure."""
    from app.modules.codegen.services.migration_packager import MigrationPackager

    solution = db.session.get(Solution, solution_id)
    if not solution or not _check_access(solution, write=True):
        abort(404)

    data = request.get_json(silent=True) or {}
    target_platform = data.get("target_platform", "docker-compose")

    valid_platforms = {"docker-compose", "kubernetes", "aws", "azure", "gcp"}
    if target_platform not in valid_platforms:
        return jsonify({
            "success": False,
            "error": f"Invalid platform. Valid: {sorted(valid_platforms)}",
        }), 400

    packager = MigrationPackager()
    try:
        package = packager.export(solution_id, target_platform)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404

    return jsonify({"success": True, **package})
