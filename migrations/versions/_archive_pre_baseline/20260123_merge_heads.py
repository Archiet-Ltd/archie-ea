"""Merge multiple Alembic heads into a single branch.

Revision ID: 20260123_merge_heads
Revises: 006_roadmap_enhancements, add_acm_tech_caps, add_import_session_001
Create Date: 2026-01-23 11:35:33.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260123_merge_heads"
down_revision = (
    "006_roadmap_enhancements",
    "add_acm_tech_caps",
    "add_import_session_001",
)  # merged heads: 006_roadmap_enhancements, add_acm_tech_caps, add_import_session_001
branch_labels = None
depends_on = None


def upgrade():
    # merge-only migration: no schema changes
    pass


def downgrade():
    # no-op
    pass
