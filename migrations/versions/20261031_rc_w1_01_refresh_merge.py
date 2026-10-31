"""Join the fw_adopt_source chain and the rc merge head into one head. No schema change.

Revision ID: 20261031_rc_w1_01_refresh_merge
Revises: 20261031_fw_adopt_source, 20261031_rc_w1_01_merge
Create Date: 2026-10-31
"""
revision = "20261031_rc_w1_01_refresh_merge"
down_revision = ("20261031_fw_adopt_source", "20261031_rc_w1_01_merge")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
