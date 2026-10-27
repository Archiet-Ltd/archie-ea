"""The public vendor catalogue: sourced facts per shared product, and each
organisation's link from its vendor products to them (ADR-0021).

Additive only:
- vendor_product_details.catalogue_slug (nullable, unique): the catalogue's
  address for a shared product record.
- vendor_catalogue_facts: one sourced fact per row, global (no organisation),
  with a check constraint that a published fact has a source address, a check
  date and a review time.
- vendor_catalogue_links: per organisation, fenced by row-level security the
  same way as every other tenant table.

Idempotent: each object is created only when missing. The down step drops them.

Revision ID: 20261025_vendor_catalogue
Revises: 20261027_stakeholder_comm_org
Create Date: 2026-10-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261025_vendor_catalogue"
down_revision = "20261027_stakeholder_comm_org"
branch_labels = None
depends_on = None

_ORG = "NULLIF(current_setting('archie.organization_id', true), '')::integer"
_PLATFORM = "COALESCE(current_setting('archie.platform_scope', true), '') = 'on'"
_TENANT_RULE = f"organization_id = {_ORG} OR {_PLATFORM}"

TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")
_TENANT_DEFINITIONS = (
    ("SELECT", _TENANT_RULE, None),
    ("INSERT", None, _TENANT_RULE),
    ("UPDATE", _TENANT_RULE, _TENANT_RULE),
    ("DELETE", _TENANT_RULE, None),
)
TENANT_TABLES = ("vendor_catalogue_links",)
HYBRID_TABLES = ()


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _may_alter(bind, table):
    return bind.execute(
        text(
            "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
            "FROM pg_class c WHERE c.oid = to_regclass(:t)"
        ),
        {"t": f"public.{table}"},
    ).scalar()


def upgrade():
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("vendor_product_details")}
    if "catalogue_slug" not in cols:
        op.add_column("vendor_product_details", sa.Column("catalogue_slug", sa.String(80), nullable=True))
        op.create_index(
            "ix_vendor_product_details_catalogue_slug",
            "vendor_product_details",
            ["catalogue_slug"],
            unique=True,
        )

    tables = _tables(bind)
    if "vendor_catalogue_facts" not in tables:
        op.create_table(
            "vendor_catalogue_facts",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column(
                "product_detail_id",
                sa.BigInteger(),
                sa.ForeignKey("vendor_product_details.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(30), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("archimate_type", sa.String(50)),
            sa.Column("detail", sa.Text()),
            sa.Column("attributes", sa.JSON()),
            sa.Column("source_url", sa.String(1000)),
            sa.Column("checked_on", sa.Date()),
            sa.Column("evidence", sa.String(300)),
            sa.Column("review_state", sa.String(20), nullable=False, server_default="draft"),
            sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("reviewed_by_name", sa.String(200)),
            sa.Column("reviewed_at", sa.DateTime()),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.CheckConstraint(
                "review_state <> 'published' OR (source_url IS NOT NULL AND source_url <> '' "
                "AND checked_on IS NOT NULL AND reviewed_at IS NOT NULL)",
                name="ck_vendor_catalogue_facts_published_sourced",
            ),
            sa.CheckConstraint(
                "review_state IN ('draft', 'published', 'needs_recheck')",
                name="ck_vendor_catalogue_facts_review_state",
            ),
            sa.UniqueConstraint("product_detail_id", "kind", "name", name="uq_vendor_catalogue_fact"),
        )
        op.create_index("ix_vendor_catalogue_facts_product_detail_id", "vendor_catalogue_facts", ["product_detail_id"])
        op.create_index("ix_vendor_catalogue_facts_kind", "vendor_catalogue_facts", ["kind"])
        op.create_index("ix_vendor_catalogue_facts_review_state", "vendor_catalogue_facts", ["review_state"])

    if "vendor_catalogue_links" not in tables:
        op.create_table(
            "vendor_catalogue_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "vendor_product_id",
                sa.Integer(),
                sa.ForeignKey("vendor_products.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "product_detail_id",
                sa.BigInteger(),
                sa.ForeignKey("vendor_product_details.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("linked_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("linked_at", sa.DateTime()),
            sa.UniqueConstraint("organization_id", "vendor_product_id", name="uq_vendor_catalogue_link"),
        )
        op.create_index("ix_vendor_catalogue_links_organization_id", "vendor_catalogue_links", ["organization_id"])
        op.create_index("ix_vendor_catalogue_links_vendor_product_id", "vendor_catalogue_links", ["vendor_product_id"])
        op.create_index("ix_vendor_catalogue_links_product_detail_id", "vendor_catalogue_links", ["product_detail_id"])

    for table in TENANT_TABLES:
        if not _may_alter(bind, table):
            print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
            continue
        for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal constant
            clause = ""
            if using is not None:
                clause += f" USING ({using})"
            if check is not None:
                clause += f" WITH CHECK ({check})"
            bind.execute(text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}"))  # nosec B608 - literal constant
        bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal constant


def downgrade():
    bind = op.get_bind()
    tables = _tables(bind)
    if "vendor_catalogue_links" in tables:
        op.drop_table("vendor_catalogue_links")
    if "vendor_catalogue_facts" in tables:
        op.drop_table("vendor_catalogue_facts")
    cols = {c["name"] for c in sa.inspect(bind).get_columns("vendor_product_details")}
    if "catalogue_slug" in cols:
        op.drop_index("ix_vendor_product_details_catalogue_slug", table_name="vendor_product_details")
        op.drop_column("vendor_product_details", "catalogue_slug")
