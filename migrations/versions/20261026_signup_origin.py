"""Where each sign-up came from: users.signup_source, signup_campaign, signup_ref, signup_at, signup_alerted_at.

Revision ID: 20261026_signup_origin
Revises: ea5dcdfa14a1
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "20261026_signup_origin"
down_revision = "ea5dcdfa14a1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    for name, kind in (
        ("signup_source", sa.String(64)), ("signup_campaign", sa.String(64)), ("signup_ref", sa.String(64)),
        ("signup_at", sa.DateTime()), ("signup_alerted_at", sa.DateTime()),
    ):
        if name not in existing:
            op.add_column("users", sa.Column(name, kind, nullable=True))
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("users")}
    if "ix_users_signup_at" not in indexes:
        op.create_index("ix_users_signup_at", "users", ["signup_at"])


def downgrade():
    op.drop_index("ix_users_signup_at", table_name="users")
    for name in ("signup_alerted_at", "signup_at", "signup_ref", "signup_campaign", "signup_source"):
        op.drop_column("users", name)
