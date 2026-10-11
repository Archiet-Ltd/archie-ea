"""One unified work package per (source_table, source_id): a partial unique index.

The model declares ``uq_unified_wp_source_copy`` (app/models/unified_work_package.py,
``__table_args__``), but no migration ever created it -- ``init_db``/``create_all`` builds it on
a fresh database, ``reconcile-schema`` is add-column-only and never creates a missing index on an
existing table. The application's own dedup path relies on it:
``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` needs a real unique constraint to have
anything to conflict against, or the second insert for the same (source_table, source_id) just
succeeds, producing a duplicate copy (confirmed via
tests/test_row_level_security.py::test_unified_work_package_unique_element_and_conflict_inserts_are_not_hidden_by_the_fence
against a database that predates this index being added to the model).

``upgrade`` first collapses any existing duplicate groups the same way
20261008_uwp_element_unique collapsed element duplicates: keep the earliest row (lowest id) in
each (source_table, source_id) group, clear ``source_table``/``source_id`` on the rest so they
survive as plain, now-unlinked work packages rather than being deleted, then creates the index.
Idempotent: a second run finds nothing to clear and the index already exists.

Revision ID: 20261011_uwp_source_copy_unique
Revises: 20261011_notif_rls
Create Date: 2026-10-11
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261011_uwp_source_copy_unique"
down_revision = "20261011_notif_rls"
branch_labels = None
depends_on = None

INDEX = "uq_unified_wp_source_copy"


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("unified_work_packages"):
        return
    columns = {c["name"] for c in inspector.get_columns("unified_work_packages")}
    if "source_table" not in columns or "source_id" not in columns:
        return

    bind.execute(
        text(
            """
        WITH duplicates AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY source_table, source_id ORDER BY id
                   ) AS rn
            FROM unified_work_packages
            WHERE source_table IS NOT NULL
        )
        UPDATE unified_work_packages
        SET source_table = NULL, source_id = NULL
        WHERE id IN (SELECT id FROM duplicates WHERE rn > 1)
        """
        )
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} "
        "ON unified_work_packages (source_table, source_id) WHERE source_table IS NOT NULL"
    )


def downgrade():
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
