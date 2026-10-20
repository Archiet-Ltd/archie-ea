"""Record which API consumer called which operation, and which operations retire.

Two new tables, both owned by one organisation:

``api_operation_calls`` holds one row per (organisation, consumer, operation):
the last call time and a call count. ``consumer_key`` is the token's own client
identifier, so a registered OAuth client and a personal API token (whose
identifier has no ``oauth_clients`` row) fit one table; ``client_id`` is set
only when the consumer is a registered client, and ``token_kind`` says which.
It is written once per bearer call by ``require_api_scope``.

``api_operation_retirements`` holds one row per (organisation, operation) an
administrator has marked as retiring: the retirement date and the replacement
operation. It is a table rather than process memory so every worker answers
with the same headers and a restart forgets nothing; no existing per-operation
store could hold it.

Both are fenced with the standard ``archie.organization_id`` /
``archie.platform_scope`` policies used by the neighbouring tenant tables
(row-level security enabled, not forced).

Idempotent: every step checks before it acts, so a second run is a no-op, and a
database that holds the earlier single-table shape of ``api_operation_calls``
is brought to this one. The migration raises when the role running it cannot
alter a table, because a half-fenced tenant table must not be recorded as
applied.

Revision ID: 20261020_api_op_calls
Revises: 20261018_tree_models_rls
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261020_api_op_calls"
down_revision = "20261018_tree_models_rls"
branch_labels = None
depends_on = None

_CALLS = "api_operation_calls"
_RETIREMENTS = "api_operation_retirements"

# The row-level security guard unions this constant across revisions.
TENANT_TABLES = (_CALLS, _RETIREMENTS)
HYBRID_TABLES = ()

# Identical text to 20261008_row_level_security's own constants -- copied, not
# imported, so this revision stays runnable on its own.
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


def _present(bind, table):
    return bool(bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())


def _columns(bind, table):
    return {
        row[0]
        for row in bind.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        )
    }


def _constraints(bind, table):
    return {
        row[0]
        for row in bind.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = to_regclass(:t)"),
            {"t": f"public.{table}"},
        )
    }


def _create_calls():
    op.create_table(
        _CALLS,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=True),
        sa.Column("consumer_key", sa.String(length=128), nullable=False),
        sa.Column("token_kind", sa.String(length=16), nullable=False, server_default="oauth_client"),
        sa.Column("operation_id", sa.String(length=255), nullable=False),
        sa.Column("last_called_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_api_operation_calls_organization_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["oauth_clients.client_id"],
            name="fk_api_operation_calls_client_id", ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id", "consumer_key", "operation_id",
            name="uq_api_operation_calls_org_consumer_operation",
        ),
    )


def _create_retirements():
    op.create_table(
        _RETIREMENTS,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.String(length=255), nullable=False),
        sa.Column("retires_on", sa.Date(), nullable=False),
        sa.Column("replacement_operation_id", sa.String(length=255), nullable=True),
        sa.Column("marked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_api_operation_retirements_organization_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["marked_by_user_id"], ["users.id"],
            name="fk_api_operation_retirements_marked_by_user_id", ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "organization_id", "operation_id",
            name="uq_api_operation_retirements_org_operation",
        ),
    )


def _bring_calls_to_current_shape(bind):
    """A database that already holds the earlier single-table shape."""
    columns = _columns(bind, _CALLS)
    if "consumer_key" not in columns:
        op.add_column(_CALLS, sa.Column("consumer_key", sa.String(length=128), nullable=True))
        bind.execute(text(f"UPDATE {_CALLS} SET consumer_key = client_id WHERE consumer_key IS NULL"))  # nosec B608 - literal table name
        op.alter_column(_CALLS, "consumer_key", nullable=False)
    if "token_kind" not in columns:
        op.add_column(
            _CALLS,
            sa.Column("token_kind", sa.String(length=16), nullable=False, server_default="oauth_client"),
        )
    op.alter_column(_CALLS, "client_id", existing_type=sa.String(length=128), nullable=True)
    constraints = _constraints(bind, _CALLS)
    if "uq_api_operation_calls_org_client_operation" in constraints:
        op.drop_constraint("uq_api_operation_calls_org_client_operation", _CALLS, type_="unique")
    if "uq_api_operation_calls_org_consumer_operation" not in constraints:
        op.create_unique_constraint(
            "uq_api_operation_calls_org_consumer_operation", _CALLS,
            ["organization_id", "consumer_key", "operation_id"],
        )


def _require_owner(bind, table):
    """Raise when the migration role cannot alter the table."""
    may_alter = bind.execute(
        text(
            "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
            "FROM pg_class c WHERE c.oid = to_regclass(:t)"
        ),
        {"t": f"public.{table}"},
    ).scalar()
    if not may_alter:
        raise RuntimeError(
            f"row-level security cannot be applied to {table}: it is not owned by the migration role"
        )


def _fence(bind, table):
    # Table name is a literal module constant; nothing here is user input.
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

    if _present(bind, _CALLS):
        _bring_calls_to_current_shape(bind)
    else:
        _create_calls()
    if not _present(bind, _RETIREMENTS):
        _create_retirements()

    for name, table, columns in (
        ("ix_api_operation_calls_organization_id", _CALLS, ["organization_id"]),
        ("ix_api_operation_calls_client_id", _CALLS, ["client_id"]),
        ("ix_api_operation_calls_consumer_key", _CALLS, ["consumer_key"]),
        ("ix_api_operation_calls_operation_id", _CALLS, ["operation_id"]),
        ("ix_api_operation_retirements_organization_id", _RETIREMENTS, ["organization_id"]),
        ("ix_api_operation_retirements_operation_id", _RETIREMENTS, ["operation_id"]),
    ):
        op.create_index(name, table, columns, if_not_exists=True)

    for table in TENANT_TABLES:
        _require_owner(bind, table)
        _fence(bind, table)


def downgrade():
    bind = op.get_bind()
    for table in reversed(TENANT_TABLES):
        if not _present(bind, table):
            continue
        for name in TENANT_POLICIES:
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table name
        bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table name
        op.drop_table(table, if_exists=True)
