"""Record when a user enrolled in two-factor sign-in: users.mfa_enrolled_at.

The column is nullable and the revision is idempotent: a database built from the models
already has it, and a second run changes nothing. Users who already have two-factor
switched on are back-filled with the time of this upgrade, so the first-week window that
applies to people who have not enrolled stays shut for them. Users without it keep NULL.

The down step drops the column.

Revision ID: 20261031_users_mfa_enrolled
Revises: 20261101_seed_default_roles
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261031_users_mfa_enrolled"
down_revision = "20261101_seed_default_roles"
branch_labels = None
depends_on = None

TABLE, COLUMN = "users", "mfa_enrolled_at"


def _has_column(bind) -> bool:
    return bool(
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :t AND column_name = :c"
            ),
            {"t": TABLE, "c": COLUMN},
        ).scalar()
    )


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text("SELECT to_regclass(:t)"), {"t": TABLE}).scalar():
        return
    if not _has_column(bind):
        op.add_column(TABLE, sa.Column(COLUMN, sa.DateTime(), nullable=True))
    bind.execute(
        text(
            "UPDATE users SET mfa_enrolled_at = (now() AT TIME ZONE 'utc') "
            "WHERE mfa_enabled IS TRUE AND mfa_enrolled_at IS NULL"
        )
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(text("SELECT to_regclass(:t)"), {"t": TABLE}).scalar() and _has_column(bind):
        op.drop_column(TABLE, COLUMN)
