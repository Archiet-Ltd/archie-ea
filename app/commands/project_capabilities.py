"""Project `business_capability` rows into the canonical `unified_capabilities` store.

`unified_capabilities` is Entelim's canonical capability store, but nothing in the
codebase ever *projected* an existing capability store into it — the seven writers
(`app/commands/seed_capabilities.py:2175`, `app/services/manufacturing_seed_service.py:51`,
and five others) are seeders, importers and per-row UI creates. Production therefore
holds 461 `business_capability` rows and an empty canonical store, so every surface
reading the canonical store answers a question about the estate with silence.

Why raw SQL and not the ORM
---------------------------
`_protect_reference_capability_writes` (`app/models/unified_capability.py:501-517`)
raises `PermissionError` for any new `UnifiedCapability` whose `organization_id`
differs from `g.current_org_id`. A projection writes for *every* tenant, so it can
only run outside a request context — never from a route, and never from a job that
fakes a request context. Raw SQL also sidesteps the identity-map hazard CLAUDE.md
documents (`Session.get()` is tenant-scoped only on a cache miss); if anyone ever
rewrites this as an ORM loop over tenants they must call `db.session.remove()`
between tenants.

Why `scope` is written here rather than left to the cutover
-----------------------------------------------------------
`install_cutover_constraints` (`app/commands/cutover_capability_tenancy.py:507`)
installs a CHECK (`:671-679`) and a BEFORE INSERT/UPDATE/DELETE trigger (`:590-611`)
that reject any row with a NULL `scope`. On a post-cutover database an insert that
did not set `scope` is rejected outright, so the projection classifies itself:
`business_capability.organization_id` is NOT NULL (`TenantMixin`,
`app/models/mixins/core.py`), which makes every projected row unambiguously
`scope='tenant'` with no judgement call and nothing invented.

Ordering against the cutover: see NOTES.md. On a *pre*-cutover database this command
refuses to apply unless `classify_capability` can classify a projected row, because
projecting first would otherwise leave every projected row `ambiguous` and permanently
block `run_cutover` (`cutover_capability_tenancy.py:745-749`).
"""

from __future__ import annotations

from pathlib import Path

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db
from app.commands.cutover_capability_tenancy import (
    ADVISORY_LOCK_ID as CUTOVER_ADVISORY_LOCK_ID,
    _foreign_keys,
    _lock_cutover_tables,
    _snapshot,
    _write_report_atomic,
)


# Neighbour of the cutover's 1_684_220_026 (`cutover_capability_tenancy.py:24`), so
# the two commands are individually serialised; both locks are taken below so they
# can also never interleave with each other.
ADVISORY_LOCK_ID = 1_684_220_027

SOURCE_TABLE = "business_capability"
PROVENANCE_INDEX = "uq_unified_capabilities_provenance"
PROVENANCE_MIGRATION = "scripts/migrate_unified_capability_provenance.sql"

# md5, not sha256: `source_checksum` is String(64) (`unified_capability.py:158`) so
# either fits, and the value is only ever compared for equality to detect a changed
# source row (`cutover_capability_tenancy.py:56-71` tests it for truthiness alone).
# md5() is core PostgreSQL; sha256() would add a pgcrypto dependency to a drift
# check that is not a security control.
_CHECKSUM_SQL = """md5(concat_ws('|',
        bc.id::text, bc.organization_id::text, bc.name, COALESCE(bc.code, ''),
        COALESCE(bc.description, ''), bc.level::text, COALESCE(bc.category, ''),
        COALESCE(bc.archimate_id, ''), COALESCE(bc.current_maturity_level::text, ''),
        COALESCE(bc.target_maturity_level::text, ''),
        COALESCE(bc.strategic_importance, ''), COALESCE(bc.business_value::text, ''),
        COALESCE(bc.business_owner, ''), COALESCE(bc.it_owner, ''),
        COALESCE(bc.parent_capability_id::text, ''),
        COALESCE(bc.is_deprecated::text, '')))"""

# The source row set, ordered and optionally limited, so --limit is deterministic:
# the same N rows on every run rather than whatever the planner returns first.
#
# `:single_id` narrows the source set to exactly one row when non-NULL, and is
# always bound (to NULL for the CLI's multi-row runs) so this single CTE serves
# both the CLI's --limit path and the write-time listener's single-row path with
# byte-identical SQL below it -- the listener in `app/models/business_capabilities.py`
# reuses `_PROJECT_SQL` verbatim, parameterised with `single_id` set and `row_limit`
# left at its default cap, rather than re-expressing the column mapping in Python.
# Two independent mappings would compute two different `source_checksum` values for
# the same row and every subsequent CLI run would report phantom drift forever.
_SOURCE_CTE = """
    source AS (
        SELECT * FROM business_capability
        WHERE (CAST(:single_id AS INTEGER) IS NULL OR id = CAST(:single_id AS INTEGER))
          AND NOT (id = ANY(CAST(:excluded_ids AS INTEGER[])))
        ORDER BY id
        LIMIT :row_limit
    )
"""

# Default bind values so every call site can omit `single_id`/`row_limit` when it
# doesn't care about narrowing, and the single-row listener path can omit `row_limit`.
_UNLIMITED_ROW_LIMIT = 2**31 - 1


def _source_params(
    *, row_limit: int | None = None, single_id: int | None = None, excluded_ids: list[int] | None = None
) -> dict[str, object]:
    """Bind values for `_SOURCE_CTE`, defaulted so either axis can be omitted."""

    return {
        "row_limit": row_limit if row_limit is not None else _UNLIMITED_ROW_LIMIT,
        "single_id": single_id,
        "excluded_ids": excluded_ids if excluded_ids is not None else []
    }


class ProjectionBlocked(RuntimeError):
    """Raised before an unsafe or unverifiable projection can proceed."""


def _has_provenance_index(connection) -> bool:
    """Is a VALID unique index on (source_table, source_id) installed?

    `indisvalid` matters: a failed CREATE INDEX CONCURRENTLY leaves an invalid index
    that PostgreSQL will not use as an ON CONFLICT arbiter, so its mere presence in
    pg_indexes is not evidence the projection is idempotent.
    """

    return bool(
        connection.execute(
            text(
                """
                SELECT count(*) FROM pg_index AS i
                JOIN pg_class AS c ON c.oid = i.indexrelid
                WHERE c.relname = :index_name
                  AND i.indrelid = to_regclass('unified_capabilities')
                  AND i.indisunique AND i.indisvalid
                """
            ),
            {"index_name": PROVENANCE_INDEX},
        ).scalar_one()
    )


def _cutover_is_complete(connection) -> bool:
    """Has `install_cutover_constraints` already run on this database?

    The scope/owner CHECK (`cutover_capability_tenancy.py:671-679`) is installed only
    by a completed cutover, so its presence is the cheapest true signal. It decides
    the ordering guard in `_plan`, not any write behaviour.
    """

    return bool(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'ck_unified_capabilities_scope_owner' "
                "AND conrelid = to_regclass('unified_capabilities')"
            )
        ).scalar_one()
    )


def _classifier_accepts_projection() -> bool:
    """Can the cutover classifier classify a projected row as `tenant`?

    A projected row has provenance but no application/benefit/work-package links, so
    `classify_capability` (`cutover_capability_tenancy.py:203-217`) reaches neither
    the `is_seeded_reference` branch (our `source_table` is `business_capability`,
    not `seed*`/`reference*`) nor the `len(owners) == 1` branch (owners is empty) and
    returns `ambiguous`. `run_cutover` then raises `CutoverBlocked` for the whole
    database. Probed by attribute rather than assumed, so this guard disappears by
    itself once the classifier gains the provenance branch described in NOTES.md.
    """

    from app.commands import cutover_capability_tenancy as cutover

    return bool(getattr(cutover, "CLASSIFIES_PROVENANCE_ONLY_TENANT", False))


def _counts(connection) -> dict[str, object]:
    """Measure both tables. Never assume either is empty."""

    row = connection.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM business_capability) AS source_rows,
              (SELECT count(*) FROM unified_capabilities) AS unified_rows,
              (SELECT count(*) FROM unified_capabilities
                WHERE source_table = :source_table) AS projected_rows,
              (SELECT count(*) FROM business_capability
                WHERE deprecated_in_favor_of_id IS NOT NULL) AS backlinked_rows,
              (SELECT count(DISTINCT organization_id)
                 FROM business_capability) AS source_organizations
            """
        ),
        {"source_table": SOURCE_TABLE},
    ).mappings().one()
    measured = {key: int(value) for key, value in row.items()}
    # Same expression as the cutover's own before/after, so the two commands'
    # numbers are directly comparable in their reports.
    measured["unified_snapshot"] = _snapshot(connection)
    return measured


def _plan(connection, row_limit: int | None) -> dict[str, object]:
    """Measure exactly what an --apply would do, and what would stop it."""

    limit = row_limit if row_limit is not None else 2**31 - 1

    # tenancy-ok: a projection is deliberately global across organisations; every
    # row carries its own source organization_id into the target.
    plan_row = connection.execute(
        text(
            f"""
            WITH {_SOURCE_CTE}
            SELECT
              count(*) AS candidates,
              count(*) FILTER (WHERE uc.id IS NULL) AS to_insert,
              count(*) FILTER (
                WHERE uc.id IS NOT NULL
                  AND uc.source_checksum IS DISTINCT FROM checksum.value
              ) AS to_update,
              count(*) FILTER (
                WHERE uc.id IS NOT NULL
                  AND uc.source_checksum IS NOT DISTINCT FROM checksum.value
              ) AS unchanged,
              count(*) FILTER (WHERE bc.code IS NULL) AS code_fallbacks,
              count(*) FILTER (WHERE bc.level > 3) AS levels_clamped,
              count(*) FILTER (WHERE bc.business_domain IS NOT NULL) AS domains_dropped
            FROM source AS bc
            CROSS JOIN LATERAL (SELECT {_CHECKSUM_SQL} AS value) AS checksum
            LEFT JOIN unified_capabilities AS uc
                   ON uc.source_table = :source_table
                  AND uc.source_id = bc.id::text
            """  # nosec B608 -- both interpolated fragments are module literals
        ),
        {"source_table": SOURCE_TABLE, "row_limit": limit, "single_id": None, "excluded_ids": []},
    ).mappings().one()
    plan: dict[str, object] = {key: int(value) for key, value in plan_row.items()}

    blockers: list[dict[str, object]] = []

    # 1. A source row with no owner cannot be classified. TenantMixin declares
    #    organization_id NOT NULL, so this is a schema-drift alarm, not an expected
    #    case — and guessing an owner would publish one tenant's data to another.
    ownerless = int(
        connection.execute(
            text("SELECT count(*) FROM business_capability WHERE organization_id IS NULL")
        ).scalar_one()
    )
    if ownerless:
        blockers.append({"kind": "ownerless_source_rows", "count": ownerless, "blocking": True})

    # 2. A projected row whose owner no longer matches its source's owner. The
    #    refresh deliberately never updates organization_id or code (those are what
    #    the four partial unique indexes police, `unified_capability.py:310-340`), so
    #    a moved row must be resolved by a human, not silently left stale.
    # tenancy-ok: cross-organisation reconciliation is the point of the check.
    moved_result = connection.execute(
        text(
            """
            SELECT bc.id AS source_id
            FROM unified_capabilities AS uc
            JOIN business_capability AS bc ON bc.id::text = uc.source_id
            WHERE uc.source_table = :source_table
              AND (uc.organization_id IS DISTINCT FROM bc.organization_id
                   OR uc.code IS DISTINCT FROM COALESCE(bc.code, 'BC-' || bc.id))
            ORDER BY bc.id
            LIMIT 5000
            """
        ),
        {"source_table": SOURCE_TABLE},
    ).fetchall()
    moved = len(moved_result)
    moved_ids = [row[0] for row in moved_result]
    if moved:
        blockers.append({
            "kind": "owner_or_code_changed_since_projection", 
            "count": moved, 
            "blocking": False,
            "source_ids": moved_ids
        })

    # 3. A code already taken inside the same tenant by a row that is NOT this
    #    source row's projection: the insert would violate
    #    uq_unified_capabilities_tenant_code. Report it rather than let the
    #    transaction die halfway through.
    # tenancy-ok: the predicate is the organisation join itself.
    collisions = connection.execute(
        text(
            f"""
            WITH {_SOURCE_CTE}
            SELECT bc.id AS source_id,
                   COALESCE(bc.code, 'BC-' || bc.id) AS code,
                   bc.organization_id,
                   existing.id AS unified_id
            FROM source AS bc
            JOIN unified_capabilities AS existing
              ON existing.organization_id = bc.organization_id
             AND existing.code = COALESCE(bc.code, 'BC-' || bc.id)
            WHERE existing.source_table IS DISTINCT FROM :source_table
               OR existing.source_id IS DISTINCT FROM bc.id::text
            ORDER BY bc.id
            LIMIT 200
            """  # nosec B608 -- the interpolated fragment is a module literal
        ),
        {"source_table": SOURCE_TABLE, "row_limit": limit, "single_id": None, "excluded_ids": []},
    ).mappings().all()
    if collisions:
        blockers.append(
            {"kind": "tenant_code_collision", "count": len(collisions),
             "examples": [dict(row) for row in collisions[:20]], "blocking": True}
        )

    # 4. archimate_id is globally unique on the source (`business_capabilities.py:91`)
    #    so it cannot self-collide, but a seeded reference or UI-created row may
    #    already hold it.
    archimate_collisions = int(
        connection.execute(
            text(
                f"""
                WITH {_SOURCE_CTE}
                SELECT count(*)
                FROM source AS bc
                JOIN unified_capabilities AS existing
                  ON existing.archimate_id = bc.archimate_id
                WHERE bc.archimate_id IS NOT NULL
                  AND (existing.source_table IS DISTINCT FROM :source_table
                       OR existing.source_id IS DISTINCT FROM bc.id::text)
                """  # nosec B608 -- the interpolated fragment is a module literal
            ),
            {"source_table": SOURCE_TABLE, "row_limit": limit, "single_id": None, "excluded_ids": []},
        ).scalar_one()
    )
    if archimate_collisions:
        blockers.append({"kind": "archimate_id_collision", "count": archimate_collisions, "blocking": True})

    # 5. A cycle in the source hierarchy. `get_full_hierarchy_path`
    #    (`unified_capability.py:410-418`) walks parents in an unbounded while loop
    #    with no depth guard, so a projected cycle is an infinite loop in a request
    #    handler. Nothing on the self-FK (`business_capabilities.py:54-56`) prevents
    #    one, so it is detected here rather than discovered in production.
    #    tenancy-ok: deliberately global, and weaker if scoped. The projection
    #    covers every organisation in one pass, so a cycle in ANY tenant's
    #    hierarchy blocks it; and because parent_capability_id has no same-org
    #    constraint, a parent link that crosses organisations is itself a
    #    corruption this walk is meant to catch. A per-tenant predicate would
    #    hide exactly that case. Read-only: it counts, it never writes.
    cycles = int(
        connection.execute(
            text(
                """
                WITH RECURSIVE walk(root_id, node_id, depth, looped) AS (
                    SELECT id, parent_capability_id, 1, false
                      FROM business_capability
                     WHERE parent_capability_id IS NOT NULL
                    UNION ALL
                    SELECT walk.root_id, bc.parent_capability_id, walk.depth + 1,
                           bc.parent_capability_id = walk.root_id
                      FROM walk
                      JOIN business_capability AS bc ON bc.id = walk.node_id
                     WHERE NOT walk.looped AND walk.depth < 64
                )
                SELECT count(DISTINCT root_id) FROM walk WHERE looped
                """
            )
        ).scalar_one()
    )
    if cycles:
        blockers.append({"kind": "source_hierarchy_cycle", "count": cycles, "blocking": True})

    plan["blockers"] = blockers
    return plan


# Pass 1 — insert or refresh. `code`, `organization_id` and `archimate_id` are
# deliberately NOT in the DO UPDATE list: they are what the four partial unique
# indexes police, and mutating them on a re-run turns an idempotent refresh into a
# constraint-violation lottery. A change to any of them surfaces as blocker 2 above.
# The `IS DISTINCT FROM` guard makes an unchanged re-run write zero rows, so the
# reported update count is a real drift measurement rather than noise.
# tenancy-ok: deliberately global across organisations; organization_id is carried
# from each source row rather than filtered on.
_PROJECT_SQL = f"""
WITH {_SOURCE_CTE}
INSERT INTO unified_capabilities (
    name, description, code, level, scope, organization_id,
    source_table, source_id, source_org_id, source_checksum,
    specialization_type, category, current_maturity_level, target_maturity_level,
    maturity_gap, maturity_assessment_date, maturity_assessment_notes,
    strategic_importance, business_value, business_owner, it_owner,
    performance_score, kpis, archimate_id, archimate_element_id,
    discovered_by_ai, discovery_confidence, discovery_source, status,
    created_at, updated_at
)
SELECT
    bc.name,
    bc.description,
    -- bc.code is nullable but drives two partial unique indexes; a deterministic
    -- fallback is required or NULL codes neither collide nor deduplicate.
    COALESCE(bc.code, 'BC-' || bc.id),
    -- Source allows 1-5 (`business_capabilities.py:48`), target documents L1-L3.
    -- Lossy, and counted as `levels_clamped` in the plan.
    LEAST(GREATEST(bc.level, 1), 3),
    'tenant',
    bc.organization_id,
    :source_table,
    bc.id::text,
    bc.organization_id,
    {_CHECKSUM_SQL},
    'BUSINESS',
    bc.category,
    -- Explicit values, including NULL: the target column defaults are 1 and 3
    -- (`unified_capability.py:193-194`), and an unassessed capability must stay
    -- NULL -> "—" rather than acquire a plausible maturity nobody measured.
    bc.current_maturity_level,
    bc.target_maturity_level,
    bc.maturity_gap,
    bc.maturity_assessment_date,
    bc.maturity_assessment_notes,
    bc.strategic_importance,
    -- Source is Integer 1-10, target is Text. '8/10' keeps the scale visible; a
    -- bare '8' in a prose field reads as an unrelated statement.
    CASE WHEN bc.business_value IS NULL THEN NULL
         ELSE bc.business_value::text || '/10' END,
    bc.business_owner,
    bc.it_owner,
    bc.performance_score,
    bc.kpis,
    bc.archimate_id,
    bc.archimate_element_id,
    bc.discovered_by_ai,
    bc.discovery_confidence,
    'projection:business_capability',
    CASE WHEN bc.is_deprecated THEN 'retiring' ELSE 'defined' END,
    bc.created_at,
    now()
FROM source AS bc
ON CONFLICT (source_table, source_id)
  WHERE source_table IS NOT NULL AND source_id IS NOT NULL
DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    level = EXCLUDED.level,
    category = EXCLUDED.category,
    current_maturity_level = EXCLUDED.current_maturity_level,
    target_maturity_level = EXCLUDED.target_maturity_level,
    maturity_gap = EXCLUDED.maturity_gap,
    maturity_assessment_date = EXCLUDED.maturity_assessment_date,
    maturity_assessment_notes = EXCLUDED.maturity_assessment_notes,
    strategic_importance = EXCLUDED.strategic_importance,
    business_value = EXCLUDED.business_value,
    business_owner = EXCLUDED.business_owner,
    it_owner = EXCLUDED.it_owner,
    performance_score = EXCLUDED.performance_score,
    kpis = EXCLUDED.kpis,
    status = EXCLUDED.status,
    source_checksum = EXCLUDED.source_checksum,
    -- 2 Sep 2026: archimate_element_id was copied on INSERT only. A capability
    -- projected before its source BusinessCapability got its ArchiMate element
    -- (e.g. the truncation-bug backfill that ran this session) stayed permanently
    -- NULL here even after every later re-run of this projection, because this
    -- column was absent from the UPDATE list — the checksum comparison below
    -- doesn't cover it, but once matched it must still be applied on every write.
    archimate_element_id = EXCLUDED.archimate_element_id,
    updated_at = now()
WHERE unified_capabilities.source_checksum IS DISTINCT FROM EXCLUDED.source_checksum
   OR unified_capabilities.archimate_element_id IS DISTINCT FROM EXCLUDED.archimate_element_id
"""  # nosec B608 -- both interpolated fragments are module literals

# Pass 2 — hierarchy. bc.parent_capability_id is a business_capability id and must
# be translated through provenance, which means it can only run after pass 1.
# tenancy-ok: parent and child are joined through provenance, which carries the
# organisation; the projection is deliberately global.
_PARENT_SQL = """
UPDATE unified_capabilities AS child
   SET parent_capability_id = parent.id
  FROM business_capability AS bc
  JOIN unified_capabilities AS parent
    ON parent.source_table = :source_table
   AND parent.source_id = bc.parent_capability_id::text
 WHERE child.source_table = :source_table
   AND child.source_id = bc.id::text
   AND bc.parent_capability_id IS NOT NULL
   AND child.parent_capability_id IS DISTINCT FROM parent.id
   AND bc.id != ALL(CAST(:excluded_ids AS INTEGER[]))
"""

# Pass 3 — back-link. `deprecated_in_favor_of_id` is the only column on
# BusinessCapability that FKs into unified_capabilities
# (`business_capabilities.py:100-102`); `canonical_capability_id` points at
# `capabilities.id` (`:87-90`) and is deliberately left alone.
# `is_deprecated` / `deprecated_as_of` are NOT set: projecting a row does not
# retire it, and conflating the two would make the work hard to reverse.
# tenancy-ok: deliberately global; the join key is provenance.
_BACKLINK_SQL = """
UPDATE business_capability AS bc
   SET deprecated_in_favor_of_id = uc.id
  FROM unified_capabilities AS uc
 WHERE uc.source_table = :source_table
   AND uc.source_id = bc.id::text
   AND bc.deprecated_in_favor_of_id IS DISTINCT FROM uc.id
   AND bc.id != ALL(CAST(:excluded_ids AS INTEGER[]))
"""


def _verify(connection, row_limit: int | None, excluded_ids: list[int] | None = None) -> dict[str, object]:
    """Post-write assertions. Every one must hold, or the transaction is rolled back."""

    excluded_ids = excluded_ids or []
    unprojected = int(
        connection.execute(
            text(
                f"""
                WITH {_SOURCE_CTE}
                SELECT count(*) FROM source AS bc
                 WHERE NOT EXISTS (
                     SELECT 1 FROM unified_capabilities AS uc
                      WHERE uc.source_table = :source_table
                        AND uc.source_id = bc.id::text)
                """  # nosec B608 -- the interpolated fragment is a module literal
            ),
            {"source_table": SOURCE_TABLE,
             "row_limit": row_limit if row_limit is not None else 2**31 - 1,
             "single_id": None,
             "excluded_ids": excluded_ids},
        ).scalar_one()
    )
    malformed = int(
        connection.execute(
            text(
                """
                SELECT count(*) FROM unified_capabilities
                 WHERE source_table = :source_table
                   AND (scope <> 'tenant'
                        OR organization_id IS NULL
                        OR organization_id IS DISTINCT FROM source_org_id
                        OR source_checksum IS NULL)
                """
            ),
            {"source_table": SOURCE_TABLE},
        ).scalar_one()
    )
    self_parented = int(
        connection.execute(
            text(
                "SELECT count(*) FROM unified_capabilities "
                "WHERE source_table = :source_table AND parent_capability_id = id"
            ),
            {"source_table": SOURCE_TABLE},
        ).scalar_one()
    )
    return {
        "unprojected_source_rows": unprojected,
        "malformed_projected_rows": malformed,
        "self_parented_rows": self_parented,
    }


def run_projection(
    connection,
    *,
    apply: bool,
    row_limit: int | None = None,
) -> dict[str, object]:
    """Measure, plan, and optionally project, all inside ``connection``'s transaction."""

    if row_limit is not None and row_limit < 1:
        raise ProjectionBlocked("--limit must be at least 1")

    if not _has_provenance_index(connection):
        raise ProjectionBlocked(
            f"the unique index {PROVENANCE_INDEX} on "
            "unified_capabilities (source_table, source_id) is absent or invalid; "
            f"run {PROVENANCE_MIGRATION} first. Without it the projection is not "
            "idempotent and a re-run silently double-inserts."
        )

    if not connection.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK_ID}
    ).scalar_one():
        raise ProjectionBlocked("another capability projection holds the advisory lock")
    # The cutover reads the scope and provenance this command writes, so the two
    # must never interleave even though they hold different locks of their own.
    if not connection.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
        {"lock_id": CUTOVER_ADVISORY_LOCK_ID},
    ).scalar_one():
        raise ProjectionBlocked("a capability tenancy cutover is in progress")

    before = _counts(connection)
    plan = _plan(connection, row_limit)
    cutover_complete = _cutover_is_complete(connection)
    
    # Separate blocking and skippable blockers
    hard_blockers = [b for b in plan["blockers"] if b.get("blocking", True)]
    skippable = [b for b in plan["blockers"] if not b.get("blocking", True)]
    
    report: dict[str, object] = {
        "mode": "apply" if apply else "dry-run",
        "source_table": SOURCE_TABLE,
        "row_limit": row_limit,
        "cutover_complete": cutover_complete,
        "before": before,
        "after": dict(before),
        "plan": plan,
        "writes": {"inserted_or_updated": 0, "reparented": 0, "backlinked": 0},
        "verification": None,
        "skipped": skippable,
    }
    if not apply:
        return report

    if hard_blockers:
        kinds = ", ".join(str(item["kind"]) for item in hard_blockers)
        raise ProjectionBlocked(f"unresolved blockers ({kinds}); nothing was written")

    if not cutover_complete and not _classifier_accepts_projection():
        raise ProjectionBlocked(
            "the tenancy cutover has not run and classify_capability cannot classify "
            "a projected row: every projected row would be 'ambiguous' and "
            "run_cutover would raise CutoverBlocked for the whole database. Run "
            "`flask cutover-capability-tenancy --apply` first, or land the "
            "classify_capability provenance branch (see NOTES.md)."
        )

    _lock_cutover_tables(connection, _foreign_keys(connection))
    
    # Compute excluded IDs from skippable blockers
    excluded_ids: list[int] = sorted({sid for b in skippable for sid in b.get("source_ids", [])})
    
    parameters = {
        "source_table": SOURCE_TABLE,
        "single_id": None,
        "row_limit": row_limit if row_limit is not None else 2**31 - 1,
        "excluded_ids": excluded_ids
    }
    report["writes"] = {
        "inserted_or_updated": connection.execute(text(_PROJECT_SQL), parameters).rowcount,
        "reparented": connection.execute(
            text(_PARENT_SQL), {"source_table": SOURCE_TABLE, "excluded_ids": excluded_ids}
        ).rowcount,
        "backlinked": connection.execute(
            text(_BACKLINK_SQL), {"source_table": SOURCE_TABLE, "excluded_ids": excluded_ids}
        ).rowcount,
    }

    verification = _verify(connection, row_limit, excluded_ids=excluded_ids)
    report["verification"] = verification
    failed = {key: value for key, value in verification.items() if value}
    if failed:
        raise ProjectionBlocked(f"post-projection verification failed: {failed}")

    report["after"] = _counts(connection)
    return report


def execute_projection_with_audit(
    engine,
    *,
    report_path: str | Path | None,
    apply: bool,
    row_limit: int | None = None,
) -> dict[str, object]:
    """Run in one transaction; commit only on --apply, and only after verification."""

    connection = engine.connect()
    transaction = connection.begin()
    try:
        payload = run_projection(connection, apply=apply, row_limit=row_limit)
        if apply:
            transaction.commit()
        else:
            # A dry-run must leave no trace, including the advisory locks.
            transaction.rollback()
    except BaseException:
        if transaction.is_active:
            transaction.rollback()
        raise
    finally:
        connection.close()

    payload["database_commit_confirmed"] = bool(apply)
    if report_path is not None:
        _write_report_atomic(report_path, payload)
        payload["report_path"] = str(report_path)
    return payload


@click.command("project-capabilities")
@click.option("--dry-run", is_flag=True, help="Measure and plan without writing.")
@click.option("--apply", "apply_changes", is_flag=True, help="Apply the projection.")
@click.option(
    "--limit",
    "row_limit",
    type=click.IntRange(min=1),
    help="Project only the first N source rows by id, for a cautious first run.",
)
@click.option(
    "--report",
    type=click.Path(path_type=Path, dir_okay=False),
    help="JSON report destination.",
)
@with_appcontext
def project_capabilities(dry_run, apply_changes, row_limit, report):
    """Project business_capability rows into the canonical unified_capabilities store."""

    # Both flags explicit and exactly one required, matching
    # `cutover_capability_tenancy.py:982-983` rather than inventing a second
    # convention for the same kind of command.
    if dry_run == apply_changes:
        raise click.UsageError("choose exactly one of --dry-run or --apply")
    try:
        payload = execute_projection_with_audit(
            db.engine, report_path=report, apply=apply_changes, row_limit=row_limit
        )
    except ProjectionBlocked as exc:
        raise click.ClickException(str(exc)) from exc

    before, after, plan = payload["before"], payload["after"], payload["plan"]
    click.echo(
        f"{payload['mode']}: business_capability={before['source_rows']} rows across "
        f"{before['source_organizations']} organisations"
    )
    click.echo(
        "unified_capabilities: "
        f"{before['unified_rows']} -> {after['unified_rows']} total, "
        f"{before['projected_rows']} -> {after['projected_rows']} projected"
    )
    click.echo(
        f"plan: {plan['to_insert']} to insert, {plan['to_update']} changed, "
        f"{plan['unchanged']} unchanged, {plan['code_fallbacks']} code fallbacks, "
        f"{plan['levels_clamped']} levels clamped, "
        f"{plan['domains_dropped']} business_domain values not carried"
    )
    if plan["blockers"]:
        click.echo(f"blockers: {plan['blockers']}")
    if payload.get("skipped"):
        click.echo(f"skipped (needs manual resolution): {payload['skipped']}")
    writes = payload["writes"]
    click.echo(
        f"writes: {writes['inserted_or_updated']} projected, "
        f"{writes['reparented']} reparented, {writes['backlinked']} back-linked"
    )
    if report:
        click.echo(f"report: {report}")


def init_app(app):
    app.cli.add_command(project_capabilities)
