"""Create the ADM board-portfolio tables.

``adm_board_portfolios``, ``adm_board_portfolio_links``, ``adm_rida_logs`` and
``adm_cross_board_dependencies`` are declared by the ADM portfolio models but
were never imported, so ``create_all()`` skipped them. This revision builds
exactly what ``create_all()`` builds from those models (no server defaults, no
extra indexes) so a database upgraded through Alembic and one built by
``init-db`` have the same shape.

Idempotent: each table is created only when absent and indexes use
``IF NOT EXISTS``, so a database where ``init-db`` got there first is a no-op.

Revision ID: 20261015_adm_portfolio_tables
Revises: 20261013_answer_records_rls
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261015_adm_portfolio_tables"
down_revision = "20261013_answer_records_rls"
branch_labels = None
depends_on = None


def _org_column():
    return sa.Column("organization_id", sa.Integer(),
                     sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("adm_board_portfolios"):
        op.create_table(
            "adm_board_portfolios",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("portfolio_code", sa.String(50), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("scope_type", sa.String(50)),
            sa.Column("business_domain", sa.String(100)),
            sa.Column("status", sa.String(50)),
            sa.Column("start_date", sa.DateTime()),
            sa.Column("target_end_date", sa.DateTime()),
            sa.Column("actual_end_date", sa.DateTime()),
            sa.Column("portfolio_owner_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("portfolio_manager_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("strategic_objectives", sa.JSON()),
            sa.Column("success_metrics", sa.JSON()),
            sa.Column("business_capabilities", sa.JSON()),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
            _org_column(),
            sa.UniqueConstraint("portfolio_code"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_adm_board_portfolios_organization_id "
               "ON adm_board_portfolios (organization_id)")

    if not inspector.has_table("adm_board_portfolio_links"):
        op.create_table(
            "adm_board_portfolio_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("portfolio_id", sa.Integer(),
                      sa.ForeignKey("adm_board_portfolios.id"), nullable=False),
            sa.Column("board_id", sa.Integer(), sa.ForeignKey("kanban_boards.id"), nullable=False),
            sa.Column("board_role", sa.String(50)),
            sa.Column("contribution_type", sa.String(50)),
            sa.Column("priority", sa.String(20)),
            sa.Column("depends_on_board_ids", sa.JSON()),
            sa.Column("added_at", sa.DateTime()),
            sa.Column("added_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        )

    if not inspector.has_table("adm_rida_logs"):
        op.create_table(
            "adm_rida_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("rida_code", sa.String(50), nullable=False),
            sa.Column("rida_type", sa.String(20), nullable=False),
            sa.Column("severity", sa.String(20)),
            sa.Column("status", sa.String(20)),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("impact", sa.Text()),
            sa.Column("mitigation", sa.Text()),
            sa.Column("rationale", sa.Text()),
            sa.Column("validity_period", sa.Text()),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("adm_board_portfolios.id")),
            sa.Column("board_id", sa.Integer(), sa.ForeignKey("kanban_boards.id")),
            sa.Column("card_id", sa.Integer(), sa.ForeignKey("kanban_cards.id")),
            sa.Column("phase_id", sa.Integer(), sa.ForeignKey("adm_phases.id")),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("assigned_to_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("stakeholder_ids", sa.JSON()),
            sa.Column("resolution", sa.Text()),
            sa.Column("resolved_at", sa.DateTime()),
            sa.Column("resolved_by_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("related_rida_ids", sa.JSON()),
            sa.Column("related_deliverable_ids", sa.JSON()),
            sa.Column("arb_review_item_id", sa.Integer(), sa.ForeignKey("arb_review_items.id")),
            sa.Column("identified_at", sa.DateTime()),
            sa.Column("target_resolution_date", sa.DateTime()),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
            _org_column(),
            sa.UniqueConstraint("rida_code"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_adm_rida_logs_organization_id "
               "ON adm_rida_logs (organization_id)")

    if not inspector.has_table("adm_cross_board_dependencies"):
        op.create_table(
            "adm_cross_board_dependencies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source_board_id", sa.Integer(), sa.ForeignKey("kanban_boards.id"), nullable=False),
            sa.Column("source_card_id", sa.Integer(), sa.ForeignKey("kanban_cards.id"), nullable=False),
            sa.Column("target_board_id", sa.Integer(), sa.ForeignKey("kanban_boards.id"), nullable=False),
            sa.Column("target_card_id", sa.Integer(), sa.ForeignKey("kanban_cards.id"), nullable=False),
            sa.Column("dependency_type", sa.String(50)),
            sa.Column("is_blocking", sa.Boolean()),
            sa.Column("is_critical", sa.Boolean()),
            sa.Column("status", sa.String(50)),
            sa.Column("description", sa.Text()),
            sa.Column("resolution_notes", sa.Text()),
            sa.Column("resolved_at", sa.DateTime()),
            sa.Column("resolved_by_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
            _org_column(),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_adm_cross_board_dependencies_organization_id "
               "ON adm_cross_board_dependencies (organization_id)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS adm_cross_board_dependencies")
    op.execute("DROP TABLE IF EXISTS adm_rida_logs")
    op.execute("DROP TABLE IF EXISTS adm_board_portfolio_links")
    op.execute("DROP TABLE IF EXISTS adm_board_portfolios")
