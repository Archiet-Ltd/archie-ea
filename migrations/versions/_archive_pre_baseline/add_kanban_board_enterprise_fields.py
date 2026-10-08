"""Add enterprise integration fields to KanbanBoard

Revision ID: add_kanban_board_enterprise_fields
Revises: <latest_revision_id>
Create Date: 2026-01-27 14:30:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "add_kanban_board_enterprise_fields"
down_revision = None  # Will be set to latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Add enterprise integration columns to kanban_boards table
    op.add_column("kanban_boards", sa.Column("application_ids", postgresql.JSON(), nullable=True))
    op.add_column("kanban_boards", sa.Column("system_ids", postgresql.JSON(), nullable=True))
    op.add_column("kanban_boards", sa.Column("initiative_ids", postgresql.JSON(), nullable=True))
    op.add_column("kanban_boards", sa.Column("project_ids", postgresql.JSON(), nullable=True))
    op.add_column(
        "kanban_boards", sa.Column("primary_architect_role", sa.String(length=50), nullable=True)
    )
    op.add_column("kanban_boards", sa.Column("created_by_id", sa.Integer(), nullable=True))
    op.add_column("kanban_boards", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.add_column("kanban_boards", sa.Column("updated_at", sa.DateTime(), nullable=True))

    # Create foreign key constraint
    op.create_foreign_key(
        "kanban_boards_created_by_id_fkey", "kanban_boards", "users", ["created_by_id"], ["id"]
    )

    # Set default values for existing records
    op.execute("UPDATE kanban_boards SET application_ids = '[]' WHERE application_ids IS NULL")
    op.execute("UPDATE kanban_boards SET system_ids = '[]' WHERE system_ids IS NULL")
    op.execute("UPDATE kanban_boards SET initiative_ids = '[]' WHERE initiative_ids IS NULL")
    op.execute("UPDATE kanban_boards SET project_ids = '[]' WHERE project_ids IS NULL")
    op.execute("UPDATE kanban_boards SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    op.execute("UPDATE kanban_boards SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")


def downgrade():
    # Remove foreign key constraint first
    op.drop_constraint("kanban_boards_created_by_id_fkey", "kanban_boards", type_="foreignkey")

    # Remove the added columns
    op.drop_column("kanban_boards", "updated_at")
    op.drop_column("kanban_boards", "created_at")
    op.drop_column("kanban_boards", "created_by_id")
    op.drop_column("kanban_boards", "primary_architect_role")
    op.drop_column("kanban_boards", "project_ids")
    op.drop_column("kanban_boards", "initiative_ids")
    op.drop_column("kanban_boards", "system_ids")
    op.drop_column("kanban_boards", "application_ids")
