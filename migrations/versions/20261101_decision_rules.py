"""decision_rules: the numbered rule rows of a DMN decision table

Revision ID: 20261101_decision_rules
Revises: 20261102_bp_one_record
Create Date: 2026-11-01 00:00:00

A decision is an ArchiMate element; this table holds only its rule rows. A new table with no
existing rows, so it expands and needs no backfill. Guarded so a database that already has the
table (created by init-db) is left alone.
"""
import sqlalchemy as sa
from alembic import op

revision = "20261101_decision_rules"
down_revision = "20261102_bp_one_record"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("decision_rules"):
        return
    op.create_table(
        "decision_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column(
            "element_id", sa.Integer(), sa.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("input_entries", sa.JSON(), nullable=False),
        sa.Column("output_entries", sa.JSON(), nullable=False),
        sa.Column("annotation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_decision_rules_element_position", "decision_rules", ["element_id", "position"])


def downgrade():
    op.drop_index("ix_decision_rules_element_position", table_name="decision_rules")
    op.drop_table("decision_rules")
