"""add_archimate_element_ids_to_kanban_cards

Revision ID: add_archimate_element_ids_to_kanban_cards
Revises: add_arb_impacted_element_ids
Create Date: 2026-02-24

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_archimate_element_ids_to_kanban_cards"
down_revision = "add_arb_impacted_element_ids"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "kanban_cards",
        sa.Column("archimate_element_ids", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("kanban_cards", "archimate_element_ids")
