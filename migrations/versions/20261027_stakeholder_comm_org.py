"""Tenant-fence stakeholder_communications (solution notes): organization_id, backfill, row-level security.

T-sec02 (severity 1). Solution notes had no organization_id, and the API accepted any
solution_id, so one organisation could read or plant notes on another's solution. This adds
the column, backfills it from the solution each note belongs to, tightens it to NOT NULL when
every row was reached, and fences the table with the standard tenant policies.

A note whose solution is gone or never set has no derivable organisation. It is left NULL
(and the column stays nullable on that database) rather than guessed; a NULL row is excluded
from every tenant-scoped read.

Idempotent: on a database built fresh from the models the table already carries the column,
and a second run is a no-op.

Revision ID: 20261027_stakeholder_comm_org
Revises: 20261026_signup_origin
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261027_stakeholder_comm_org"
down_revision = "20261026_signup_origin"
branch_labels = None
depends_on = None

TABLE = "stakeholder_communications"

# Identical text to 20261008_row_level_security's constants -- copied, not imported, so this
# revision stays runnable on its own.
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

# Read by tests/test_row_level_security.py's guard that every TenantMixin table is fenced.
TENANT_TABLES = (TABLE,)
HYBRID_TABLES = ()


def _may_alter(bind) -> bool:
    return bool(
        bind.execute(
            text(
                "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
                "FROM pg_class c WHERE c.oid = to_regclass(:t)"
            ),
            {"t": f"public.{TABLE}"},
        ).scalar()
    )


def _fence(bind) -> None:
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{TABLE}"))  # nosec B608 - literal table name
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(text(f"CREATE POLICY {name} ON public.{TABLE} FOR {command}{clause}"))  # nosec B608 - literal table name
    bind.execute(text(f"ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table name


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{TABLE}"}).scalar():
        # create_all / the baseline brings the table up with the column already on it.
        return

    columns = {
        row[0]
        for row in bind.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": TABLE},
        )
    }
    if "organization_id" not in columns:
        op.add_column(TABLE, sa.Column("organization_id", sa.Integer(), nullable=True))

    if bind.execute(text("SELECT to_regclass('solutions')")).scalar():
        bind.execute(
            text(
                "UPDATE stakeholder_communications AS sc "
                "SET organization_id = s.organization_id "
                "FROM solutions AS s "
                "WHERE sc.solution_id = s.id AND sc.organization_id IS NULL"
            )
        )

    remaining_null = bind.execute(
        text("SELECT COUNT(*) FROM stakeholder_communications WHERE organization_id IS NULL")
    ).scalar()
    if remaining_null == 0:
        op.alter_column(TABLE, "organization_id", existing_type=sa.Integer(), nullable=False)

    op.create_index(
        "ix_stakeholder_communications_organization_id", TABLE, ["organization_id"],
        if_not_exists=True,
    )
    fk_exists = bind.execute(
        text(
            "SELECT 1 FROM pg_constraint WHERE conname = "
            "'fk_stakeholder_communications_organization_id'"
        )
    ).scalar()
    if not fk_exists:
        # Another constraint on organization_id from create_all may already exist under the
        # default name; only add ours when no foreign key covers the column.
        covered = bind.execute(
            text(
                "SELECT 1 FROM pg_constraint c JOIN pg_attribute a "
                "ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
                "WHERE c.contype = 'f' AND c.conrelid = to_regclass('public.stakeholder_communications') "
                "AND a.attname = 'organization_id'"
            )
        ).scalar()
        if not covered:
            op.create_foreign_key(
                "fk_stakeholder_communications_organization_id", TABLE, "organizations",
                ["organization_id"], ["id"], ondelete="CASCADE",
            )

    if _may_alter(bind):
        _fence(bind)
        print(f"row-level security: fenced {TABLE}")
    else:
        print(f"WARNING row-level security skipped: {TABLE} is not owned by the migration role")


def downgrade():
    bind = op.get_bind()
    if not bind.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{TABLE}"}).scalar():
        return
    if _may_alter(bind):
        for name in TENANT_POLICIES:
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{TABLE}"))  # nosec B608 - literal table name
        bind.execute(text(f"ALTER TABLE public.{TABLE} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table name
    # The column is kept: dropping it would discard the only record of which organisation
    # owns each note. Loosen to nullable so older code can still insert.
    op.alter_column(TABLE, "organization_id", existing_type=sa.Integer(), nullable=True)
