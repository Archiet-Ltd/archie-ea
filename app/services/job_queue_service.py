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
from app.models.registry import import_all_models

# The worker maps every model on its first look at the queue, so all of them are loaded before it can run.
import_all_models()

logger = logging.getLogger(__name__)

# Worker threads when the app sets no JOB_WORKER_THREADS: a long job holds one, and another organisation's first read
# always finds a free one. Set JOB_WORKER_THREADS in the environment (read by Config) to change it.
WORKER_THREADS = 3
# A job that has been running for longer than its task allows was cut off (a restart, a crash); one table holds the limits.
STALE_AFTER_MINUTES = {"website_profile_crawl": 15, "website_profile_crawl_more": 15}
DEFAULT_STALE_AFTER_MINUTES = 30
SWEEP_EVERY_SECONDS = 60
# Jobs that take long and can wait: they are picked after every first read that is waiting.
LONG_TASKS = ("website_profile_crawl_more",)


class JobQueueService:
    """Service for managing background jobs with database persistence."""

    app = None  # Set by create_app

    def __init__(self):
        self._shutdown_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_threads: List[threading.Thread] = []
        self._last_sweep = 0.0
        self._sweep_lock = threading.Lock()
        self._claim_lock = threading.Lock()  # next-job and claim happen together, so two threads never pick one organisation twice

    def worker_threads(self, requested: Optional[int] = None) -> int:
        """How many worker threads to run: the argument, else the app's JOB_WORKER_THREADS, else WORKER_THREADS."""
        if requested is None and self.app is not None:
            requested = self.app.config.get("JOB_WORKER_THREADS")
        try:
            return max(1, int(requested)) if requested is not None else WORKER_THREADS
        except (TypeError, ValueError):
            return WORKER_THREADS

    def all_workers_busy(self) -> bool:
        """True when every worker thread has a job in progress (or no worker is running), so a new job must wait."""
        total = self.worker_threads()
        busy = Job.query.filter_by(status=JobStatus.IN_PROGRESS.value).count()
        return busy >= total

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

    def start_worker(self, threads: Optional[int] = None):
        """Start the background worker threads (after recovering the jobs a restart cut off)."""
        if any(t.is_alive() for t in self._worker_threads):
            logger.warning("Worker threads already running")
            return

        self._shutdown_event.clear()
        try:
            self.recover_stale_jobs()
        except Exception:
            logger.warning("could not recover interrupted jobs at worker start", exc_info=True)
        self._worker_threads = [
            threading.Thread(target=self._worker_loop, name="job-worker-%d" % number, daemon=True)
            for number in range(1, self.worker_threads(threads) + 1)
        ]
        for thread in self._worker_threads:
            thread.start()
        self._worker_thread = self._worker_threads[0]
        logger.info("Job queue worker started (%d threads)", len(self._worker_threads))

    def stop_worker(self):
        """Stop the background worker threads."""
        if self._worker_threads:
            self._shutdown_event.set()
            for thread in self._worker_threads:
                thread.join(timeout=5)
            logger.info("Job queue worker stopped")

    def recover_stale_jobs(self, now: Optional[datetime] = None) -> int:
        """Fail the jobs a restart or a crash cut off, and read the websites again.

        A job that has been ``in_progress`` for longer than its task allows (``STALE_AFTER_MINUTES``) is failed with the
        reason "interrupted" and a plain sentence. A website job also gets one new job of the same task, unless one is
        already waiting for that organisation. Returns how many jobs were failed.
        """
        from datetime import timedelta

        now = now or datetime.utcnow()
        shortest = min([DEFAULT_STALE_AFTER_MINUTES, *STALE_AFTER_MINUTES.values()])
        recovered = 0
        with self.app.app_context():
            running = Job.query.filter(
                Job.status == JobStatus.IN_PROGRESS.value, Job.started_at < now - timedelta(minutes=shortest)
            ).all()
            for job in running:
                allowed = STALE_AFTER_MINUTES.get(job.task, DEFAULT_STALE_AFTER_MINUTES)
                if job.started_at is None or job.started_at > now - timedelta(minutes=allowed):
                    continue
                job.status = JobStatus.FAILED.value
                job.finished_at = now
                job.result = {
                    "success": False, "reason": "interrupted",
                    "message": "The work was cut off by a restart before it finished, and has been queued again."
                    if job.task in STALE_AFTER_MINUTES else "The work was cut off by a restart before it finished.",
                }
                job.error = "interrupted: running for more than %d minutes" % allowed
                recovered += 1
                org_id = (job.payload or {}).get("organization_id") if isinstance(job.payload, dict) else None
                if job.task in STALE_AFTER_MINUTES and org_id is not None:
                    waiting = self.list_jobs(org_id, limit=5, task=job.task, statuses=[JobStatus.PENDING.value])
                    if not waiting:
                        payload = {k: v for k, v in (job.payload or {}).items() if k not in ("job_id", "not_before")}
                        self.create_job(job.name, job.task, payload)
            db.session.commit()
        if recovered:
            logger.warning("failed %d job(s) that were cut off by a restart", recovered)
        return recovered

    def _sweep_if_due(self) -> None:
        """About once a minute, whichever worker thread gets there first."""
        if time.monotonic() - self._last_sweep < SWEEP_EVERY_SECONDS:
            return
        if not self._sweep_lock.acquire(blocking=False):
            return
        try:
            self._last_sweep = time.monotonic()
            self.recover_stale_jobs()
        except Exception:
            logger.warning("the sweep for interrupted jobs failed", exc_info=True)
            self._reset_session(rollback=True)
        finally:
            self._sweep_lock.release()

    def _claim(self, job_id: int) -> bool:
        """Take a pending job for this thread: exactly one thread's update finds it still pending."""
        result = db.session.execute(
            db.update(Job).where(Job.id == job_id, Job.status == JobStatus.PENDING.value)
            .values(status=JobStatus.IN_PROGRESS.value, started_at=datetime.utcnow())
        )
        db.session.commit()
        return bool(result.rowcount)

    def _next_job(self) -> Optional[Job]:
        """The oldest due pending job; first reads come before the long jobs that can wait."""
        pending = Job.query.filter_by(status=JobStatus.PENDING.value).order_by(Job.id).limit(50).all()
        due = [j for j in pending if self._is_due(j)]
        # One organisation runs one job at a time (a read and its model pass never overlap); others proceed in parallel.
        busy = {
            (j.payload or {}).get("organization_id")
            for j in Job.query.filter_by(status=JobStatus.IN_PROGRESS.value).all()
            if isinstance(j.payload, dict) and (j.payload or {}).get("organization_id") is not None
        }
        due = [
            j for j in due
            if not (isinstance(j.payload, dict) and j.payload.get("organization_id") in busy)
        ]
        due.sort(key=lambda j: (j.task in LONG_TASKS, j.id))
        return due[0] if due else None

    def _reset_session(self, rollback: bool = False):
        """Clear worker-thread DB session state after each loop or DB failure."""
        if self.app is None:
            return
        with self.app.app_context():
            if rollback:
                db.session.rollback()
            db.session.remove()

    def _worker_loop(self):
        """Worker loop that processes pending jobs."""
        logger.info("Job queue worker loop started")

        while not self._shutdown_event.is_set():
            try:
                # Use application context for database operations
                self._sweep_if_due()
                with self.app.app_context():
                    with self._claim_lock:
                        job = self._next_job()
                        claimed = bool(job) and self._claim(job.id)

                    if claimed:
                        self._process_job(job)
                    else:
                        # No jobs (or another thread took it first), sleep briefly
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

    @staticmethod
    def _is_due(job: Job) -> bool:
        """A job whose payload names ``not_before`` (UTC, ISO) waits until then."""
        not_before = (job.payload or {}).get("not_before") if isinstance(job.payload, dict) else None
        if not not_before:
            return True
        try:
            return datetime.fromisoformat(str(not_before)) <= datetime.utcnow()
        except ValueError:
            return True

    def _process_job(self, job: Job):
        """Process a single job."""
        try:
            # The worker loop has already marked it in progress (_claim, atomically); a direct caller has not
            if job.status != JobStatus.IN_PROGRESS.value:
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
        elif task == "github_first_sync":
            return self._execute_github_first_sync(payload, job_id)
        elif task == "website_profile_crawl":
            from app.modules.organization.website_crawl import run_website_crawl_job

            return run_website_crawl_job({**payload, "job_id": job_id})
        elif task == "website_profile_crawl_more":
            from app.modules.organization.website_crawl import run_website_retry_job

            return run_website_retry_job(payload)
        elif task == "company_signals_refresh":
            from app.modules.organization.public_signals import run_signals_job

            return run_signals_job(payload)
        elif task in ("onboarding_github_import", "onboarding_jira_import"):
            return self._execute_onboarding_import(task, payload)
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

    def _execute_github_first_sync(self, payload: Dict[str, Any], job_id: int) -> Dict[str, Any]:
        """Read one organisation's GitHub repositories into proposals (the connect page's first sync
        and its re-sync). The job carries the organisation and the connector id; never a credential."""
        org_id = payload.get("organization_id")
        connector_id = payload.get("connector_id")
        if org_id is None or not connector_id:
            raise ValueError("github_first_sync job requires organization_id and connector_id in payload")

        from app.connectors.github import run_first_sync

        return run_first_sync(org_id, connector_id)

    def _execute_onboarding_import(self, task: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Read one source the person gave during sign-up. The job carries the organisation, the
        connector id and who gave it; never a credential."""
        org_id = payload.get("organization_id")
        connector_id = payload.get("connector_id")
        if org_id is None or not connector_id:
            raise ValueError("%s job requires organization_id and connector_id in payload" % task)

        from app.modules.account.services import onboarding_service as onboarding

        if task == onboarding.GITHUB_TASK:
            return onboarding.run_github_import(org_id, connector_id, payload.get("approver_id"))
        return onboarding.run_jira_import(org_id, connector_id)

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
