"""Add VendorOrganization.named_by_organization_id and ApplicationComponent.vendor_organization_id.

Creates the two nullable foreign-key columns the vendor naming round needs:
one on ``vendor_organizations`` (who first named this vendor) and one on
``application_components`` (which vendor supplies this application). Both are
nullable, indexed, and use SET NULL on delete so removing a vendor or
organisation does not cascade-delete rows.

Idempotent: columns, indexes and constraints are created only when absent.

Revision ID: 20261025_vendor_naming
Revises: 20261025_data_erd_tables
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261025_vendor_naming"
down_revision = "20261025_data_erd_tables"
branch_labels = None
depends_on = None


def _get_fk_names(inspector, table: str, column: str) -> list:
    """Return FK constraint names for a given column."""
    names = []
    for fk in inspector.get_foreign_keys(table):
        if fk["constrained_columns"] == [column]:
            names.append(fk["name"])
    return names


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # 1. VendorOrganization.named_by_organization_id
    have_vo = {c["name"] for c in inspector.get_columns("vendor_organizations")}
    if "named_by_organization_id" not in have_vo:
        op.add_column(
            "vendor_organizations",
            sa.Column("named_by_organization_id", sa.Integer(), nullable=True),
        )
    # Create FK constraint if it does not already exist
    if not _get_fk_names(inspector, "vendor_organizations", "named_by_organization_id"):
        op.create_foreign_key(
            "fk_vendor_organizations_named_by_organization_id",
            "vendor_organizations",
            "organizations",
            ["named_by_organization_id"],
            ["id"],
            ondelete="SET NULL",
        )
    existing = {i["name"] for i in inspector.get_indexes("vendor_organizations")}
    if "ix_vendor_organizations_named_by_organization_id" not in existing:
        op.create_index(
            "ix_vendor_organizations_named_by_organization_id",
            "vendor_organizations",
            ["named_by_organization_id"],
        )

    # 2. ApplicationComponent.vendor_organization_id
    have_ac = {c["name"] for c in inspector.get_columns("application_components")}
    if "vendor_organization_id" not in have_ac:
        op.add_column(
            "application_components",
            sa.Column("vendor_organization_id", sa.Integer(), nullable=True),
        )
    if not _get_fk_names(inspector, "application_components", "vendor_organization_id"):
        op.create_foreign_key(
            "fk_application_components_vendor_organization_id",
            "application_components",
            "vendor_organizations",
            ["vendor_organization_id"],
            ["id"],
            ondelete="SET NULL",
        )
    existing_ac = {i["name"] for i in inspector.get_indexes("application_components")}
    if "ix_application_components_vendor_organization_id" not in existing_ac:
        op.create_index(
            "ix_application_components_vendor_organization_id",
            "application_components",
            ["vendor_organization_id"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # Drop application_components FK and column
    have_ac = {c["name"] for c in inspector.get_columns("application_components")}
    if "vendor_organization_id" in have_ac:
        for name in _get_fk_names(inspector, "application_components", "vendor_organization_id"):
            op.drop_constraint(name, "application_components", type_="foreignkey")
        op.drop_column("application_components", "vendor_organization_id")

    # Drop vendor_organizations FK and column
    have_vo = {c["name"] for c in inspector.get_columns("vendor_organizations")}
    if "named_by_organization_id" in have_vo:
        for name in _get_fk_names(inspector, "vendor_organizations", "named_by_organization_id"):
            op.drop_constraint(name, "vendor_organizations", type_="foreignkey")
        op.drop_column("vendor_organizations", "named_by_organization_id")