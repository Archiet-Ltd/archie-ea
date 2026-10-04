"""Add product_inquiries table.

Lead capture for the two fixed-price offer pages (architecture health check,
Team annual plan with onboarding). Mirrors waitlist_signups: no organisation
or user link, a visitor may not have an account.

Revision ID: 20261004_product_inquiries
Revises: 20261004_agten_cascade
Create Date: 2026-10-04
"""
from alembic import op
from sqlalchemy import text

from app.commands.schema_migrations import ContractBlocked

revision = "20261004_product_inquiries"
down_revision = "20261004_agten_cascade"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text("""
        CREATE TABLE IF NOT EXISTS product_inquiries (
            id SERIAL PRIMARY KEY,
            email VARCHAR(254) NOT NULL,
            name VARCHAR(200),
            offer VARCHAR(50) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            consent_text TEXT NOT NULL,
            CONSTRAINT uq_product_inquiry_email_offer UNIQUE (email, offer)
        )
    """))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_product_inquiries_email "
        "ON product_inquiries (email)"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_product_inquiries_offer "
        "ON product_inquiries (offer)"
    ))


def downgrade():
    bind = op.get_bind()
    rows = bind.execute(text("SELECT count(*) FROM product_inquiries")).scalar()
    if rows:
        raise ContractBlocked(
            f"product_inquiries: {rows} row(s) hold inquiries; downgrading "
            "would discard them. Forward-fix instead."
        )
    bind.execute(text("DROP INDEX IF EXISTS ix_product_inquiries_offer"))
    bind.execute(text("DROP INDEX IF EXISTS ix_product_inquiries_email"))
    bind.execute(text("DROP TABLE IF EXISTS product_inquiries"))
