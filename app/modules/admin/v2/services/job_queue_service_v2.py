"""Re-export of the one job queue service.

This module used to hold a second, near-identical copy of the database job queue.
Both import paths now resolve to the same class and the same shared instance in
``app.services.job_queue_service``. Callers should import from there; this module
is kept for one release so nothing that still imports the old path breaks, and is
then deleted.
"""

from app.services.job_queue_service import (  # noqa: F401
    JobQueueService,
    get_job_queue_service,
)

__all__ = ["JobQueueService", "get_job_queue_service"]
