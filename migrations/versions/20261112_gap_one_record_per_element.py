"""One gap record for each Gap element of an organisation.

Two first reads of the Gaps page could each write a record for the same Gap element. This merges any
organisation's duplicate records for one element into the oldest (everything that pointed at a duplicate
now points at the kept record), gives every Gap element that has no record its record (the register no
longer writes them when it is read), and adds the unique index that makes a second record impossible.

Revision ID: 20261112_gap_one_record
Revises: 20261110_founder_charter
Create Date: 2026-11-12
"""
import sqlalchemy as sa
from alembic import op

revision = "20261112_gap_one_record"
down_revision = "20261110_founder_charter"
branch_labels = None
depends_on = None

INDEX = "uq_gaps_org_element"


def _referencing_columns(bind):
    return bind.execute(sa.text(
        """
        SELECT cl.relname AS tbl, att.attname AS col
        FROM pg_constraint con
        JOIN pg_class cl ON cl.oid = con.conrelid
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
        WHERE con.contype = 'f' AND con.confrelid = 'gaps'::regclass AND array_length(con.conkey, 1) = 1
        """
    )).fetchall()


def _merge_duplicates(bind):
    pairs = bind.execute(sa.text(
        """
        SELECT g.id AS dup, k.keep
        FROM gaps g
        JOIN (SELECT organization_id, archimate_element_id, MIN(id) AS keep
              FROM gaps WHERE archimate_element_id IS NOT NULL AND organization_id IS NOT NULL
              GROUP BY organization_id, archimate_element_id HAVING COUNT(*) > 1) k
          ON k.organization_id = g.organization_id AND k.archimate_element_id = g.archimate_element_id
        WHERE g.id <> k.keep
        """
    )).fetchall()
    if not pairs:
        return
    refs = [(r.tbl, r.col) for r in _referencing_columns(bind)]
    for dup, keep in pairs:
        for tbl, col in refs:
            if tbl == "gaps" and col == "archimate_element_id":
                continue
            # a row the kept record already has (the same link) is dropped, any other moves across
            try:
                with bind.begin_nested():
                    bind.execute(sa.text(
                        f'UPDATE "{tbl}" SET "{col}" = :keep WHERE "{col}" = :dup'
                    ), {"keep": keep, "dup": dup})
            except sa.exc.IntegrityError:
                with bind.begin_nested():
                    bind.execute(sa.text(f'DELETE FROM "{tbl}" WHERE "{col}" = :dup'), {"dup": dup})
        bind.execute(sa.text("DELETE FROM gaps WHERE id = :dup"), {"dup": dup})


def _adopt_gap_elements(bind):
    bind.execute(sa.text(
        """
        INSERT INTO gaps (organization_id, name, description, archimate_element_id, resolution_status,
                          gap_kind, context, auto_generated, created_at, updated_at)
        SELECT e.organization_id, LEFT(COALESCE(NULLIF(e.name, ''), 'Untitled gap'), 255),
               COALESCE(e.description, ''), e.id, 'identified',
               'capability_shortfall', 'architecture', FALSE, now(), now()
        FROM archimate_elements e
        WHERE lower(e.type) = 'gap' AND e.superseded_at IS NULL AND e.organization_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM gaps g
                          WHERE g.archimate_element_id = e.id AND g.organization_id = e.organization_id)
        """
    ))


def upgrade():
    bind = op.get_bind()
    _merge_duplicates(bind)
    _adopt_gap_elements(bind)
    op.create_index(
        INDEX, "gaps", ["organization_id", "archimate_element_id"], unique=True,
        postgresql_where=sa.text("archimate_element_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index(INDEX, table_name="gaps")
