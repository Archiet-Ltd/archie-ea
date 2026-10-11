"""add_workflow_artifact_tables

Create tables for workflow output artifacts per ADR-001.
Tables: architecture_vision_documents, architecture_review_findings,
vendor_selection_reports, compliance_scan_reports, workflow_completion_summaries.

Revision ID: add_workflow_artifact_tables
Revises: add_ea_workflow_notifications
Create Date: 2026-02-24
"""
from alembic import op
import sqlalchemy as sa

revision = "add_workflow_artifact_tables"
down_revision = "add_ea_workflow_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "architecture_vision_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_instance_id",
            sa.Integer(),
            sa.ForeignKey("ea_workflow_instances.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("scope_summary", sa.Text(), nullable=True),
        sa.Column("stakeholder_concerns", sa.JSON(), nullable=True),
        sa.Column("architecture_principles", sa.JSON(), nullable=True),
        sa.Column("business_goals", sa.JSON(), nullable=True),
        sa.Column("constraints", sa.JSON(), nullable=True),
        sa.Column("target_architecture_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "architecture_review_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_instance_id",
            sa.Integer(),
            sa.ForeignKey("ea_workflow_instances.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("finding_type", sa.String(64), nullable=False, server_default="recommendation"),
        sa.Column("severity", sa.String(32), nullable=False, server_default="medium"),
        sa.Column("element_id", sa.Integer(), sa.ForeignKey("archimate_elements.id"), nullable=True),
        sa.Column("element_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("resolution_status", sa.String(32), server_default="open"),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("resolved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "vendor_selection_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_instance_id",
            sa.Integer(),
            sa.ForeignKey("ea_workflow_instances.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("capability_gap_summary", sa.Text(), nullable=True),
        sa.Column("shortlisted_vendors", sa.JSON(), nullable=True),
        sa.Column("vendor_scores", sa.JSON(), nullable=True),
        sa.Column("tco_analysis", sa.JSON(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("decision_rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "compliance_scan_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_instance_id",
            sa.Integer(),
            sa.ForeignKey("ea_workflow_instances.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("scan_scope", sa.String(64), server_default="full"),
        sa.Column("total_violations", sa.Integer(), server_default="0"),
        sa.Column("violations_by_severity", sa.JSON(), nullable=True),
        sa.Column("policies_evaluated", sa.Integer(), server_default="0"),
        sa.Column("applications_scanned", sa.Integer(), server_default="0"),
        sa.Column("auto_remediated_count", sa.Integer(), server_default="0"),
        sa.Column("remediation_summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "workflow_completion_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workflow_instance_id",
            sa.Integer(),
            sa.ForeignKey("ea_workflow_instances.id"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("workflow_code", sa.String(100), nullable=False),
        sa.Column("workflow_name", sa.String(255), nullable=True),
        sa.Column("total_steps", sa.Integer(), server_default="0"),
        sa.Column("completed_steps", sa.Integer(), server_default="0"),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("artifacts_created", sa.JSON(), nullable=True),
        sa.Column("steps_summary", sa.JSON(), nullable=True),
        sa.Column("key_outputs", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("workflow_completion_summaries")
    op.drop_table("compliance_scan_reports")
    op.drop_table("vendor_selection_reports")
    op.drop_table("architecture_review_findings")
    op.drop_table("architecture_vision_documents")
