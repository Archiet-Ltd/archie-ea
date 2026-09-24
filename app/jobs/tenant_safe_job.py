"""Tenant-safe scheduled-job harness (ADR 0008).

WHY THIS FILE EXISTS
====================
Multi-tenancy in Entelim is enforced *implicitly*, by two SQLAlchemy event
listeners in ``app/middleware/tenant_isolation.py`` that key off
``flask.g.current_org_id``:

* ``do_orm_execute``  — adds ``WHERE organization_id = g.current_org_id`` to ORM
  SELECT / UPDATE / DELETE on every ``TenantMixin`` model.
* ``before_flush``    — stamps ``organization_id`` on new ``TenantMixin`` rows.

Both listeners begin with::

    if not hasattr(g, "current_org_id") or g.current_org_id is None:
        return

so **outside a request there is no filter at all**. Every scheduled job runs
outside a request. A job that simply does ``Model.query.all()`` under
``app.app_context()`` reads the whole estate, and — worse — a job that *writes*
under a partially-set context writes into whichever tenant happens to be on
``g`` at that moment. The harness below is the only sanctioned way to run
per-tenant work on a cadence.

The four hazards it closes, in order of how easy they are to reintroduce:

1. **No tenant context** → unfiltered global read/write. Closed by setting
   ``g.current_org_id`` inside a real ``test_request_context``-equivalent app
   context before the callable is invoked, and asserting it is set.
2. **Identity-map carry-over between tenants.** ``Query.get()`` /
   ``Session.get()`` are scoped only on an identity-map *miss*. On a hit they
   return the cached object and emit **no SQL at all**, so ``do_orm_execute``
   never runs and no tenant predicate is applied. A loop over tenants inside one
   session therefore hands tenant B the object it loaded for tenant A. Closed by
   ``db.session.remove()`` between every tenant — see ``_reset_session``.
   *Do not weaken this to ``expire_all()``*: that keeps identities in the map.
3. **Cross-tenant transaction bleed.** An exception in tenant A leaves the
   session in a failed transaction; tenant B then hits ``InFailedSqlTransaction``
   and the whole run reports a cascade of bogus failures. Closed by rolling back
   and removing the session in the ``finally`` of every tenant iteration.
4. **Multi-process duplication.** ``preload_app = True`` with three gunicorn
   workers means anything registered in ``create_app()`` runs three times.
   Closed by ``pg_try_advisory_lock`` — the pattern already used in
   ``app/commands/cutover_capability_tenancy.py`` — held on a dedicated
   connection for the life of the run.

Failures are **never swallowed**. Per-tenant exceptions are caught only so one
tenant cannot abort the others; each is logged at ERROR with the org id and a
full traceback, counted, and re-surfaced in the returned ``JobRun`` — and
``JobRun.failed`` is what the CLI exit code and the operator page read.

Intended home: ``app/jobs/tenant_safe_job.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import datetime as _dt
import logging
import time
from typing import Callable, Iterator, Sequence

from flask import g

from app.extensions import db

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Result types — a job run is a measurement, not a log line
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TenantResult:
    """Outcome for a single organisation."""

    organization_id: int
    ok: bool
    duration_ms: int
    value: object = None          # whatever the callable returned (ok runs)
    error: str | None = None      # repr of the exception (failed runs)


@dataclass
class JobRun:
    """Outcome of one whole scheduled run, across all organisations.

    ``succeeded + failed`` is the number of tenants actually attempted. It is
    deliberately NOT defaulted to zero-on-error anywhere: a run that could not
    enumerate tenants reports ``skipped_locked`` or raises, so an operator can
    never mistake "nothing ran" for "nothing was wrong". (CLAUDE.md: a 0 that
    means "not computed" is indistinguishable from a measured zero.)
    """

    job_name: str
    started_at: _dt.datetime
    finished_at: _dt.datetime | None = None
    results: list[TenantResult] = field(default_factory=list)
    skipped_locked: bool = False   # another process held the advisory lock

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def duration_ms(self) -> int:
        if self.finished_at is None:
            return 0
        return int((self.finished_at - self.started_at).total_seconds() * 1000)

    def as_dict(self) -> dict:
        return {
            "job_name": self.job_name,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped_locked": self.skipped_locked,
            "failures": [
                {"organization_id": r.organization_id, "error": r.error}
                for r in self.results
                if not r.ok
            ],
        }


class JobLockUnavailable(RuntimeError):
    """Another process already holds this job's advisory lock."""


# --------------------------------------------------------------------------- #
# Session hygiene
# --------------------------------------------------------------------------- #


def _reset_session() -> None:
    """Discard the scoped session entirely, between tenants.

    ``db.session.remove()`` rolls back any open transaction AND drops the
    Session object, which drops the identity map with it. That is the point:
    ``Session.get()`` / ``Query.get()`` short-circuit on an identity-map hit
    without emitting SQL, so with a surviving map a lookup made under tenant B
    can return the instance loaded under tenant A — no query, therefore no
    ``do_orm_execute``, therefore no tenant predicate.

    ``expire_all()`` is NOT sufficient (identities stay in the map and a
    ``get()`` still short-circuits into a refresh of the same row);
    ``expunge_all()`` clears the map but leaves the transaction and connection
    state, so it does not cover hazard 3. Only ``remove()`` covers both.
    """
    try:
        db.session.rollback()
    except Exception:  # a broken connection must not stop us reaching remove()
        logger.exception("tenant_safe_job: rollback before session reset failed")
    db.session.remove()


@contextmanager
def tenant_scope(organization_id: int) -> Iterator[int]:
    """Run a block as if it were a request belonging to *organization_id*.

    Sets ``g.current_org_id`` — the single value the isolation listeners read —
    and guarantees a clean session on both entry and exit, so nothing loaded
    before or during the block can survive into another tenant's scope.

    Must be called inside an ``app.app_context()``; ``g`` is the app-context
    globals object, so the listeners see it exactly as they do in a request.
    """
    if organization_id is None:
        # A None here would make both listeners no-op and run the body
        # UNFILTERED across every tenant. Refuse rather than degrade.
        raise ValueError("tenant_scope requires a concrete organization_id")

    _reset_session()                      # nothing inherited from the previous tenant
    previous = getattr(g, "current_org_id", None)
    g.current_org_id = organization_id
    g.current_org = None                  # jobs must not rely on the ORM object
    try:
        yield organization_id
    finally:
        _reset_session()                  # nothing leaks forward to the next tenant
        g.current_org_id = previous


# --------------------------------------------------------------------------- #
# Cross-process single-run guard
# --------------------------------------------------------------------------- #


@contextmanager
def job_lock(job_name: str, *, required: bool = True) -> Iterator[bool]:
    """Hold a session-level PostgreSQL advisory lock for the whole run.

    WHY: ``gunicorn.conf.py`` sets ``preload_app = True`` and production runs
    ``GUNICORN_WORKERS=3``. Anything registered during ``create_app()`` exists in
    every worker, so an in-process scheduler fires each job three times
    concurrently. Advisory locks are held by the *session* (connection), are
    released automatically if the process dies or the connection drops, and cost
    nothing when uncontended — which is exactly the semantics a single-run guard
    needs, and strictly better than a lock row that a crashed worker leaves set.

    A dedicated connection is used, not ``db.session``: the harness calls
    ``db.session.remove()`` between every tenant, and a session-level lock taken
    on the scoped session would be released the moment that connection returned
    to the pool.

    Yields True when the lock was acquired. With ``required=True`` (the default)
    a contended lock raises ``JobLockUnavailable`` so the caller records a
    skipped run rather than silently doing nothing.
    """
    connection = db.engine.connect()
    key = None
    acquired = False
    try:
        key = int(
            connection.execute(
                db.text("SELECT hashtext(:name)::bigint"),
                {"name": f"archie_job:{job_name}"},
            ).scalar_one()
        )
        acquired = bool(
            connection.execute(
                db.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            ).scalar_one()
        )
        if not acquired:
            logger.info(
                "tenant_safe_job: %s skipped — advisory lock held by another process",
                job_name,
            )
            if required:
                raise JobLockUnavailable(job_name)
        yield acquired
    finally:
        try:
            if acquired and key is not None:
                connection.execute(
                    db.text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                )
        except Exception:
            # Not fatal: closing the connection below releases the lock anyway.
            logger.exception("tenant_safe_job: advisory unlock failed for %s", job_name)
        connection.close()


# --------------------------------------------------------------------------- #
# Tenant enumeration
# --------------------------------------------------------------------------- #


def active_organization_ids() -> list[int]:
    """IDs of organisations a scheduled job should visit.

    Deliberately a raw ``select`` of primary keys, executed while NO tenant
    context is set — this is the one query in a job that is *supposed* to be
    global. It returns ids, not ORM objects, so nothing enters an identity map
    that a later ``get()`` could serve from under the wrong tenant.

    ``Organization`` is not a ``TenantMixin`` model (it is the tenant), so no
    filter applies to it either way.
    """
    from app.models.organization import Organization

    rows = db.session.execute(
        db.select(Organization.id)
        .where(Organization.is_active.isnot(False))  # NULL is legacy-active
        .order_by(Organization.id)
    ).all()
    return [int(row[0]) for row in rows]


# --------------------------------------------------------------------------- #
# The harness
# --------------------------------------------------------------------------- #


def run_for_each_tenant(
    app,
    job_name: str,
    func: Callable[[int], object],
    *,
    organization_ids: Sequence[int] | None = None,
    use_lock: bool = True,
    on_result: Callable[[TenantResult], None] | None = None,
) -> JobRun:
    """Run ``func(organization_id)`` exactly once per organisation, safely.

    ``func`` receives the organisation id and runs with ``g.current_org_id`` set,
    so ordinary ORM code inside it is tenant-filtered by the existing listeners —
    it must NOT add its own ``organization_id`` filter to ``TenantMixin`` models
    (that double-filters), but it SHOULD put ``organization_id`` in any raw-SQL
    predicate, which the listeners cannot reach.

    Guarantees:
      * one advisory-locked run across all gunicorn workers and any cron overlap;
      * ``g.current_org_id`` set for the whole of ``func``, never None;
      * ``db.session.remove()`` before and after every tenant, so no identity
        map, transaction, or connection state crosses a tenant boundary;
      * one tenant's failure never aborts the others, and never disappears —
        it is logged with a traceback and returned in the ``JobRun``.
    """
    run = JobRun(job_name=job_name, started_at=_dt.datetime.utcnow())

    with app.app_context():
        lock_cm = job_lock(job_name, required=False) if use_lock else _always_acquired()
        with lock_cm as acquired:
            if not acquired:
                run.skipped_locked = True
                run.finished_at = _dt.datetime.utcnow()
                return run

            # Enumerate BEFORE entering any tenant scope, and materialise to a
            # plain list of ints: the loop must not hold a query cursor open
            # across the session removals below.
            try:
                ids = (
                    list(organization_ids)
                    if organization_ids is not None
                    else active_organization_ids()
                )
            finally:
                _reset_session()

            logger.info(
                "tenant_safe_job: %s starting for %d organisation(s)", job_name, len(ids)
            )

            for organization_id in ids:
                started = time.monotonic()
                try:
                    with tenant_scope(organization_id):
                        value = func(organization_id)
                        # Commit inside the tenant scope so the flush still
                        # carries this tenant's stamp from before_flush.
                        db.session.commit()
                    result = TenantResult(
                        organization_id=organization_id,
                        ok=True,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        value=value,
                    )
                except Exception as exc:  # noqa: BLE001 — deliberate, see below
                    # Caught ONLY to keep one tenant from aborting the rest.
                    # It is logged with a traceback and returned in the JobRun,
                    # which the CLI turns into a non-zero exit and the operator
                    # page renders. Nothing here is swallowed.
                    logger.exception(
                        "tenant_safe_job: %s FAILED for organization_id=%s",
                        job_name,
                        organization_id,
                    )
                    _reset_session()
                    result = TenantResult(
                        organization_id=organization_id,
                        ok=False,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        error=repr(exc),
                    )

                run.results.append(result)
                if on_result is not None:
                    try:
                        on_result(result)
                    except Exception:
                        logger.exception(
                            "tenant_safe_job: %s on_result hook failed for org %s",
                            job_name,
                            organization_id,
                        )

            run.finished_at = _dt.datetime.utcnow()
            logger.info(
                "tenant_safe_job: %s finished — %d ok, %d failed, %d ms",
                job_name,
                run.succeeded,
                run.failed,
                run.duration_ms,
            )
            return run


@contextmanager
def _always_acquired() -> Iterator[bool]:
    """Lock stand-in for tests and single-process CLI use."""
    yield True


def tenant_job(job_name: str, **harness_kwargs):
    """Decorator form.

    ::

        @tenant_job("archimate-coverage")
        def reconcile_coverage(organization_id: int) -> dict:
            ...   # ordinary tenant-filtered ORM code

        run = reconcile_coverage.run(app)      # JobRun

    The undecorated callable stays reachable as ``.func`` so unit tests can
    exercise the body directly inside ``tenant_ctx``.
    """

    def _decorate(func: Callable[[int], object]):
        def _run(app, **kwargs) -> JobRun:
            merged = {**harness_kwargs, **kwargs}
            return run_for_each_tenant(app, job_name, func, **merged)

        func.run = _run          # type: ignore[attr-defined]
        func.job_name = job_name  # type: ignore[attr-defined]
        func.func = func          # type: ignore[attr-defined]
        return func

    return _decorate


__all__ = [
    "JobLockUnavailable",
    "JobRun",
    "TenantResult",
    "active_organization_ids",
    "job_lock",
    "run_for_each_tenant",
    "tenant_job",
    "tenant_scope",
]
