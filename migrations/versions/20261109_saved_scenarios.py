"""saved_scenarios table, fenced by organisation.

Creates ``saved_scenarios`` (a named change a person tried on an element, with the
analysis it produced, kept for the organisation) and fences it with the same
row-level security policies as the other tenant tables, forced for the owner role
as well, in this one revision, so the table is never readable across organisations
on a real deploy. Imports nothing from ``app``.

Idempotent: the table and indexes are created only when absent; policies are
dropped and recreated.

Revision ID: 20261109_saved_scenarios
Revises: 20261108_impact_capability
Create Date: 2026-11-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261109_saved_scenarios"
down_revision = "20261108_impact_capability"
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

TENANT_TABLES = ("saved_scenarios",)
INDEXED_COLUMNS = ("organization_id", "element_id")


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
    bind.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))  # nosec B608 - literal table name


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "saved_scenarios" not in inspector.get_table_names():
        op.create_table(
            "saved_scenarios",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("element_id", sa.Integer(), nullable=True),
            sa.Column("element_name", sa.String(500), nullable=True),
            sa.Column("element_type", sa.String(100), nullable=True),
            sa.Column("change_type", sa.String(30), nullable=False),
            sa.Column("change_label", sa.String(100), nullable=True),
            sa.Column("analysis_result", sa.JSON(), nullable=True),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    existing_indexes = {i["name"] for i in inspect(bind).get_indexes("saved_scenarios")}
    for column in INDEXED_COLUMNS:
        name = f"ix_saved_scenarios_{column}"
        if name not in existing_indexes:
            op.create_index(name, "saved_scenarios", [column])

    if bind.dialect.name == "postgresql":
        for table in TENANT_TABLES:
            _fence(bind, table)


def downgrade():
    inspector = inspect(op.get_bind())
    if "saved_scenarios" in inspector.get_table_names():
        op.drop_table("saved_scenarios")
