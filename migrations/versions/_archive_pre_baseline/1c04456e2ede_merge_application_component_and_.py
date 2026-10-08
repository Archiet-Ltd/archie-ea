"""Merge application component and business event migrations

Revision ID: 1c04456e2ede
Revises: add_app_component_id_wp, add_triggering_business_event_id
Create Date: 2026-01-10 18:38:17.434174

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "1c04456e2ede"
down_revision = ("add_app_component_id_wp", "add_triggering_business_event_id")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
