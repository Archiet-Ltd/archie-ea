"""Add the review columns to ai_chat_crud_approvals.

A requested change is now checked, reviewed by a model other than the one that drafted it, and applied
or declined by the server. The record keeps what that took: the drafting and reviewing models, how many
reviews ran, the checks and the reviewer's reason for every criterion in every round, the version of the
written rubric, and the fields the change wrote as they were just before it was applied.

All six columns are nullable, so a row written before this revision reads as it did. Idempotent: each
column is added only when absent (reconcile-schema may already have added it). The downgrade drops only
what is present. Imports nothing from ``app``.

Revision ID: 20261110_change_review_columns
Revises: 20261109_saved_scenarios
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261110_change_review_columns"
down_revision = "20261109_saved_scenarios"
branch_labels = None
depends_on = None

TABLE = "ai_chat_crud_approvals"
COLUMNS = (
    ("drafter_model", lambda: sa.String(length=160)),
    ("reviewer_model", lambda: sa.String(length=160)),
    ("review_rounds", lambda: sa.Integer()),
    ("review_reasons", lambda: sa.JSON()),
    ("rubric_version", lambda: sa.String(length=20)),
    ("before_snapshot", lambda: sa.JSON()),
)


def _columns(bind):
    return {c["name"] for c in inspect(bind).get_columns(TABLE)}


def upgrade():
    have = _columns(op.get_bind())
    for name, kind in COLUMNS:
        if name not in have:
            op.add_column(TABLE, sa.Column(name, kind(), nullable=True))


def downgrade():
    have = _columns(op.get_bind())
    for name, _kind in reversed(COLUMNS):
        if name in have:
            op.drop_column(TABLE, name)
