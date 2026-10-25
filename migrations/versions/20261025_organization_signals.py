"""organization_signals table, fenced by organisation.

Creates ``organization_signals`` (one cited public signal about an organisation:
a news item, filing, figure, patent, paper or mention, with its source, link and
date) and fences it with the same row-level security policies as the other
tenant tables, in this one revision, so the table is never readable across
organisations on a real deploy. Imports nothing from ``app``.

Idempotent: the table and indexes are created only when absent; policies are
dropped and recreated.

Revision ID: 20261025_organization_signals
Revises: 20261025_canvas_types
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261025_organization_signals"
down_revision = "20261025_canvas_types"
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

TENANT_TABLES = ("organization_signals",)
INDEXED_COLUMNS = ("organization_id", "kind", "source")


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
    if "organization_signals" not in inspector.get_table_names():
        op.create_table(
            "organization_signals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(24), nullable=True),
            sa.Column("source", sa.String(32), nullable=True),
            sa.Column("source_label", sa.String(80), nullable=True),
            sa.Column("subject", sa.String(200), nullable=True),
            sa.Column("title", sa.String(500), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("url", sa.String(1000), nullable=True),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(), nullable=True),
            sa.Column("pestle_factor", sa.String(16), nullable=True),
            sa.Column("swot_quadrant", sa.String(16), nullable=True),
            sa.Column("sentiment_score", sa.Float(), nullable=True),
            sa.Column("details", sa.JSON(), nullable=True),
            sa.Column("checksum", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("organization_id", "checksum", name="uq_organization_signal_checksum"),
        )
    existing_indexes = {i["name"] for i in inspect(bind).get_indexes("organization_signals")}
    for column in INDEXED_COLUMNS:
        name = f"ix_organization_signals_{column}"
        if name not in existing_indexes:
            op.create_index(name, "organization_signals", [column])

    if bind.dialect.name == "postgresql":
        for table in TENANT_TABLES:
            _fence(bind, table)


def downgrade():
    inspector = inspect(op.get_bind())
    if "organization_signals" in inspector.get_table_names():
        op.drop_table("organization_signals")
