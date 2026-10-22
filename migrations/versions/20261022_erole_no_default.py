"""Stop defaulting new accounts to the platform_admin persona.

users.enterprise_role was NOT NULL with a server default of platform_admin, so
every new account started on the widest persona. The column becomes nullable
with no default: a new account carries no persona until an administrator
assigns one. Existing rows are left exactly as they are (changing them is a
data decision for the release lead), and an account with no persona reads as
the lowest-privilege lens in the sidebar.

Idempotent and reversible: downgrade restores the default and NOT NULL (rows
with no persona are set to platform_admin first, which is why a downgrade
should only follow a deliberate decision).

Revision ID: 20261022_erole_no_default
Revises: 20261020_change_feed_offsets
Create Date: 2026-10-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261022_erole_no_default"
down_revision = "20261020_change_feed_offsets"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    if "enterprise_role" not in cols:
        return
    op.alter_column(
        "users",
        "enterprise_role",
        existing_type=sa.String(50),
        nullable=True,
        server_default=None,
    )


def downgrade():
    op.execute("UPDATE users SET enterprise_role = 'platform_admin' WHERE enterprise_role IS NULL")
    op.alter_column(
        "users",
        "enterprise_role",
        existing_type=sa.String(50),
        nullable=False,
        server_default="platform_admin",
    )
