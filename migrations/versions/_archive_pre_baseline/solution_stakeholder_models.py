"""
Solution Stakeholder Models - ArchiMate 3.2 Stakeholder Implementation

Revision ID: solution_stakeholder_models
Revises: merge_arb_and_workspace
Create Date: 2026-01-27 10:00:00.000000

Creates tables for:
- solution_stakeholders: Core stakeholder entity with power/interest grid
- solution_stakeholder_concerns: Specific stakeholder concerns
- solution_stakeholder_mappings: Junction table linking to solutions/sessions
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "solution_stakeholder_models"
down_revision = "merge_arb_and_workspace"  # Latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Define enums
    stakeholder_type = postgresql.ENUM(
        "individual", "group", "organization", "role", name="stakeholdertype", create_type=False
    )
    stakeholder_attitude = postgresql.ENUM(
        "champion",
        "supporter",
        "neutral",
        "skeptic",
        "blocker",
        name="stakeholderattitude",
        create_type=False,
    )
    concern_type = postgresql.ENUM(
        "cost",
        "timeline",
        "quality",
        "risk",
        "compliance",
        "capability",
        "integration",
        name="concerntype",
        create_type=False,
    )
    stakeholder_role = postgresql.ENUM(
        "sponsor",
        "owner",
        "contributor",
        "reviewer",
        "informed",
        "consulted",
        name="stakeholderrole",
        create_type=False,
    )
    engagement_level = postgresql.ENUM(
        "high", "medium", "low", name="engagementlevel", create_type=False
    )
    communication_preference = postgresql.ENUM(
        "detailed", "summary", "exceptions_only", name="communicationpreference", create_type=False
    )

    # Create enums first
    stakeholder_type.create(op.get_bind(), checkfirst=True)
    stakeholder_attitude.create(op.get_bind(), checkfirst=True)
    concern_type.create(op.get_bind(), checkfirst=True)
    stakeholder_role.create(op.get_bind(), checkfirst=True)
    engagement_level.create(op.get_bind(), checkfirst=True)
    communication_preference.create(op.get_bind(), checkfirst=True)

    # =========================================================================
    # Create solution_stakeholders table
    # =========================================================================
    op.create_table(
        "solution_stakeholders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stakeholder_type", stakeholder_type, nullable=False),
        sa.Column("influence_level", sa.Integer(), nullable=True, default=3),
        sa.Column("interest_level", sa.Integer(), nullable=True, default=3),
        sa.Column("attitude", stakeholder_attitude, nullable=True),
        sa.Column("concerns", sa.JSON(), nullable=True),
        sa.Column("contact_info", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_stakeholder_type", "solution_stakeholders", ["stakeholder_type"])
    op.create_index("idx_stakeholder_influence", "solution_stakeholders", ["influence_level"])
    op.create_index("idx_stakeholder_interest", "solution_stakeholders", ["interest_level"])
    op.create_index("idx_stakeholder_attitude", "solution_stakeholders", ["attitude"])

    # =========================================================================
    # Create solution_stakeholder_concerns table
    # =========================================================================
    op.create_table(
        "solution_stakeholder_concerns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stakeholder_id", sa.Integer(), nullable=False),
        sa.Column("concern_type", concern_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True, default=3),
        sa.Column("is_addressed", sa.Boolean(), nullable=True, default=False),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("addressed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["stakeholder_id"], ["solution_stakeholders.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_concern_stakeholder", "solution_stakeholder_concerns", ["stakeholder_id"])
    op.create_index("idx_concern_type", "solution_stakeholder_concerns", ["concern_type"])
    op.create_index("idx_concern_priority", "solution_stakeholder_concerns", ["priority"])
    op.create_index("idx_concern_addressed", "solution_stakeholder_concerns", ["is_addressed"])

    # =========================================================================
    # Create solution_stakeholder_mappings table
    # =========================================================================
    op.create_table(
        "solution_stakeholder_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stakeholder_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("solution_id", sa.Integer(), nullable=True),
        sa.Column("role", stakeholder_role, nullable=False),
        sa.Column("engagement_level", engagement_level, nullable=True),
        sa.Column("communication_preference", communication_preference, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["stakeholder_id"], ["solution_stakeholders.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["solution_analysis_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stakeholder_id", "session_id", name="uq_stakeholder_session"),
        sa.UniqueConstraint("stakeholder_id", "solution_id", name="uq_stakeholder_solution"),
    )
    op.create_index("idx_mapping_stakeholder", "solution_stakeholder_mappings", ["stakeholder_id"])
    op.create_index("idx_mapping_session", "solution_stakeholder_mappings", ["session_id"])
    op.create_index("idx_mapping_solution", "solution_stakeholder_mappings", ["solution_id"])
    op.create_index("idx_mapping_role", "solution_stakeholder_mappings", ["role"])


def downgrade():
    # Drop tables in reverse order of creation
    op.drop_index("idx_mapping_role", table_name="solution_stakeholder_mappings")
    op.drop_index("idx_mapping_solution", table_name="solution_stakeholder_mappings")
    op.drop_index("idx_mapping_session", table_name="solution_stakeholder_mappings")
    op.drop_index("idx_mapping_stakeholder", table_name="solution_stakeholder_mappings")
    op.drop_table("solution_stakeholder_mappings")

    op.drop_index("idx_concern_addressed", table_name="solution_stakeholder_concerns")
    op.drop_index("idx_concern_priority", table_name="solution_stakeholder_concerns")
    op.drop_index("idx_concern_type", table_name="solution_stakeholder_concerns")
    op.drop_index("idx_concern_stakeholder", table_name="solution_stakeholder_concerns")
    op.drop_table("solution_stakeholder_concerns")

    op.drop_index("idx_stakeholder_attitude", table_name="solution_stakeholders")
    op.drop_index("idx_stakeholder_interest", table_name="solution_stakeholders")
    op.drop_index("idx_stakeholder_influence", table_name="solution_stakeholders")
    op.drop_index("idx_stakeholder_type", table_name="solution_stakeholders")
    op.drop_table("solution_stakeholders")

    # Drop enums
    communication_preference = postgresql.ENUM(
        "detailed", "summary", "exceptions_only", name="communicationpreference"
    )
    communication_preference.drop(op.get_bind(), checkfirst=True)

    engagement_level = postgresql.ENUM("high", "medium", "low", name="engagementlevel")
    engagement_level.drop(op.get_bind(), checkfirst=True)

    stakeholder_role = postgresql.ENUM(
        "sponsor",
        "owner",
        "contributor",
        "reviewer",
        "informed",
        "consulted",
        name="stakeholderrole",
    )
    stakeholder_role.drop(op.get_bind(), checkfirst=True)

    concern_type = postgresql.ENUM(
        "cost",
        "timeline",
        "quality",
        "risk",
        "compliance",
        "capability",
        "integration",
        name="concerntype",
    )
    concern_type.drop(op.get_bind(), checkfirst=True)

    stakeholder_attitude = postgresql.ENUM(
        "champion", "supporter", "neutral", "skeptic", "blocker", name="stakeholderattitude"
    )
    stakeholder_attitude.drop(op.get_bind(), checkfirst=True)

    stakeholder_type = postgresql.ENUM(
        "individual", "group", "organization", "role", name="stakeholdertype"
    )
    stakeholder_type.drop(op.get_bind(), checkfirst=True)
