"""DE-4: recompute stale derived facts, per tenant, through the existing
tenant-safe scheduler harness (ADR-003).

Two harness entry points, deliberately (see task 03's binding lock-scope
design, ``00-verification-notes-and-sr1.md`` correction #2):

* the SCHEDULED sweep calls ``run_for_each_tenant(..., use_lock=True)`` --
  its own sweep-level ``job_lock`` stops duplicate gunicorn workers firing
  the same sweep concurrently;
* the ON-DEMAND endpoint calls ``run_for_each_tenant(..., use_lock=False)``
  for a single tenant -- no sweep lock, so one tenant's on-demand call is
  never blocked by an unrelated tenant's scheduled sweep.

Both share the SAME per-tenant inner lock
(``derived_facts_recompute:org:{organization_id}``), acquired with
``job_lock(name, required=False)`` inside the per-tenant work function, so a
scheduled run and an on-demand call for the SAME tenant can never overlap,
while different tenants proceed independently. This reuses
``tenant_safe_job.job_lock``'s advisory-lock mechanism; it does not
re-implement it.
"""

from __future__ import annotations

import functools
import datetime as _dt
import logging
import time

from contextlib import nullcontext
from flask import current_app, has_app_context

from app.extensions import db
from app.jobs.tenant_safe_job import (
    JobRun,
    TenantResult,
    job_lock,
    platform_scope,
    run_for_each_tenant,
)

logger = logging.getLogger(__name__)

JOB_NAME = "derived_facts_recompute"
ON_DEMAND_JOB_NAME = "derived_facts_recompute_on_demand"


def per_tenant_lock_name(organization_id: int) -> str:
    """The ONE lock name shared by the scheduled and on-demand paths.

    Binding per task 03: this is what makes "one concurrent run per tenant"
    hold across both entry points, without re-implementing locking -- both
    callers pass this exact string to ``job_lock``.
    """
    return f"derived_facts_recompute:org:{organization_id}"


def stale_carrying_organization_ids() -> list[int]:
    """Organisation ids with at least one stale row -- plain ints, no ORM.

    Runs with NO tenant context, like ``active_organization_ids`` --
    ``organization_id`` is in this raw-SQL grouping explicitly, and nothing
    here enters an identity map a later lookup could serve under the wrong
    tenant.
    """
    # Row-level security shows the runtime role only the session organisation's
    # rows and this sweep has none yet; it reads the ids of the organisations
    # that carry stale rows, nothing else.
    with platform_scope("derived facts sweep: which organisations carry stale derived relationships"):
        rows = db.session.execute(
            db.text(
                "SELECT DISTINCT organization_id FROM archimate_derived_relationships "
                "WHERE stale = TRUE ORDER BY organization_id"
            )
        ).all()
    return [int(row[0]) for row in rows]


def _recompute_one_tenant(organization_id: int, *, trigger: str) -> dict:
    """The per-tenant work function ``run_for_each_tenant`` calls.

    Runs INSIDE ``tenant_scope(organization_id)`` already (the harness sets
    that up), so this function adds no ``organization_id`` predicate of its
    own to any ORM read. Wraps its body in the shared per-tenant lock; when
    not acquired, returns a ``skipped_locked`` result rather than a silent
    success or a fabricated zero.

    ``trigger`` (T-005 D5/D7) is passed straight through to
    ``run_and_persist`` -- ``"scheduled"`` from the sweep, ``"on_demand"``
    from the API-triggered call -- never guessed here.
    """
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    with job_lock(per_tenant_lock_name(organization_id), required=False) as acquired:
        if not acquired:
            return {"skipped_locked": True}

        runner = DerivationRunner()
        result = runner.run_and_persist(organization_id, trigger=trigger)
        return {
            "skipped_locked": False,
            "explicit_count": result.explicit_count,
            "derived_count": result.derived_count,
            "ratio": result.ratio,
            "duration_ms": result.duration_ms,
            "engine_version": result.engine_version,
        }


def recompute_derived_facts(app) -> JobRun:
    """The scheduled sweep (DE-4). Visits only stale-carrying tenants.

    Must be called with a real Flask ``app`` -- ``run_for_each_tenant`` opens
    its own ``app.app_context()``. Enumerates BEFORE entering tenant scope,
    same discipline as ``active_organization_ids``.
    """
    with app.app_context():
        organization_ids = stale_carrying_organization_ids()

    return run_for_each_tenant(
        app,
        JOB_NAME,
        functools.partial(_recompute_one_tenant, trigger="scheduled"),
        organization_ids=organization_ids,
        use_lock=True,
    )


def recompute_derived_facts_on_demand(app, organization_id: int) -> JobRun:
    """The on-demand path (API-7). One tenant, sweep lock OFF.

    Mutual exclusion against a concurrently-running scheduled sweep for the
    SAME tenant comes from the shared per-tenant lock inside
    ``_recompute_one_tenant`` -- not from this call's own (absent) sweep
    lock, which is deliberately off so this tenant's request is never
    blocked by an unrelated tenant's scheduled sweep.
    """
    run = JobRun(job_name=ON_DEMAND_JOB_NAME, started_at=_dt.datetime.now(_dt.UTC))

    app_ctx = (
        nullcontext()
        if has_app_context() and current_app._get_current_object() is app
        else app.app_context()
    )

    with app_ctx:
        started = time.monotonic()
        try:
            from app.modules.intelligence.services.derivation_runner import DerivationRunner

            with job_lock(per_tenant_lock_name(organization_id), required=False) as acquired:
                if not acquired:
                    value = {"skipped_locked": True}
                else:
                    result = DerivationRunner().run_and_persist(
                        organization_id, trigger="on_demand"
                    )
                    value = {
                        "skipped_locked": False,
                        "explicit_count": result.explicit_count,
                        "derived_count": result.derived_count,
                        "ratio": result.ratio,
                        "duration_ms": result.duration_ms,
                        "engine_version": result.engine_version,
                    }
            result = TenantResult(
                organization_id=organization_id,
                ok=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                value=value,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced in JobRun
            logger.exception(
                "recompute_job: on-demand recompute FAILED for organization_id=%s",
                organization_id,
            )
            db.session.rollback()
            db.session.remove()
            result = TenantResult(
                organization_id=organization_id,
                ok=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=repr(exc),
            )

    run.results.append(result)
    run.finished_at = _dt.datetime.now(_dt.UTC)
    return run


__all__ = [
    "JOB_NAME",
    "ON_DEMAND_JOB_NAME",
    "per_tenant_lock_name",
    "recompute_derived_facts",
    "recompute_derived_facts_on_demand",
    "stale_carrying_organization_ids",
]
