"""Connect Google and Microsoft accounts to people.

Creates sign_in_identities: one row per connected provider account, holding the provider's subject,
the issuer that vouched for it and the address verified at the time. Nothing is backfilled: no
platform sign-in links exist before this revision. The table can already exist when this runs
(init-db creates mapped tables first), so creation is guarded.

The downgrade drops the table.

Revision ID: 20261114_sign_in_identities
Revises: 20261114_subscription_offer
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261114_sign_in_identities"
down_revision = "20261114_subscription_offer"
branch_labels = None
depends_on = None

_TABLE = "sign_in_identities"


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text("SELECT to_regclass('sign_in_identities')")).scalar():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("issuer", sa.String(length=255), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("email_at_link", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("provider", "subject", name="uq_sign_in_identities_provider_subject"),
            sa.UniqueConstraint("user_id", "provider", name="uq_sign_in_identities_user_provider"),
            sa.CheckConstraint("provider IN ('google', 'microsoft')", name="ck_sign_in_identities_provider"),
        )
    op.create_index("ix_sign_in_identities_user_id", _TABLE, ["user_id"], if_not_exists=True)


def downgrade():
    op.execute("DROP TABLE IF EXISTS sign_in_identities")
