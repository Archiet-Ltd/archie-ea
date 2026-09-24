"""
Entelim Production Gunicorn Configuration (S2-03)

Usage:
    gunicorn -c gunicorn.conf.py manage:app

Environment variables:
    GUNICORN_WORKERS      — Number of worker processes (default: CPU*2+1)
    GUNICORN_THREADS      — Threads per worker (default: 4)
    GUNICORN_BIND         — Bind address (default: 0.0.0.0:5000)
    GUNICORN_TIMEOUT      — Worker timeout in seconds (default: 120)
    GUNICORN_GRACEFUL_TIMEOUT — Graceful shutdown timeout (default: 30)
    GUNICORN_MAX_REQUESTS  — Requests before worker restart (default: 2000)
    GUNICORN_LOG_LEVEL    — Log level (default: info)
"""

import multiprocessing
import os

# ---------------------------------------------------------------------------
# Server socket
# ---------------------------------------------------------------------------
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")
backlog = 2048

# ---------------------------------------------------------------------------
# Worker processes
# ---------------------------------------------------------------------------
# Default: 2 * CPU cores + 1 (recommended by Gunicorn docs)
_default_workers = multiprocessing.cpu_count() * 2 + 1
workers = int(os.environ.get("GUNICORN_WORKERS", _default_workers))

# Use gthread worker class for mixed I/O workloads (DB queries + API calls)
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 4))

# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------
# Worker timeout — kill workers that hang longer than this
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))

# Graceful shutdown — time to finish in-flight requests before SIGKILL
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", 30))

# Keep-alive connections — seconds to wait for next request on same connection
keepalive = 5

# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------
# Restart workers after N requests to prevent memory leaks
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", 10000))

# Jitter: randomize restart to avoid all workers restarting simultaneously
max_requests_jitter = 200

# Preload app for faster worker fork (shared memory for read-only data)
preload_app = True

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
accesslog = "-"  # stdout
errorlog = "-"   # stderr
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# ---------------------------------------------------------------------------
# Process naming
# ---------------------------------------------------------------------------
proc_name = "archie"

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
# Limit request sizes to prevent abuse
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# ---------------------------------------------------------------------------
# Server hooks
# ---------------------------------------------------------------------------


def on_starting(server):
    """Called just before the master process is initialized."""
    server.log.info("Entelim starting with %d workers", server.app.cfg.workers)


def _rss_mb() -> "float | None":
    """Best-effort RSS of the current process in MB, or None if unavailable.

    ARCH-001 memory telemetry: the 07/17/18 Aug OOM kills were diagnosed by
    inference (mem_limit + `dmesg -T`), not measurement, because nothing in
    the app logged actual worker RSS. psutil is already a dependency
    (app/monitoring/metrics_service.py uses it for host-level metrics), so
    this reuses it rather than shelling out to /proc.
    """
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:  # never let telemetry break worker lifecycle
        return None


def post_fork(server, worker):
    """Called after a worker has been forked."""
    server.log.info("Worker spawned (pid: %s)", worker.pid)
    # CRITICAL with preload_app=True: the SQLAlchemy engine is created in the master
    # process, and create_app() opens DB connections during preload (admin bootstrap,
    # prompt seeding). Those connections/sockets are inherited by every forked worker,
    # so two workers end up sharing one libpq connection -> intermittent
    # "error with status PGRES_TUPLES_OK and no message from the libpq" on queries.
    # Dispose the pool here so each worker opens its own fresh connections.
    try:
        from manage import app
        from app import db

        with app.app_context():
            db.engine.dispose()
        server.log.info("post_fork: disposed DB engine for worker %s", worker.pid)
    except Exception as exc:  # never let a hook failure crash worker startup
        server.log.warning("post_fork: db.engine.dispose() failed: %s", exc)

    rss = _rss_mb()
    if rss is not None:
        server.log.info("worker boot RSS: pid=%s rss_mb=%.1f", worker.pid, rss)


def pre_request(worker, req):
    """Called inside the worker process just before it handles a request.

    `worker_exit` runs in the MASTER process after the worker has already
    terminated (gunicorn's own docs: "in the master process"), so
    psutil.Process() there measures the master, not the recycled worker —
    a hook there would silently telemetry the wrong process. This hook runs
    inside the worker itself, so it is used instead to catch the request
    immediately before a max_requests recycle: gunicorn's worker loop bumps
    `worker.nr` per request and stops accepting once it reaches
    `worker.max_requests`, so `nr == max_requests - 1` is the last request
    this worker will serve before exiting to restart.
    """
    try:
        if worker.max_requests and worker.nr >= worker.max_requests - 1:
            rss = _rss_mb()
            if rss is not None:
                worker.log.info(
                    "worker max_requests recycle: pid=%s nr=%s rss_mb=%.1f",
                    worker.pid, worker.nr, rss,
                )
    except Exception as exc:  # never let telemetry break request handling
        worker.log.warning("pre_request: recycle RSS telemetry failed: %s", exc)


def worker_exit(server, worker):
    """Called when a worker exits, in the MASTER process — so `worker` here is
    the (already-dead) WorkerTmp handle, not something psutil.Process() can
    read RSS from; the boot-time and recycle-time RSS are logged instead, in
    post_fork and pre_request above, both of which run inside the worker.
    """
    server.log.info("Worker exited (pid: %s)", worker.pid)
