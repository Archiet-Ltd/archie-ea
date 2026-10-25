"""business_case_options table and four new columns on business_cases.

Creates ``business_case_options`` (one option compared in a business case,
with its name, summary, advantages, drawbacks, recommended flag and position)
and fences it with the same row-level security policies as the other tenant
tables, in this one revision, so the table is never readable across
organisations on a real deploy. Adds the four nullable columns the business
case keeps for its decision record and work-package link on ``business_cases``.
Imports nothing from ``app``.

Idempotent: the table, index and columns are created only when absent;
policies are dropped and recreated.

Revision ID: 20261025_business_case_options
Revises: 20261025_data_erd_tables
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261025_business_case_options"
down_revision = "20261025_data_erd_tables"
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

TENANT_TABLES = ("business_case_options",)

BUSINESS_CASE_COLUMNS = ("work_package_id", "decided_by_id", "decided_at", "decision_note")


def _fence(bind, table: str) -> None:
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table name
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(
            text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}")  # nosec B608 - literal table name
        )
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table name


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "business_case_options" not in inspector.get_table_names():
        op.create_table(
            "business_case_options",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "business_case_id",
                sa.Integer(),
                sa.ForeignKey("business_cases.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(256), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("advantages", sa.Text(), nullable=True),
            sa.Column("drawbacks", sa.Text(), nullable=True),
            sa.Column("is_recommended", sa.Boolean(), nullable=True, default=False),
            sa.Column("position", sa.Integer(), nullable=True, default=0),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    existing_indexes = {i["name"] for i in inspect(bind).get_indexes("business_case_options")}
    for column in ("organization_id", "business_case_id"):
        name = f"ix_business_case_options_{column}"
        if name not in existing_indexes:
            op.create_index(name, "business_case_options", [column])

    have = {c["name"] for c in inspector.get_columns("business_cases")}
    for column in BUSINESS_CASE_COLUMNS:
        if column not in have:
            if column == "work_package_id":
                op.add_column("business_cases", sa.Column(column, sa.BigInteger(), nullable=True))
            elif column == "decided_by_id":
                op.add_column("business_cases", sa.Column(column, sa.Integer(), nullable=True))
            elif column == "decided_at":
                op.add_column("business_cases", sa.Column(column, sa.DateTime(), nullable=True))
            elif column == "decision_note":
                op.add_column("business_cases", sa.Column(column, sa.Text(), nullable=True))

    if bind.dialect.name == "postgresql":
        for table in TENANT_TABLES:
            _fence(bind, table)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    have = {c["name"] for c in inspector.get_columns("business_cases")}
    for column in BUSINESS_CASE_COLUMNS:
        if column in have:
            op.drop_column("business_cases", column)
    if "business_case_options" in inspector.get_table_names():
        op.drop_table("business_case_options")
