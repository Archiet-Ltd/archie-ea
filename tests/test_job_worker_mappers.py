"""Every relationship between models names a class that is loaded by the time the worker first queries.

The worker imports the job queue service and the job model, nothing else. A model it does not import, but
that another model's relationship names ("RoadmapResource", "ARBReviewItem"), must still be known when the
mappers are configured, or the worker's first look at the queue raises and no job ever runs.
"""
import subprocess
import sys
import os
import textwrap

SCRIPT = textwrap.dedent(
    """
    from app.services.job_queue_service import get_job_queue_service  # all the worker imports
    from sqlalchemy.orm import configure_mappers
    configure_mappers()
    print("MAPPERS_CONFIGURED")
    """
)


def test_the_mappers_configure_after_importing_only_what_the_worker_imports():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env.pop("RUN_JOB_WORKER", None)
    done = subprocess.run([sys.executable, "-c", SCRIPT], cwd=root, env=env, capture_output=True, text=True, timeout=300)
    assert "MAPPERS_CONFIGURED" in done.stdout, done.stderr[-3000:]
