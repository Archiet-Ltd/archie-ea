"""Accounts that still store the old default persona store none.

``platform_admin`` used to be the stored persona of every new account. New
accounts stopped getting it, but existing rows kept it. A stored persona is a
view preference and never authority, so every account that stores
``platform_admin`` without the platform-admin flag is set to no persona (the
column is nullable and an unset persona reads as the least-privileged one).
Accounts that hold the flag are untouched. Idempotent: a second run matches
nothing. The down step does nothing: the old value cannot be told apart from a
deliberate choice afterwards, and restoring it would widen access.

Revision ID: 20261029_persona_default_unset
Revises: 20261029_llm_spend_ledger
Create Date: 2026-10-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_persona_default_unset"
down_revision = "20261029_llm_spend_ledger"
branch_labels = None
depends_on = None


def upgrade():
    result = op.get_bind().execute(
        sa.text(
            "UPDATE users SET enterprise_role = NULL "
            "WHERE enterprise_role = 'platform_admin' "
            "AND COALESCE(is_platform_admin, FALSE) = FALSE"
        )
    )
    print(f"personas: {result.rowcount} account(s) moved from the old default to none")


def downgrade():
    pass
