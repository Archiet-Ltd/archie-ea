"""Model-health findings can be dismissed with a recorded reason.

``drift_reports`` gains a nullable ``dismissals`` JSON column holding, per
finding key, the reason a person gave, who gave it and when. It is kept apart
from the stored report so a re-scan, which replaces the report, never discards a
decision. Existing rows get ``NULL`` (nothing dismissed), so every organisation's
report reads exactly as before.

Idempotent: the column is added only when it is missing.

Revision ID: 20261018_drift_report_dismissals
Revises: 20261016_compliance_org_rls
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261018_drift_report_dismissals"
down_revision = "20261016_compliance_org_rls"
branch_labels = None
depends_on = None


def _has_column(bind, table, column):
    return bool(
        bind.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c)"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text("SELECT to_regclass('public.drift_reports') IS NOT NULL")).scalar():
        return
    if not _has_column(bind, "drift_reports", "dismissals"):
        op.add_column("drift_reports", sa.Column("dismissals", sa.JSON(), nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.execute(text("SELECT to_regclass('public.drift_reports') IS NOT NULL")).scalar() and _has_column(
        bind, "drift_reports", "dismissals"
    ):
        op.drop_column("drift_reports", "dismissals")
