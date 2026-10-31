"""
DEPRECATED: This file is migrated to app/modules/import_batch/.
Registration is now centralized via app.modules.import_batch.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Batch Import API Routes

REST API endpoints for batch import operations with approval workflow.
Includes Server-Sent Events (SSE) for real-time progress streaming.
"""

import json
import logging
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context, url_for
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from app import db
from app.models.audit_log import AuditLog
from app.models.batch_import import BatchImportJob, BatchJobStatus
from app.services.batch_approval_service import BatchApprovalService
from app.modules.import_batch.services.batch_import_service import ActiveJobExists, ImportRefused
from app.services.batch_import_service import BatchImportService
from app.services.import_audit_service import log_batch_approval  # dead-code-ok
from app.services.batch_processor_service import BatchProcessorService
from app.utils.error_sanitizer import ErrorSanitizer, handle_import_error
from app.utils.file_validation import validate_mime_type, InvalidFileTypeError, get_allowed_extensions_display
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

batch_import_bp = Blueprint(
    "batch_import_api", __name__, url_prefix="/api/batch-import"
)

from app.utils.import_rate_limiter import import_rate_limit  # dead-code-ok
from app.schemas.api_schemas import BatchImportOptionsSchema, _load_and_validate


# Services
import_service = BatchImportService()
processor_service = BatchProcessorService()
approval_service = BatchApprovalService()


# =============================================================================
# JOB MANAGEMENT
# =============================================================================


@batch_import_bp.route("/jobs", methods=["POST"])
@login_required
@import_rate_limit(max_calls_per_minute=5, max_calls_per_hour=30, max_calls_per_day=150)
def create_job():
    """
    Create a new batch import job.

    Expects multipart form with:
    - file: The file to import (CSV, Excel, JSON)
    - batch_size: (optional) Applications per batch, default 20
    - archimate_mode: (optional) quick/standard/comprehensive
    - enable_ai_generation: (optional) Boolean
    - budget_limit_usd: (optional) Maximum budget
    - confidence_threshold: (optional) Auto-approval threshold
    - auto_approve_high_confidence: (optional) Boolean
    """
    try:
        # DEF-042, Capgemini dry-run: the "Paste Data" tab never sends a
        # `file` part at all — it posts raw text as the `paste_data` form
        # field (see app/templates/batch_import/new_import.html) — so this
        # unconditionally required `file` and every paste-path submission
        # failed with "No file provided" after the preview and cost estimate
        # had already rendered (client-side, from the same pasted text).
        # Build an in-memory file from the pasted text so the paste path
        # reaches the same import pipeline as an uploaded file.
        if "file" in request.files and request.files["file"].filename:
            file = request.files["file"]
        else:
            paste_data = request.form.get("paste_data", "").strip()
            if not paste_data:
                return jsonify({"success": False, "error": "No file provided"}), 400
            from io import BytesIO

            from werkzeug.datastructures import FileStorage

            file = FileStorage(
                stream=BytesIO(paste_data.encode("utf-8")),
                filename="pasted_data.csv",
                content_type="text/csv",
            )

        if file.filename == "":
            return jsonify({"success": False, "error": "No file selected"}), 400

        # Store original filename before sanitization
        original_filename = file.filename

        # Security: sanitize filename
        file.filename = secure_filename(file.filename)
        if not file.filename:
            # IMP-002: Audit failed upload
            AuditLog.log_file_upload(
                user_id=current_user.id,
                filename=original_filename,
                ip_address=request.remote_addr,
                route=request.endpoint,
                status="failed",
                error_message="Invalid filename"
            )
            return jsonify({"success": False, "error": "Invalid filename"}), 400

        # Security: validate MIME type (IMP-001: File upload security)
        try:
            mime_type = validate_mime_type(file, file.filename)
        except InvalidFileTypeError:
            # IMP-002: Audit failed upload
            AuditLog.log_file_upload(
                user_id=current_user.id,
                filename=original_filename,
                sanitized_filename=file.filename,
                ip_address=request.remote_addr,
                route=request.endpoint,
                status="failed",
                error_message="Invalid file type. Please upload a valid file format."
            )
            return jsonify({
                "success": False,
                "error": f"Invalid file type. Allowed: {get_allowed_extensions_display()}"
            }), 400

        # Get file size for audit logging
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning

        # T-031: marshmallow schema validation for import options
        _raw_opts = {
            "batch_size": request.form.get("batch_size", type=int),
            "archimate_mode": request.form.get("archimate_mode"),
            "enable_ai_generation": request.form.get("enable_ai_generation"),
            "budget_limit_usd": request.form.get("budget_limit_usd", type=float),
            "confidence_threshold": request.form.get("confidence_threshold", type=float),
            "auto_approve_high_confidence": request.form.get("auto_approve_high_confidence"),
        }
        # Strip None values so marshmallow uses load_defaults
        _raw_opts = {k: v for k, v in _raw_opts.items() if v is not None}
        # Coerce string booleans before schema load
        for _bool_key in ("enable_ai_generation", "auto_approve_high_confidence"):
            if isinstance(_raw_opts.get(_bool_key), str):
                _raw_opts[_bool_key] = _raw_opts[_bool_key].lower() == "true"
        _opts_schema = BatchImportOptionsSchema()
        _validated_opts, _opts_err = _load_and_validate(_opts_schema, _raw_opts)
        if _opts_err is not None:
            return _opts_err
        # Get options from validated schema output
        batch_size = _validated_opts.get("batch_size")
        archimate_mode = _validated_opts["archimate_mode"]
        enable_ai = _validated_opts["enable_ai_generation"]
        budget = _validated_opts.get("budget_limit_usd")
        threshold = _validated_opts["confidence_threshold"]
        auto_approve = _validated_opts["auto_approve_high_confidence"]

        job = import_service.create_job(
            user_id=current_user.id,
            file=file,
            batch_size=batch_size,
            archimate_mode=archimate_mode,
            enable_ai_generation=enable_ai,
            budget_limit_usd=budget,
            confidence_threshold=threshold,
            auto_approve_high_confidence=auto_approve,
            name=request.form.get("name") or None,
        )

        # PROG-002: optional Transformation Programme target — committed apps
        # get linked to the programme's current-state baseline solution.
        programme_id = request.form.get("programme_initiative_id", type=int)
        if programme_id:
            mappings = dict(job.custom_field_mappings or {})
            mappings["programme_initiative_id"] = programme_id
            job.custom_field_mappings = mappings
            db.session.commit()

        # IMP-002: Audit successful upload
        AuditLog.log_file_upload(
            user_id=current_user.id,
            filename=original_filename,
            sanitized_filename=file.filename,
            file_size_bytes=file_size,
            mime_type=mime_type,
            ip_address=request.remote_addr,
            route=request.endpoint,
            status="success"
        )

        job_data = job.to_dict()
        return jsonify(
            {
                "success": True,
                "data": job_data,
                "job": job_data,
                "job_id": job.id,
                "cost_estimate": import_service.estimate_cost(job),
                "message": "Import job created",
            }
        )

    except ActiveJobExists as e:
        return jsonify(
            {
                "success": False,
                "error": str(e),
                "existing_job_id": e.job_id,
                "existing_job_url": url_for("batch_import_view.job_detail", job_id=e.job_id),
            }
        ), 409
    except ImportRefused as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except ValueError as e:
        ErrorSanitizer.log_sanitized_warning(
            error=e,
            error_type='validation',
            context={'operation': 'batch_import_create'},
            user_id=current_user.id
        )
        return jsonify(
            {
                "success": False,
                "error": "Invalid file or parameters. Please check your file format (CSV, Excel, or JSON).",
            }
        ), 400
    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_create',
            context={'file_filename': file.filename if file else None},
            user_id=current_user.id
        )
        return jsonify(
            {
                "success": False,
                "error": sanitized_error['error'],
                "error_code": sanitized_error['error_code']
            }
        ), 500


@batch_import_bp.route("/jobs/analyze", methods=["POST"])
@login_required
@import_rate_limit(max_calls_per_minute=10, max_calls_per_hour=50, max_calls_per_day=200)
def analyze_import():
    """
    Analyze import file before creating job.

    Returns:
    - File stats (row count, columns)
    - Duplicate detection (in-file and database)
    - Data quality issues (missing required fields, invalid dates)
    - Cost estimation
    - Required vs optional fields
    """
    try:
        if "file" not in request.files:
            return jsonify({"success": False, "error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"success": False, "error": "No file selected"}), 400

        # Get options for cost estimation
        archimate_mode = request.form.get("archimate_mode", "standard")
        enable_ai = request.form.get("enable_ai_generation", "true").lower() == "true"

        # Analyze the file
        analysis = import_service.analyze_file(
            file=file,
            user_id=current_user.id,
            archimate_mode=archimate_mode,
            enable_ai_generation=enable_ai,
        )

        return jsonify({"success": True, **analysis})

    except ValueError as e:
        ErrorSanitizer.log_sanitized_warning(
            error=e,
            error_type='validation',
            context={'operation': 'batch_import_analyze'},
            user_id=current_user.id
        )
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_analyze',
            user_id=current_user.id
        )
        return jsonify({
            "success": False, 
            "error": sanitized_error['error'],
            "error_code": sanitized_error['error_code']
        }), 500


@batch_import_bp.route("/jobs", methods=["GET"])
@login_required
def list_jobs():
    """List user's batch import jobs."""
    try:
        from sqlalchemy import func

        from app import db

        status = request.args.get("status")
        limit = safe_int_arg('limit', 50, minimum=1, maximum=500)
        offset = safe_int_arg('offset', 0, minimum=0)

        jobs, total = import_service.get_user_jobs(
            user_id=current_user.id,
            status_filter=status,
            limit=limit,
            offset=offset,
        )

        # Calculate stats for dashboard
        # Note: Jobs don't have elements_generated directly - need to aggregate from batches
        # For now, using actual_cost_usd and calculating elements from batches
        from app.models.batch_import import BatchImportBatch

        stats_query = (
            db.session.query(
                func.count(BatchImportJob.id).label("total_jobs"),
                func.sum(
                    db.case(
                        (BatchImportJob.status == BatchJobStatus.PROCESSING.value, 1),
                        else_=0,
                    )
                ).label("processing"),
                func.sum(BatchImportJob.actual_cost_usd).label("total_cost"),
            )
            .filter(BatchImportJob.user_id == current_user.id)
            .first()
        )

        # Get total elements generated from all batches for this user's jobs
        elements_query = (
            db.session.query(func.sum(BatchImportBatch.total_elements_generated))
            .join(BatchImportJob)
            .filter(BatchImportJob.user_id == current_user.id)
            .scalar()
        )

        stats = {
            "totalJobs": stats_query.total_jobs or 0,
            "processing": stats_query.processing or 0,
            "elementsGenerated": elements_query or 0,
            "totalCost": float(stats_query.total_cost or 0),
        }

        return jsonify(
            {
                "success": True,
                "jobs": [job.to_dict() for job in jobs],
                "stats": stats,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_list_jobs',
            user_id=current_user.id
        )
        return jsonify({
            "success": False, 
            "error": sanitized_error['error'],
            "error_code": sanitized_error['error_code']
        }), 500


@batch_import_bp.route("/jobs/<int:job_id>", methods=["GET"])
@login_required
def get_job(job_id):
    """Get job details."""
    try:
        from app.models.batch_import import BatchImportJob

        job = BatchImportJob.query.get_or_404(job_id)

        if job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        job_data = job.to_dict()
        batches = [b.to_dict() for b in job.batches]
        return jsonify(
            {
                "success": True,
                "data": job_data,
                "job": job_data,
                "batches": batches,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_get_job',
            context={'job_id': job_id},
            user_id=current_user.id
        )
        return jsonify({
            "success": False, 
            "error": sanitized_error['error'],
            "error_code": sanitized_error['error_code']
        }), 500


@batch_import_bp.route("/jobs/<int:job_id>/progress", methods=["GET"])
@login_required
def get_job_progress(job_id):
    """Get real-time job progress."""
    try:
        from app.models.batch_import import BatchImportJob

        job = BatchImportJob.query.get_or_404(job_id)

        if job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        progress = import_service.get_job_progress(job_id)

        return jsonify(
            {
                "success": True,
                "data": progress,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_job_progress',
            context={'job_id': job_id},
            user_id=current_user.id
        )
        return jsonify({
            "success": False, 
            "error": sanitized_error['error'],
            "error_code": sanitized_error['error_code']
        }), 500


@batch_import_bp.route("/jobs/<int:job_id>/start", methods=["POST"])
@login_required
def start_job(job_id):
    """
    Start processing a job.

    When Celery is enabled (CELERY_ENABLED=true), the job is dispatched
    to a background worker and the endpoint returns HTTP 202 with a
    task_id that can be polled via /jobs/<id>/task-status/<task_id>.

    When Celery is not enabled, behaviour is unchanged: the job is
    marked as PROCESSING and the client should connect to the /stream
    SSE endpoint for real-time progress updates.
    """
    try:
        job = import_service.start_job(job_id, current_user.id)

        # Attempt async dispatch when Celery is enabled
        from app.tasks.import_tasks import is_celery_available, dispatch_import_job

        if is_celery_available():
            result = dispatch_import_job(job_id)
            if result.get("async"):
                return jsonify(
                    {
                        "success": True,
                        "data": job.to_dict(),
                        "async": True,
                        "task_id": result["task_id"],
                        "message": "Job dispatched to background worker.",
                        "status_url": f"/api/batch-import/jobs/{job_id}/task-status/{result['task_id']}",
                    }
                ), 202

        # Synchronous fallback -- existing behaviour
        return jsonify(
            {
                "success": True,
                "data": job.to_dict(),
                "async": False,
                "message": "Job started. Connect to /stream endpoint for real-time progress.",
                "stream_url": f"/api/batch-import/jobs/{job_id}/stream",
            }
        )

    except PermissionError:
        return jsonify({"success": False, "error": "Access denied"}), 403
    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_start_job',
            context={'job_id': job_id},
            user_id=current_user.id
        )
        return jsonify({
            "success": False,
            "error": sanitized_error['error'],
            "error_code": sanitized_error['error_code']
        }), 500


@batch_import_bp.route("/jobs/<int:job_id>/task-status/<task_id>", methods=["GET"])
@login_required
def get_task_status(job_id, task_id):
    """
    Poll the status of an async Celery import task.

    Path Parameters:
        job_id: Batch import job ID (for ownership verification)
        task_id: Celery task ID returned by the /start endpoint

    Returns:
        JSON with task state (PENDING, STARTED, SUCCESS, FAILURE, RETRY)
    """
    try:
        from app.models.batch_import import BatchImportJob

        job = BatchImportJob.query.get_or_404(job_id)

        if job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        from app.tasks.import_tasks import get_import_status

        status = get_import_status(task_id)
        status["success"] = True
        status["job"] = job.to_dict()

        return jsonify(status)

    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_task_status',
            context={'job_id': job_id, 'task_id': task_id},
            user_id=current_user.id
        )
        return jsonify({
            "success": False,
            "error": sanitized_error['error'],
            "error_code": sanitized_error['error_code']
        }), 500


@batch_import_bp.route("/jobs/<int:job_id>/stream", methods=["GET"])
@login_required
def stream_job_progress(job_id):
    """
    Server-Sent Events endpoint for real-time job processing.

    Processes batches inline and streams progress events to the client.
    Events:
    - job_started: Job processing has begun
    - batch_started: A batch is being processed
    - app_processing: An application within the batch is being processed
    - app_completed: An application finished processing
    - batch_completed: A batch finished processing
    - batch_ready: A batch is ready for review
    - job_paused: Job was paused by user
    - job_completed: All batches processed
    - job_failed: Job encountered an error
    - error: An error occurred
    """
    from app.models.batch_import import (
        BatchImportBatch,
        BatchImportJob,
        BatchJobStatus,
        BatchStatus,
    )
    from app.utils.route_guards import require_entity

    # Refuse before the stream opens: a 200 text/event-stream for a job that does
    # not exist is a stream of invented progress.
    require_entity(BatchImportJob, job_id, description="Job not found")

    def generate():
        """Generator function for SSE events."""
        try:
            job = BatchImportJob.query.get(job_id)
            if not job:
                yield _sse_event("error", {"message": "Job not found"})
                return

            if job.user_id != current_user.id:
                yield _sse_event("error", {"message": "Access denied"})
                return

            # Check if job can be processed
            if job.status not in [BatchJobStatus.PROCESSING, BatchJobStatus.PAUSED]:
                yield _sse_event(
                    "error",
                    {
                        "message": f"Job cannot be processed in status: {job.status.value}"
                    },
                )
                return

            # Resume if paused
            if job.status == BatchJobStatus.PAUSED:
                job.status = BatchJobStatus.PROCESSING
                db.session.commit()

            yield _sse_event(
                "job_started",
                {
                    "job_id": job.id,
                    "total_batches": job.total_batches,
                    "total_applications": job.total_applications,
                },
            )

            # Process each queued batch
            while True:
                # Refresh job status to check for pause/cancel
                db.session.refresh(job)

                if job.status == BatchJobStatus.PAUSED:
                    yield _sse_event(
                        "job_paused",
                        {
                            "job_id": job.id,
                            "message": "Job paused by user",
                            "batches_completed": job.batches_completed,
                        },
                    )
                    return

                if job.status == BatchJobStatus.CANCELLED:
                    yield _sse_event(
                        "job_cancelled",
                        {
                            "job_id": job.id,
                            "message": "Job cancelled",
                        },
                    )
                    return

                # Check budget
                if job.budget_limit_usd and job.actual_cost_usd >= job.budget_limit_usd:
                    job.status = BatchJobStatus.PAUSED
                    job.error_message = "Budget limit reached"
                    db.session.commit()
                    yield _sse_event(
                        "job_paused",
                        {
                            "job_id": job.id,
                            "message": "Budget limit reached",
                            "actual_cost": float(job.actual_cost_usd),
                            "budget_limit": float(job.budget_limit_usd),
                        },
                    )
                    return

                # Get next batch to process
                batch = (
                    BatchImportBatch.query.filter(
                        BatchImportBatch.job_id == job_id,
                        BatchImportBatch.status == BatchStatus.QUEUED,
                    )
                    .order_by(BatchImportBatch.batch_number)
                    .first()
                )

                if not batch:
                    # No more batches to process
                    break

                # Process this batch with progress events
                for event in _process_batch_with_events(batch, job):
                    yield event

                # Send heartbeat to keep connection alive
                yield _sse_event("heartbeat", {"timestamp": time.time()})

            # Check if all batches are done
            ready_count = BatchImportBatch.query.filter(
                BatchImportBatch.job_id == job_id,
                BatchImportBatch.status == BatchStatus.READY_FOR_REVIEW,
            ).count()

            committed_count = BatchImportBatch.query.filter(
                BatchImportBatch.job_id == job_id,
                BatchImportBatch.status == BatchStatus.COMMITTED,
            ).count()
            rejected_count = BatchImportBatch.query.filter(
                BatchImportBatch.job_id == job_id,
                BatchImportBatch.status == BatchStatus.REJECTED,
            ).count()
            skipped_count = BatchImportBatch.query.filter(
                BatchImportBatch.job_id == job_id,
                BatchImportBatch.status == BatchStatus.SKIPPED,
            ).count()

            terminal_count = committed_count + rejected_count + skipped_count
            if ready_count + terminal_count >= job.total_batches:
                if ready_count > 0:
                    yield _sse_event(
                        "job_ready_for_review",
                        {
                            "job_id": job.id,
                            "batches_ready": ready_count,
                            "message": "All batches processed. Ready for review.",
                        },
                    )
                else:
                    import_service.check_and_complete_job(job.id)
                    db.session.refresh(job)
                    yield _sse_event(
                        "job_completed",
                        {
                            "job_id": job.id,
                            "total_elements": sum(
                                b.total_elements_generated for b in job.batches
                            ),
                            "total_cost": float(job.actual_cost_usd),
                        },
                    )

        except HTTPException:
            raise
        except Exception as e:
            sanitized_error = handle_import_error(
                error=e,
                operation='batch_import_stream',
                context={'job_id': job_id},
                user_id=current_user.id
            )
            yield _sse_event(
                "error",
                {
                    "message": sanitized_error['error'],
                    "error_code": sanitized_error['error_code']
                }
            )

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


def _sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _process_batch_with_events(batch, job):
    """
    Process a batch and yield SSE events for progress.

    This is a generator that processes applications one by one
    and yields progress events.
    """
    from datetime import datetime

    from app.models.batch_import import (
        AppProcessingStatus,
        BatchImportApplication,
        BatchImportCheckpoint,
        BatchStatus,
        CheckpointType,
    )

    batch.status = BatchStatus.PROCESSING
    batch.started_at = batch.started_at or datetime.utcnow()
    db.session.commit()

    yield _sse_event(
        "batch_started",
        {
            "batch_id": batch.id,
            "batch_number": batch.batch_number,
            "total_applications": batch.total_applications,
        },
    )

    # Create checkpoint
    checkpoint = BatchImportCheckpoint(
        batch_id=batch.id,
        checkpoint_type=CheckpointType.BATCH_STARTED,
        checkpoint_name=f"batch_{batch.batch_number}_started",
        elements_staged=0,
    )
    db.session.add(checkpoint)
    db.session.commit()

    try:
        # Get applications to process
        applications = (
            BatchImportApplication.query.filter(
                BatchImportApplication.batch_id == batch.id,
                BatchImportApplication.status.in_(
                    [
                        AppProcessingStatus.PENDING,
                        AppProcessingStatus.FAILED,
                    ]
                ),
            )
            .order_by(BatchImportApplication.row_number)
            .all()
        )

        for app in applications:
            # Check if job was paused (refresh from DB)
            db.session.refresh(job)
            if job.status != BatchJobStatus.PROCESSING:
                batch.status = BatchStatus.QUEUED
                db.session.commit()
                return

            yield _sse_event(
                "app_processing",
                {
                    "batch_id": batch.id,
                    "app_id": app.id,
                    "app_name": app.application_name,
                    "progress": batch.processed_applications,
                    "total": batch.total_applications,
                },
            )

            # Process the application
            start_time = time.time()
            
            # Create savepoint for this application processing
            db.session.begin_nested()
            
            try:
                # Set processing status
                app.status = AppProcessingStatus.PROCESSING
                batch.current_application_name = app.application_name
                
                # Don't commit yet - wait until completion
                
                elements = []

                if job.enable_ai_generation:
                    # Generate elements (using processor service logic)
                    elements = _generate_elements_inline(app, job, batch)

                # Update application stats
                app.status = AppProcessingStatus.COMPLETED
                app.elements_generated = len(elements)
                app.processing_time_seconds = time.time() - start_time

                if elements:
                    confidence_scores = [
                        e.confidence_score for e in elements if e.confidence_score
                    ]
                    if confidence_scores:
                        app.average_confidence_score = sum(confidence_scores) / len(
                            confidence_scores
                        )

                # Update batch stats
                batch.processed_applications += 1
                batch.successful_applications += 1
                batch.total_elements_generated += len(elements)
                batch.current_application_name = None

                app.processed_at = datetime.utcnow()
                
                # Commit all application changes in one transaction
                db.session.commit()
                
                # Release savepoint
                db.session.commit()

                yield _sse_event(
                    "app_completed",
                    {
                        "batch_id": batch.id,
                        "app_id": app.id,
                        "app_name": app.application_name,
                        "elements_generated": len(elements),
                        "processing_time": round(time.time() - start_time, 2),
                        "progress": batch.processed_applications,
                        "total": batch.total_applications,
                    },
                )

            except HTTPException:
                raise
            except Exception as e:
                # Rollback the application savepoint
                db.session.rollback()
                
                sanitized_error = handle_import_error(
                    error=e,
                    operation='batch_import_process_application',
                    context={'app_id': app.id, 'job_id': job.id},
                    user_id=current_user.id
                )
                app.status = AppProcessingStatus.FAILED
                app.error_message = sanitized_error['error']
                batch.processed_applications += 1
                batch.failed_applications += 1
                batch.current_application_name = None
                
                # Commit error state
                db.session.commit()

                yield _sse_event(
                    "app_failed",
                    {
                        "batch_id": batch.id,
                        "app_id": app.id,
                        "app_name": app.application_name,
                        "error": "Failed to process application",
                    },
                )

        # Batch completed
        batch.status = BatchStatus.READY_FOR_REVIEW
        batch.completed_at = datetime.utcnow()

        # Update job progress
        job.batches_completed = sum(
            1
            for b in job.batches
            if b.status
            in [
                BatchStatus.READY_FOR_REVIEW,
                BatchStatus.APPROVED,
                BatchStatus.COMMITTED,
            ]
        )

        # Create completion checkpoint
        checkpoint = BatchImportCheckpoint(
            batch_id=batch.id,
            checkpoint_type=CheckpointType.BATCH_COMPLETED,
            checkpoint_name=f"batch_{batch.batch_number}_completed",
            elements_staged=batch.total_elements_generated,
        )
        db.session.add(checkpoint)
        db.session.commit()

        yield _sse_event(
            "batch_completed",
            {
                "batch_id": batch.id,
                "batch_number": batch.batch_number,
                "status": "ready_for_review",
                "successful": batch.successful_applications,
                "failed": batch.failed_applications,
                "elements_generated": batch.total_elements_generated,
                "job_progress": job.batches_completed,
                "job_total": job.total_batches,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_process_batch',
            context={'batch_id': batch.id},
            user_id=current_user.id
        )
        batch.status = BatchStatus.FAILED
        batch.error_message = sanitized_error['error']
        batch.retry_count += 1
        db.session.commit()

        yield _sse_event(
            "batch_failed",
            {
                "batch_id": batch.id,
                "batch_number": batch.batch_number,
                "error": "Batch processing failed. Check job details for more information.",
                "can_retry": batch.can_retry,
            },
        )


def _generate_elements_inline(app, job, batch):
    """
    Generate ArchiMate elements for an application inline.

    This is a simplified version that generates mock elements.
    In production, this would call the actual AI service.
    """
    import uuid
    from decimal import Decimal

    from app.models.batch_import import BatchImportElement, ElementApprovalStatus

    elements = []
    app_name = app.application_name
    mode = job.archimate_mode

    # Define layers and element types based on mode
    if mode == "quick":
        layers = ["application"]
        elements_per_layer = 3
    elif mode == "comprehensive":
        layers = [
            "motivation",
            "strategy",
            "business",
            "application",
            "technology",
            "implementation",
        ]
        elements_per_layer = 4
    else:  # standard
        layers = ["business", "application", "technology"]
        elements_per_layer = 3

    layer_element_types = {
        "motivation": ["stakeholder", "driver", "goal"],
        "strategy": ["capability", "resource", "course_of_action"],
        "business": ["business_actor", "business_process", "business_service"],
        "application": ["application_component", "application_service", "data_object"],
        "technology": ["node", "system_software", "technology_service"],
        "implementation": ["work_package", "deliverable", "plateau"],
    }

    for layer in layers:
        element_types = layer_element_types.get(layer, ["element"])[:elements_per_layer]

        for elem_type in element_types:
            elem_data = {
                "layer": layer,
                "type": elem_type,
                "name": f"{app_name} {elem_type.replace('_', ' ').title()}",
                "description": f"Auto-generated {elem_type.replace('_', ' ')} for {app_name}",
                "confidence": 0.75 + (0.2 * (1 if layer == "application" else 0.5)),
                "model": "gpt - 4",
                "properties": {},
            }

            element = BatchImportElement(
                batch_id=batch.id,
                application_id=app.id,
                element_uuid=str(uuid.uuid4()),
                element_type="archimate_element",
                element_subtype=elem_type,
                element_name=elem_data["name"],
                element_description=elem_data["description"],
                element_data=elem_data,
                archimate_layer=layer,
                generated_by_model="gpt - 4",
                confidence_score=elem_data["confidence"],
                approval_status=ElementApprovalStatus.PENDING,
            )
            db.session.add(element)
            elements.append(element)

    # Track costs (estimated)
    tokens_used = len(elements) * 500
    cost = Decimal(str(tokens_used * 0.00003))

    app.tokens_used = tokens_used
    app.llm_calls = 1
    app.cost_usd = cost

    batch.batch_tokens_used += tokens_used
    batch.batch_llm_calls += 1
    batch.batch_cost_usd += cost

    job.total_tokens_used += tokens_used
    job.total_llm_calls += 1
    job.actual_cost_usd += cost

    db.session.flush()

    return elements


@batch_import_bp.route("/jobs/<int:job_id>/pause", methods=["POST"])
@login_required
def pause_job(job_id):
    """Pause a running job."""
    try:
        job = import_service.pause_job(job_id, current_user.id)

        return jsonify(
            {
                "success": True,
                "data": job.to_dict(),
                "message": "Job paused",
            }
        )

    except PermissionError:
        return jsonify({"success": False, "error": "Access denied"}), 403
    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_pause_job',
            context={'job_id': job_id},
            user_id=current_user.id
        )
        return jsonify({
            "success": False, 
            "error": sanitized_error['error'],
            "error_code": sanitized_error['error_code']
        }), 500


@batch_import_bp.route("/jobs/<int:job_id>/resume", methods=["POST"])
@login_required
def resume_job(job_id):
    """Resume a paused job."""
    try:
        job = import_service.resume_job(job_id, current_user.id)

        return jsonify(
            {
                "success": True,
                "data": job.to_dict(),
                "message": "Job resumed",
            }
        )

    except PermissionError:
        return jsonify({"success": False, "error": "Access denied"}), 403
    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_resume_job',
            context={'job_id': job_id},
            user_id=current_user.id
        )
        return jsonify({
            "success": False, 
            "error": sanitized_error['error'],
            "error_code": sanitized_error['error_code']
        }), 500


@batch_import_bp.route("/jobs/<int:job_id>/cancel", methods=["POST"])
@login_required
def cancel_job(job_id):
    """Cancel a job."""
    try:
        delete_staged = (
            request.json.get("delete_staged", True) if request.json else True
        )
        job = import_service.cancel_job(job_id, current_user.id, delete_staged)

        return jsonify(
            {
                "success": True,
                "data": job.to_dict(),
                "message": "Job cancelled",
            }
        )

    except PermissionError:
        return jsonify({"success": False, "error": "Access denied"}), 403
    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        sanitized_error = handle_import_error(
            error=e,
            operation='batch_import_cancel_job',
            context={'job_id': job_id},
            user_id=current_user.id
        )
        return jsonify({
            "success": False, 
            "error": sanitized_error['error'],
            "error_code": sanitized_error['error_code']
        }), 500


@batch_import_bp.route("/jobs/<int:job_id>", methods=["DELETE"])
@login_required
def delete_job(job_id):
    """Delete a job."""
    try:
        import_service.delete_job(job_id, current_user.id)

        return jsonify(
            {
                "success": True,
                "message": "Job deleted",
            }
        )

    except PermissionError:
        return jsonify({"success": False, "error": "Access denied"}), 403
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting job: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to delete job"}), 500


# =============================================================================
# BATCH MANAGEMENT
# =============================================================================


@batch_import_bp.route("/jobs/<int:job_id>/batches", methods=["GET"])
@login_required
def list_batches(job_id):
    """List all batches for a job."""
    try:
        from app.models.batch_import import BatchImportJob

        job = BatchImportJob.query.get_or_404(job_id)

        if job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        batches = [b.to_dict() for b in job.batches]
        return jsonify(
            {
                "success": True,
                "data": batches,
                "batches": batches,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing batches: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to list batches"}), 500


@batch_import_bp.route("/batches/<int:batch_id>", methods=["GET"])
@login_required
def get_batch(batch_id):
    """Get batch details with summary."""
    try:
        summary = approval_service.get_batch_summary(batch_id)

        # Verify access
        from app.models.batch_import import BatchImportBatch

        batch = BatchImportBatch.query.get_or_404(batch_id)
        if batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        return jsonify(
            {
                "success": True,
                "data": summary,
                "batch": summary,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting batch: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to get batch"}), 500


@batch_import_bp.route("/batches/<int:batch_id>/elements", methods=["GET"])
@login_required
def get_batch_elements(batch_id):
    """Get elements in a batch with filtering."""
    try:
        from app.models.batch_import import BatchImportBatch

        batch = BatchImportBatch.query.get_or_404(batch_id)
        if batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        status = request.args.get("status")
        layer = request.args.get("layer")
        limit = safe_int_arg('limit', 100, minimum=1, maximum=500)
        offset = safe_int_arg('offset', 0, minimum=0)

        elements, total = approval_service.get_batch_elements(
            batch_id=batch_id,
            status_filter=status,
            layer_filter=layer,
            limit=limit,
            offset=offset,
        )

        element_data = [e.to_dict() for e in elements]
        return jsonify(
            {
                "success": True,
                "data": element_data,
                "elements": element_data,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting batch elements: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to get elements"}), 500


@batch_import_bp.route("/batches/<int:batch_id>/approve", methods=["POST"])
@login_required
def approve_batch(batch_id):
    """Approve all elements in a batch."""
    try:
        from app.models.batch_import import BatchImportBatch

        batch = BatchImportBatch.query.get_or_404(batch_id)
        if batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        data = request.json or {}
        notes = data.get("notes")
        auto_commit = data.get("auto_commit", True)

        batch = approval_service.approve_batch(
            batch_id=batch_id,
            user_id=current_user.id,
            notes=notes,
            auto_commit=auto_commit,
        )

        # Log batch approval
        log_batch_approval(
            batch_id=batch_id,
            job_id=batch.job_id,
            approval_results={
                'approved_count': batch.elements_approved,
                'auto_commit': auto_commit,
                'notes': notes
            }
        )

        return jsonify(
            {
                "success": True,
                "data": batch.to_dict(),
                "message": "Batch approved" + (" and committed" if auto_commit else ""),
            }
        )

    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving batch: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to approve batch"}), 500


@batch_import_bp.route("/batches/<int:batch_id>/reject", methods=["POST"])
@login_required
def reject_batch(batch_id):
    """Reject a batch."""
    try:
        from app.models.batch_import import BatchImportBatch

        batch = BatchImportBatch.query.get_or_404(batch_id)
        if batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        data = request.json or {}
        reason = data.get("reason", "Rejected by user")

        batch = approval_service.reject_batch(
            batch_id=batch_id,
            user_id=current_user.id,
            reason=reason,
        )

        return jsonify(
            {
                "success": True,
                "data": batch.to_dict(),
                "message": "Batch rejected",
            }
        )

    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting batch: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to reject batch"}), 500


@batch_import_bp.route("/batches/<int:batch_id>/commit", methods=["POST"])
@login_required
def commit_batch(batch_id):
    """Commit approved batch elements to database."""
    try:
        from app.models.batch_import import BatchImportBatch

        batch = BatchImportBatch.query.get_or_404(batch_id)
        if batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        batch = approval_service.commit_batch(batch_id, current_user.id)

        batch_data = batch.to_dict()
        return jsonify(
            {
                "success": True,
                "data": batch_data,
                "batch": batch_data,
                "committed_count": batch_data.get("elements_approved", 0),
                "message": "Batch committed",
            }
        )

    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error committing batch: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to commit batch"}), 500


@batch_import_bp.route("/batches/<int:batch_id>/retry", methods=["POST"])
@login_required
def retry_batch(batch_id):
    """Retry a failed batch."""
    try:
        from app.models.batch_import import BatchImportBatch

        batch = BatchImportBatch.query.get_or_404(batch_id)
        if batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        batch = processor_service.recover_batch(batch_id)

        return jsonify(
            {
                "success": True,
                "data": batch.to_dict(),
                "message": "Batch queued for retry",
            }
        )

    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrying batch: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to retry batch"}), 500


@batch_import_bp.route("/batches/<int:batch_id>/auto-approve", methods=["POST"])
@login_required
def auto_approve_batch(batch_id):
    """Auto-approve high confidence elements in a batch."""
    try:
        from app.models.batch_import import BatchImportBatch

        batch = BatchImportBatch.query.get_or_404(batch_id)
        if batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        data = request.json or {}
        threshold = data.get("threshold", 0.85)

        count = approval_service.approve_high_confidence_elements(
            batch_id=batch_id,
            user_id=current_user.id,
            threshold=threshold,
        )

        return jsonify(
            {
                "success": True,
                "approved_count": count,
                "message": f"Auto-approved {count} elements with confidence >= {threshold}",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error auto-approving: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to auto-approve"}), 500


# =============================================================================
# ELEMENT MANAGEMENT
# =============================================================================


@batch_import_bp.route("/elements/<int:element_id>", methods=["GET"])
@login_required
def get_element(element_id):
    """Get element details."""
    try:
        from app.models.batch_import import BatchImportElement

        element = BatchImportElement.query.get_or_404(element_id)
        if element.batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        return jsonify(
            {
                "success": True,
                "data": element.to_dict(include_data=True),
            }
        )

    except HTTPException:
        # Let get_or_404 (unknown element) surface as a real 404, not a 500.
        raise
    except Exception as e:
        logger.error(f"Error getting element: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to get element"}), 500


@batch_import_bp.route("/elements/<int:element_id>", methods=["PUT"])
@login_required
def update_element(element_id):
    """Modify an element."""
    try:
        from app.models.batch_import import BatchImportElement

        element = BatchImportElement.query.get_or_404(element_id)
        if element.batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        element = approval_service.modify_element(
            element_id=element_id,
            user_id=current_user.id,
            new_data=data,
        )

        element_data = element.to_dict(include_data=True)
        return jsonify(
            {
                "success": True,
                "data": element_data,
                "element": element_data,
                "message": "Element modified",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error modifying element: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to modify element"}), 500


@batch_import_bp.route("/elements/<int:element_id>/approve", methods=["POST"])
@login_required
def approve_element(element_id):
    """Approve a single element."""
    try:
        from app.models.batch_import import BatchImportElement

        element = BatchImportElement.query.get_or_404(element_id)
        if element.batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        element = approval_service.approve_element(element_id, current_user.id)

        element_data = element.to_dict()
        return jsonify(
            {
                "success": True,
                "data": element_data,
                "element": element_data,
                "message": "Element approved",
            }
        )

    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving element: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to approve element"}), 500


@batch_import_bp.route("/elements/<int:element_id>/reject", methods=["POST"])
@login_required
def reject_element(element_id):
    """Reject a single element."""
    try:
        from app.models.batch_import import BatchImportElement

        element = BatchImportElement.query.get_or_404(element_id)
        if element.batch.job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        data = request.json or {}
        reason = data.get("reason", "Rejected by user")

        element = approval_service.reject_element(element_id, current_user.id, reason)

        element_data = element.to_dict()
        return jsonify(
            {
                "success": True,
                "data": element_data,
                "element": element_data,
                "message": "Element rejected",
            }
        )

    except ValueError:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting element: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to reject element"}), 500


# =============================================================================
# PROCESSING CONTROL
# =============================================================================


@batch_import_bp.route(
    "/jobs/<int:job_id>/process-batch/<int:batch_id>", methods=["POST"]
)
@login_required
def process_single_batch(job_id, batch_id):
    """
    Process a single batch synchronously.

    Use this for retrying failed batches or processing one batch at a time.
    For real-time progress, use the /stream endpoint instead.
    """
    try:
        from app.models.batch_import import BatchImportBatch, BatchImportJob

        job = BatchImportJob.query.get_or_404(job_id)
        if job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        batch = BatchImportBatch.query.get_or_404(batch_id)
        if batch.job_id != job_id:
            return jsonify(
                {"success": False, "error": "Batch does not belong to this job"}
            ), 400

        # Process the batch
        batch = processor_service.process_batch(batch.id)

        return jsonify(
            {
                "success": True,
                "data": batch.to_dict(),
                "message": f"Processed batch {batch.batch_number}",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing batch: {e}", exc_info=True)
        return jsonify(
            {"success": False, "error": "Batch processing failed. Please try again."}
        ), 500


# =============================================================================
# IMPORT PREVIEW & CONFLICT RESOLUTION
# =============================================================================


@batch_import_bp.route("/jobs/<int:job_id>/preview", methods=["GET"])
@login_required
def get_job_preview(job_id):
    """
    Generate import preview with validation, duplicate detection, and impact analysis.

    Returns preview data for a job in AWAITING_CONFIRMATION status.
    """
    try:
        from app.models.batch_import import BatchImportJob

        job = BatchImportJob.query.get_or_404(job_id)
        if job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        from app.services.import_preview_service import ImportPreviewService

        service = ImportPreviewService()
        preview_data = service.generate_preview(job_id)

        return jsonify(
            {
                "success": True,
                "data": preview_data,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating preview for job {job_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to generate preview"}), 500


@batch_import_bp.route("/jobs/<int:job_id>/resolve-conflicts", methods=["POST"])
@login_required
def resolve_import_conflicts(job_id):
    """
    Save conflict resolution choices for duplicate matches.

    Expects JSON body with:
    - resolutions: List of {import_row, action, target_app_id}
    """
    try:
        from app.models.batch_import import BatchImportJob

        job = BatchImportJob.query.get_or_404(job_id)
        if job.user_id != current_user.id:
            return jsonify({"success": False, "error": "Access denied"}), 403

        data = request.json
        if not data or "resolutions" not in data:
            return jsonify({"success": False, "error": "No resolutions provided"}), 400

        from app.services.import_preview_service import ImportPreviewService

        service = ImportPreviewService()
        result = service.resolve_conflicts(job_id, data["resolutions"])

        return jsonify(
            {
                "success": True,
                "data": result,
                "message": f"Saved {result['total_resolved']} conflict resolutions",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resolving conflicts for job {job_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to save resolutions"}), 500
