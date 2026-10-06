"""Repoint business_function.capability_id's FK from business_capability.id
to unified_capabilities.id.

ensure_function() in
app/modules/applications/services/application_capability_catalog.py builds
every BusinessFunction it creates from a UnifiedCapability instance (via
walk() -> get_or_create_capability() -> UnifiedCapability), never a
BusinessCapability, and it is the ONLY code path that ever writes
business_function.capability_id. unified_capabilities is this codebase's
single source of truth for capability modeling; a BusinessFunction must
point at a row there. The old FK target let that write succeed only when a
UnifiedCapability.id happened to collide with a business_capability.id --
the two tables have independent id sequences, so this was not a reliable
coincidence, and the real shape of the bug was a ForeignKeyViolation the
first time it did not.

The important consequence: every existing business_function row's
capability_id is ALREADY a unified_capabilities.id. It only ever passed the
OLD FK by coincidence, because a Postgres FK check merely confirms the
integer value exists as some row's primary key in the referenced table --
it does not care which table conceptually "owns" that id space. There is
therefore no legacy-id-to-unified-id translation to perform here, and an
earlier version of this migration that attempted one via the
(source_table='business_capability', source_id) provenance lookup was
itself a data-corruption bug: it treated each row's already-correct
unified_capabilities.id as if it were a legacy business_capability.id
needing translation, and if any unified_capabilities row happened to have
a source_id matching that same numeric string -- plausible, since legacy
business_capability ids and unified_capabilities rows projected from
business_capability can easily have overlapping-looking id values -- the
UPDATE silently rewrote the row to point at a different, unrelated
capability. Its "unresolved rows" safety check ran AFTER that UPDATE had
already mutated the data, so it was validating the new, already-corrupted
values rather than the ones it was meant to guard.

This revision therefore does no UPDATE at all. It only validates that the
assumption above actually holds -- every business_function.capability_id is
already a valid unified_capabilities.id -- and repoints the FK. The
validation exists because a different, as-yet-undiscovered write path could
in principle have put a bad value in some row; it is not assumed away, it
is checked, and the migration fails loudly (a RuntimeError naming the
offending ids) rather than guessing or rewriting anything, matching this
file's own "don't guess, surface the gap" convention elsewhere
(app/models/business_capabilities.py's _project_capability_row,
app/commands/backfill_decision_register_consolidation.py's decision_ledger
orphan handling).

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

    # (a) Drop the old FK so the validation query below is never blocked by
    # it (and so the ADD CONSTRAINT in step (c) has a clean slate).
    if current_target is not None:
        bind.execute(text(
            f"ALTER TABLE business_function DROP CONSTRAINT {_FK_NAME}"
        ))

    # (b) Validate the assumption this migration relies on: every existing
    # capability_id is already a valid unified_capabilities.id (see the
    # module docstring for why that is expected to always be true). No
    # rewrite happens here -- only a check. Anything that fails this check
    # fails the migration loudly, naming the offending rows, rather than
    # guessing or silently leaving a dangling value in place.
    unresolved = bind.execute(text(
        """
        SELECT bf.id, bf.capability_id
          FROM business_function AS bf
         WHERE NOT EXISTS (
               SELECT 1 FROM unified_capabilities AS uc
                WHERE uc.id = bf.capability_id
         )
        """
    )).fetchall()
    if unresolved:
        ids = ", ".join(
            f"business_function.id={row[0]} (capability_id={row[1]})"
            for row in unresolved
        )
        raise RuntimeError(
            f"{len(unresolved)} business_function row(s) have a capability_id "
            f"that is not a valid unified_capabilities.id: {ids}. "
            "Re-project the missing capability row(s) with `flask --app manage "
            "project-capabilities --apply`, or correct these rows' "
            "capability_id by hand, then re-run this migration."
        )

    # (c) Repoint the FK now that every row's value is confirmed to already
    # live in the unified_capabilities id space.
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

    # No data rewrite happened in upgrade() (see the module docstring), so
    # there is nothing to reverse here: this just re-adds the old
    # constraint. That re-add is NOT guaranteed to succeed, and that is the
    # correct, honest outcome rather than a bug to paper over. upgrade()
    # never changed any capability_id value, so every row that already
    # existed before this revision's upgrade() ran still has the same
    # business_capability.id-shaped value it always had, and this ADD
    # CONSTRAINT will hold for it exactly as it did before. But any
    # business_function row created AFTER upgrade() ran -- the normal,
    # expected state once this fix is live -- legitimately carries a
    # unified_capabilities.id that was never also a business_capability.id
    # (that was always the correct value; the old FK merely forbade it by
    # accident of overlapping id ranges). This downgrade has no way to
    # invent a legacy id for such a row, because no legacy id for it has
    # ever existed -- there is nothing to un-corrupt, since nothing was
    # corrupted. The ADD CONSTRAINT below will then fail with a
    # ForeignKeyViolation naming that row, which is the correct, honest
    # outcome: this downgrade can only be relied on before any new-shaped
    # row has been written.
    bind.execute(text(
        f"ALTER TABLE business_function ADD CONSTRAINT {_FK_NAME} "
        "FOREIGN KEY (capability_id) REFERENCES business_capability(id)"
    ))
