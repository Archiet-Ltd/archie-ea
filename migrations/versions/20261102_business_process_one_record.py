"""One process record for each process element in an organisation.

Adds a unique constraint on ``business_processes`` (organization_id,
archimate_element_id), so a double submit or two requests at once cannot leave
two records for one element. Rows with no element are not constrained.

Before the constraint, any element that already has several records keeps the
oldest one (lowest id): everything pointing at a newer duplicate is repointed
at the kept record, a drawing is carried over when the kept record has none,
and a reference that would repeat one the kept record already has is dropped
with the duplicate. Then the duplicates go. Imports nothing from ``app``.

Idempotent: the constraint is added or dropped only when its presence says so.

Revision ID: 20261102_bp_one_record
Revises: 20261025_bpmn_flow
Create Date: 2026-11-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261102_bp_one_record"
down_revision = "20261025_bpmn_flow"
branch_labels = None
depends_on = None

NAME = "uq_business_processes_org_element"


def _has_constraint(bind) -> bool:
    return any(c["name"] == NAME for c in inspect(bind).get_unique_constraints("business_processes"))


def _references(bind) -> list:
    """Every (table, column) holding a foreign key to business_processes.id."""
    rows = bind.execute(text("""
        SELECT cl.relname, att.attname
        FROM pg_constraint con
        JOIN pg_class cl ON cl.oid = con.conrelid
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
        WHERE con.contype = 'f' AND con.confrelid = 'business_processes'::regclass
          AND array_length(con.conkey, 1) = 1
    """)).fetchall()
    return [(t, c) for t, c in rows]


def _merge_duplicates(bind) -> None:
    groups = bind.execute(text("""
        SELECT organization_id, archimate_element_id, array_agg(id ORDER BY id)
        FROM business_processes
        WHERE archimate_element_id IS NOT NULL
        GROUP BY organization_id, archimate_element_id HAVING count(*) > 1
    """)).fetchall()
    if not groups:
        return
    references = _references(bind)
    for _org, _element, ids in groups:
        keep, extras = ids[0], ids[1:]
        for extra in extras:
            bind.execute(text(
                "UPDATE business_processes k SET bpmn_flow = d.bpmn_flow "
                "FROM business_processes d WHERE k.id = :keep AND d.id = :extra "
                "AND k.bpmn_flow IS NULL AND d.bpmn_flow IS NOT NULL"), {"keep": keep, "extra": extra})
            for table, column in references:
                ref_ids = bind.execute(text(
                    'SELECT ctid FROM "%s" WHERE "%s" = :extra' % (table, column)), {"extra": extra}).fetchall()
                for (ctid,) in ref_ids:
                    savepoint = bind.begin_nested()
                    try:
                        bind.execute(text(
                            'UPDATE "%s" SET "%s" = :keep WHERE ctid = CAST(:ctid AS tid)' % (table, column)),
                            {"keep": keep, "ctid": ctid})
                        savepoint.commit()
                    except sa.exc.IntegrityError:
                        savepoint.rollback()
                        bind.execute(text('DELETE FROM "%s" WHERE ctid = CAST(:ctid AS tid)' % table), {"ctid": ctid})
            bind.execute(text("DELETE FROM business_processes WHERE id = :extra"), {"extra": extra})


def upgrade():
    bind = op.get_bind()
    if _has_constraint(bind):
        return
    _merge_duplicates(bind)
    op.create_unique_constraint(NAME, "business_processes", ["organization_id", "archimate_element_id"])


def downgrade():
    if _has_constraint(op.get_bind()):
        op.drop_constraint(NAME, "business_processes", type_="unique")
