"""framework_adoptions.source

Revision ID: 20261031_fw_adopt_source
Revises: 20261029_deliv_wp_nullable
Create Date: 2026-10-31 00:00:00

Adds the nullable column recording where an adoption came from. Guarded so a
schema built from the models (which already has the column) does not fail.
"""
from alembic import op

revision = "20261031_fw_adopt_source"
down_revision = "20261029_deliv_wp_nullable"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE framework_adoptions ADD COLUMN IF NOT EXISTS source VARCHAR(100)")


def downgrade():
    op.execute("ALTER TABLE framework_adoptions DROP COLUMN IF EXISTS source")
