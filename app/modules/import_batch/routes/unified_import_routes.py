"""
DEPRECATED: This file is migrated to app/modules/import_batch/.
Registration is now centralized via app.modules.import_batch.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Unified Import API Routes

Provides unified API for both Quick Mode and Governed Mode imports.

Endpoints:
  POST /api/import/analyze  - Analyze file and recommend mode
  POST /api/import/quick    - Execute quick mode import (< 50 rows)
  POST /api/import/governed - Start governed mode import (>= 50 rows)
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.services.unified_import import ImportOrchestrator

logger = logging.getLogger(__name__)
bp = Blueprint("unified_import", __name__, url_prefix="/api/import")


@bp.route("/analyze", methods=["POST"])
@login_required
def analyze_import():
    """
    Analyze an import file and recommend the appropriate mode.

    Request:
    {
        "file": <file>,
        "archimate_mode": "quick|standard|comprehensive" (optional, default: "standard"),
        "enable_ai": bool (optional, default: true),
        "budget_limit_usd": float (optional),
        "check_duplicates": bool (optional, default: true)
    }

    Response (200):
    {
        "file_stats": {
            "total_rows": 10,
            "total_columns": 5,
            "columns": ["name", "description", ...]
        },
        "duplicate_analysis": {
            "in_file_count": 0,
            "database_count": 2,
            "missing_names_count": 0,
            "will_create": 8,
            "will_update": 2
        },
        "cost_estimate": {
            "estimated_total_usd": "2.50",
            "estimated_per_application_usd": "0.25",
            "within_budget": true
        },
        "recommended_mode": "quick",
        "mode_reason": "File has 10 rows, suitable for quick import",
        "can_use_quick_mode": true,
        "warnings": []
    }
    """
    try:
        # Validate file upload
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        # Get optional parameters
        archimate_mode = request.form.get("archimate_mode", "standard")
        enable_ai = request.form.get("enable_ai", "true").lower() == "true"
        budget_limit = None
        if request.form.get("budget_limit_usd"):
            try:
                budget_limit = float(request.form.get("budget_limit_usd"))
            except (ValueError, TypeError):
                logger.exception("Failed to request processing")
                pass

        check_duplicates = request.form.get("check_duplicates", "true").lower() == "true"

        # Create orchestrator and analyze
        orchestrator = ImportOrchestrator()

        result = orchestrator.analyze_import(
            file_storage=file,
            user_id=current_user.id,
            archimate_mode=archimate_mode,
            enable_ai=enable_ai,
            budget_limit_usd=budget_limit,
            check_database_duplicates=check_duplicates,
        )

        logger.info(
            f"Analysis complete: {file.filename}, "
            f"recommended_mode: {result.recommended_mode.value}"
        )

        return jsonify(result.to_dict()), 200

    except Exception as e:
        logger.error(f"Error analyzing import file: {e}", exc_info=True)
        return jsonify({"error": "Analysis failed. Please try again."}), 500


@bp.route("/quick", methods=["POST"])
@login_required
def quick_import():
    """
    Execute a quick mode import (direct commit, no approval).

    Only accepts files with < 50 rows (QUICK_MODE_MAX_ROWS = 100).
    Creates/updates applications immediately.

    Request:
    {
        "file": <file>,
        "archimate_mode": "quick|standard|comprehensive" (optional, default: "standard"),
        "enable_ai": bool (optional, default: true),
        "conflict_resolution": "skip|overwrite|merge" (optional, default: "merge"),
        "update_existing": bool (optional, deprecated — use conflict_resolution)
    }

    Response (200):
    {
        "success": true,
        "applications_created": 5,
        "applications_updated": 2,
        "applications_skipped": 0,
        "elements_generated": 84,
        "total_cost_usd": 2.10,
        "processing_time_seconds": 3.45,
        "errors": [],
        "created_app_ids": [101, 102, 103, 104, 105]
    }
    """
    try:
        # Validate file upload
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        # Get optional parameters
        archimate_mode = request.form.get("archimate_mode", "standard")
        enable_ai = request.form.get("enable_ai", "true").lower() == "true"

        # conflict_resolution supersedes the legacy update_existing boolean.
        # Accepted values: "skip", "overwrite", "merge" (default).
        conflict_resolution = request.form.get("conflict_resolution")
        update_existing = request.form.get("update_existing", "true").lower() == "true"

        # Create orchestrator and execute quick import
        orchestrator = ImportOrchestrator()

        result = orchestrator.start_quick_import(
            file_storage=file,
            user_id=current_user.id,
            archimate_mode=archimate_mode,
            enable_ai=enable_ai,
            update_existing=update_existing,
            conflict_resolution=conflict_resolution,
        )

        if result.success:
            logger.info(
                f"Quick import successful: {result.applications_created} created, "
                f"{result.applications_updated} updated"
            )
            return jsonify(result.to_dict()), 200
        else:
            logger.warning(f"Quick import failed: {result.errors}")
            return jsonify(result.to_dict()), 400

    except Exception as e:
        logger.error(f"Error during quick import: {e}", exc_info=True)
        return jsonify({"error": "Quick import failed. Please try again."}), 500


@bp.route("/governed", methods=["POST"])
@login_required
def governed_import():
    """
    Start a governed mode import (creates batch job for dashboard).

    For files with >= 50 rows or high cost estimates.
    Creates a batch import job that can be monitored/approved via dashboard.

    Request:
    {
        "file": <file>,
        "job_name": "string" (optional),
        "archimate_mode": "quick|standard|comprehensive" (optional, default: "standard"),
        "enable_ai": bool (optional, default: true),
        "budget_limit_usd": float (optional),
        "batch_size": int (optional, default: 10)
    }

    Response (202 Accepted):
    {
        "job_id": 42,
        "job_name": "Import applications.csv",
        "status": "processing",
        "total_rows": 150,
        "batch_size": 10,
        "estimated_cost_usd": 7.50,
        "created_at": "2026-02-02T14:30:00Z"
    }
    """
    try:
        # Validate file upload
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        # Get optional parameters
        job_name = request.form.get("job_name", f"Import {file.filename}")
        archimate_mode = request.form.get("archimate_mode", "standard")
        enable_ai = request.form.get("enable_ai", "true").lower() == "true"
        budget_limit = None
        if request.form.get("budget_limit_usd"):
            try:
                budget_limit = float(request.form.get("budget_limit_usd"))
            except (ValueError, TypeError):
                logger.exception("Failed to request processing")
                pass
        batch_size = int(request.form.get("batch_size", 10))

        # Create orchestrator and start governed import
        orchestrator = ImportOrchestrator()

        job = orchestrator.start_governed_import(
            file_storage=file,
            user_id=current_user.id,
            job_name=job_name,
            archimate_mode=archimate_mode,
            enable_ai=enable_ai,
            budget_limit_usd=budget_limit,
            batch_size=batch_size,
        )

        logger.info(
            f"Governed import job created: {job.id} - {job.filename}, "
            f"total_applications: {job.total_applications}"
        )

        # Return job details
        return (
            jsonify(
                {
                    "job_id": job.id,
                    "job_name": job.filename,
                    "status": job.status.value if hasattr(job.status, "value") else str(job.status),
                    "total_rows": job.total_applications,
                    "batch_size": job.batch_size,
                    "estimated_cost_usd": str(job.estimated_cost_usd)
                    if job.estimated_cost_usd
                    else "0",
                    "created_at": job.created_at.isoformat()
                    if hasattr(job, "created_at") and job.created_at
                    else None,
                }
            ),
            202,
        )

    except Exception as e:
        logger.error(f"Error starting governed import: {e}", exc_info=True)
        return jsonify({"error": "Governed import failed. Please try again."}), 500
