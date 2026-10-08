"""empty message

Revision ID: fac924608f6e
Revises: 20260202_add_archimate_id_to_business_capability, 20260203_arb_capability_tracking, 20260203_add_usage_analytics_table
Create Date: 2026-02-03 22:31:38.698275

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fac924608f6e'
down_revision = ('20260202_add_archimate_id_to_business_capability', '20260203_arb_capability_tracking', '20260203_add_usage_analytics_table')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
