"""Add the column that holds hashed two-factor recovery codes.

users.mfa_recovery_codes is a nullable text column holding a JSON list of
salted hashes, one per unused recovery code. Existing rows stay NULL, which
the sign-in treats as "no recovery codes". Idempotent: the column is added
only when it is missing. The down step drops it.

Revision ID: 20261024_mfa_recovery_codes
Revises: 20261024_deliv_adm_template
Create Date: 2026-10-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261024_mfa_recovery_codes"
down_revision = "20261024_deliv_adm_template"
branch_labels = None
depends_on = None


def upgrade():
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "mfa_recovery_codes" not in cols:
        op.add_column("users", sa.Column("mfa_recovery_codes", sa.Text(), nullable=True))


def downgrade():
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "mfa_recovery_codes" in cols:
        op.drop_column("users", "mfa_recovery_codes")
