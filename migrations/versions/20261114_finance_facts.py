"""finance_facts table: dated money facts, fenced by organisation.

Creates ``finance_facts`` (cash, spending, revenue, funding and paying customers, each a dated fact
with its source and who stated it) and fences it with the same row-level security policies as the
other tenant tables, forced for the owner role as well, in this one revision. A stored row is never
rewritten; the application refuses every change except pointing a replaced fact at its replacement.
Imports nothing from ``app``.

Idempotent: the table and index are created only when absent; policies are dropped and recreated.

Revision ID: 20261114_finance_facts
Revises: 20261114_subscription_offer
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261114_finance_facts"
down_revision = "20261114_subscription_offer"
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

TABLE = "finance_facts"
INDEX = "ix_finance_facts_org_measure_subject_as_of"
MEASURES = ("cash_balance", "revenue", "spend", "funding_in", "paying_customers")
KINDS = ("actual", "forecast")
SOURCE_KINDS = ("person", "import")


def _in_list(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


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
    bind.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))  # nosec B608 - literal table name


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE not in inspector.get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("measure", sa.String(32), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False, server_default="actual"),
            sa.Column(
                "subject_element_id", sa.Integer(), sa.ForeignKey("archimate_elements.id", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column("amount", sa.Numeric(18, 4), nullable=True),
            sa.Column("currency", sa.String(3), nullable=True),
            sa.Column("unavailable_reason", sa.Text(), nullable=True),
            sa.Column("as_of", sa.Date(), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=True),
            sa.Column("period_end", sa.Date(), nullable=True),
            sa.Column("source_kind", sa.String(16), nullable=False, server_default="person"),
            sa.Column("source_ref", sa.String(512), nullable=False),
            sa.Column("stated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column(
                "assumes_element_id", sa.Integer(), sa.ForeignKey("archimate_elements.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "superseded_by_id", sa.Integer(), sa.ForeignKey("finance_facts.id", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column("recorded_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(_in_list("measure", MEASURES), name="ck_finance_fact_measure"),
            sa.CheckConstraint(_in_list("kind", KINDS), name="ck_finance_fact_kind"),
            sa.CheckConstraint(_in_list("source_kind", SOURCE_KINDS), name="ck_finance_fact_source_kind"),
            sa.CheckConstraint(
                "amount IS NOT NULL OR length(btrim(unavailable_reason)) > 0", name="ck_finance_fact_amount_or_reason"
            ),
            sa.CheckConstraint("length(btrim(source_ref)) > 0", name="ck_finance_fact_source"),
        )
    existing_indexes = {i["name"] for i in inspect(bind).get_indexes(TABLE)}
    if INDEX not in existing_indexes:
        op.create_index(INDEX, TABLE, ["organization_id", "measure", "subject_element_id", "as_of"])

    if bind.dialect.name == "postgresql":
        _fence(bind, TABLE)


def downgrade():
    inspector = inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        op.drop_table(TABLE)
