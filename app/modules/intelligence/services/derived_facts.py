"""DE-3 read path: the ONE accessor over the derived-fact store (ADR-004).

Every read applies ``stale = FALSE`` by default, in this one place, so
"forgot the filter" is not reachable from a caller. ``include_stale=True``
returns stale rows too, each carrying ``"stale": True`` and
``"reason": "derivation_stale"`` (the DE-14 closed-vocabulary member).

Task 03's endpoints are the only permitted callers beyond this module's own
tests -- no other read path over the store may exist at L1 (NFR-8).

Deliberately does NOT use ``app.jobs.tenant_safe_job.tenant_scope()`` (round-1
refuter finding D4). ``tenant_scope()`` is a background-job harness: it calls
``db.session.remove()`` on entry and exit, which is exactly right for a
scheduled job's own dedicated session lifecycle but destructive inside a live
request -- it discards the REQUEST's session (detaching whatever
``flask_login`` cached on ``g._login_user``, corrupting `after_request`
handlers that touch ``current_user``) and clobbers ``g.current_org`` for the
rest of the request (``tenant_scope``'s ``finally`` only restores
``current_org_id``). A request already has ``g.current_org_id`` set correctly
by the ordinary request lifecycle, and the existing tenant-isolation
``do_orm_execute`` listener (``app/middleware/tenant_isolation.py``) already
filters every ORM read by it -- no extra wrapper is needed or safe here. An
explicit ``organization_id`` equality check is still applied below as
defence-in-depth for the id-lookup path, matching the pattern used elsewhere
in this module for raw-SQL predicates.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.extensions import db
from app.modules.intelligence.services.reason_codes import validate_reason_code

STALE_REASON = validate_reason_code("derivation_stale")


def _apply_default_staleness_filter(stmt, model, include_stale: bool):
    """The one place the ``stale = FALSE`` default is applied (ADR-004).

    Isolated as its own function -- not inlined in ``list_derived_facts`` --
    so the task 02 mutation-proof test (acceptance item 13) can monkeypatch
    exactly this seam to simulate "the guard was removed" and confirm the
    stale-never-current test goes red, without editing source under test.
    """
    if include_stale:
        return stmt
    return stmt.where(model.stale.is_(False))


def _serialize(row) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": row.id,
        "organization_id": row.organization_id,
        "source_element_id": row.source_element_id,
        "target_element_id": row.target_element_id,
        "derived_type": row.derived_type,
        "rule_id": row.rule_id,
        "chain": list(row.chain or []),
        "chain_element_ids": list(row.chain_element_ids or []),
        "depth": row.depth,
        "confidence": float(row.confidence) if row.confidence is not None else None,
        "provenance": row.provenance,
        "engine_version": row.engine_version,
        "computed_at": row.computed_at.isoformat() if row.computed_at else None,
    }
    if row.stale:
        payload["stale"] = True
        payload["reason"] = STALE_REASON
    else:
        payload["stale"] = False
    return payload


def list_derived_facts(
    organization_id: int,
    *,
    include_stale: bool = False,
    source_element_id: Optional[int] = None,
    target_element_id: Optional[int] = None,
    max_depth: Optional[int] = None,
    direction: Optional[str] = None,
    layer: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The single read path over ``archimate_derived_relationships``.

    Must be called inside ``app.app_context()``. Called from a request, the
    existing tenant-isolation ``do_orm_execute`` listener already filters
    this read by ``g.current_org_id``; the explicit ``organization_id ==``
    predicate below is defence-in-depth and is what makes this function
    correct even when called with no ambient request context (e.g. a future
    job caller), where the listener would otherwise no-op entirely.

    T-004 additive parameters (D4 in ``00-verification-notes.md``):

    - ``max_depth`` -- a predicate on ``depth`` (SQL, uses no new index;
      ``depth`` is a plain column comparison and stays within NFR-5).
    - ``direction`` -- when both ``source_element_id`` and
      ``target_element_id`` are given, the existing behaviour (both
      positional filters, effectively an AND on that pair) is preserved for
      any caller not passing ``direction``. When ``direction="both"`` is
      passed together with a single ``element_id``-shaped filter (i.e. the
      caller passes the SAME id as both ``source_element_id`` and
      ``target_element_id``, T-004's calling convention -- see
      ``query_service.py``), the predicate becomes ``source = X OR
      target = X`` instead of ANDing the two columns. This is additive: an
      existing caller that never passes ``direction`` sees no behaviour
      change.
    - ``layer`` -- filters to rows whose source OR target element is on the
      named ArchiMate layer, via a join to ``ArchiMateElement`` (also
      ``TenantMixin``-fenced, so no hand-written predicate is needed on that
      side either).
    """
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    stmt = db.select(DerivedRelationship).where(
        DerivedRelationship.organization_id == organization_id
    )
    stmt = _apply_default_staleness_filter(stmt, DerivedRelationship, include_stale)

    if (
        direction == "both"
        and source_element_id is not None
        and target_element_id is not None
        and source_element_id == target_element_id
    ):
        anchor = source_element_id
        stmt = stmt.where(
            db.or_(
                DerivedRelationship.source_element_id == anchor,
                DerivedRelationship.target_element_id == anchor,
            )
        )
    else:
        if source_element_id is not None:
            stmt = stmt.where(DerivedRelationship.source_element_id == source_element_id)
        if target_element_id is not None:
            stmt = stmt.where(DerivedRelationship.target_element_id == target_element_id)

    if max_depth is not None:
        stmt = stmt.where(DerivedRelationship.depth <= max_depth)

    if layer is not None:
        from app.models import ArchiMateElement

        src_el = db.aliased(ArchiMateElement)
        tgt_el = db.aliased(ArchiMateElement)
        stmt = stmt.join(
            src_el, src_el.id == DerivedRelationship.source_element_id, isouter=True
        ).join(tgt_el, tgt_el.id == DerivedRelationship.target_element_id, isouter=True)
        stmt = stmt.where(db.or_(src_el.layer == layer, tgt_el.layer == layer))

    rows = db.session.execute(stmt).scalars().all()
    return [_serialize(r) for r in rows]


def derived_fact_aggregates(organization_id: int) -> Dict[str, Any]:
    """T-005 (D7/D8): per-tenant SQL aggregates over the derived-fact store.

    Genuine ``COUNT``/``MAX``/``COUNT(DISTINCT ...)`` SQL aggregates -- never
    ``len(list_derived_facts(...))``, which would materialise every row into
    Python just to count them (task 01 constraint; a tenant with 100k derived
    rows must not pay that cost). Must be called inside ``app.app_context()``;
    called from a request, the existing tenant-isolation ``do_orm_execute``
    listener already filters this read by ``g.current_org_id`` -- the
    explicit ``organization_id ==`` predicate below is defence-in-depth,
    matching this module's own documented pattern, and is what makes this
    function correct when called with no ambient request context too.

    Returns:
      - ``derived_count`` -- non-stale row count.
      - ``stale_count`` -- stale row count.
      - ``computed_at`` -- ``MAX(computed_at)`` over the tenant's rows
        (across stale and non-stale), ``None`` when there are none -- never a
        fabricated date.
      - ``engine_versions`` -- the DISTINCT ``engine_version`` values present
        on the tenant's rows (D9: the store's own values, never
        ``derivation_runner.ENGINE_VERSION``), as a sorted list.
    """
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    derived_count = db.session.execute(
        db.select(db.func.count(DerivedRelationship.id)).where(
            DerivedRelationship.organization_id == organization_id,
            DerivedRelationship.stale.is_(False),
        )
    ).scalar_one()

    stale_count = db.session.execute(
        db.select(db.func.count(DerivedRelationship.id)).where(
            DerivedRelationship.organization_id == organization_id,
            DerivedRelationship.stale.is_(True),
        )
    ).scalar_one()

    computed_at = db.session.execute(
        db.select(db.func.max(DerivedRelationship.computed_at)).where(
            DerivedRelationship.organization_id == organization_id
        )
    ).scalar_one()

    engine_versions = db.session.execute(
        db.select(DerivedRelationship.engine_version)
        .where(DerivedRelationship.organization_id == organization_id)
        .distinct()
    ).scalars().all()

    return {
        "derived_count": int(derived_count),
        "stale_count": int(stale_count),
        "computed_at": computed_at,
        "engine_versions": sorted(v for v in engine_versions if v is not None),
    }


def stale_derived_fact_ids(organization_id: int, limit: Optional[int] = None) -> List[int]:
    """This tenant's stale ``DerivedRelationship`` ids, explicitly scoped.

    ``derived_fact_aggregates(organization_id)`` returns ``stale_count`` (a
    number) but not which rows; a caller that needs to tell "the same facts
    are stale" from "a different fact went stale" (not only that the count
    held steady) needs the id set itself. The staleness filter is applied
    here, in this module, rather than re-implemented at the call site --
    this module's own docstring is the one place that filter belongs. Must
    be called inside ``app.app_context()``; the explicit ``organization_id
    ==`` predicate is defence-in-depth on top of the tenant-isolation
    listener, matching this module's pattern.

    ``limit``, when given, bounds the query itself (``ORDER BY id LIMIT``),
    not a slice taken after fetching every row: a caller that persists the
    result (a baseline snapshot, not a live UI list) can ask for a bounded
    id set without paying to materialise an unbounded one first. Whether the
    true stale set is bigger than what came back is already answered by
    ``derived_fact_aggregates``'s own ``stale_count`` -- comparing it against
    ``len(result)`` tells a capped caller whether it was truncated, without
    this function needing to say so itself.
    """
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    stmt = (
        db.select(DerivedRelationship.id)
        .where(
            DerivedRelationship.organization_id == organization_id,
            DerivedRelationship.stale.is_(True),
        )
        .order_by(DerivedRelationship.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = db.session.execute(stmt).scalars().all()
    return list(rows)


def latest_derivation_run(organization_id: int):
    """T-005 (D5/D6/D7): the most recent completed ``DerivationRun`` for a
    tenant, or ``None`` when derivation has never completed for it.

    ``None`` is the exact fact the yield endpoint's not-computed branch
    reads -- distinct from "ran and derived zero", which returns a real row
    with ``derived_count == 0`` (D6). Must be called inside
    ``app.app_context()``; the explicit ``organization_id ==`` predicate is
    defence-in-depth on top of the tenant-isolation listener, matching this
    module's pattern.
    """
    from app.modules.intelligence.models.derivation_run import DerivationRun

    stmt = (
        db.select(DerivationRun)
        .where(DerivationRun.organization_id == organization_id)
        .order_by(DerivationRun.finished_at.desc(), DerivationRun.id.desc())
        .limit(1)
    )
    return db.session.execute(stmt).scalars().first()


def derivation_status(organization_id: int, *, recent: int = 5) -> Dict[str, Any]:
    """What the Ask page shows about worked-out connections, for one tenant.

    The latest completed runs (newest first, at most ``recent``), the
    current and out-of-date counts from the derived-fact store, and how many
    out-of-date rows each stale reason accounts for. Reads the same two
    stores the recompute and yield endpoints answer from; nothing is
    computed here. ``runs`` is empty when derivation has never completed --
    the page renders that as "—", never as zero.
    """
    from app.modules.intelligence.models.derivation_run import DerivationRun
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    runs = db.session.execute(
        db.select(DerivationRun)
        .where(DerivationRun.organization_id == organization_id)
        .order_by(DerivationRun.finished_at.desc(), DerivationRun.id.desc())
        .limit(recent)
    ).scalars().all()
    reasons = db.session.execute(
        db.select(DerivedRelationship.stale_reason, db.func.count(DerivedRelationship.id))
        .where(
            DerivedRelationship.organization_id == organization_id,
            DerivedRelationship.stale.is_(True),
        )
        .group_by(DerivedRelationship.stale_reason)
        .order_by(DerivedRelationship.stale_reason)
    ).all()
    aggregates = derived_fact_aggregates(organization_id)
    return {
        "runs": list(runs),
        "last_run": runs[0] if runs else None,
        "current_count": aggregates["derived_count"],
        "stale_count": aggregates["stale_count"],
        "stale_reasons": [(reason, int(count)) for reason, count in reasons],
    }


def get_derived_fact(
    organization_id: int, derived_id: int, *, include_stale: bool = True
) -> Optional[Dict[str, Any]]:
    """Fetch one derived fact by id, scoped to *organization_id*.

    ``include_stale`` defaults True here (unlike ``list_derived_facts``)
    because the provenance-expansion endpoint (API-2) is explicitly allowed
    to show a stale row's provenance -- it must still carry the flag; task 03
    is responsible for treating a cross-tenant id as a 404, which this
    function's tenant scoping already guarantees (a row outside
    *organization_id* is invisible, not merely denied).
    """
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    stmt = db.select(DerivedRelationship).where(
        DerivedRelationship.id == derived_id,
        DerivedRelationship.organization_id == organization_id,
    )
    if not include_stale:
        stmt = stmt.where(DerivedRelationship.stale.is_(False))
    row = db.session.execute(stmt).scalar_one_or_none()
    return _serialize(row) if row is not None else None


__all__ = [
    "STALE_REASON",
    "derived_fact_aggregates",
    "get_derived_fact",
    "latest_derivation_run",
    "list_derived_facts",
    "stale_derived_fact_ids",
]
