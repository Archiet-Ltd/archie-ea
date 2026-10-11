"""
Solution Outcomes Tracking Models Migration

Revision ID: solution_outcomes_models
Revises: merge_arb_and_workspace
Create Date: 2026-01-27 10:00:00.000000

Creates tables for:
- solution_outcomes: Track predicted vs actual outcomes
- solution_outcome_measurements: Time-series measurements
- solution_benefit_realizations: Aggregate benefits reporting
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "solution_outcomes_models"
down_revision = "merge_arb_and_workspace"
branch_labels = None
depends_on = None


def upgrade():
    # Define enums
    outcome_type = postgresql.ENUM(
        "cost",
        "timeline",
        "quality",
        "capability",
        "risk",
        "benefit",
        name="outcometype",
        create_type=False,
    )
    tracking_status = postgresql.ENUM(
        "not_started",
        "in_progress",
        "achieved",
        "missed",
        "exceeded",
        name="trackingstatus",
        create_type=False,
    )
    realization_status = postgresql.ENUM(
        "on_track", "at_risk", "off_track", "exceeded", name="realizationstatus", create_type=False
    )

    # Create enums first
    outcome_type.create(op.get_bind(), checkfirst=True)
    tracking_status.create(op.get_bind(), checkfirst=True)
    realization_status.create(op.get_bind(), checkfirst=True)

    # =========================================================================
    # Create solution_outcomes table
    # =========================================================================
    op.create_table(
        "solution_outcomes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("solution_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        # Outcome identification
        sa.Column("outcome_type", outcome_type, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # Predicted values (from recommendation)
        sa.Column("predicted_value", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("predicted_unit", sa.String(length=50), nullable=True),
        sa.Column("predicted_date", sa.Date(), nullable=True),
        sa.Column("prediction_confidence", sa.Float(), nullable=True),
        # Actual values (tracked over time)
        sa.Column("actual_value", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("actual_date", sa.Date(), nullable=True),
        sa.Column("variance_percentage", sa.Float(), nullable=True),
        sa.Column("variance_explanation", sa.Text(), nullable=True),
        # Status tracking
        sa.Column("tracking_status", tracking_status, nullable=False),
        sa.Column("last_measured_at", sa.DateTime(), nullable=True),
        sa.Column("next_measurement_date", sa.Date(), nullable=True),
        # Metadata
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        # Foreign keys
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["solution_analysis_sessions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for solution_outcomes
    op.create_index("idx_outcome_solution", "solution_outcomes", ["solution_id"])
    op.create_index("idx_outcome_session", "solution_outcomes", ["session_id"])
    op.create_index("idx_outcome_type", "solution_outcomes", ["outcome_type"])
    op.create_index("idx_outcome_status", "solution_outcomes", ["tracking_status"])
    op.create_index("idx_outcome_next_measurement", "solution_outcomes", ["next_measurement_date"])

    # =========================================================================
    # Create solution_outcome_measurements table
    # =========================================================================
    op.create_table(
        "solution_outcome_measurements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outcome_id", sa.Integer(), nullable=False),
        # Measurement data
        sa.Column("measured_value", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("measured_at", sa.DateTime(), nullable=False),
        sa.Column("measured_by_id", sa.Integer(), nullable=False),
        # Context
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("evidence_links", sa.JSON(), nullable=True),
        # Foreign keys
        sa.ForeignKeyConstraint(["outcome_id"], ["solution_outcomes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["measured_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for solution_outcome_measurements
    op.create_index("idx_measurement_outcome", "solution_outcome_measurements", ["outcome_id"])
    op.create_index("idx_measurement_date", "solution_outcome_measurements", ["measured_at"])

    # =========================================================================
    # Create solution_benefit_realizations table
    # =========================================================================
    op.create_table(
        "solution_benefit_realizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("solution_id", sa.Integer(), nullable=False),
        # Reporting period
        sa.Column("reporting_period_start", sa.Date(), nullable=False),
        sa.Column("reporting_period_end", sa.Date(), nullable=False),
        # Financial metrics
        sa.Column("planned_cost_savings", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("actual_cost_savings", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("planned_revenue_impact", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("actual_revenue_impact", sa.Numeric(precision=15, scale=2), nullable=True),
        # Non-financial metrics
        sa.Column("planned_efficiency_gain_percent", sa.Float(), nullable=True),
        sa.Column("actual_efficiency_gain_percent", sa.Float(), nullable=True),
        sa.Column("planned_quality_improvement_percent", sa.Float(), nullable=True),
        sa.Column("actual_quality_improvement_percent", sa.Float(), nullable=True),
        # Overall assessment
        sa.Column("realization_score", sa.Float(), nullable=True),
        sa.Column("status", realization_status, nullable=False),
        sa.Column("executive_summary", sa.Text(), nullable=True),
        # Additional metrics (flexible)
        sa.Column("additional_metrics", sa.JSON(), nullable=True),
        # Metadata
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        # Foreign keys
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        # Unique constraint for solution + period
        sa.UniqueConstraint(
            "solution_id",
            "reporting_period_start",
            "reporting_period_end",
            name="uq_solution_benefit_period",
        ),
    )

    # Create indexes for solution_benefit_realizations
    op.create_index("idx_benefit_solution", "solution_benefit_realizations", ["solution_id"])
    op.create_index(
        "idx_benefit_period",
        "solution_benefit_realizations",
        ["reporting_period_start", "reporting_period_end"],
    )
    op.create_index("idx_benefit_status", "solution_benefit_realizations", ["status"])


def downgrade():
    # Drop tables in reverse order (respecting foreign key dependencies)

    # Drop solution_benefit_realizations
    op.drop_index("idx_benefit_status", table_name="solution_benefit_realizations")
    op.drop_index("idx_benefit_period", table_name="solution_benefit_realizations")
    op.drop_index("idx_benefit_solution", table_name="solution_benefit_realizations")
    op.drop_table("solution_benefit_realizations")

    # Drop solution_outcome_measurements
    op.drop_index("idx_measurement_date", table_name="solution_outcome_measurements")
    op.drop_index("idx_measurement_outcome", table_name="solution_outcome_measurements")
    op.drop_table("solution_outcome_measurements")

    # Drop solution_outcomes
    op.drop_index("idx_outcome_next_measurement", table_name="solution_outcomes")
    op.drop_index("idx_outcome_status", table_name="solution_outcomes")
    op.drop_index("idx_outcome_type", table_name="solution_outcomes")
    op.drop_index("idx_outcome_session", table_name="solution_outcomes")
    op.drop_index("idx_outcome_solution", table_name="solution_outcomes")
    op.drop_table("solution_outcomes")

    # Drop enums
    realization_status = postgresql.ENUM(
        "on_track", "at_risk", "off_track", "exceeded", name="realizationstatus"
    )
    realization_status.drop(op.get_bind(), checkfirst=True)

    tracking_status = postgresql.ENUM(
        "not_started", "in_progress", "achieved", "missed", "exceeded", name="trackingstatus"
    )
    tracking_status.drop(op.get_bind(), checkfirst=True)

    outcome_type = postgresql.ENUM(
        "cost", "timeline", "quality", "capability", "risk", "benefit", name="outcometype"
    )
    outcome_type.drop(op.get_bind(), checkfirst=True)
