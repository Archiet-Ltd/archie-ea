"""Row-level security policies for tenant isolation (PR 3).

This migration enables PostgreSQL row-level security as a second enforcement
layer behind the ORM-level do_orm_execute filter (PR 1/PR 2). The database
itself refuses queries that forget the organisation.

Two categories of tables are fenced:

1. TenantMixin tables (255 tables) — every row belongs to exactly one
   organisation. Policy: organization_id = current_setting('archie.organization_id').

2. HybridTenantMixin tables (12 tables) — shared catalogue rows readable by
   every organisation (organization_id IS NULL), plus per-organisation override
   rows. Policy: organization_id IS NULL OR organization_id = current_setting(...).

A platform database role 'archie_platform' is created for migrations. It has
BYPASSRLS so schema changes can touch every organisation's rows legitimately.
Application code must never connect as this role.

The session organisation is set per-transaction by
app.middleware.tenant_isolation.set_database_tenant_context (called from the
do_orm_execute and before_flush listeners, and from tenant_scope in
app.jobs.tenant_safe_job). CLI, scheduler and worker paths use tenant_scope
explicitly — no second mechanism is introduced.

Revision ID: 20261003_row_level_security
Revises: 20261002_data_domain_org_unique
Create Date: 2026-10-03
"""
from alembic import op
from sqlalchemy import text

revision = "20261003_row_level_security"
down_revision = "20261002_event_log"
branch_labels = None
depends_on = None


# TenantMixin tables — every row has a non-NULL organization_id.
# Sourced from the ORM at migration time to stay in sync with models.
TENANT_TABLES = [
    "account_tokens",
    "adm_phase_approvals",
    "adm_transition_history",
    "ai_chat_crud_approvals",
    "ai_chat_document_uploads",
    "ai_chat_feedback",
    "api_settings",
    "application_compliance_controls",
    "application_components",
    "application_consolidation_recommendations",
    "application_custom_field_values",
    "application_data_objects",
    "application_dependencies",
    "application_disposition_records",
    "application_events",
    "application_functions",
    "application_import_history",
    "application_interfaces",
    "application_ownership",
    "application_processes",
    "application_rationalization_scores",
    "application_replacements",
    "arb_audit_logs",
    "arb_board_members",
    "arb_canonical_conditions",
    "arb_capability_impacts",
    "arb_condition_events",
    "arb_condition_evidence_records",
    "arb_decision_events",
    "arb_documents",
    "arb_exceptions",
    "arb_review_comments",
    "arb_review_cycles",
    "arb_review_items",
    "arb_subject_evidence_snapshots",
    "arb_submission_events",
    "arb_submission_evidence_snapshots",
    "archimate_contracts",
    "archimate_derived_relationships",
    "archimate_elements",
    "archimate_relationships",
    "archimate_representations",
    "archimate_resources",
    "archimate_viewpoints",
    "archimate_views",
    "architecture_change_requests",
    "architecture_decision_records",
    "architecture_decisions",
    "architecture_journey_links",
    "architecture_journey_members",
    "architecture_journeys",
    "architecture_models",
    "architecture_policies",
    "architecture_review_boards",
    "architecture_sessions",
    "assessments",
    "assumptions",
    "benefits",
    "billing_events",
    "business_actors",
    "business_capability",
    "business_cases",
    "business_collaborations",
    "business_events",
    "business_function",
    "business_interfaces",
    "business_model_canvases",
    "business_objects",
    "business_processes",
    "business_roles",
    "business_services",
    "candidate_overlap_dispositions",
    "candidate_signals",
    "capabilities",
    "capability_assessments",
    "capability_cost_allocations",
    "capability_dependency",
    "capability_gap_analysis",
    "capability_governance_decision",
    "capability_health_overrides",
    "capability_maturity_assessment",
    "capability_roadmap",
    "capability_tags",
    "capability_value_stream_mapping",
    "command_idempotency_records",
    "command_materialisations",
    "compliance_status",
    "conceptual_data_models",
    "connector_configs",
    "courses_of_action",
    "data_access_controls",
    "data_catalogs",
    "data_domains",
    "data_entities",
    "data_governance_workflows",
    "data_lineage",
    "data_object_storage",
    "data_quality_metrics",
    "data_retention_policies",
    "data_stores",
    "data_transformations",
    "decision_brief_evidence_citations",
    "decision_brief_option_citations",
    "decision_brief_versions",
    "decision_briefs",
    "decision_events",
    "decision_ledger",
    "delivery_export_attempts",
    "demands",
    "document_chunk_embeddings",
    "drift_reports",
    "drivers",
    "ea_workflow_instances",
    "ea_workflow_notifications",
    "ea_workflow_schedules",
    "ea_workflow_step_executions",
    "enterprise_briefings",
    "enterprise_initiatives",
    "enterprise_raci_assignments",
    "entity_history",
    "evidence_claim_heads",
    "evidence_head_events",
    "evidence_records",
    "evidence_requests",
    "external_identity_crosswalk",
    "framework_instances",
    "gaps",
    "gdpr_requests",
    "goals",
    "governance_gates",
    "implementation_events",
    "industry_process_recommendation",
    "intelligence_derivation_runs",
    "kanban_boards",
    "kanban_cards",
    "license_entitlements",
    "logical_data_models",
    "meanings",
    "measure_definitions",
    "migration_waves",
    "missing_business_collaborations",
    "missing_business_interactions",
    "missing_business_interfaces",
    "monitoring_alerts",
    "monitoring_baselines",
    "motivation_bridge_links",
    "operation_results",
    "options_analysis",
    "organization_units",
    "outcome_measurements",
    "outcomes",
    "physical_data_models",
    "physical_distribution_networks",
    "physical_equipment",
    "physical_facilities",
    "physical_materials",
    "plateaus",
    "policy_violations",
    "principles",
    "process_data_crud",
    "products",
    "programme_outcome_commitments",
    "programme_role_assignments",
    "programme_snapshots",
    "programme_workstreams",
    "projects",
    "raid_items",
    "rate_cards",
    "reference_model_import",
    "representations",
    "requirements",
    "review_queue_items",
    "risk_assessments",
    "risk_entity_links",
    "risk_score_history",
    "risks",
    "roadmap_deliverables",
    "roadmap_tasks",
    "saved_diagrams",
    "solution_adr_links",
    "solution_analysis_sessions",
    "solution_arb_drafts",
    "solution_arb_reviews",
    "solution_assessments",
    "solution_blueprint_proposals",
    "solution_capability",
    "solution_constraints",
    "solution_contracts_model",
    "solution_cost_line_items",
    "solution_cost_models",
    "solution_deployment_architectures",
    "solution_domain_specs",
    "solution_drivers",
    "solution_execution_tracking",
    "solution_goals",
    "solution_issues",
    "solution_metrics",
    "solution_migration_roadmaps",
    "solution_options",
    "solution_outcome_measurements",
    "solution_outcomes",
    "solution_patterns",
    "solution_principles",
    "solution_problem_definitions",
    "solution_recommendations",
    "solution_requirements",
    "solution_risks",
    "solution_stakeholders",
    "solution_versions",
    "solution_workflows",
    "solutions",
    "sso_group_role_mappings",
    "stakeholder_inputs",
    "stakeholders",
    "strategic_initiatives",
    "strategic_milestones",
    "strategic_recommendations",
    "strategic_roadmap_items",
    "tech_radar_entries",
    "technology_artifacts",
    "technology_collaborations",
    "technology_collaborations_full",
    "technology_communication_networks",
    "technology_devices",
    "technology_events",
    "technology_functions",
    "technology_interactions",
    "technology_interfaces",
    "technology_nodes",
    "technology_paths",
    "technology_processes",
    "technology_services",
    "technology_standards",
    "technology_system_software",
    "transformation_candidates",
    "transformation_option_versions",
    "transformation_options",
    "transformation_outbox_events",
    "unified_value_stream_stages",
    "value_streams",
    "values",
    "vendor_component_architecture",
    "vendor_contracts",
    "vendor_cost_breakdown",
    "vendor_process_mappings",
    "vendor_product_capabilities",
    "vendor_service_catalog",
    "vendor_stack_templates",
    "vendor_taxonomy",
    "webhook_deliveries",
    "webhook_events",
    "webhook_subscriptions",
    "work_package_resource_demand",
    "work_packages",
    "workbench_artifact_evidence",
]


# HybridTenantMixin tables — shared catalogue (organization_id IS NULL) plus
# per-organisation overrides. Read policy admits both; write policy is handled
# by application code (reference rows are read-only for tenants).
HYBRID_TABLES = [
    "capability_framework_configuration",
    "enterprise_architecture_frameworks",
    "framework_configuration_templates",
    "framework_extensions",
    "framework_migration_mappings",
    "framework_validation_rules",
    "industry_apqc_framework",
    "industry_apqc_process",
    "industry_frameworks",
    "quality_frameworks",
    "reference_model",
    "reference_model_capability",
]


def _table_exists(bind, table: str) -> bool:
    """Check if a table exists in the current schema."""
    return bind.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"),
        {"t": table},
    ).scalar()


def _policy_exists(bind, table: str, policy: str) -> bool:
    """Check if a policy exists on a table."""
    return bind.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_policies"
            "  WHERE schemaname = 'public'"
            "    AND tablename = :t"
            "    AND policyname = :p"
            ")"
        ),
        {"t": table, "p": policy},
    ).scalar()


def _role_exists(bind, role: str) -> bool:
    """Check if a database role exists."""
    return bind.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :r)"),
        {"r": role},
    ).scalar()


def upgrade():
    bind = op.get_bind()

    # ------------------------------------------------------------------ #
    # 1. Create the platform role for migrations (BYPASSRLS).
    #    This role must NEVER be used by application code.
    #
    #    Role creation is guarded (CREATE ROLE is cluster-wide and must
    #    only happen once), but the GRANT statements below are NOT --
    #    grants are per-database, so a freshly created database still
    #    needs them applied even when the role itself already exists on
    #    this Postgres cluster from a previous database. Gating the
    #    grants behind "role doesn't exist yet" was the original bug
    #    here: BYPASSRLS only bypasses row-level security POLICIES, not
    #    ordinary table-level GRANT/REVOKE permissions, so a role with no
    #    grants in this database still gets "permission denied" on any
    #    table regardless of BYPASSRLS.
    # ------------------------------------------------------------------ #
    if not _role_exists(bind, "archie_platform"):
        bind.execute(text("CREATE ROLE archie_platform BYPASSRLS NOLOGIN"))
    bind.execute(text("GRANT USAGE ON SCHEMA public TO archie_platform"))
    bind.execute(text("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO archie_platform"))
    bind.execute(text("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO archie_platform"))
    # Future tables: default privileges for the role that runs migrations.
    bind.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO archie_platform"))
    bind.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO archie_platform"))

    # ------------------------------------------------------------------ #
    # 1b. The application's own role (archie_app), a non-superuser,
    #     non-BYPASSRLS role so RLS actually constrains it. Role creation
    #     is cluster-wide and guarded like archie_platform above; the
    #     grants are per-database and always (re)applied, since a freshly
    #     created database needs them even when the role already exists
    #     on this Postgres cluster from a previous database. RLS filters
    #     which ROWS archie_app can see or affect; it is not a substitute
    #     for the baseline table-level grant every ordinary table needs,
    #     so this grants the same standard DML privileges a normal
    #     non-superuser application role would have in production.
    # ------------------------------------------------------------------ #
    if not _role_exists(bind, "archie_app"):
        bind.execute(text("CREATE ROLE archie_app NOLOGIN"))
    bind.execute(text("GRANT USAGE ON SCHEMA public TO archie_app"))
    bind.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO archie_app"))
    bind.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO archie_app"))
    bind.execute(text(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO archie_app"
    ))
    bind.execute(text(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO archie_app"
    ))

    # ------------------------------------------------------------------ #
    # 2. Enable RLS with FORCE ROW LEVEL SECURITY on TenantMixin tables.
    #    Policy: organization_id = current_setting('archie.organization_id')::int
    #    The setting is set per-transaction by set_database_tenant_context().
    # ------------------------------------------------------------------ #
    for table in TENANT_TABLES:
        if not _table_exists(bind, table):
            continue  # table may not exist in all deployments (optional features)

        # Enable RLS and force it (even for table owner).
        bind.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

        # SELECT policy — the core isolation guarantee.
        if not _policy_exists(bind, table, "tenant_select"):
            bind.execute(text(f"""
                CREATE POLICY tenant_select ON {table}
                FOR SELECT
                USING (organization_id = current_setting('archie.organization_id', true)::int)
            """))

        # INSERT policy — before_flush sets organization_id, but belt-and-suspenders.
        if not _policy_exists(bind, table, "tenant_insert"):
            bind.execute(text(f"""
                CREATE POLICY tenant_insert ON {table}
                FOR INSERT
                WITH CHECK (organization_id = current_setting('archie.organization_id', true)::int)
            """))

        # UPDATE policy — bulk UPDATE is now filtered at the database level too.
        if not _policy_exists(bind, table, "tenant_update"):
            bind.execute(text(f"""
                CREATE POLICY tenant_update ON {table}
                FOR UPDATE
                USING (organization_id = current_setting('archie.organization_id', true)::int)
                WITH CHECK (organization_id = current_setting('archie.organization_id', true)::int)
            """))

        # DELETE policy — bulk DELETE is now filtered at the database level too.
        if not _policy_exists(bind, table, "tenant_delete"):
            bind.execute(text(f"""
                CREATE POLICY tenant_delete ON {table}
                FOR DELETE
                USING (organization_id = current_setting('archie.organization_id', true)::int)
            """))

    # ------------------------------------------------------------------ #
    # 3. Enable RLS with FORCE ROW LEVEL SECURITY on HybridTenantMixin tables.
    #    Read policy: organization_id IS NULL (shared) OR organization_id = session org.
    #    Write policies: tenants can only write their own rows (organization_id = session org).
    #    Shared rows (organization_id IS NULL) are written only by platform migrations.
    # ------------------------------------------------------------------ #
    for table in HYBRID_TABLES:
        if not _table_exists(bind, table):
            continue

        bind.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

        # SELECT policy — shared catalogue + own rows.
        if not _policy_exists(bind, table, "hybrid_select"):
            bind.execute(text(f"""
                CREATE POLICY hybrid_select ON {table}
                FOR SELECT
                USING (
                    organization_id IS NULL
                    OR organization_id = current_setting('archie.organization_id', true)::int
                )
            """))

        # INSERT policy — tenants can only create their own override rows.
        if not _policy_exists(bind, table, "hybrid_insert"):
            bind.execute(text(f"""
                CREATE POLICY hybrid_insert ON {table}
                FOR INSERT
                WITH CHECK (organization_id = current_setting('archie.organization_id', true)::int)
            """))

        # UPDATE policy — tenants can only update their own override rows.
        if not _policy_exists(bind, table, "hybrid_update"):
            bind.execute(text(f"""
                CREATE POLICY hybrid_update ON {table}
                FOR UPDATE
                USING (organization_id = current_setting('archie.organization_id', true)::int)
                WITH CHECK (organization_id = current_setting('archie.organization_id', true)::int)
            """))

        # DELETE policy — tenants can only delete their own override rows.
        if not _policy_exists(bind, table, "hybrid_delete"):
            bind.execute(text(f"""
                CREATE POLICY hybrid_delete ON {table}
                FOR DELETE
                USING (organization_id = current_setting('archie.organization_id', true)::int)
            """))


def downgrade():
    bind = op.get_bind()

    # Drop policies and disable RLS on HybridTenantMixin tables.
    for table in HYBRID_TABLES:
        if not _table_exists(bind, table):
            continue
        for policy in ("hybrid_select", "hybrid_insert", "hybrid_update", "hybrid_delete"):
            if _policy_exists(bind, table, policy):
                bind.execute(text(f"DROP POLICY {policy} ON {table}"))
        bind.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        bind.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    # Drop policies and disable RLS on TenantMixin tables.
    for table in TENANT_TABLES:
        if not _table_exists(bind, table):
            continue
        for policy in ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete"):
            if _policy_exists(bind, table, policy):
                bind.execute(text(f"DROP POLICY {policy} ON {table}"))
        bind.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        bind.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    # Revoke the platform role's grants in this database. The role itself
    # is cluster-wide (CREATE ROLE's own guard above is what makes that
    # safe to re-run) and is NOT dropped here: a Postgres cluster hosting
    # several databases -- exactly what a CI run's shared service container
    # does across shards -- still has this role granted in the OTHER
    # databases, and DROP ROLE fails while the role holds privileges
    # anywhere in the cluster, not just in this database. A downgrade must
    # only undo this database's own state.
    if _role_exists(bind, "archie_platform"):
        bind.execute(text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM archie_platform"
        ))
        bind.execute(text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM archie_platform"
        ))
        bind.execute(text("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM archie_platform"))
        bind.execute(text("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM archie_platform"))
        bind.execute(text("REVOKE USAGE ON SCHEMA public FROM archie_platform"))

    # Revoke the DML grants given to archie_app in this database, for the
    # same cluster-wide reason -- the role itself is not dropped here.
    if _role_exists(bind, "archie_app"):
        bind.execute(text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM archie_app"
        ))
        bind.execute(text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "REVOKE USAGE, SELECT ON SEQUENCES FROM archie_app"
        ))
        bind.execute(text("REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM archie_app"))
        bind.execute(text("REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM archie_app"))