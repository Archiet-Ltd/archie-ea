"""Join the tenant-fence chain and the delivery work-package revision into one head.

Revision ID: 20261031_merge_fence_and_delivery
Revises: 20261027_fence_capability, 20261029_deliv_wp_nullable
"""
revision = "20261031_merge_fence_and_delivery"
down_revision = ("20261027_fence_capability", "20261029_deliv_wp_nullable")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
