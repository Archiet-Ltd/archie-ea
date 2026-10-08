"""Merge extraction feedback migration

Revision ID: 4f32aa43ea3e
Revises: 1c04456e2ede, f1a2b3c4d5e6
Create Date: 2026-01-12 18:35:09.797534

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "4f32aa43ea3e"
down_revision = ("1c04456e2ede", "f1a2b3c4d5e6")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
