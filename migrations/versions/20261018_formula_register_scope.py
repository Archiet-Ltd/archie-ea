"""Scoring configurations become shared-or-owned; scores say which one they used.

``scoring_configurations`` had no organisation: one row set the weights for
every tenant and any of them could be read as the platform's. It gains a
nullable ``organization_id`` with the shared-reference meaning used elsewhere
(``NULL`` = shared by every organisation, changeable only by a platform
administrator). Every existing row keeps ``NULL``, so no organisation's
scores change when this runs.

``application_rationalization_scores`` gains a nullable
``scoring_configuration_id`` naming the configuration a score was computed
with. Scores written before this column existed cannot be attributed
reliably, so they stay ``NULL``; they keep the ``scoring_model_version`` they
already recorded.

The code no longer creates a default weighting on first read. A database with
no configuration at all (a fresh install) therefore gets the one shared
baseline row here, once; a database that has any row is left untouched.

Idempotent: every step checks before it acts.

Revision ID: 20261018_formula_register_scope
Revises: 20261016_compliance_org_rls
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261018_formula_register_scope"
down_revision = "20261016_compliance_org_rls"
branch_labels = None
depends_on = None


def _present(bind, table):
    return bool(bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())


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

    if _present(bind, "scoring_configurations"):
        if not _has_column(bind, "scoring_configurations", "organization_id"):
            op.add_column(
                "scoring_configurations",
                sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
            )
        op.execute("CREATE INDEX IF NOT EXISTS ix_scoring_configurations_organization_id ON scoring_configurations (organization_id)")
        empty = not bind.execute(text("SELECT EXISTS (SELECT 1 FROM scoring_configurations)")).scalar()
        if empty:
            bind.execute(
                text(
                    "INSERT INTO scoring_configurations (name, description, scope_type, "
                    "technical_health_weight, business_value_weight, cost_efficiency_weight, vendor_risk_weight, "
                    "eliminate_threshold, migrate_technical_threshold, migrate_business_threshold, "
                    "invest_business_threshold, invest_technical_threshold, tolerate_min_threshold, "
                    "strategic_critical_boost, strategic_high_boost, critical_dependency_penalty, "
                    "high_dependency_penalty, is_active, is_default, configuration_version, created_at, updated_at) "
                    "VALUES ('CIO.gov Federal Baseline', 'Default weights per CIO.gov Application Rationalization Playbook', "
                    "'global', 30, 35, 25, 10, 40, 40, 50, 70, 50, 40, 10, 5, 20, 10, true, true, 1, now(), now())"
                )
            )

    if _present(bind, "application_rationalization_scores") and _present(bind, "scoring_configurations"):
        if not _has_column(bind, "application_rationalization_scores", "scoring_configuration_id"):
            op.add_column(
                "application_rationalization_scores",
                sa.Column(
                    "scoring_configuration_id",
                    sa.Integer(),
                    sa.ForeignKey("scoring_configurations.id", ondelete="SET NULL"),
                    nullable=True,
                ),
            )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_application_rationalization_scores_scoring_configuration_id "
            "ON application_rationalization_scores (scoring_configuration_id)"
        )


def downgrade():
    bind = op.get_bind()
    if _present(bind, "application_rationalization_scores") and _has_column(
        bind, "application_rationalization_scores", "scoring_configuration_id"
    ):
        op.execute("DROP INDEX IF EXISTS ix_application_rationalization_scores_scoring_configuration_id")
        op.drop_column("application_rationalization_scores", "scoring_configuration_id")
    if _present(bind, "scoring_configurations") and _has_column(bind, "scoring_configurations", "organization_id"):
        op.execute("DROP INDEX IF EXISTS ix_scoring_configurations_organization_id")
        op.drop_column("scoring_configurations", "organization_id")
