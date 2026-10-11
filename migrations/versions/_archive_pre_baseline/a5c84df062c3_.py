"""empty message

Revision ID: a5c84df062c3
Revises: 004_strategic_apqc, 005_app_vendor_product, add_import_fields_001
Create Date: 2026-01-19 18:25:51.221292

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a5c84df062c3"
down_revision = ("004_strategic_apqc", "005_app_vendor_product", "add_import_fields_001")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
