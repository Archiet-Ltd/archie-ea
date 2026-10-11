"""Widen api_settings.api_key column to 1000 chars for Fernet-encrypted tokens.

Fernet tokens are ~100 chars longer than the plaintext they encrypt.
A 500-char column is too narrow for keys with long values.
This migration is additive / backward-compatible — existing plaintext
values continue to work until they are re-saved through Admin > API Settings.

Revision ID: encrypt_api_keys
Revises: solution_stakeholder_models
Create Date: 2026-03-09
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "encrypt_api_keys"
down_revision = "20260309_schema_gap_fill"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("api_settings", schema=None) as batch_op:
        batch_op.alter_column(
            "api_key",
            existing_type=sa.String(500),
            type_=sa.String(1000),
            existing_nullable=True,
        )


def downgrade():
    with op.batch_alter_table("api_settings", schema=None) as batch_op:
        batch_op.alter_column(
            "api_key",
            existing_type=sa.String(1000),
            type_=sa.String(500),
            existing_nullable=True,
        )
