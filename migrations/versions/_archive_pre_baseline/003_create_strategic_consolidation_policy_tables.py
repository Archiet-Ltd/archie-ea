"""Create Strategic, Consolidation, and Policy Monitoring tables

Revision ID: 003_strategic_consolidation_policy
Revises: 002_derivation_audit
Create Date: 2026-01-18

This migration creates tables for:
1. Strategic Module (initiatives, milestones, roadmap items)
2. Consolidation Module (candidates, opportunities, savings)
3. Policy Monitoring Module (policies, violations, compliance, exemptions)
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "003_strategic_consolidation_policy"
down_revision = "002_derivation_audit"
branch_labels = None
depends_on = None


def upgrade():
    """Create all new module tables"""

    # === STRATEGIC MODULE TABLES ===

    # Strategic Initiatives table
    op.create_table(
        "strategic_initiatives",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("target_completion_date", sa.Date(), nullable=True),
        sa.Column("actual_completion_date", sa.Date(), nullable=True),
        sa.Column("budget_allocated", sa.Float(), nullable=True),
        sa.Column("budget_spent", sa.Float(), nullable=True, server_default="0"),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("business_value_score", sa.Integer(), nullable=True),
        sa.Column("risk_level", sa.String(20), nullable=True, server_default="medium"),
        sa.Column("strategic_alignment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Strategic Milestones table
    op.create_table(
        "strategic_milestones",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "initiative_id",
            sa.Integer(),
            sa.ForeignKey("strategic_initiatives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("deliverables", sa.Text(), nullable=True),
        sa.Column("dependencies", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Strategic Roadmap Items table
    op.create_table(
        "strategic_roadmap_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "initiative_id",
            sa.Integer(),
            sa.ForeignKey("strategic_initiatives.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("quarter", sa.String(10), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("lane", sa.String(50), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="planned"),
        sa.Column("effort_estimate", sa.String(20), nullable=True),
        sa.Column("dependencies", sa.Text(), nullable=True),
        sa.Column("linked_capabilities", sa.Text(), nullable=True),
        sa.Column("linked_applications", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # === CONSOLIDATION MODULE TABLES ===

    # Consolidation Candidates table
    op.create_table(
        "consolidation_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "primary_application_id",
            sa.Integer(),
            sa.ForeignKey("application_components.id"),
            nullable=False,
        ),
        sa.Column(
            "duplicate_application_id",
            sa.Integer(),
            sa.ForeignKey("application_components.id"),
            nullable=False,
        ),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("detection_method", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending_review"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Consolidation Opportunities table
    op.create_table(
        "consolidation_opportunities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="identified"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="medium"),
        sa.Column(
            "target_application_id",
            sa.Integer(),
            sa.ForeignKey("application_components.id"),
            nullable=True,
        ),
        sa.Column("source_applications", sa.Text(), nullable=True),
        sa.Column("estimated_annual_savings", sa.Float(), nullable=True),
        sa.Column("estimated_one_time_savings", sa.Float(), nullable=True),
        sa.Column("implementation_cost", sa.Float(), nullable=True),
        sa.Column("roi_percentage", sa.Float(), nullable=True),
        sa.Column("payback_period_months", sa.Integer(), nullable=True),
        sa.Column("risk_level", sa.String(20), nullable=True, server_default="medium"),
        sa.Column("complexity", sa.String(20), nullable=True, server_default="moderate"),
        sa.Column("business_impact", sa.Text(), nullable=True),
        sa.Column("technical_dependencies", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("target_completion_date", sa.Date(), nullable=True),
        sa.Column("actual_completion_date", sa.Date(), nullable=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Savings Realization table
    op.create_table(
        "savings_realizations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("consolidation_opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("realized_savings", sa.Float(), nullable=False),
        sa.Column("savings_category", sa.String(50), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("verified_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # === POLICY MONITORING MODULE TABLES ===

    # Architecture Policies table
    op.create_table(
        "architecture_policies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("policy_type", sa.String(50), nullable=False, server_default="recommended"),
        sa.Column("scope", sa.String(50), nullable=False, server_default="enterprise"),
        sa.Column("rule_definition", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("enforcement_level", sa.String(20), nullable=False, server_default="warning"),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("exemption_allowed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "exemption_approval_required", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Policy Violations table
    op.create_table(
        "policy_violations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "policy_id",
            sa.Integer(),
            sa.ForeignKey("architecture_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("entity_name", sa.String(256), nullable=True),
        sa.Column("violation_details", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(50), nullable=False, server_default="open"),
        sa.Column("detected_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("acknowledged_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("remediated_at", sa.DateTime(), nullable=True),
        sa.Column("remediation_notes", sa.Text(), nullable=True),
        sa.Column("exemption_reason", sa.Text(), nullable=True),
        sa.Column("exemption_approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("exemption_expiry", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Compliance Status table
    op.create_table(
        "compliance_statuses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("entity_name", sa.String(256), nullable=True),
        sa.Column("total_policies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compliant_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("violation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exemption_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compliance_percentage", sa.Float(), nullable=True),
        sa.Column("last_scan_at", sa.DateTime(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Policy Exemptions table
    op.create_table(
        "policy_exemptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "violation_id",
            sa.Integer(),
            sa.ForeignKey("policy_violations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("business_justification", sa.Text(), nullable=True),
        sa.Column("mitigation_plan", sa.Text(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # === CREATE INDEXES ===

    # Strategic module indexes
    op.create_index("ix_strategic_initiatives_status", "strategic_initiatives", ["status"])
    op.create_index("ix_strategic_initiatives_priority", "strategic_initiatives", ["priority"])
    op.create_index(
        "ix_strategic_milestones_initiative_id", "strategic_milestones", ["initiative_id"]
    )
    op.create_index("ix_strategic_milestones_status", "strategic_milestones", ["status"])
    op.create_index("ix_strategic_milestones_due_date", "strategic_milestones", ["due_date"])
    op.create_index(
        "ix_strategic_roadmap_items_year_quarter", "strategic_roadmap_items", ["year", "quarter"]
    )
    op.create_index("ix_strategic_roadmap_items_lane", "strategic_roadmap_items", ["lane"])

    # Consolidation module indexes
    op.create_index("ix_consolidation_candidates_status", "consolidation_candidates", ["status"])
    op.create_index(
        "ix_consolidation_candidates_primary_app",
        "consolidation_candidates",
        ["primary_application_id"],
    )
    op.create_index(
        "ix_consolidation_candidates_duplicate_app",
        "consolidation_candidates",
        ["duplicate_application_id"],
    )
    op.create_index(
        "ix_consolidation_opportunities_status", "consolidation_opportunities", ["status"]
    )
    op.create_index(
        "ix_consolidation_opportunities_priority", "consolidation_opportunities", ["priority"]
    )
    op.create_index(
        "ix_savings_realizations_opportunity_id", "savings_realizations", ["opportunity_id"]
    )

    # Policy monitoring module indexes
    op.create_index("ix_architecture_policies_category", "architecture_policies", ["category"])
    op.create_index("ix_architecture_policies_is_active", "architecture_policies", ["is_active"])
    op.create_index("ix_policy_violations_policy_id", "policy_violations", ["policy_id"])
    op.create_index(
        "ix_policy_violations_entity", "policy_violations", ["entity_type", "entity_id"]
    )
    op.create_index("ix_policy_violations_status", "policy_violations", ["status"])
    op.create_index("ix_policy_violations_severity", "policy_violations", ["severity"])
    op.create_index(
        "ix_compliance_statuses_entity", "compliance_statuses", ["entity_type", "entity_id"]
    )
    op.create_index("ix_policy_exemptions_violation_id", "policy_exemptions", ["violation_id"])
    op.create_index("ix_policy_exemptions_status", "policy_exemptions", ["status"])


def downgrade():
    """Drop all module tables"""

    # Drop indexes
    op.drop_index("ix_policy_exemptions_status", "policy_exemptions")
    op.drop_index("ix_policy_exemptions_violation_id", "policy_exemptions")
    op.drop_index("ix_compliance_statuses_entity", "compliance_statuses")
    op.drop_index("ix_policy_violations_severity", "policy_violations")
    op.drop_index("ix_policy_violations_status", "policy_violations")
    op.drop_index("ix_policy_violations_entity", "policy_violations")
    op.drop_index("ix_policy_violations_policy_id", "policy_violations")
    op.drop_index("ix_architecture_policies_is_active", "architecture_policies")
    op.drop_index("ix_architecture_policies_category", "architecture_policies")
    op.drop_index("ix_savings_realizations_opportunity_id", "savings_realizations")
    op.drop_index("ix_consolidation_opportunities_priority", "consolidation_opportunities")
    op.drop_index("ix_consolidation_opportunities_status", "consolidation_opportunities")
    op.drop_index("ix_consolidation_candidates_duplicate_app", "consolidation_candidates")
    op.drop_index("ix_consolidation_candidates_primary_app", "consolidation_candidates")
    op.drop_index("ix_consolidation_candidates_status", "consolidation_candidates")
    op.drop_index("ix_strategic_roadmap_items_lane", "strategic_roadmap_items")
    op.drop_index("ix_strategic_roadmap_items_year_quarter", "strategic_roadmap_items")
    op.drop_index("ix_strategic_milestones_due_date", "strategic_milestones")
    op.drop_index("ix_strategic_milestones_status", "strategic_milestones")
    op.drop_index("ix_strategic_milestones_initiative_id", "strategic_milestones")
    op.drop_index("ix_strategic_initiatives_priority", "strategic_initiatives")
    op.drop_index("ix_strategic_initiatives_status", "strategic_initiatives")

    # Drop tables in reverse order (respecting foreign keys)
    op.drop_table("policy_exemptions")
    op.drop_table("compliance_statuses")
    op.drop_table("policy_violations")
    op.drop_table("architecture_policies")
    op.drop_table("savings_realizations")
    op.drop_table("consolidation_opportunities")
    op.drop_table("consolidation_candidates")
    op.drop_table("strategic_roadmap_items")
    op.drop_table("strategic_milestones")
    op.drop_table("strategic_initiatives")
