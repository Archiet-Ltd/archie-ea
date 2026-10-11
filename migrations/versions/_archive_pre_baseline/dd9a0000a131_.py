"""empty message

Revision ID: dd9a0000a131
Revises: add_kanban_board_enterprise_fields, solution_cost_models, solution_outcomes_models, solution_stakeholder_models
Create Date: 2026-01-27 19:39:25.922421

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "dd9a0000a131"
down_revision = (
    "add_kanban_board_enterprise_fields",
    "solution_cost_models",
    "solution_outcomes_models",
    "solution_stakeholder_models",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
