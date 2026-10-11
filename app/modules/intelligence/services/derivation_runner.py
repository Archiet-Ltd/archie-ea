"""DE-1: the Derivation Runner (FR-1) — the derivation engine's only caller.

Loads a tenant's ArchiMate model, calls the ADR-002-v2-extended
``compute_derived``, and returns a ``DerivationResult``. Computes only: it
does not persist anything, expose a route, or render a screen. The
derived-fact table, its upsert, the invalidation hook and the recompute job
belong to T-003 (sdd-v2.md AA-3; ADR-003 — derivation never runs in the
write path).
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import datetime as _dt
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from flask import g, has_app_context

from app.extensions import db
from app.jobs.tenant_safe_job import tenant_scope
from app.services.archimate_derivation_service import ArchiMateDerivationService

# The module constant AA-3 requires: a later rule change bumps this, which is
# what makes T-003's recompute selectively re-triggerable rather than a
# blanket recompute of every tenant regardless of whether its rules changed.
ENGINE_VERSION = "1.0.0"


@dataclass(frozen=True)
class DerivationResult:
    """The outcome of one ``DerivationRunner.run()`` call, for one tenant.

    ``ratio`` is ``None`` rather than ``0`` when ``explicit_count`` is zero —
    a measured zero and "not computed" must stay distinguishable (CLAUDE.md
    "Never invent data": a 0 that means "not computed" is indistinguishable
    from a measured zero).

    ``derived`` carries the raw rows ``compute_derived`` returned (each with
    ``source_id``, ``target_id``, ``type``, ``chain``, ``depth``,
    ``relationship_chain``, ``rule_id`` — ADR-002-v2). Nothing here persists
    them; T-003's upsert is the first consumer.
    """

    explicit_count: int
    derived_count: int
    ratio: Optional[float]
    duration_ms: int
    engine_version: str
    derived: List[Dict[str, Any]] = field(default_factory=list)


class DerivationRunner:
    """DE-1. The only caller of ``ArchiMateDerivationService.compute_derived``."""

    def __init__(self, service: Optional[ArchiMateDerivationService] = None) -> None:
        self._service = service or ArchiMateDerivationService()

    @staticmethod
    @contextmanager
    def _tenant_scope_for(organization_id: int):
        """Reuse an existing harness-managed tenant scope when one is active.

        ``run_for_each_tenant`` already establishes ``tenant_scope`` for the
        organisation it is visiting. Re-entering ``tenant_scope`` for the same
        organisation from inside that harness is harmless in production, but in
        the rollback-backed test session it adds a second scoped-session reset
        around the write, which rolls back the just-written run record before
        the yield endpoint can read it. Reuse the active harness scope instead
        of nesting a second one.
        """
        if (
            has_app_context()
            and (
                getattr(g, "_tenant_scope_organization_id", None) == organization_id
                or getattr(g, "current_org_id", None) == organization_id
            )
        ):
            with nullcontext():
                yield
            return
        with tenant_scope(organization_id):
            yield

    def run(self, organization_id: int) -> DerivationResult:
        """Compute derived relationships for one tenant.

        Must run inside an ``app.app_context()`` — ``tenant_scope`` requires
        one. Tenant scope comes from ``tenant_scope()`` only: the ORM reads
        below carry no hand-written ``organization_id`` predicate, so the
        existing ``do_orm_execute`` listener does the filtering and this
        runner cannot double-filter (AA-3; CLAUDE.md "Multi-tenancy is
        implicit").
        """
        # Deferred import: avoids importing the whole app.models package at
        # module-import time, matching the lazy-model-import convention used
        # elsewhere in this codebase (e.g. app/commands/archimate_commands.py).
        from app.models import ArchiMateElement, ArchiMateRelationship

        started = time.monotonic()

        with self._tenant_scope_for(organization_id):
            element_rows = db.session.execute(db.select(ArchiMateElement)).scalars().all()
            relationship_rows = (
                db.session.execute(db.select(ArchiMateRelationship)).scalars().all()
            )

            elements = [
                {"id": e.id, "name": e.name, "type": e.type, "layer": e.layer}
                for e in element_rows
            ]
            relationships = [
                {
                    "id": r.id,
                    "source_id": r.source_id,
                    "target_id": r.target_id,
                    "type": r.type,
                }
                for r in relationship_rows
            ]

            derived = self._service.compute_derived(elements, relationships)

        duration_ms = int((time.monotonic() - started) * 1000)
        explicit_count = len(relationships)
        derived_count = len(derived)
        ratio = (derived_count / explicit_count) if explicit_count else None

        return DerivationResult(
            explicit_count=explicit_count,
            derived_count=derived_count,
            ratio=ratio,
            duration_ms=duration_ms,
            engine_version=ENGINE_VERSION,
            derived=derived,
        )

    def run_and_persist(
        self, organization_id: int, *, trigger: str
    ) -> DerivationResult:
        """Compute, then upsert into the T-003 store (DE-2), for one tenant.

        Additive to ``run()`` — computing without writing stays possible (the
        recompute job in task 03 is the caller that wants both). Must run
        inside ``app.app_context()``; tenant scope is set once here and
        covers both the compute and the persist, so no in-between window can
        be observed from another tenant's scope.

        Upsert semantics (DA-1 natural key
        ``organization_id, source_element_id, target_element_id,
        derived_type, rule_id``):
          * one ``INSERT ... ON CONFLICT ON CONSTRAINT uq_derived_rel DO
            UPDATE`` per batch, not one statement per row;
          * ``confidence = 1.00`` / ``provenance = 'derivation'`` always,
            with no caller override (ADR-001);
          * ``computed_at`` stamped, ``stale`` cleared, ``stale_since`` /
            ``stale_reason`` set NULL;
          * rows for this tenant the engine no longer produces are deleted.

        ``trigger`` (T-005 D7/D5) — ``"scheduled"`` or ``"on_demand"``,
        whatever the caller actually is; never guessed here. Required
        (no default) so a future caller cannot silently default to a wrong
        value. A ``DerivationRun`` row is written in the SAME
        ``tenant_scope`` block, before the commit that persists the facts —
        so the run record and the facts it describes land atomically and no
        reader can ever observe one without the other (D7's binding
        "producer ships with the store" rule).
        """
        result = self.run(organization_id)

        with self._tenant_scope_for(organization_id):
            self._persist(organization_id, result.derived)
            self._record_run(organization_id, result, trigger)
            db.session.commit()

        return result

    def _record_run(
        self, organization_id: int, result: DerivationResult, trigger: str
    ) -> None:
        """Write one ``DerivationRun`` row for a completed run (D7).

        Every field is copied from the real, measured ``DerivationResult`` —
        never a literal (CLAUDE.md "never invent data"). Called only from
        inside ``run_and_persist``'s success path: a raised exception or a
        lock-skip never reaches here, so a failed/skipped run correctly
        writes no row (D6 -- absence means "no completed run").
        """
        from app.modules.intelligence.models.derivation_run import DerivationRun

        # Naive UTC, not datetime.now(timezone.utc) (reviews/pr304-ruling-v2.md,
        # finding 2): the DerivationRun columns are plain TIMESTAMP WITHOUT TIME
        # ZONE, matching this codebase's own convention (~1,400 plain-UTC
        # columns against ~70 zone-aware). psycopg2 does not store a tz-aware
        # datetime into a naive column as-is -- it converts it to the
        # connection's session timezone first, then strips tzinfo, so an aware
        # UTC value silently becomes the session's LOCAL wall-clock time
        # mislabelled as UTC the moment that session isn't itself UTC (BST
        # here is UTC+1; confirmed directly -- a round-trip read back a value
        # exactly one hour ahead of the true UTC instant). datetime.utcnow()
        # is naive to begin with, so psycopg2 stores it unconverted.
        finished = _dt.datetime.utcnow()
        started = finished - _dt.timedelta(milliseconds=result.duration_ms)
        db.session.add(
            DerivationRun(
                organization_id=organization_id,
                started_at=started,
                finished_at=finished,
                duration_ms=result.duration_ms,
                explicit_count=result.explicit_count,
                derived_count=result.derived_count,
                ratio=result.ratio,
                engine_version=result.engine_version,
                trigger=trigger,
            )
        )

    def _persist(self, organization_id: int, derived: List[Dict[str, Any]]) -> None:
        """Upsert *derived* rows for *organization_id* and prune the rest.

        Raw SQL — the ``organization_id`` predicate is written explicitly on
        every statement because the ORM tenant listeners do not reach raw
        SQL, and this path is inside ``tenant_scope`` already so an ORM
        write would be double-filtered if it also carried the predicate.
        """
        now = _dt.datetime.utcnow()
        natural_keys: List[tuple] = []

        if derived:
            rows = []
            for item in derived:
                chain = list(item.get("relationship_chain") or item.get("chain") or [])
                chain_element_ids = list(item.get("chain") or [])
                depth = item.get("depth")
                rule_id = item.get("rule_id")
                derived_type = item.get("type")
                source_id = item.get("source_id")
                target_id = item.get("target_id")
                rows.append(
                    {
                        "organization_id": organization_id,
                        "source_element_id": source_id,
                        "target_element_id": target_id,
                        "derived_type": derived_type,
                        "rule_id": rule_id,
                        "chain": chain,
                        "chain_element_ids": chain_element_ids,
                        "depth": depth,
                        "engine_version": ENGINE_VERSION,
                        "computed_at": now,
                    }
                )
                natural_keys.append((source_id, target_id, derived_type, rule_id))

            db.session.execute(
                db.text(
                    """
                    INSERT INTO archimate_derived_relationships (
                        organization_id, source_element_id, target_element_id,
                        derived_type, rule_id, chain, chain_element_ids, depth,
                        confidence, provenance, engine_version, computed_at,
                        stale, stale_since, stale_reason
                    ) VALUES (
                        :organization_id, :source_element_id, :target_element_id,
                        :derived_type, :rule_id, :chain, :chain_element_ids, :depth,
                        1.00, 'derivation', :engine_version, :computed_at,
                        FALSE, NULL, NULL
                    )
                    ON CONFLICT ON CONSTRAINT uq_derived_rel DO UPDATE SET
                        chain = EXCLUDED.chain,
                        chain_element_ids = EXCLUDED.chain_element_ids,
                        depth = EXCLUDED.depth,
                        engine_version = EXCLUDED.engine_version,
                        computed_at = EXCLUDED.computed_at,
                        confidence = 1.00,
                        provenance = 'derivation',
                        stale = FALSE,
                        stale_since = NULL,
                        stale_reason = NULL
                    """
                ),
                rows,
            )

        # Prune: delete this tenant's rows the engine no longer produces.
        # A row is kept only if its natural key (minus organization_id,
        # which is fixed to this tenant by the WHERE clause) is one of the
        # keys just written this run.
        if natural_keys:
            # Compute the set of ids to delete explicitly, then delete by id
            # in chunks (round-1 refuter finding D7). A single "NOT IN
            # (VALUES <every surviving natural key>)" statement uses 4 bound
            # params per surviving row and would exceed Postgres's 65535
            # parameter limit once a tenant has roughly 16,380+ derived rows
            # in one run -- but naively chunking that survivor VALUES list
            # is itself wrong: a DELETE scoped to "NOT IN (this one chunk of
            # survivors)" would delete every row that is a survivor in a
            # DIFFERENT chunk too, since it is absent from *this* chunk's
            # list. Instead: select this tenant's current natural keys,
            # diff against the full survivor set in Python, and delete only
            # the resulting stale ids -- a positive "id IN (...)" DELETE is
            # safe to chunk because each chunk's membership list is already
            # exactly the rows meant to be deleted, independent of any other
            # chunk.
            survivor_keys = {
                (src, tgt, typ, rule) for src, tgt, typ, rule in natural_keys
            }
            existing_rows = db.session.execute(
                db.text(
                    """
                    SELECT id, source_element_id, target_element_id, derived_type, rule_id
                    FROM archimate_derived_relationships
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": organization_id},
            ).all()
            stale_ids = [
                row.id
                for row in existing_rows
                if (row.source_element_id, row.target_element_id, row.derived_type, row.rule_id)
                not in survivor_keys
            ]

            _PRUNE_CHUNK_SIZE = 1000
            for start in range(0, len(stale_ids), _PRUNE_CHUNK_SIZE):
                chunk_ids = stale_ids[start : start + _PRUNE_CHUNK_SIZE]
                db.session.execute(
                    db.text(
                        """
                        DELETE FROM archimate_derived_relationships
                        WHERE organization_id = :organization_id
                          AND id = ANY(CAST(:ids AS integer[]))
                        """
                    ),
                    {"organization_id": organization_id, "ids": chunk_ids},
                )
        else:
            # The engine produced nothing this run: every existing row for
            # this tenant is stale by definition of "no longer produced".
            db.session.execute(
                db.text(
                    "DELETE FROM archimate_derived_relationships "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )


__all__ = ["DerivationRunner", "DerivationResult", "ENGINE_VERSION"]
