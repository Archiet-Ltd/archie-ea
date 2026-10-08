"""Add archimate_id field to BusinessCapability for Abacus integration

Revision ID: 20260202_add_archimate_id_to_business_capability
Revises: 20260202_add_abacus_fields
Create Date: 2026-02-02 22:30:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260202_add_archimate_id_to_business_capability"
down_revision = "20260202_add_abacus_fields"
branch_labels = None
depends_on = None


def upgrade():
    # Add archimate_id field to business_capability table for Abacus EEID storage
    op.add_column(
        "business_capability",
        sa.Column("archimate_id", sa.String(255), nullable=True, unique=True, index=True),
    )


def downgrade():
    # Remove archimate_id field from business_capability table
    op.drop_column("business_capability", "archimate_id")
