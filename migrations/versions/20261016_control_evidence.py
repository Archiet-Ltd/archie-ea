"""Evidence validity on an adopted control, and control tests.

Adds four nullable columns to ``application_compliance_controls`` (when the
evidence was collected, until when it is valid, whether a person or a connector
recorded it, and who owns renewing it) and creates ``control_tests``, one row
per design or operating test of an adopted control. Builds what ``create_all()``
builds from the models, so a database upgraded through Alembic and one built by
``init-db`` have the same shape.

Idempotent: each column and table is added only when absent, so a database
where ``init-db`` or ``reconcile-schema`` got there first is a no-op. The
row-level-security fence for ``control_tests`` is the next revision.

Revision ID: 20261016_control_evidence
Revises: 20261016_compliance_org_rls
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261016_control_evidence"
down_revision = "20261016_compliance_org_rls"
branch_labels = None
depends_on = None

_EVIDENCE_COLUMNS = (
    ("evidence_collected_at", lambda: sa.Column("evidence_collected_at", sa.DateTime())),
    ("evidence_valid_until", lambda: sa.Column("evidence_valid_until", sa.DateTime())),
    ("evidence_source", lambda: sa.Column("evidence_source", sa.String(20))),
    (
        "evidence_owner_id",
        lambda: sa.Column(
            "evidence_owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
    ),
)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("application_compliance_controls"):
        present = {c["name"] for c in inspector.get_columns("application_compliance_controls")}
        for name, build in _EVIDENCE_COLUMNS:
            if name not in present:
                op.add_column("application_compliance_controls", build())

    if not inspector.has_table("control_tests"):
        op.create_table(
            "control_tests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "application_control_id",
                sa.Integer(),
                sa.ForeignKey("application_compliance_controls.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("test_kind", sa.String(20), nullable=False),
            sa.Column("result", sa.String(20), nullable=False),
            sa.Column("tested_at", sa.DateTime(), nullable=False),
            sa.Column("tested_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("notes", sa.Text()),
            sa.Column("evidence_url", sa.String(500)),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_control_tests_application_control_id "
        "ON control_tests (application_control_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_control_tests_organization_id ON control_tests (organization_id)")


def downgrade():
    # Expand-only: the table and columns hold recorded tests and evidence, so
    # they are left in place rather than dropped.
    pass
