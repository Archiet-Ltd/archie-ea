"""AI evaluation plans and runs (R1-B55): ai_evaluation_plan and ai_evaluation_run.

Expand step only: two new tables; nothing existing is read differently, altered
or dropped. Idempotent -- every table and index is created only when absent, so
a second run, or a database where ``init-db`` got there first, is a no-op. The
nullable ``architecture_decisions.ai_system_id`` / ``ai_design_review`` columns
need no revision: ``reconcile-schema`` adds them (ADR-0002).

Revision ID: 20261013_ai_evaluation
Revises: 20261011_notif_rls
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261013_ai_evaluation"
down_revision = "20261011_notif_rls"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("ai_evaluation_plan"):
        op.create_table(
            "ai_evaluation_plan",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ai_system_id", sa.Integer(),
                      sa.ForeignKey("archimate_elements.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("test_set_name", sa.String(200), nullable=False),
            sa.Column("test_set_size", sa.Integer(), nullable=False),
            sa.Column("thresholds", sa.JSON(), nullable=False),
            sa.Column("created_by_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint("organization_id", "ai_system_id",
                                name="uq_ai_evaluation_plan_org_system"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_evaluation_plan_organization_id "
               "ON ai_evaluation_plan (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_evaluation_plan_ai_system_id "
               "ON ai_evaluation_plan (ai_system_id)")

    if not inspector.has_table("ai_evaluation_run"):
        op.create_table(
            "ai_evaluation_run",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ai_system_id", sa.Integer(),
                      sa.ForeignKey("archimate_elements.id", ondelete="CASCADE"), nullable=False),
            sa.Column("plan_id", sa.Integer(),
                      sa.ForeignKey("ai_evaluation_plan.id", ondelete="CASCADE"), nullable=False),
            sa.Column("plan_version", sa.Integer(), nullable=False),
            sa.Column("model_id", sa.String(200), nullable=True),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("cases_run", sa.Integer(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("recorded_by_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_evaluation_run_organization_id "
               "ON ai_evaluation_run (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_evaluation_run_ai_system_id "
               "ON ai_evaluation_run (ai_system_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_evaluation_run_plan_id "
               "ON ai_evaluation_run (plan_id)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS ai_evaluation_run")
    op.execute("DROP TABLE IF EXISTS ai_evaluation_plan")
