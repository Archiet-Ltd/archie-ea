"""A work package's progress is empty until a person enters a figure.

The column used to default to 0, so 0 meant both "nobody entered it" and "someone
entered 0". Expand: drop the default and keep the column nullable. Backfill: every
stored 0 becomes NULL unless the audit log shows a person changed progress on that
package. The down step restores the default and writes 0 back into empty figures.

Revision ID: 20261114_progress_not_entered
Revises: 20261114_subscription_offer
Create Date: 2026-11-14
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261114_progress_not_entered"
down_revision = "20261114_subscription_offer"
branch_labels = None
depends_on = None

TABLE = "unified_work_packages"


def _present(bind, table, column=None):
    insp = inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column is None or column in {c["name"] for c in insp.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    if not _present(bind, TABLE, "progress_percentage"):
        return
    bind.execute(text(f"ALTER TABLE {TABLE} ALTER COLUMN progress_percentage DROP DEFAULT"))
    bind.execute(text(f"ALTER TABLE {TABLE} ALTER COLUMN progress_percentage DROP NOT NULL"))
    keep = ""
    if _present(bind, "soc2_audit_log"):
        keep = (
            " AND NOT EXISTS (SELECT 1 FROM soc2_audit_log a WHERE a.table_name = :t"
            " AND a.record_id = w.id AND a.new_value::text LIKE '%progress_percentage%')"
        )
    changed = bind.execute(
        text(f"UPDATE {TABLE} w SET progress_percentage = NULL "
             f"WHERE w.progress_percentage = 0{keep}"),
        {"t": TABLE} if keep else {},
    ).rowcount
    print(f"{TABLE}.progress_percentage: {changed} stored zero(s) set to not entered")


def downgrade():
    bind = op.get_bind()
    if not _present(bind, TABLE, "progress_percentage"):
        return
    bind.execute(text(f"UPDATE {TABLE} SET progress_percentage = 0 WHERE progress_percentage IS NULL"))
    bind.execute(text(f"ALTER TABLE {TABLE} ALTER COLUMN progress_percentage SET DEFAULT 0"))
