"""One unified work package per ArchiMate element: a partial unique index.

Main's merge copied ``archimate_element_id`` across without checks, so two copies can hold one
element, or a copy can hold another organisation's. ``upgrade`` first applies the one element
rule to every existing copy (``clear_untaken_elements``: the element is cleared, nothing is
deleted, and a cleared copy gets its own element at its first link), then creates the index.
Idempotent: a second run clears nothing and the index already exists.

Revision ID: 20261008_uwp_element_unique
Revises: 20261007_public_visitor_events
Create Date: 2026-10-08
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261008_uwp_element_unique"
down_revision = "20261007_public_visitor_events"
branch_labels = None
depends_on = None

INDEX = "uq_unified_wp_archimate_element"


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("unified_work_packages") or "archimate_element_id" not in {
            c["name"] for c in inspector.get_columns("unified_work_packages")}:
        return
    from app.commands.consolidate_work_packages import clear_untaken_elements

    clear_untaken_elements(bind)
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} "
        "ON unified_work_packages (archimate_element_id) WHERE archimate_element_id IS NOT NULL"
    )


def downgrade():
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
