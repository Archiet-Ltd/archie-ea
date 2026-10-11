"""add_adm_phase_to_workflow_definitions

Revision ID: add_adm_phase_to_workflow_definitions
Revises: add_ea_workflow_notifications
Create Date: 2026-02-24

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_adm_phase_to_workflow_definitions"
down_revision = "add_ea_workflow_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ea_workflow_definitions",
        sa.Column("adm_phase", sa.String(20), nullable=True),
    )
    op.add_column(
        "ea_workflow_definitions",
        sa.Column("adm_phase_name", sa.String(100), nullable=True),
    )


def downgrade():
    op.drop_column("ea_workflow_definitions", "adm_phase_name")
    op.drop_column("ea_workflow_definitions", "adm_phase")
