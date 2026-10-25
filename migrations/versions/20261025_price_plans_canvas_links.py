"""price_plans table and the canvas's saved-diagram and vision links.

Creates ``price_plans`` (what a product or service costs the customer, per
ArchiMate element) and fences it with the same row-level security policies as
the other tenant tables, in this one revision, so the table is never readable
across organisations on a real deploy. Adds the two nullable columns the
Business Model Canvas keeps on ``business_model_canvases``. Imports nothing
from ``app``.

Idempotent: the table, index and columns are created only when absent;
policies are dropped and recreated.

Revision ID: 20261025_price_plans_canvas
Revises: 20261024_mfa_recovery_codes
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261025_price_plans_canvas"
down_revision = "20261024_mfa_recovery_codes"
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

TENANT_TABLES = ("price_plans",)
HYBRID_TABLES = ()

CANVAS_COLUMNS = ("saved_diagram_id", "vision_element_id")


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
    if "price_plans" not in inspector.get_table_names():
        op.create_table(
            "price_plans",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "element_id",
                sa.Integer(),
                sa.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("price", sa.Numeric(18, 4), nullable=True),
            sa.Column("currency", sa.String(3), nullable=True),
            sa.Column("billing_period", sa.String(16), nullable=True),
            sa.Column("pricing_mechanism", sa.String(32), nullable=True),
            sa.Column("revenue_share", sa.Numeric(5, 2), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
    existing_indexes = {i["name"] for i in inspect(bind).get_indexes("price_plans")}
    for column in ("organization_id", "element_id"):
        name = f"ix_price_plans_{column}"
        if name not in existing_indexes:
            op.create_index(name, "price_plans", [column])

    have = {c["name"] for c in inspector.get_columns("business_model_canvases")}
    for column in CANVAS_COLUMNS:
        if column not in have:
            op.add_column("business_model_canvases", sa.Column(column, sa.Integer(), nullable=True))

    if bind.dialect.name == "postgresql":
        for table in TENANT_TABLES:
            _fence(bind, table)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    have = {c["name"] for c in inspector.get_columns("business_model_canvases")}
    for column in CANVAS_COLUMNS:
        if column in have:
            op.drop_column("business_model_canvases", column)
    if "price_plans" in inspector.get_table_names():
        op.drop_table("price_plans")
