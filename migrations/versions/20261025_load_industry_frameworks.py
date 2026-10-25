"""Load the industry process frameworks for every organisation.

The frameworks are one shared copy (organization_id NULL) that every
organisation reads, so this loads each default framework and its process
hierarchy once, adding only what is missing. A new organisation finds them
already there. Idempotent; the downgrade leaves the rows in place because
organisations may have adopted processes from them.

Revision ID: 20261025_load_industry_frameworks
Revises: 20261024_mfa_recovery_codes
Create Date: 2026-10-25
"""
from __future__ import annotations

from alembic import op

revision = "20261025_industry_frameworks"
down_revision = "20261025_price_plans_canvas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.modules.industry_apqc.framework_data import load_default_frameworks

    load_default_frameworks(op.get_bind())


def downgrade() -> None:
    pass
