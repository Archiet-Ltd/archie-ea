"""Control tests and evidence validity on adopted compliance controls.

Expand step only: creates the ``control_tests`` table and adds four nullable
columns to ``application_compliance_controls`` (when the evidence was
collected, until when it holds, its source, and who renews it). Nothing
existing is read differently, altered or dropped. Idempotent: each table,
column and index is created only when absent.

Revision ID: 20261016_control_tests
Revises: 20261016_technical_debt_rls
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261016_control_tests"
down_revision = "20261016_technical_debt_rls"
branch_labels = None
depends_on = None

_EVIDENCE_COLUMNS = (
    ("evidence_collected_at", sa.DateTime()),
    ("evidence_valid_until", sa.DateTime()),
    ("evidence_source", sa.String(20)),
)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("application_compliance_controls"):
        existing = {c["name"] for c in inspector.get_columns("application_compliance_controls")}
        for name, column_type in _EVIDENCE_COLUMNS:
            if name not in existing:
                op.add_column("application_compliance_controls", sa.Column(name, column_type))
        if "evidence_owner_id" not in existing:
            op.add_column(
                "application_compliance_controls",
                sa.Column(
                    "evidence_owner_id",
                    sa.Integer(),
                    sa.ForeignKey("users.id", ondelete="SET NULL"),
                ),
            )

    if not inspector.has_table("control_tests"):
        op.create_table(
            "control_tests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("application_control_id", sa.Integer(),
                      sa.ForeignKey("application_compliance_controls.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("test_kind", sa.String(20), nullable=False),
            sa.Column("result", sa.String(20), nullable=False),
            sa.Column("tested_at", sa.DateTime(), nullable=False),
            sa.Column("tested_by_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("notes", sa.Text()),
            sa.Column("evidence_url", sa.String(500)),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_control_tests_organization_id "
               "ON control_tests (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_control_tests_application_control_id "
               "ON control_tests (application_control_id)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS control_tests")
    for name in ("evidence_owner_id", "evidence_source", "evidence_valid_until", "evidence_collected_at"):
        op.execute(
            f"ALTER TABLE IF EXISTS application_compliance_controls DROP COLUMN IF EXISTS {name}"  # nosec B608 - literal names
        )
