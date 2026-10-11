"""add_arb_impacted_element_ids

Revision ID: add_arb_impacted_element_ids
Revises: add_ea_workflow_notifications
Create Date: 2026-02-24

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_arb_impacted_element_ids"
down_revision = "merge_pre_existing_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "architecture_review_boards",
        sa.Column("impacted_element_ids", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("architecture_review_boards", "impacted_element_ids")
