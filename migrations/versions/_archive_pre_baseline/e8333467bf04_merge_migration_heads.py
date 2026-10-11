"""Merge migration heads

Revision ID: e8333467bf04
Revises: 001_create_nlp_tables, alter_app_cat_001
Create Date: 2026-01-17 13:43:37.314751

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e8333467bf04"
down_revision = ("001_create_nlp_tables", "alter_app_cat_001")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
