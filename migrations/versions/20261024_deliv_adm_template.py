"""Link a solution deliverable to the TOGAF phase checklist item it answers.

deliverables.adm_template_id points at adm_deliverables (the phase checklist).
The ADM board reads each solution's own deliverables against that checklist.
The column is nullable, so existing deliverables are untouched, and deleting a
checklist item clears the link. Idempotent and reversible.

Revision ID: 20261024_deliv_adm_template
Revises: 20261022_erole_no_default
Create Date: 2026-10-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261024_deliv_adm_template"
down_revision = "20261022_erole_no_default"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if _has_column("deliverables", "adm_template_id"):
        return
    op.add_column("deliverables", sa.Column("adm_template_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_deliverables_adm_template_id", "deliverables", "adm_deliverables",
        ["adm_template_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_deliverables_adm_template_id", "deliverables", ["adm_template_id"])


def downgrade() -> None:
    if not _has_column("deliverables", "adm_template_id"):
        return
    # A database built from the models (create_all) carries the column with a
    # generated foreign-key name, and upgrade() leaves it alone because the column
    # is already there. So drop whatever foreign key and index sit on the column
    # rather than assuming the names this revision would have given them.
    insp = sa.inspect(op.get_bind())
    for fk in insp.get_foreign_keys("deliverables"):
        if fk["constrained_columns"] == ["adm_template_id"] and fk.get("name"):
            op.drop_constraint(fk["name"], "deliverables", type_="foreignkey")
    for ix in insp.get_indexes("deliverables"):
        if ix["column_names"] == ["adm_template_id"]:
            op.drop_index(ix["name"], table_name="deliverables")
    op.drop_column("deliverables", "adm_template_id")
