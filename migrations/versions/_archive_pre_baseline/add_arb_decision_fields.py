"""Add decision_type and decided_at fields to ARBReviewItem

Revision ID: add_arb_decision_fields
Revises:
Create Date: 2026-01-26 19:25:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "add_arb_decision_fields"
down_revision = "20260125_tech_cap_cols"  # Same parent as solution_architect_workspace_v1
branch_labels = ("arb",)
depends_on = None


def upgrade():
    """Add decision_type and decided_at fields to arb_review_items table."""

    # Add decision_type field
    op.add_column(
        "arb_review_items", sa.Column("decision_type", sa.String(length=50), nullable=True)
    )

    # Add decided_at field
    op.add_column("arb_review_items", sa.Column("decided_at", sa.DateTime(), nullable=True))


def downgrade():
    """Remove decision_type and decided_at fields from arb_review_items table."""

    # Remove decision_type field
    op.drop_column("arb_review_items", "decision_type")

    # Remove decided_at field
    op.drop_column("arb_review_items", "decided_at")
