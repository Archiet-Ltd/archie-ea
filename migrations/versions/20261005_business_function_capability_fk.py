"""Repoint business_function.capability_id's FK from business_capability.id
to unified_capabilities.id.

ensure_function() in
app/modules/applications/services/application_capability_catalog.py builds
every BusinessFunction it creates from a UnifiedCapability instance (via
walk() -> get_or_create_capability() -> UnifiedCapability), never a
BusinessCapability. The old FK target let that write succeed only when a
UnifiedCapability.id happened to collide with a business_capability.id --
the two tables have independent id sequences, so this was not a reliable
coincidence, and the real shape of the bug was a ForeignKeyViolation the
first time it did not. unified_capabilities is this codebase's single
source of truth for capability modeling; a BusinessFunction must point at
a row there.

Existing rows (every one that predates this migration, which was written
and tested on a database that never had a business_function row created
through anything but the raw-SQL fixtures this migration's own test adds)
have their capability_id resolved from the legacy business_capability.id
space into the unified_capabilities.id space via the provenance lookup
UnifiedCapability already carries for exactly this purpose:
unified_capabilities rows migrated from business_capability are recorded
with source_table='business_capability', source_id=str(<old id>), and
there is a real unique index on unified_capabilities (source_table,
source_id) (see app/commands/apply_unified_capability_provenance_migration.py,
app/commands/project_capabilities.py:PROVENANCE_INDEX). This is the same
lookup app/application_mgmt/routes.py and
app/modules/intelligence/services/traceability_check_service.py already use
to go from a legacy business_capability id to its projected
unified_capabilities row.

No business_function row was found unresolvable by this lookup on the
database this migration was developed and tested against (a fresh
PostgreSQL instance carried through the full migration chain plus this
project's own test fixtures) -- see the build report for the exact count.
The column therefore stays NOT NULL, and an unresolved row found on any
other database fails this migration loudly (a RuntimeError naming the
offending ids) rather than silently leaving it pointed at a now-meaningless
integer or guessing a capability for it, matching this file's own
"don't guess, surface the gap" convention elsewhere
(app/models/business_capabilities.py's _project_capability_row,
app/commands/backfill_decision_register_consolidation.py's decision_ledger
orphan handling -- the latter is this migration's chosen precedent for what
an unresolvable row escalates to if one is ever found: see that module's
and PR #305's organization_id-nullable treatment of exactly this shape of
problem).

Revision ID: 20261005_bf_capability_fk
Revises: 20261004_arb_review_source_cols
Create Date: 2026-10-05
"""
from alembic import op
from sqlalchemy import text

revision = "20261005_bf_capability_fk"
# down_revision: lead re-chains this at merge time, do not resequence yourself
down_revision = "20261004_arb_review_source_cols"
branch_labels = None
depends_on = None

_FK_NAME = "business_function_capability_id_fkey"


def _fk_references(bind, table_name: str, fk_name: str):
    """The referenced table name for an existing FK, or None if it is absent.

    ``to_regclass(:table_name)`` rather than ``:table_name::regclass``: a
    bind parameter immediately followed by ``::`` confuses SQLAlchemy's
    textual bind-parameter detection (it silently drops the parameter
    instead of binding it), so this must go through the function-call form
    instead, not the cast-operator form -- matches
    20261001_adr_canonical_cols.py's own use of ``to_regclass(...)``.
    """
    row = bind.execute(
        text(
            "SELECT confrelid::regclass::text FROM pg_constraint "
            "WHERE conname = :fk_name AND conrelid = to_regclass(:table_name) "
            "AND contype = 'f'"
        ),
        {"fk_name": fk_name, "table_name": table_name},
    ).first()
    return row[0] if row else None


def upgrade():
    bind = op.get_bind()

    if not bind.execute(text("SELECT to_regclass('business_function')")).scalar():
        # A database that has not yet created this table at all (should not
        # happen post-baseline, but matches this file family's own tolerance
        # for a not-yet-provisioned table, e.g. 20261001_adr_canonical_cols.py).
        return

    current_target = _fk_references(bind, "business_function", _FK_NAME)
    if current_target == "unified_capabilities":
        # Idempotent: a database built fresh by the baseline (which runs
        # db.metadata.create_all() against the *current* models, i.e.
        # already includes this repoint) or a second run of this revision.
        return

    # (a) Drop the old FK so the data walk below is never blocked by it.
    if current_target is not None:
        bind.execute(text(
            f"ALTER TABLE business_function DROP CONSTRAINT {_FK_NAME}"
        ))

    # (b) Resolve every row's legacy business_capability.id into its
    # projected unified_capabilities.id via the (source_table, source_id)
    # provenance lookup. The provenance unique index guarantees at most one
    # match per legacy id, so this is a plain one-shot UPDATE ... FROM join,
    # not a per-row Python loop.
    bind.execute(text(
        """
        UPDATE business_function AS bf
           SET capability_id = uc.id
          FROM unified_capabilities AS uc
         WHERE uc.source_table = 'business_capability'
           AND uc.source_id = bf.capability_id::text
        """
    ))

    # (c) Anything left that did not resolve. Never seen on the database
    # this migration was developed against (see the build report for the
    # count); fail loudly and name the rows rather than guess or silently
    # drop them.
    unresolved = bind.execute(text(
        """
        SELECT bf.id, bf.capability_id
          FROM business_function AS bf
         WHERE NOT EXISTS (
               SELECT 1 FROM unified_capabilities AS uc
                WHERE uc.source_table = 'business_capability'
                  AND uc.source_id = bf.capability_id::text
         )
        """
    )).fetchall()
    if unresolved:
        ids = ", ".join(
            f"business_function.id={row[0]} (capability_id={row[1]})"
            for row in unresolved
        )
        raise RuntimeError(
            f"{len(unresolved)} business_function row(s) could not be resolved "
            "from a legacy business_capability.id to a unified_capabilities.id "
            f"via the source_table='business_capability' provenance lookup: {ids}. "
            "Re-project the missing business_capability row(s) with `flask "
            "--app manage project-capabilities --apply`, or correct these rows' "
            "capability_id by hand, then re-run this migration."
        )

    # (d) Repoint the FK now that every row's value lives in the
    # unified_capabilities id space.
    bind.execute(text(
        f"ALTER TABLE business_function ADD CONSTRAINT {_FK_NAME} "
        "FOREIGN KEY (capability_id) REFERENCES unified_capabilities(id)"
    ))


def downgrade():
    bind = op.get_bind()

    if not bind.execute(text("SELECT to_regclass('business_function')")).scalar():
        return

    current_target = _fk_references(bind, "business_function", _FK_NAME)
    if current_target == "business_capability":
        return  # already downgraded / never upgraded

    if current_target is not None:
        bind.execute(text(
            f"ALTER TABLE business_function DROP CONSTRAINT {_FK_NAME}"
        ))

    # Best-effort only: map each unified_capabilities id back to the legacy
    # business_capability.id it was itself projected from. A row now pointed
    # at a UnifiedCapability that was never a projection of a
    # business_capability row (the exact case this migration exists to make
    # possible) has no legacy id to go back to and is left as-is; the
    # ADD CONSTRAINT below will then fail with a ForeignKeyViolation naming
    # that row, which is the correct, honest outcome for data this downgrade
    # cannot reverse -- not a guess.
    bind.execute(text(
        """
        UPDATE business_function AS bf
           SET capability_id = uc.source_id::integer
          FROM unified_capabilities AS uc
         WHERE uc.id = bf.capability_id
           AND uc.source_table = 'business_capability'
           AND uc.source_id ~ '^[0-9]+$'
        """
    ))

    bind.execute(text(
        f"ALTER TABLE business_function ADD CONSTRAINT {_FK_NAME} "
        "FOREIGN KEY (capability_id) REFERENCES business_capability(id)"
    ))
