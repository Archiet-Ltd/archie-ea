"""
DEPRECATED: This file is migrated to app/modules/import_batch/.
Registration is now centralized via app.modules.import_batch.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Batch Import View Routes

Web interface for batch import operations with approval workflow.
Renders HTML pages for the batch import UI.
"""

import logging

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models.batch_import import BatchImportBatch, BatchImportJob, BatchJobStatus, BatchStatus
from app.services.batch_approval_service import BatchApprovalService
from app.services.batch_import_service import BatchImportService
from app.modules.import_batch.services.batch_import_service import format_estimate_money, org_money_format
from app.template_helpers import safe_url_for_with_fallback
from app.security.import_decorators import with_import_security
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

batch_import_view_bp = Blueprint("batch_import_view", __name__, url_prefix="/batch-import")

# Services
import_service = BatchImportService()
approval_service = BatchApprovalService()


# =============================================================================
# DASHBOARD
# =============================================================================


@batch_import_view_bp.route("/")
@login_required
@with_import_security
def dashboard():
    """
    Dashboard showing all batch import jobs.

    Displays jobs with filtering, pagination, and status summary.
    """
    try:
        # Get filter parameters
        status_filter = request.args.get("status")
        page = safe_int_arg('page', 1, minimum=1)
        per_page = safe_int_arg('per_page', 20, minimum=1, maximum=500)

        # Ensure reasonable limits
        per_page = min(per_page, 100)
        offset = (page - 1) * per_page

        # Get jobs for current user
        jobs, total = import_service.get_user_jobs(
            user_id=current_user.id,
            status_filter=status_filter,
            limit=per_page,
            offset=offset,
        )

        # Calculate pagination
        total_pages = (total + per_page - 1) // per_page

        # Get status counts for summary cards
        status_counts = _get_status_counts(current_user.id)

        # Get available statuses for filter dropdown
        available_statuses = [status.value for status in BatchJobStatus]

        return render_template(
            "batch_import/dashboard.html",
            jobs=jobs,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            status_filter=status_filter,
            status_counts=status_counts,
            available_statuses=available_statuses,
            money_format=org_money_format(),
        )

    except Exception as e:
        logger.error(f"Error loading batch import dashboard: {e}", exc_info=True)
        flash("Error loading dashboard. Please try again.", "error")
        return redirect(safe_url_for_with_fallback("admin.index", "/admin"))


def _get_status_counts(user_id: int) -> dict:
    """Get count of jobs by status for dashboard summary."""
    try:
        from sqlalchemy import func

        from app import db

        counts = (
            db.session.query(BatchImportJob.status, func.count(BatchImportJob.id))
            .filter(BatchImportJob.user_id == user_id)
            .group_by(BatchImportJob.status)
            .all()
        )

        result = {status.value: 0 for status in BatchJobStatus}
        for status, count in counts:
            if status:
                result[status.value] = count

        # Calculate totals
        result["total"] = sum(result.values())
        result["active"] = (
            result.get("processing", 0)
            + result.get("awaiting_confirmation", 0)
            + result.get("paused", 0)
        )
        result["review_needed"] = _get_review_needed_count(user_id)

        return result

    except Exception as e:
        # Do not fabricate zero counts on failure; the dashboard view catches
        # this and shows an error instead of a fake "0 imports".
        logger.error(f"Error getting status counts: {e}")
        raise


def _get_review_needed_count(user_id: int) -> int:
    """Get count of batches awaiting review for user's jobs.

    Deliberately unguarded: the only caller, _get_status_counts, already wraps
    this in a handler that logs the exception. Catching here returned 0, which
    told the user no import was waiting for review when the truth was that we
    could not find out - and an import waiting unnoticed is exactly the thing
    this number exists to prevent.
    """
    from app import db

    return (
        db.session.query(BatchImportBatch)
        .join(BatchImportJob)
        .filter(
            BatchImportJob.user_id == user_id,
            BatchImportBatch.status == BatchStatus.READY_FOR_REVIEW,
        )
        .count()
    )


# =============================================================================
# NEW IMPORT
# =============================================================================


@batch_import_view_bp.route("/new")
@login_required
@with_import_security
def new_import():
    """
    New import form page.

    Allows users to upload a file and configure import settings.
    """
    try:
        # Default settings
        default_settings = {
            "batch_size": 20,
            "archimate_mode": "standard",
            "enable_ai_generation": True,
            "confidence_threshold": 0.85,
            "auto_approve_high_confidence": False,
        }

        # Available archimate modes with descriptions
        archimate_modes = [
            {
                "value": "quick",
                "label": "Quick",
                "description": "Basic element generation, lower cost",
                "multiplier": 1.0,
            },
            {
                "value": "standard",
                "label": "Standard",
                "description": "Balanced detail and cost",
                "multiplier": 2.5,
            },
            {
                "value": "comprehensive",
                "label": "Comprehensive",
                "description": "Full detail with relationships, higher cost",
                "multiplier": 5.0,
            },
        ]

        # Supported file types
        supported_types = [".csv", ".xlsx", ".xls", ".json"]

        return render_template(
            "batch_import/new_import.html",
            default_settings=default_settings,
            archimate_modes=archimate_modes,
            supported_types=supported_types,
            money_format=org_money_format(),
        )

    except Exception as e:
        logger.error(f"Error loading new import form: {e}", exc_info=True)
        flash("Error loading form. Please try again.", "error")
        return redirect(url_for("batch_import_view.dashboard"))


# =============================================================================
# JOB DETAIL
# =============================================================================


@batch_import_view_bp.route("/jobs/<int:job_id>")
@login_required
@with_import_security
def job_detail(job_id: int):
    """
    Job detail page.

    Shows comprehensive job information, batches, and progress.
    """
    try:
        job = BatchImportJob.query.get_or_404(job_id)

        # Check access
        if job.user_id != current_user.id:
            abort(403, description="You don't have permission to view this job")

        # Get progress information
        progress = import_service.get_job_progress(job_id)

        # Get batches with pagination
        page = request.args.get("batch_page", 1, type=int)
        per_page = request.args.get("batch_per_page", 10, type=int)

        # Filter batches
        batch_status_filter = request.args.get("batch_status")

        batches = job.batches
        if batch_status_filter:
            try:
                status_enum = BatchStatus(batch_status_filter)
                batches = [b for b in batches if b.status == status_enum]
            except ValueError:
                logger.exception("Failed to compute status_enum")
                pass

        # Paginate batches
        total_batches_filtered = len(batches)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_batches = batches[start_idx:end_idx]
        total_batch_pages = (total_batches_filtered + per_page - 1) // per_page

        # Get cost estimate if job is awaiting confirmation
        cost_estimate = None
        show_preview = False
        if job.status == BatchJobStatus.AWAITING_CONFIRMATION:
            cost_estimate = import_service.estimate_cost(job)
            show_preview = True

        # Available batch statuses for filter
        available_batch_statuses = [status.value for status in BatchStatus]

        return render_template(
            "batch_import/job_detail.html",
            job=job,
            progress=progress,
            batches=paginated_batches,
            batch_page=page,
            batch_per_page=per_page,
            total_batch_pages=total_batch_pages,
            total_batches_filtered=total_batches_filtered,
            batch_status_filter=batch_status_filter,
            available_batch_statuses=available_batch_statuses,
            cost_estimate=cost_estimate,
            show_preview=show_preview,
            estimated_cost_display=format_estimate_money(job.estimated_cost_usd),
            money_format=org_money_format(),
        )

    except Exception as e:
        logger.error(f"Error loading job detail {job_id}: {e}", exc_info=True)
        flash("Error loading job. Please try again.", "error")
        return redirect(url_for("batch_import_view.dashboard"))


# =============================================================================
# BATCH REVIEW
# =============================================================================


@batch_import_view_bp.route("/batches/<int:batch_id>/review")
@login_required
@with_import_security
def batch_review(batch_id: int):
    """
    Batch review page.

    Allows users to review, approve, reject, and modify elements.
    """
    try:
        batch = BatchImportBatch.query.get_or_404(batch_id)

        # Check access
        if batch.job.user_id != current_user.id:
            abort(403, description="You don't have permission to review this batch")

        # Get batch summary
        summary = approval_service.get_batch_summary(batch_id)

        # Get elements with filtering and pagination
        page = safe_int_arg('page', 1, minimum=1)
        per_page = safe_int_arg('per_page', 50, minimum=1, maximum=500)
        status_filter = request.args.get("status")
        layer_filter = request.args.get("layer")

        # Ensure reasonable limits
        per_page = min(per_page, 200)
        offset = (page - 1) * per_page

        elements, total_elements = approval_service.get_batch_elements(
            batch_id=batch_id,
            status_filter=status_filter,
            layer_filter=layer_filter,
            limit=per_page,
            offset=offset,
        )

        total_pages = (total_elements + per_page - 1) // per_page

        # Get applications for context
        applications = batch.applications

        # Available filters
        from app.models.batch_import import ElementApprovalStatus

        available_statuses = [status.value for status in ElementApprovalStatus]
        available_layers = [
            "motivation",
            "strategy",
            "business",
            "application",
            "technology",
            "physical",
            "implementation",
        ]

        # Check if batch can be approved/rejected
        can_approve = batch.status in [BatchStatus.READY_FOR_REVIEW, BatchStatus.APPROVED]
        can_commit = batch.status == BatchStatus.APPROVED

        return render_template(
            "batch_import/batch_review.html",
            batch=batch,
            job=batch.job,
            summary=summary,
            elements=elements,
            applications=applications,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            total_elements=total_elements,
            status_filter=status_filter,
            layer_filter=layer_filter,
            available_statuses=available_statuses,
            available_layers=available_layers,
            can_approve=can_approve,
            can_commit=can_commit,
        )

    except Exception as e:
        logger.error(f"Error loading batch review {batch_id}: {e}", exc_info=True)
        flash("Error loading batch. Please try again.", "error")
        # Try to redirect to job detail if we know the job
        try:
            batch = BatchImportBatch.query.get(batch_id)
            if batch:
                return redirect(
                    safe_url_for_with_fallback(
                        "batch_import_view.job_detail",
                        f"/batch-import/jobs/{batch.job_id}",
                        job_id=batch.job_id,
                    )
                )
        except Exception as e:
            logger.debug("Failed to redirect to job detail after batch import: %s", e)
        return redirect(url_for("batch_import_view.dashboard"))
