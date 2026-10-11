"""Add application_component_id to implementation_work_packages

Revision ID: add_app_component_id_wp
Revises: 385f4e01433f
Create Date: 2026-01-10 12:40:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "add_app_component_id_wp"
down_revision = "385f4e01433f"
branch_labels = None
depends_on = None


def upgrade():
    # Add application_component_id column to implementation_work_packages
    # Using IF NOT EXISTS pattern for safety
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("implementation_work_packages")]

    if "application_component_id" not in columns:
        op.add_column(
            "implementation_work_packages",
            sa.Column("application_component_id", sa.Integer(), nullable=True),
        )
        op.create_index(
            "ix_implementation_work_packages_app_component_id",
            "implementation_work_packages",
            ["application_component_id"],
            unique=False,
        )
        op.create_foreign_key(
            "fk_impl_work_packages_app_component",
            "implementation_work_packages",
            "application_components",
            ["application_component_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade():
    # Remove the column and its constraints
    op.drop_constraint(
        "fk_impl_work_packages_app_component", "implementation_work_packages", type_="foreignkey"
    )
    op.drop_index(
        "ix_implementation_work_packages_app_component_id",
        table_name="implementation_work_packages",
    )
    op.drop_column("implementation_work_packages", "application_component_id")
