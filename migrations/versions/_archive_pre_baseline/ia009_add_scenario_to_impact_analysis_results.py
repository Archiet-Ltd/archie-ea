"""IA-009: Add scenario column to impact_analysis_results for server-side scenario persistence.

Revision ID: ia009_scenario
Revises: 20260309_schema_gap_fill
Create Date: 2026-03-10

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "ia009_scenario"
down_revision = "20260309_schema_gap_fill"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "impact_analysis_results",
        sa.Column("scenario", sa.String(50), nullable=True),
    )


def downgrade():
    op.drop_column("impact_analysis_results", "scenario")
