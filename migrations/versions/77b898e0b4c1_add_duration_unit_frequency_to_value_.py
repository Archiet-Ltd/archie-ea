"""add duration_unit frequency to value stream stages

Revision ID: 77b898e0b4c1
Revises: 20261021_chat_records_org
Create Date: 2026-10-22 14:38:40.808032

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '77b898e0b4c1'
down_revision = '20261021_chat_records_org'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("unified_value_stream_stages", sa.Column("duration_unit", sa.String(20), nullable=True))
    op.add_column("unified_value_stream_stages", sa.Column("frequency_count", sa.Integer(), nullable=True))
    op.add_column("unified_value_stream_stages", sa.Column("frequency_period", sa.String(30), nullable=True))


def downgrade():
    op.drop_column("unified_value_stream_stages", "frequency_period")
    op.drop_column("unified_value_stream_stages", "frequency_count")
    op.drop_column("unified_value_stream_stages", "duration_unit")
