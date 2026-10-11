"""
Abacus Scheduled Sync Task

Background task for daily incremental synchronization with Avolution Abacus.
Runs at 2 AM by default (configurable via ExternalSystem.sync_interval_minutes).

Uses APScheduler for reliable scheduling with:
- Cron-style scheduling (daily at specified hour)
- Async task execution
- Error handling and retry logic
- Job persistence across app restarts

Named platform job, not a per-tenant one: ``ExternalSystem`` (the Abacus
connection record this reads) carries no ``organization_id`` -- there is one
Abacus connection for the whole platform, not one per tenant. It is listed as
deliberately unfenced in ``scripts/unfenced_tables.txt``. ``run_abacus_sync_job``
is therefore guarded by ``job_lock`` (a cross-process advisory lock, the same
mechanism ``app/jobs/capability_projection_job.py`` uses for the same reason),
not by ``tenant_scope`` -- there is no organisation to scope it by.

``init_abacus_scheduler`` is a no-op: the sync job is NOT registered, neither
by the worker process (``app/jobs/worker.py``) nor by any other caller,
because ``run_abacus_sync_job`` calls ``sync_service.run_incremental_sync()``
which does not exist (the real method is ``async_run_incremental_sync``).
The scheduler registration is disabled until a working sync method exists.
"""

import asyncio
import logging
from datetime import datetime

from app.services.abacus_sync_service import get_sync_service

logger = logging.getLogger(__name__)


def run_abacus_sync_job(app=None):
    """
    Background job to run Abacus incremental sync.

    Called by APScheduler on schedule. Wraps async sync call in event loop.

    ``app`` is the Flask application the caller is running under. Reading
    ``ExternalSystem``/config through the ORM requires an application
    context, which APScheduler's background thread does not push on its own
    -- ``init_abacus_scheduler`` always passes ``app`` through a closure, so
    the ``app is None`` branch only matters for a direct unit-test call.
    """
    logger.info("Abacus scheduled sync job started")

    from app.jobs.tenant_safe_job import job_lock

    def _run_sync():
        try:
            # Get sync service
            sync_service = get_sync_service()

            # Run incremental sync in async context
            # Create new event loop for background task
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                result = loop.run_until_complete(sync_service.run_incremental_sync())
                logger.info(f"Scheduled sync completed: {result.get('status')}")

                if result.get("status") == "error":
                    logger.error(f"Sync error: {result.get('message')}")

            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Abacus sync job failed: {e}", exc_info=True)

    def _run_locked():
        with job_lock("abacus_incremental_sync", required=False) as acquired:
            if not acquired:
                logger.info(
                    "Abacus sync job skipped -- advisory lock held by another process"
                )
                return
            _run_sync()

    if app is not None:
        with app.app_context():
            _run_locked()
    else:
        _run_locked()


def init_abacus_scheduler(app, scheduler=None):
    """
    Initialize the Abacus sync job on an APScheduler instance.

    .. caution::

       This function is intentionally a no-op.  ``run_abacus_sync_job`` calls
       ``sync_service.run_incremental_sync()`` which does not exist (the real
       method on ``AbacusSyncService`` is ``async_run_incremental_sync``).
       The job is not registered until a working sync method exists and the
       scheduler registration path is re-enabled.
    """
    logger.warning(
        "Abacus sync scheduler NOT started — run_abacus_sync_job calls "
        "run_incremental_sync() which does not exist. "
        "Re-enable init_abacus_scheduler once a working sync method is in place."
    )
    return None


def shutdown_abacus_scheduler(app):
    """
    Shutdown Abacus sync scheduler gracefully.

    Args:
        app: Flask application instance
    """
    if hasattr(app, "abacus_scheduler") and app.abacus_scheduler:
        logger.info("Shutting down Abacus sync scheduler...")
        app.abacus_scheduler.shutdown(wait=False)
        logger.info("Abacus sync scheduler stopped")


def trigger_manual_sync():
    """
    Trigger manual Abacus sync (used by admin panel).

    Returns:
        Dictionary with sync result
    """
    logger.info("Manual Abacus sync triggered")

    try:
        # Get sync service
        sync_service = get_sync_service()

        # Run FULL sync for manual triggers to get all data
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(sync_service.run_full_sync())
            return result

        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Manual sync failed: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Manual sync failed: {str(e)}",
            "timestamp": datetime.utcnow().isoformat(),
        }
