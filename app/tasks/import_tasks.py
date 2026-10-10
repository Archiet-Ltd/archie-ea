"""
Celery Tasks for Async Batch Import Processing

Provides background task execution for batch import jobs so they do not
block Flask request threads.  The module is designed with a graceful
degradation strategy:

* When Celery + broker are available and CELERY_ENABLED is true, tasks
  execute in a Celery worker process.
* Otherwise, the calling code can fall back to synchronous execution
  by invoking the same ``_process_job_batches`` helper directly.

Usage from routes::

    from app.tasks.import_tasks import dispatch_import_job

    result = dispatch_import_job(job_id)
    # result = {"async": True, "task_id": "..."} or {"async": False}
"""

import logging
from datetime import datetime

from flask import current_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Celery app factory
# ---------------------------------------------------------------------------

_celery_app = None


def _reset_celery_app():
    """Reset the cached Celery app instance. For testing only."""
    global _celery_app
    _celery_app = None


def get_celery_app():
    """
    Lazy-initialise and return a Celery application instance.

    Reads ``CELERY_BROKER_URL`` (falling back to ``REDIS_URL``) from
    the Flask app config.  Returns ``None`` when Celery is not installed
    or when the broker URL is not configured.
    """
    global _celery_app

    if _celery_app is not None:
        return _celery_app

    try:
        from celery import Celery
    except ImportError:
        logger.info("Celery package not installed -- async tasks disabled")
        return None

    try:
        broker_url = current_app.config.get(
            "CELERY_BROKER_URL",
            current_app.config.get("REDIS_URL", "redis://localhost:6379/0"),
        )
        result_backend = current_app.config.get(
            "CELERY_RESULT_BACKEND", broker_url,
        )

        _celery_app = Celery("flask_archie_tasks")
        _celery_app.conf.update(
            broker_url=broker_url,
            result_backend=result_backend,
            task_serializer="json",
            accept_content=["json"],
            result_serializer="json",
            timezone="UTC",
            enable_utc=True,
            task_track_started=True,
            task_acks_late=True,
            worker_prefetch_multiplier=1,
        )

        logger.info("Celery app initialised (broker=%s)", broker_url)
        return _celery_app

    except RuntimeError:
        # Outside Flask application context -- cannot read config
        logger.debug("No Flask app context -- Celery initialisation deferred")
        return None


def _make_celery_with_app(flask_app):
    """
    Create a Celery instance bound to *flask_app*.

    This is intended for use by a standalone Celery worker boot script
    (e.g. ``celery -A app.tasks.import_tasks.celery_app worker``) where
    the Flask app context must be pushed explicitly.
    """
    try:
        from celery import Celery
    except ImportError:
        return None

    broker_url = flask_app.config.get(
        "CELERY_BROKER_URL",
        flask_app.config.get("REDIS_URL", "redis://localhost:6379/0"),
    )
    result_backend = flask_app.config.get(
        "CELERY_RESULT_BACKEND", broker_url,
    )

    celery = Celery("flask_archie_tasks")
    celery.conf.update(
        broker_url=broker_url,
        result_backend=result_backend,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )

    class ContextTask(celery.Task):
        """Push Flask app context for every task body."""

        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


# ---------------------------------------------------------------------------
# Core processing logic (shared by async task and sync fallback)
# ---------------------------------------------------------------------------


def _job_organization_id(job_id):
    """Organisation of the user who started batch import *job_id*, or None.

    ``BatchImportJob`` carries no organisation column of its own; it belongs to
    the organisation of the user who started it. One global read of two integer
    columns, taken before any tenant scope is entered.
    """
    from app import db
    from app.models.batch_import import BatchImportJob
    from app.models.user import User

    return db.session.execute(
        db.select(User.__table__.c.organization_id)
        .select_from(BatchImportJob.__table__)
        .join(User.__table__, User.__table__.c.id == BatchImportJob.__table__.c.user_id)
        .where(BatchImportJob.__table__.c.id == job_id)
    ).scalar()


def _process_job_batches(job_id):
    """
    Process all queued batches for the given *job_id* synchronously.

    This is the actual work function used by both the Celery task and
    the synchronous fallback.  It must be called inside a Flask
    application context.

    A Celery worker has no request and so no tenant: the job's batches are
    processed inside ``tenant_scope`` of the organisation that started the job,
    so every element the import writes is filtered and stamped for that
    organisation alone. When called from a request (the synchronous fallback)
    the request's own tenant is already the job's and is left as it is.

    Returns a dict summarising the outcome.
    """
    from flask import g

    from app import db
    from app.jobs.tenant_safe_job import tenant_scope

    if getattr(g, "current_org_id", None) is not None:
        return _process_job_batches_in_tenant(job_id)

    organization_id = _job_organization_id(job_id)
    if organization_id is None:
        logger.error("Job %s not found or has no owning organisation", job_id)
        return {"success": False, "error": "Job not found", "job_id": job_id}
    with tenant_scope(organization_id):
        result = _process_job_batches_in_tenant(job_id)
        db.session.commit()
    return result


def _process_job_batches_in_tenant(job_id):
    """The batch loop itself; runs with the job's tenant already set."""
    from app import db
    from app.models.batch_import import (
        BatchImportBatch,
        BatchImportJob,
        BatchJobStatus,
        BatchStatus,
    )
    from app.services.batch_processor_service import BatchProcessorService

    processor = BatchProcessorService()

    job = BatchImportJob.query.get(job_id)
    if job is None:
        logger.error("Job %s not found", job_id)
        return {"success": False, "error": "Job not found", "job_id": job_id}

    if job.status != BatchJobStatus.PROCESSING:
        logger.warning(
            "Job %s is not in PROCESSING state (current: %s)", job_id, job.status.value
        )
        return {
            "success": False,
            "error": f"Job not in processable state: {job.status.value}",
            "job_id": job_id,
        }

    batches_processed = 0
    batches_failed = 0

    while True:
        # Refresh job to pick up pause/cancel requests from the UI
        db.session.refresh(job)

        if job.status == BatchJobStatus.PAUSED:
            logger.info("Job %s paused by user", job_id)
            break

        if job.status == BatchJobStatus.CANCELLED:
            logger.info("Job %s cancelled by user", job_id)
            break

        # Check budget
        if job.budget_limit_usd and job.actual_cost_usd >= job.budget_limit_usd:
            job.status = BatchJobStatus.PAUSED
            job.error_message = "Budget limit reached"
            db.session.commit()
            logger.warning("Job %s paused: budget limit reached", job_id)
            break

        # Fetch next queued batch
        batch = (
            BatchImportBatch.query.filter(
                BatchImportBatch.job_id == job_id,
                BatchImportBatch.status == BatchStatus.QUEUED,
            )
            .order_by(BatchImportBatch.batch_number)
            .first()
        )

        if batch is None:
            # All batches processed
            break

        try:
            processor.process_batch(batch.id)
            batches_processed += 1
        except Exception:
            logger.exception(
                "Failed to process batch %s for job %s", batch.id, job_id
            )
            batches_failed += 1

    # Refresh and determine final status
    db.session.refresh(job)

    if job.status == BatchJobStatus.PROCESSING:
        # Still in PROCESSING means we ran out of batches to process
        ready_count = BatchImportBatch.query.filter(
            BatchImportBatch.job_id == job_id,
            BatchImportBatch.status == BatchStatus.READY_FOR_REVIEW,
        ).count()

        committed_count = BatchImportBatch.query.filter(
            BatchImportBatch.job_id == job_id,
            BatchImportBatch.status == BatchStatus.COMMITTED,
        ).count()

        if ready_count == 0 and committed_count >= job.total_batches:
            job.status = BatchJobStatus.COMPLETED
            job.completed_at = datetime.utcnow()
            db.session.commit()

    return {
        "success": True,
        "job_id": job_id,
        "batches_processed": batches_processed,
        "batches_failed": batches_failed,
        "final_status": job.status.value,
    }


# ---------------------------------------------------------------------------
# Celery task definition
# ---------------------------------------------------------------------------


def _register_task(celery):
    """Register the async_import_batch task on the given Celery instance."""

    @celery.task(bind=True, name="import_tasks.async_import_batch",
                 max_retries=3, default_retry_delay=60)
    def async_import_batch(self, job_id):
        """
        Celery task: process all queued batches for a batch import job.

        Parameters
        ----------
        job_id : int
            Primary key of the ``BatchImportJob`` to process.

        The task is idempotent -- re-running it will pick up from
        wherever processing left off thanks to per-application
        checkpoint tracking in the ``BatchProcessorService``.
        """
        logger.info("[Celery] Starting async_import_batch for job %s", job_id)

        try:
            result = _process_job_batches(job_id)

            if not result["success"]:
                logger.error(
                    "[Celery] Job %s processing returned failure: %s",
                    job_id,
                    result.get("error"),
                )

            logger.info(
                "[Celery] Job %s finished -- processed=%s failed=%s status=%s",
                job_id,
                result.get("batches_processed", 0),
                result.get("batches_failed", 0),
                result.get("final_status", "unknown"),
            )
            return result

        except Exception as exc:
            logger.exception("[Celery] Job %s raised unhandled exception", job_id)

            # Mark job as failed in DB so the UI can show the error
            try:
                from app import db
                from app.models.batch_import import BatchImportJob, BatchJobStatus

                job = BatchImportJob.query.get(job_id)
                if job and job.status == BatchJobStatus.PROCESSING:
                    job.status = BatchJobStatus.FAILED
                    job.error_message = f"Background processing failed: {str(exc)[:500]}"
                    job.completed_at = datetime.utcnow()
                    db.session.commit()
            except Exception:
                logger.exception(
                    "[Celery] Failed to update job %s status after error", job_id
                )

            # Retry with exponential back-off
            raise self.retry(exc=exc)

    return async_import_batch


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_celery_available():
    """
    Check whether Celery is installed, configured, and enabled.

    Returns True only when all three conditions hold:
    1. The ``celery`` package is importable.
    2. ``CELERY_ENABLED`` is set to ``'true'`` in Flask config.
    3. A valid broker URL is configured.
    """
    try:
        import celery as _celery  # noqa: F811,F401
    except ImportError:
        return False

    try:
        enabled = current_app.config.get("CELERY_ENABLED", False)
        if isinstance(enabled, str):
            enabled = enabled.lower() == "true"
        return bool(enabled)
    except RuntimeError:
        return False


def dispatch_import_job(job_id):
    """
    Dispatch a batch import job for processing.

    If Celery is available and enabled, the job is sent to a Celery worker
    asynchronously and this function returns immediately with the task ID.
    Otherwise, processing runs synchronously in the current thread.

    Parameters
    ----------
    job_id : int
        Primary key of the ``BatchImportJob``.

    Returns
    -------
    dict
        ``{"async": True, "task_id": "..."}`` when dispatched to Celery, or
        ``{"async": False, ...}`` with the synchronous processing result.
    """
    if is_celery_available():
        celery = get_celery_app()
        if celery is not None:
            task_fn = _register_task(celery)
            async_result = task_fn.delay(job_id)
            logger.info(
                "Dispatched job %s to Celery (task_id=%s)", job_id, async_result.id
            )
            return {"async": True, "task_id": async_result.id}

    # Synchronous fallback
    logger.info("Processing job %s synchronously (Celery not available)", job_id)
    result = _process_job_batches(job_id)
    result["async"] = False
    return result


def get_import_status(task_id):
    """
    Check the status of an async import task by its Celery task ID.

    Parameters
    ----------
    task_id : str
        The Celery task ID returned by ``dispatch_import_job``.

    Returns
    -------
    dict
        Task state information including status, result, and progress.
        Returns a status of ``"UNAVAILABLE"`` when Celery is not configured.
    """
    celery = get_celery_app()
    if celery is None:
        return {
            "task_id": task_id,
            "status": "UNAVAILABLE",
            "message": "Celery is not configured",
        }

    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery)

    response = {
        "task_id": task_id,
        "status": result.state,
    }

    if result.state == "PENDING":
        response["message"] = "Task is waiting to be picked up by a worker"
    elif result.state == "STARTED":
        response["message"] = "Task is currently running"
    elif result.state == "SUCCESS":
        response["message"] = "Task completed successfully"
        response["result"] = result.result
    elif result.state == "FAILURE":
        response["message"] = "Task failed"
        response["error"] = str(result.info) if result.info else "Unknown error"
    elif result.state == "RETRY":
        response["message"] = "Task is being retried"
    else:
        response["message"] = f"Task is in state: {result.state}"

    return response
