"""Merge multiple heads

Revision ID: 385f4e01433f
Revises: 001_create_roadmap_tables, arch_crud_001, add_capability_code_column, e51338e46a4f
Create Date: 2026-01-10 12:38:53.578715

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "385f4e01433f"
down_revision = (
    "001_create_roadmap_tables",
    "arch_crud_001",
    "add_capability_code_column",
    "e51338e46a4f",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
