"""Add jira_issue_key and jira_push_status to kanban_cards.

Revision ID: jira001_kanban_fields
Revises: (set to current head — run: flask db current)
Create Date: 2026-03-04

IMPORTANT: Review with DBA before running. Never run flask db upgrade automatically.
"""
from alembic import op
import sqlalchemy as sa

revision = "jira001_kanban_fields"
down_revision = "8758cda215b7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("kanban_cards") as batch_op:
        batch_op.add_column(
            sa.Column("jira_issue_key", sa.String(50), nullable=True, unique=True)
        )
        batch_op.add_column(
            sa.Column("jira_push_status", sa.String(50), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("kanban_cards") as batch_op:
        batch_op.drop_column("jira_push_status")
        batch_op.drop_column("jira_issue_key")
