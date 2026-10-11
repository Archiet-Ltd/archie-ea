"""
Job Queue Service

Database-backed job queue for long-running tasks like Abacus sync.
Provides proper job lifecycle management, status tracking, and worker processes.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app import db
from app.models import Job, JobStatus

logger = logging.getLogger(__name__)


class JobQueueService:
    """Service for managing background jobs with database persistence."""

    app = None  # Set by create_app

    def __init__(self):
        self._shutdown_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

    def create_job(
        self,
        name: str,
        task: str,
        payload: Optional[Dict[str, Any]] = None,
        organization_id: Optional[int] = None,
    ) -> Job:
        """Create a new job in the queue.

        A job that belongs to one organisation carries its id in the payload
        (``organization_id``), which is where the worker and ``list_jobs`` read it.
        """
        payload = dict(payload or {})
        if organization_id is not None:
            payload["organization_id"] = organization_id
        job = Job(name=name, task=task, payload=payload, status=JobStatus.PENDING.value)
        db.session.add(job)
        db.session.commit()
        logger.info(f"Created job {job.id}: {name}")
        return job

    def list_jobs(
        self,
        organization_id: int,
        limit: int = 50,
        task: Optional[str] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Job]:
        """Most recent jobs belonging to one organisation, newest first.

        ``organization_id`` must be the caller's own organisation (``g.current_org_id``
        or the organisation a worker is running for), never a value taken from a
        request parameter: the argument is trusted as given. Only jobs whose payload
        names this organisation are returned; a job with no organisation in its
        payload (platform-wide work) is never listed here. ``task`` and ``statuses``
        narrow the list to one task name and to jobs in any of those statuses.
        """
        query = Job.query.filter(
            Job.payload["organization_id"].as_string() == str(organization_id)
        )
        if task is not None:
            query = query.filter(Job.task == task)
        if statuses:
            query = query.filter(Job.status.in_(list(statuses)))
        return query.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit).all()

    def get_job(self, job_id: int) -> Optional[Job]:
        """Get a job by ID."""
        return Job.query.get(job_id)

    def update_job_status(
        self, job_id: int, status: str, result: Optional[Dict] = None, error: Optional[str] = None
    ) -> bool:
        """Update job status and optionally set result or error."""
        job = self.get_job(job_id)
        if not job:
            return False

        job.status = status

        if status == JobStatus.IN_PROGRESS.value and not job.started_at:
            job.started_at = datetime.utcnow()
        elif (
            status in [JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value]
            and not job.finished_at
        ):
            job.finished_at = datetime.utcnow()

        if result is not None:
            job.result = result
        if error is not None:
            job.error = error

        db.session.commit()
        logger.info(f"Updated job {job_id} status to {status}")
        return True

    def cancel_job(self, job_id: int) -> bool:
        """Cancel a pending or in-progress job."""
        job = self.get_job(job_id)
        if not job:
            return False

        if job.status in [
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        ]:
            logger.warning(f"Cannot cancel job {job_id} - already in terminal state: {job.status}")
            return False

        job.status = JobStatus.CANCELLED.value
        job.finished_at = datetime.utcnow()
        job.error = "Job cancelled by user"
        db.session.commit()
        logger.info(f"Cancelled job {job_id}")
        return True

    def start_worker(self):
        """Start the background worker thread."""
        if self._worker_thread and self._worker_thread.is_alive():
            logger.warning("Worker thread already running")
            return

        self._shutdown_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("Job queue worker started")

    def stop_worker(self):
        """Stop the background worker thread."""
        if self._worker_thread:
            self._shutdown_event.set()
            self._worker_thread.join(timeout=5)
            logger.info("Job queue worker stopped")

    def _reset_session(self, rollback: bool = False):
        """Clear worker-thread DB session state after each loop or DB failure."""
        if self.app is None:
            return
        with self.app.app_context():
            if rollback:
                db.session.rollback()
            db.session.remove()

    def _worker_loop(self):
        """Main worker loop that processes pending jobs."""
        logger.info("Job queue worker loop started")

        while not self._shutdown_event.is_set():
            try:
                # Use application context for database operations
                with self.app.app_context():
                    # Get next pending job
                    job = Job.query.filter_by(status=JobStatus.PENDING.value).first()

                    if job:
                        self._process_job(job)
                    else:
                        # No jobs, sleep briefly
                        time.sleep(1)

            except Exception as e:
                self._reset_session(rollback=True)
                # Transient DB connection errors (SSL EOF, idle timeout) are expected and noisy;
                # log as WARNING so they don't fill the ERROR log.
                err_str = str(e)
                if "SSL SYSCALL" in err_str or "connection" in err_str.lower():
                    logger.warning(f"Transient DB error in worker loop (will retry): {e}")
                else:
                    logger.error(f"Error in worker loop: {e}", exc_info=True)
                time.sleep(5)  # Back off on errors
            else:
                self._reset_session()

        logger.info("Job queue worker loop ended")

    def _process_job(self, job: Job):
        """Process a single job."""
        try:
            # Mark as in progress
            self.update_job_status(job.id, JobStatus.IN_PROGRESS.value)

            # Check if job was cancelled before we started
            with self.app.app_context():
                job = self.get_job(job.id)
                if job.status == JobStatus.CANCELLED.value:
                    logger.info(f"Job {job.id} was cancelled before execution")
                    return

            # Execute the job based on task type
            result = self._execute_task(job.task, job.payload, job.id)

            # Check if cancelled during execution
            with self.app.app_context():
                job = self.get_job(job.id)
                if job.status == JobStatus.CANCELLED.value:
                    logger.info(f"Job {job.id} was cancelled during execution")
                    return

            # Mark as completed
            self.update_job_status(job.id, JobStatus.COMPLETED.value, result=result)

        except Exception as e:
            # Mark as failed
            error_msg = f"{type(e).__name__}: {str(e)}"
            self._reset_session(rollback=True)
            self.update_job_status(job.id, JobStatus.FAILED.value, error=error_msg)
            logger.error(f"Job {job.id} failed: {error_msg}", exc_info=True)

    def _execute_task(self, task: str, payload: Dict[str, Any], job_id: int) -> Dict[str, Any]:
        """Execute a specific task type."""
        if task == "abacus_sync":
            return self._execute_abacus_sync(payload, job_id)
        elif task == "model_health_scan":
            return self._execute_model_health_scan(payload, job_id)
        else:
            raise ValueError(f"Unknown task type: {task}")

    def _execute_abacus_sync(self, payload: Dict[str, Any], job_id: int) -> Dict[str, Any]:
        """Execute Abacus synchronization."""
        import asyncio

        from app.services.abacus_sync_service import get_sync_service

        sync_type = payload.get("sync_type", "full")
        sync_service = get_sync_service()

        # Create event loop for async execution
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Call async method and run it synchronously
        if sync_type == "full":
            return loop.run_until_complete(sync_service.async_run_full_sync())
        elif sync_type == "incremental":
            return loop.run_until_complete(sync_service.async_run_incremental_sync())
        else:
            raise ValueError(f"Unknown sync type: {sync_type}")

    def _execute_model_health_scan(self, payload: Dict[str, Any], job_id: int) -> Dict[str, Any]:
        """Execute a model-health drift scan for one organisation."""
        org_id = payload.get("organization_id")
        if org_id is None:
            raise ValueError("model_health_scan job requires organization_id in payload")

        from app.jobs.tenant_safe_job import tenant_scope
        from app.models.drift_report import DriftReport
        from app.modules.genome.services.drift_detector import detect_model_drift

        with tenant_scope(org_id):
            report = detect_model_drift(org_id)
            DriftReport.upsert(org_id, report)
            db.session.commit()
            return {
                "organization_id": org_id,
                "finding_count": report.get("summary", {}).get("total", 0),
            }


# Global service instance
_job_queue_service = None


def get_job_queue_service() -> JobQueueService:
    """Get the global job queue service instance."""
    global _job_queue_service
    if _job_queue_service is None:
        _job_queue_service = JobQueueService()
    return _job_queue_service
