"""The unified-capability provenance natural key, on the chain.

``uq_unified_capabilities_provenance`` (unique on ``unified_capabilities
(source_table, source_id)``, partial: both columns NOT NULL) is the ON
CONFLICT arbiter ``app/commands/project_capabilities.py`` needs to project a
``business_capability`` row idempotently. Before this revision it existed
only via ``flask apply-unified-capability-provenance-migration`` running
``scripts/migrate_unified_capability_provenance.sql`` by hand on each
database -- neither a fresh ``db.create_all()`` nor the plain migration
chain carried it. The index is now also declared on ``UnifiedCapability.
__table_args__`` (``app/models/unified_capability.py``), so a build from an
empty database gets it directly from ``create_all()``/the baseline
revision's own ``create_all`` call; this revision is for every database
whose ``unified_capabilities`` table already existed before this change,
where ``create_all(checkfirst=True)`` skips a table it finds already
present and therefore never backfills a newly declared index onto it.

Same name, same columns, same partial predicate as the SQL file above.
``CREATE UNIQUE INDEX IF NOT EXISTS`` makes the upgrade a no-op on a
database that already carries the index (from the command, or from
``create_all`` on a fresh install). Refuses, changing nothing, if two rows
already share a ``(source_table, source_id)`` pair -- that means the store
was already double-projected and needs a human to reconcile it before the
key can be declared; the duplicate pairs are listed in the error exactly as
``scripts/migrate_unified_capability_provenance.sql``'s own diagnostic query
would report them. No row is read into a new shape, updated or deleted
either way.

Revision ID: 20261029_cap_prov_index
Revises: 20261029_llm_spend_ledger
Create Date: 2026-10-29
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20261029_cap_prov_index"
down_revision = "20261029_llm_spend_ledger"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_unified_capabilities_provenance"


def _duplicate_provenance_pairs(bind):
    return bind.execute(
        text(
            """
            SELECT source_table, source_id, count(*) AS n, array_agg(id ORDER BY id) AS ids
              FROM unified_capabilities
             WHERE source_table IS NOT NULL AND source_id IS NOT NULL
             GROUP BY source_table, source_id
            HAVING count(*) > 1
             ORDER BY source_table, source_id
            """
        )
    ).mappings().all()


def upgrade():
    bind = op.get_bind()
    if not bind.dialect.has_table(bind, "unified_capabilities"):
        # Nothing to index yet; the baseline revision (which runs before this
        # one on every chain) creates the table with this index already
        # declared on the model, so this is only reachable on a chain that
        # somehow skipped the baseline -- a no-op either way, never a failure.
        return

    duplicates = _duplicate_provenance_pairs(bind)
    if duplicates:
        pairs = "; ".join(
            f"{row['source_table']!r}/{row['source_id']!r} -> ids {row['ids']} ({row['n']} rows)"
            for row in duplicates
        )
        raise RuntimeError(
            f"{revision}: cannot create "
            f"{INDEX_NAME}, duplicate (source_table, source_id) pairs already "
            f"exist and must be reconciled by hand first (no row was changed): {pairs}"
        )

    bind.execute(
        text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
                ON unified_capabilities (source_table, source_id)
                WHERE source_table IS NOT NULL AND source_id IS NOT NULL
            """
        )
    )


def downgrade():
    bind = op.get_bind()
    bind.execute(text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
