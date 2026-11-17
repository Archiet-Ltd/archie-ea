-- Make (source_table, source_id) a unique natural key on unified_capabilities.
--
-- Measured in production, 31 Aug 2026:
--   business_capability                                            461 rows
--   unified_capabilities                                             0 rows
--   unified_capabilities WHERE source_table = 'business_capability'   0 rows
--
-- `flask project-capabilities` projects each business_capability row into the
-- canonical store keyed on its provenance. Idempotency is delegated to
-- PostgreSQL: the projection is a single INSERT ... ON CONFLICT
-- (source_table, source_id) DO UPDATE. That arbiter needs a unique index, and
-- none exists: app/models/unified_capability.py:310-340 declares four partial
-- unique indexes (reference/tenant x code/archimate_id) and none of them
-- mentions provenance, and install_cutover_constraints
-- (app/commands/cutover_capability_tenancy.py:507-680) creates the same four
-- plus FKs, a CHECK and a trigger — again nothing on provenance.
--
-- This cannot be done by `flask reconcile-schema`: that command emits only
-- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, all nullable, and never adds an
-- index, constraint, trigger or foreign key. Hence a standalone migration.
--
-- Safe because:
--   * It adds an index. This file alone reads, updates and deletes no row; the
--     duplicate clean-up described below runs only from the command or the
--     Alembic revision.
--   * The predicate excludes rows with NULL provenance, so every capability
--     created by the seven existing UnifiedCapability writers (seeders,
--     importers, UI creates) is outside the index entirely and cannot collide.
--   * It is a no-op on re-run (IF NOT EXISTS), so it is safe on a database
--     where it has already been applied.
--   * If two rows already share a (source_table, source_id) pair the CREATE
--     would fail. Applied through the command or the Alembic revision, the
--     duplicates are cleared first: the oldest row keeps its source, every
--     later row loses its source values and is marked retired into the older
--     row (retired_into_id). No row is deleted, and the count is logged. Run
--     as this file alone, the CREATE still fails and the transaction rolls
--     back, leaving the schema untouched. The query under "Diagnosing a
--     failure" below lists the offenders.
--
-- NOT CONCURRENTLY, deliberately. CREATE INDEX CONCURRENTLY cannot run inside a
-- transaction block, so using it would mean shipping an unwrapped statement
-- that can leave an INVALID index behind on failure — an index PostgreSQL will
-- not accept as an ON CONFLICT arbiter, so the projection would then either
-- refuse (it checks indisvalid) or, worse on a less careful command, silently
-- double-insert. The tradeoff bought by wrapping in BEGIN/COMMIT is a brief
-- SHARE lock on unified_capabilities (writes wait, reads continue) while the
-- index builds. At the
-- measured size (0 rows now, 461 after the first projection) that build is
-- milliseconds, so the plain form is strictly better here. Revisit only if this
-- table ever reaches a size where an exclusive lock is a real outage.
--
-- Reversal, should this prove wrong:
--   DROP INDEX IF EXISTS uq_unified_capabilities_provenance;
--
-- Diagnosing a failure (run outside the transaction):
--   SELECT source_table, source_id, count(*), array_agg(id ORDER BY id)
--     FROM unified_capabilities
--    WHERE source_table IS NOT NULL AND source_id IS NOT NULL
--    GROUP BY source_table, source_id
--   HAVING count(*) > 1;

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_unified_capabilities_provenance
    ON unified_capabilities (source_table, source_id)
    WHERE source_table IS NOT NULL AND source_id IS NOT NULL;

COMMIT;

-- Verify (expect one row, indisvalid = t):
--   SELECT c.relname, i.indisunique, i.indisvalid
--     FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid
--    WHERE c.relname = 'uq_unified_capabilities_provenance';
