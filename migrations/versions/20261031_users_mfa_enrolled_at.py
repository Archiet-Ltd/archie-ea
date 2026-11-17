"""Record when a user enrolled in two-factor sign-in (users.mfa_enrolled_at) and when their first
week without it began (users.mfa_window_started_at).

The column is nullable and the revision is idempotent: a database built from the models
already has it, and a second run changes nothing. Users who already have two-factor
switched on are back-filled with the time of this upgrade, so the first-week window that
applies to people who have not enrolled stays shut for them. Everyone else keeps NULL in both
columns: an existing account's week starts at its first sign-in after the release, which the
sign-in path stamps, so the upgrade sets no start time of its own.

The down step drops the column.

Revision ID: 20261031_users_mfa_enrolled
Revises: 20261102_bp_one_record
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261031_users_mfa_enrolled"
down_revision = "20261102_bp_one_record"
branch_labels = None
depends_on = None

TABLE, COLUMN = "users", "mfa_enrolled_at"
START_COLUMN = "mfa_window_started_at"


def _has_column(bind, column=COLUMN) -> bool:
    return bool(
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :t AND column_name = :c"
            ),
            {"t": TABLE, "c": column},
        ).scalar()
    )


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text("SELECT to_regclass(:t)"), {"t": TABLE}).scalar():
        return
    if not _has_column(bind):
        op.add_column(TABLE, sa.Column(COLUMN, sa.DateTime(), nullable=True))
    if not _has_column(bind, START_COLUMN):
        op.add_column(TABLE, sa.Column(START_COLUMN, sa.DateTime(), nullable=True))
    bind.execute(
        text(
            "UPDATE users SET mfa_enrolled_at = (now() AT TIME ZONE 'utc') "
            "WHERE mfa_enabled IS TRUE AND mfa_enrolled_at IS NULL"
        )
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(text("SELECT to_regclass(:t)"), {"t": TABLE}).scalar():
        for column in (START_COLUMN, COLUMN):
            if _has_column(bind, column):
                op.drop_column(TABLE, column)
