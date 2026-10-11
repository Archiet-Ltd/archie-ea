"""Worker-process entry point for the platform's scheduled jobs.

WHY THIS FILE EXISTS
=====================
Every job registered by ``app._bootstrap.extensions.init_scheduler`` and
``app.tasks.abacus_sync_task.init_abacus_scheduler`` used to run inside the
web process: ``create_app()`` called ``init_scheduler(app)`` unconditionally,
so with ``GUNICORN_WORKERS=3`` (``preload_app = True``) every job fired in
every gunicorn worker. That is a scaling and blast-radius problem for a web
process that should be free to restart, scale to zero, or roll during a
deploy without silently dropping the platform's only running copy of these
jobs.

This module is that dedicated process. It is not itself a Flask app or a
request handler -- it boots the application once, registers exactly the same
jobs the web process would have, sets ``RUNNING_AS_JOBS_WORKER`` so
``init_scheduler`` never skips itself here even when ``JOBS_RUN_IN_WORKER``
is set on the web process (see ``_scheduler_belongs_in_this_process`` in
``app/_bootstrap/extensions.py``), and then blocks: APScheduler's
``BackgroundScheduler`` runs jobs on its own daemon threads, so the only job
of the main thread here is to keep the process alive until it is told to
stop.

Run as::

    python -m app.jobs.worker

``Dockerfile.worker``'s default command still runs the existing RQ task
queue (``flask --app manage run-worker``) for backward compatibility; a
deployment that wants this process instead overrides that image's command to
``python -m app.jobs.worker``. The compose service that runs this command is
added separately.
"""

from __future__ import annotations

import logging
import os
import signal
import threading

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Must be set before create_app() runs init_scheduler() internally, so
    # that first call does not skip itself -- see
    # _scheduler_belongs_in_this_process() in app/_bootstrap/extensions.py.
    os.environ["RUNNING_AS_JOBS_WORKER"] = "1"

    from app import create_app

    app = create_app()

# create_app() already called init_scheduler(app) once (app/__init__.py
    # step 1a) -- with RUNNING_AS_JOBS_WORKER set above, that call does not
    # skip itself, so the APScheduler jobs from extensions.py are already
    # registered and running by the time create_app() returns.  Calling
    # init_scheduler() again here would just re-add the same job ids
    # (APScheduler's replace_existing=True makes that a harmless no-op, but
    # there is no reason to).
    scheduler = app.extensions.get("ea_workflow_scheduler")
    if scheduler is None:
        logger.error(
            "jobs worker: no scheduler was registered by create_app() -- "
            "check RUNNING_AS_JOBS_WORKER and app.testing"
        )
        raise SystemExit(1)

    stop = threading.Event()

    def _handle_stop(signum, _frame):
        logger.info("jobs worker: received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    logger.info("jobs worker: started; scheduled jobs running on background threads")
    while not stop.is_set():
        stop.wait(60)

    try:
        scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("jobs worker: error shutting down scheduler")
    logger.info("jobs worker: stopped")


if __name__ == "__main__":
    main()


__all__ = ["main"]
