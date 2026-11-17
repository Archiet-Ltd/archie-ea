"""data_entity_attributes and data_entity_relationships tables.

Creates the two tables behind the entity relationship diagram (the attributes
of a data entity, and a relationship between two data entities with a
cardinality at each end) and fences both with the same row-level security
policies as the other tenant tables, in this one revision, so neither table is
readable across organisations on a real deploy. Imports nothing from ``app``.

Idempotent: tables and indexes are created only when absent; policies are
dropped and recreated.

Revision ID: 20261025_data_erd_tables
Revises: 20261025_canvas_types
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261025_data_erd_tables"
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

TENANT_TABLES = ("data_entity_attributes", "data_entity_relationships")


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


def _organization_column():
    return sa.Column(
        "organization_id",
        sa.Integer(),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def _entity_fk(name: str):
    return sa.Column(name, sa.Integer(), sa.ForeignKey("data_entities.id", ondelete="CASCADE"), nullable=False)


def upgrade():
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    if "data_entity_attributes" not in tables:
        op.create_table(
            "data_entity_attributes",
            sa.Column("id", sa.Integer(), primary_key=True),
            _organization_column(),
            _entity_fk("entity_id"),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("data_type", sa.String(60), nullable=True),
            sa.Column("is_primary_key", sa.Boolean(), nullable=False),
            sa.Column("is_foreign_key", sa.Boolean(), nullable=False),
            sa.Column("is_nullable", sa.Boolean(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    if "data_entity_relationships" not in tables:
        op.create_table(
            "data_entity_relationships",
            sa.Column("id", sa.Integer(), primary_key=True),
            _organization_column(),
            _entity_fk("source_entity_id"),
            _entity_fk("target_entity_id"),
            sa.Column("label", sa.String(200), nullable=True),
            sa.Column("source_cardinality", sa.String(20), nullable=False),
            sa.Column("target_cardinality", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    inspector = inspect(bind)
    wanted = {
        "data_entity_attributes": ("organization_id", "entity_id"),
        "data_entity_relationships": ("organization_id", "source_entity_id", "target_entity_id"),
    }
    for table, columns in wanted.items():
        existing = {i["name"] for i in inspector.get_indexes(table)}
        for column in columns:
            name = f"ix_{table}_{column}"
            if name not in existing:
                op.create_index(name, table, [column])

    if bind.dialect.name == "postgresql":
        for table in TENANT_TABLES:
            _fence(bind, table)


def downgrade():
    tables = set(inspect(op.get_bind()).get_table_names())
    for table in ("data_entity_relationships", "data_entity_attributes"):
        if table in tables:
            op.drop_table(table)
