"""Versioned policies and the binding of a policy clause to a compliance control.

Expand step only: creates the ``policy_clause_controls`` table and adds four
nullable columns to ``architecture_policies`` (version number, when it was
published, by whom, and the version it supersedes). Nothing existing is read
differently, altered or dropped. Idempotent: each table, column and index is
created only when absent.

Revision ID: 20261016_policy_clauses
Revises: 20261016_control_tests_rls
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261016_policy_clauses"
down_revision = "20261016_control_tests_rls"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("architecture_policies"):
        existing = {c["name"] for c in inspector.get_columns("architecture_policies")}
        if "version_number" not in existing:
            op.add_column("architecture_policies", sa.Column("version_number", sa.Integer()))
        if "published_at" not in existing:
            op.add_column("architecture_policies", sa.Column("published_at", sa.DateTime()))
        if "published_by_id" not in existing:
            op.add_column(
                "architecture_policies",
                sa.Column("published_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            )
        if "supersedes_id" not in existing:
            op.add_column(
                "architecture_policies",
                sa.Column(
                    "supersedes_id", sa.Integer(),
                    sa.ForeignKey("architecture_policies.id", ondelete="SET NULL"),
                ),
            )

    if not inspector.has_table("policy_clause_controls"):
        op.create_table(
            "policy_clause_controls",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("policy_id", sa.Integer(),
                      sa.ForeignKey("architecture_policies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("clause_ref", sa.String(50), nullable=False),
            sa.Column("clause_text", sa.Text()),
            sa.Column("control_id", sa.Integer(),
                      sa.ForeignKey("compliance_controls.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("organization_id", "policy_id", "clause_ref", "control_id",
                                name="uq_policy_clause_control"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_clause_controls_organization_id "
               "ON policy_clause_controls (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_clause_controls_policy_id "
               "ON policy_clause_controls (policy_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_policy_clause_controls_control_id "
               "ON policy_clause_controls (control_id)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS policy_clause_controls")
    for name in ("supersedes_id", "published_by_id", "published_at", "version_number"):
        op.execute(
            f"ALTER TABLE IF EXISTS architecture_policies DROP COLUMN IF EXISTS {name}"  # nosec B608 - literal names
        )
