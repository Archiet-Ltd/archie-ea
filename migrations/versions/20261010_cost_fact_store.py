"""One cost fact store, a dated exchange rate table, and a reporting currency per organisation.

Expand step only: adds the ``cost_facts`` and ``exchange_rates`` tables and a
nullable ``organizations.reporting_currency`` column. Nothing existing is read
differently, altered or dropped; the backfill command fills the store from the
existing cost records afterwards.

Idempotent: every table, column, index and constraint is created only when
absent, so a second run (or a database where ``init-db`` got there first) is a
no-op.

Revision ID: 20261010_cost_fact_store
Revises: 20261010_arb_change_requests_rls
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261010_cost_fact_store"
down_revision = "20261010_arb_change_requests_rls"
branch_labels = None
depends_on = None


def _columns(inspector, table):
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("organizations") and "reporting_currency" not in _columns(
            inspector, "organizations"):
        op.add_column("organizations", sa.Column("reporting_currency", sa.String(3), nullable=True))

    if not inspector.has_table("cost_facts"):
        op.create_table(
            "cost_facts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("element_type", sa.String(32), nullable=False),
            sa.Column("element_id", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(48), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("amount", sa.Numeric(18, 4), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False),
            sa.Column("period", sa.String(16), nullable=False),
            sa.Column("period_start", sa.Date()),
            sa.Column("period_end", sa.Date()),
            sa.Column("source", sa.String(48), nullable=False),
            sa.Column("source_table", sa.String(64)),
            sa.Column("source_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint("organization_id", "element_type", "element_id", "category",
                                "kind", "source", "source_id", name="uq_cost_fact_identity"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_cost_facts_organization_id "
               "ON cost_facts (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cost_fact_element "
               "ON cost_facts (organization_id, element_type, element_id)")

    if not inspector.has_table("exchange_rates"):
        op.create_table(
            "exchange_rates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("from_currency", sa.String(3), nullable=False),
            sa.Column("to_currency", sa.String(3), nullable=False),
            sa.Column("rate", sa.Numeric(18, 8), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column("source", sa.String(100)),
            sa.Column("created_at", sa.DateTime()),
            sa.UniqueConstraint("from_currency", "to_currency", "effective_date",
                                name="uq_exchange_rate_day"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_exchange_rates_from_currency "
               "ON exchange_rates (from_currency)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exchange_rates_to_currency "
               "ON exchange_rates (to_currency)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS exchange_rates")
    op.execute("DROP TABLE IF EXISTS cost_facts")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS reporting_currency")
