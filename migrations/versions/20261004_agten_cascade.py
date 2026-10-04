"""Add ON DELETE CASCADE to agent_charters and agent_run_records FKs.

TenantMixin declares organization_id with ondelete="CASCADE". The existing
FKs were created without it. This migration drops and recreates them with
CASCADE, idempotently.

Revision ID: 20261004_agten_cascade
Revises: 20261003_fw_adopt
Create Date: 2026-10-04
"""
from alembic import op
from sqlalchemy import text

revision = "20261004_agten_cascade"
down_revision = "20261002_event_log"
branch_labels = None
depends_on = None


def _fk_has_cascade(bind, table_name, fk_name):
    """Return True if the FK constraint already has ON DELETE CASCADE."""
    row = bind.execute(text(
        f"SELECT confdeltype FROM pg_constraint "
        f"WHERE conname = :fk_name "
        f"AND conrelid = '{table_name}'::regclass "
        f"AND contype = 'f'"
    ), {"fk_name": fk_name}).fetchone()
    if row is None:
        return None  # constraint does not exist
    # confdeltype: 'a' = NO ACTION, 'r' = RESTRICT, 'c' = CASCADE,
    #              'n' = SET NULL, 'd' = SET DEFAULT
    return row[0] == 'c'


def _replace_fk_with_cascade(bind, table_name, fk_name, column, ref_table, ref_column):
    """Drop and recreate a FK constraint with ON DELETE CASCADE, if needed."""
    cascade = _fk_has_cascade(bind, table_name, fk_name)
    if cascade:
        return  # already has CASCADE
    if cascade is False:
        # Exists but without CASCADE — drop and recreate
        bind.execute(text(
            f"ALTER TABLE {table_name} DROP CONSTRAINT {fk_name}"
        ))
    # Create (or recreate) with CASCADE
    bind.execute(text(
        f"ALTER TABLE {table_name} ADD CONSTRAINT {fk_name} "
        f"FOREIGN KEY ({column}) REFERENCES {ref_table}({ref_column}) "
        f"ON DELETE CASCADE"
    ))


def upgrade():
    bind = op.get_bind()
    _replace_fk_with_cascade(
        bind, "agent_charters",
        "agent_charters_organization_id_fkey",
        "organization_id", "organizations", "id",
    )
    _replace_fk_with_cascade(
        bind, "agent_run_records",
        "agent_run_records_organization_id_fkey",
        "organization_id", "organizations", "id",
    )


def downgrade():
    bind = op.get_bind()
    for table, fk_name, column in [
        ("agent_charters", "agent_charters_organization_id_fkey", "organization_id"),
        ("agent_run_records", "agent_run_records_organization_id_fkey", "organization_id"),
    ]:
        cascade = _fk_has_cascade(bind, table, fk_name)
        if cascade is None:
            continue  # constraint does not exist, nothing to revert
        # Drop CASCADE version and recreate without CASCADE
        bind.execute(text(
            f"ALTER TABLE {table} DROP CONSTRAINT {fk_name}"
        ))
        bind.execute(text(
            f"ALTER TABLE {table} ADD CONSTRAINT {fk_name} "
            f"FOREIGN KEY ({column}) REFERENCES organizations(id)"
        ))