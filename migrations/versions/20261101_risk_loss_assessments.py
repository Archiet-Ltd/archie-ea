"""risk_loss_assessments for a database built from migrations

Revision ID: 20261101_risk_loss_assessments
Revises: 20261101_seed_default_roles
Create Date: 2026-11-01 00:00:00

The yearly-loss card on a risk keeps the ranges a person entered in
risk_loss_assessments (app/models/risk_loss_assessment.py). The table was only
ever created by init-db, so a database upgraded with the migrations alone had
nowhere to save them. This creates it, guarded so a database that already has
it is left as it is, and fences it by organisation the way cost_facts is
fenced: the runtime role sees and writes only its own organisation's rows.

The money result is written as cost facts with element type "risk". The column
holding that type is a plain string with no database constraint, so nothing
changes there.

The downgrade drops the table and the cost facts derived from it (source
"open_fair", written from risk_loss_assessments), so no yearly loss is left
standing without the ranges behind it.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261101_risk_loss_assessments"
down_revision = "20261101_seed_default_roles"
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

# Only the table this revision exists to fence. The guard unions this with
# every other revision's TENANT_TABLES/HYBRID_TABLES -- do not add tables
# already listed in 20261008_row_level_security here.
TENANT_TABLES = ("risk_loss_assessments",)
HYBRID_TABLES = ()


def _skip_reason(bind, table: str):
    present = bind.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}
    ).scalar()
    if not present:
        return "absent"
    has_column = bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t "
            "AND column_name = 'organization_id')"
        ),
        {"t": table},
    ).scalar()
    if not has_column:
        print(f"WARNING row-level security skipped: {table} has no organization_id column")
        return "no organization_id column"
    may_alter = bind.execute(
        text(
            "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
            "FROM pg_class c WHERE c.oid = to_regclass(:t)"
        ),
        {"t": f"public.{table}"},
    ).scalar()
    if not may_alter:
        print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
        return "not owned by the migration role"
    return None


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


def _create_table(bind):
    inspector = sa.inspect(bind)
    if not inspector.has_table("risk_loss_assessments") and inspector.has_table("risks"):
        op.create_table(
            "risk_loss_assessments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("risk_id", sa.Integer(),
                      sa.ForeignKey("risks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("factors", sa.JSON(), nullable=False),
            sa.Column("trials", sa.Integer(), nullable=False),
            sa.Column("seed", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False),
            sa.Column("entered_by_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("entered_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("organization_id", "risk_id", name="uq_risk_loss_assessment_risk"),
        )
    if sa.inspect(bind).has_table("risk_loss_assessments"):
        op.execute("CREATE INDEX IF NOT EXISTS ix_risk_loss_assessments_organization_id "
                   "ON risk_loss_assessments (organization_id)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_risk_loss_assessments_risk_id "
                   "ON risk_loss_assessments (risk_id)")


def upgrade():
    bind = op.get_bind()
    _create_table(bind)
    for table in TENANT_TABLES:
        reason = _skip_reason(bind, table)
        if reason is not None:
            print(f"row-level security: skipped {table} ({reason})")
            continue
        _fence(bind, table)
        print(f"row-level security: fenced {table}")


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("cost_facts"):
        op.execute(
            "DELETE FROM public.cost_facts WHERE element_type = 'risk' "
            "AND source = 'open_fair' AND source_table = 'risk_loss_assessments'"
        )
    # Dropping the table removes its policies and indexes with it.
    op.execute("DROP TABLE IF EXISTS public.risk_loss_assessments")
