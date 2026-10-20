"""Record which OAuth client called which API operation, per organisation.

New table ``api_operation_calls`` holds one row per (organisation, client,
operation): the last call timestamp and a call count. Written from
``require_api_scope`` with one upsert per request so the deprecation admin
page can name the consumers of an interface version before it retires.

TenantMixin shape: organization_id FK + index, RLS enabled (ENABLE not FORCE)
with the standard ``archie.organization_id`` / ``archie.platform_scope``
policies used by neighbouring tenant tables.

Idempotent: guards the table create, each column, each index, each constraint
and each policy so a second run is a no-op.

Revision ID: 20261020_api_op_calls
Revises: 20261018_tree_models_rls
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261020_api_op_calls"
down_revision = "20261018_tree_models_rls"
branch_labels = None
depends_on = None

_TABLE = "api_operation_calls"

_ORG = "NULLIF(current_setting('archie.organization_id', true), '')::integer"
_PLATFORM = "COALESCE(current_setting('archie.platform_scope', true), '') = 'on'"
_TENANT_RULE = f"organization_id = {_ORG} OR {_PLATFORM}"

TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")


def _table_exists(bind):
    return bind.execute(text(f"SELECT to_regclass('public.{_TABLE}')")).scalar()


def _column_names(bind):
    return {
        row[0]
        for row in bind.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": _TABLE},
        )
    }


def _index_names(bind):
    return {
        row[0]
        for row in bind.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = :t"
            ),
            {"t": _TABLE},
        )
    }


def _constraint_names(bind):
    return {
        row[0]
        for row in bind.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = to_regclass(:t)"
            ),
            {"t": _TABLE},
        )
    }


def _policy_exists(bind, name):
    return bind.execute(
        text(
            "SELECT 1 FROM pg_policies WHERE schemaname = 'public' "
            "AND tablename = :t AND policyname = :n"
        ),
        {"t": _TABLE, "n": name},
    ).scalar()


def _create_table(bind):
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "client_id",
            sa.String(length=128),
            sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("operation_id", sa.String(length=255), nullable=False, index=True),
        sa.Column(
            "last_called_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "call_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "client_id",
            "operation_id",
            name="uq_api_operation_calls_org_client_operation",
        ),
    )


_TENANT_DEFINITIONS = (
    ("SELECT", "tenant_select", _TENANT_RULE, None),
    ("INSERT", "tenant_insert", None, _TENANT_RULE),
    ("UPDATE", "tenant_update", _TENANT_RULE, _TENANT_RULE),
    ("DELETE", "tenant_delete", _TENANT_RULE, None),
)


def _fence(bind):
    for command, name, using, check in _TENANT_DEFINITIONS:
        if _policy_exists(bind, name):
            continue
        bind.execute(
            text(f"DROP POLICY IF EXISTS {name} ON public.{_TABLE}")  # nosec B608 - table name is a literal constant
        )
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(
            text(
                f"CREATE POLICY {name} ON public.{_TABLE} FOR {command}{clause}"  # nosec B608 - table name is a literal constant
            )
        )
    bind.execute(
        text(f"ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY")  # nosec B608 - table name is a literal constant
    )


def upgrade():
    bind = op.get_bind()

    if not _table_exists(bind):
        _create_table(bind)

    existing = _column_names(bind)
    for column in (
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("operation_id", sa.String(length=255), nullable=False),
        sa.Column("last_called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False),
    ):
        if column.name not in existing:
            op.add_column(_TABLE, column)

    indexes = _index_names(bind)
    if "ix_api_operation_calls_organization_id" not in indexes:
        op.create_index(
            "ix_api_operation_calls_organization_id",
            _TABLE,
            ["organization_id"],
            if_not_exists=True,
        )
    if "ix_api_operation_calls_client_id" not in indexes:
        op.create_index(
            "ix_api_operation_calls_client_id",
            _TABLE,
            ["client_id"],
            if_not_exists=True,
        )
    if "ix_api_operation_calls_operation_id" not in indexes:
        op.create_index(
            "ix_api_operation_calls_operation_id",
            _TABLE,
            ["operation_id"],
            if_not_exists=True,
        )

    constraints = _constraint_names(bind)
    if "uq_api_operation_calls_org_client_operation" not in constraints:
        op.create_unique_constraint(
            "uq_api_operation_calls_org_client_operation",
            _TABLE,
            ["organization_id", "client_id", "operation_id"],
        )

    fk_exists = bind.execute(
        text(
            "SELECT 1 FROM pg_constraint WHERE conname = "
            "'fk_api_operation_calls_organization_id'"
        )
    ).scalar()
    if not fk_exists:
        op.create_foreign_key(
            "fk_api_operation_calls_organization_id",
            _TABLE,
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="CASCADE",
        )

    fk_client_exists = bind.execute(
        text(
            "SELECT 1 FROM pg_constraint WHERE conname = "
            "'fk_api_operation_calls_client_id'"
        )
    ).scalar()
    if not fk_client_exists:
        op.create_foreign_key(
            "fk_api_operation_calls_client_id",
            _TABLE,
            "oauth_clients",
            ["client_id"],
            ["client_id"],
            ondelete="CASCADE",
        )

    _fence(bind)


def downgrade():
    bind = op.get_bind()
    if not _table_exists(bind):
        return
    for name in TENANT_POLICIES:
        bind.execute(
            text(f"DROP POLICY IF EXISTS {name} ON public.{_TABLE}")  # nosec B608 - table name is a literal constant
        )
    bind.execute(
        text(f"ALTER TABLE public.{_TABLE} DISABLE ROW LEVEL SECURITY")  # nosec B608 - table name is a literal constant
    )
    op.drop_table(_TABLE, if_exists=True)
