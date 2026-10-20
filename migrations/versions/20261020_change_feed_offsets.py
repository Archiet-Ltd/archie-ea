"""The change feed's consumer offset, one row per organisation and consumer.

Expand step only: adds ``change_feed_offsets``, the ordinal of the last event
in an organisation's event log whose notifications have been committed, and
fences it with row-level security like every other tenant table. Nothing
existing is read differently, altered or dropped.

Idempotent: the table and index are created only when absent, and the fence is
dropped and recreated, so a second run is a no-op.

Revision ID: 20261020_change_feed_offsets
Revises: 20261020_provenance_origin
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261020_change_feed_offsets"
down_revision = "20261020_provenance_origin"
branch_labels = None
depends_on = None


# Identical text to 20261008_row_level_security's own constants -- copied, not
# imported, so this revision stays runnable on its own if the base file ever
# changes.
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

# Only the table this revision exists to create and fence.
TENANT_TABLES = ("change_feed_offsets",)
HYBRID_TABLES = ()


def _may_alter(bind, table: str) -> bool:
    return bool(
        bind.execute(
            text(
                "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
                "FROM pg_class c WHERE c.oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).scalar()
    )


def _fence(bind, table: str) -> None:
    # Table name is a literal module constant; nothing here is user input.
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(
            text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}")  # nosec B608 - table names from a literal constant
        )
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - table names from a literal constant


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("change_feed_offsets"):
        op.create_table(
            "change_feed_offsets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "organization_id", sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("consumer", sa.String(48), nullable=False),
            sa.Column("last_ordinal", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint("organization_id", "consumer", name="uq_change_feed_offset_consumer"),
        )
        op.create_index(
            "ix_change_feed_offsets_organization_id", "change_feed_offsets", ["organization_id"]
        )

    for table in TENANT_TABLES:
        if _may_alter(bind, table):
            _fence(bind, table)
            print(f"row-level security: fenced {table}")
        else:
            print(f"row-level security: skipped {table} (not owned by the migration role)")


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("change_feed_offsets"):
        op.drop_table("change_feed_offsets")
