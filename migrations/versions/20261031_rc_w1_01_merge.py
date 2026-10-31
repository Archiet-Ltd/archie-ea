"""Join the two migration chains into one head. No schema change.

Revision ID: 20261031_rc_w1_01_merge
Revises: 20261029_deliv_wp_nullable, 20261030_sample_source_kind
Create Date: 2026-10-31
"""
revision = "20261031_rc_w1_01_merge"
down_revision = ("20261029_deliv_wp_nullable", "20261030_sample_source_kind")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
