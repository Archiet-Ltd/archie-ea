"""Add conversation tables

Revision ID: add_conversation_tables
Revises:
Create Date: 2026-01-24 17:14:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "add_conversation_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create conversation_threads table
    op.create_table(
        "conversation_threads",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("message_count", sa.Integer(), default=0),
        sa.Index("idx_threads_user_updated", "user_id", "updated_at"),
    )

    # Create conversation_messages table
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "thread_id",
            sa.String(length=36),
            sa.ForeignKey("conversation_threads.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=50), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Index("idx_messages_thread_created", "thread_id", "created_at"),
    )


def downgrade():
    op.drop_table("conversation_messages")
    op.drop_table("conversation_threads")
