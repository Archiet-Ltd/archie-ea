"""decision_parties: the people on an architecture decision

Revision ID: 20261103_decision_parties
Revises: 20261103_requirement_source
Create Date: 2026-11-03 00:00:00

An architecture decision names the elements it affects but not the people
behind it. This adds decision_parties: one row per Stakeholder, Business Actor
or Business Role and the part it plays on one decision (decider, owner,
consulted, informed). The governance checks read it to find decisions nobody
owns and affected elements whose owner was never consulted.

Created only when absent, and fenced by organisation the same way
risk_loss_assessments is: the runtime role sees and writes its own rows only.
The downgrade drops the table.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261103_decision_parties"
down_revision = "20261103_requirement_source"
branch_labels = None
depends_on = None


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

TENANT_TABLES = ("decision_parties",)
HYBRID_TABLES = ()


def _may_fence(bind, table):
    present = bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar()
    if not present:
        return False
    return bool(bind.execute(
        text("SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
             "FROM pg_class c WHERE c.oid = to_regclass(:t)"),
        {"t": f"public.{table}"},
    ).scalar())


def _fence(bind, table):
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}"))  # nosec B608 - table names from a literal constant
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - table names from a literal constant


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if (not inspector.has_table("decision_parties")
            and inspector.has_table("architecture_decisions")
            and inspector.has_table("archimate_elements")):
        op.create_table(
            "decision_parties",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("decision_id", sa.Integer(),
                      sa.ForeignKey("architecture_decisions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("element_id", sa.Integer(),
                      sa.ForeignKey("archimate_elements.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("decision_id", "element_id", "role", name="uq_decision_party_role"),
        )
    if sa.inspect(bind).has_table("decision_parties"):
        for col in ("organization_id", "decision_id", "element_id"):
            op.execute(f"CREATE INDEX IF NOT EXISTS ix_decision_parties_{col} ON decision_parties ({col})")
    for table in TENANT_TABLES:
        if _may_fence(bind, table):
            _fence(bind, table)
            print(f"row-level security: fenced {table}")
        else:
            print(f"row-level security: skipped {table}")


def downgrade():
    op.execute("DROP TABLE IF EXISTS public.decision_parties")
