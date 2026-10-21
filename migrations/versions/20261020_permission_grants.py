"""permission_grants: one permission matrix per organisation (applications first).

Creates ``permission_grants`` (organisation, subject, object type, action,
allowed) and fences it with the same row-level security policies as the other
tenant tables, in this one revision, so the table is never readable across
organisations on a real deploy. Imports nothing from ``app``.

Idempotent: the table and index are created only when absent; policies are
dropped and recreated.

Revision ID: 20261020_permission_grants
Revises: 20261019_reference_counters
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261020_permission_grants"
down_revision = "20261019_reference_counters"
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

TENANT_TABLES = ("permission_grants",)
HYBRID_TABLES = ()


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
    if "permission_grants" not in inspector.get_table_names():
        op.create_table(
            "permission_grants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("subject_type", sa.String(16), nullable=False),
            sa.Column("subject_key", sa.Text(), nullable=False),
            sa.Column("object_type", sa.String(64), nullable=False),
            sa.Column("action", sa.String(16), nullable=False),
            sa.Column("allowed", sa.Boolean(), nullable=False),
            sa.Column("granted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint(
                "organization_id",
                "subject_type",
                "subject_key",
                "object_type",
                "action",
                name="uq_permission_grants_subject_action",
            ),
        )
        op.create_index(
            "ix_permission_grants_organization_id", "permission_grants", ["organization_id"]
        )
    if bind.dialect.name == "postgresql":
        for table in TENANT_TABLES:
            _fence(bind, table)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "permission_grants" in inspector.get_table_names():
        op.drop_table("permission_grants")
