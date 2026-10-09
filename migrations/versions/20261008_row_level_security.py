"""Row-level security on the tenant and shared-catalogue tables (R1-B20 PR 3).

PostgreSQL itself refuses a query that forgets the organisation: the database
role the application runs as (``archie_runtime``) sees and changes only the
session organisation's rows, and shared catalogue rows read-only. This is the
second enforcement layer behind the ORM filter in
``app/middleware/tenant_isolation.py``; the session organisation it reads is
the one that module already sets for every transaction.

Re-land of the reverted #364 / held #423, rewritten to the decisions in the
R1-B20 PR 3 brief:

* One revision. No role creation, no grants, no default privileges: access
  grants stay with ``scripts/database/configure_roles.py``.
* ``ENABLE`` row-level security, not ``FORCE``. The deploy role
  (``archie_deploy``) owns the tables, so it is exempt and ``schema-upgrade``
  and every ``backfill-*`` command in ``deploy-schema.sh`` keep working.
  ``archie_runtime`` is not the owner and does not bypass row security, so every policy
  applies to it.
* The policies compare against ``NULLIF(current_setting(..., true), '')``:
  a connection that previously ran a transaction with the setting returns
  ``''`` (not NULL) and ``''::integer`` would raise. No context means NULL
  means zero rows, never an error.
* Each table is checked before it is touched (table present, ``organization_id``
  present, migration role may alter it). A table that fails a check is skipped
  with a printed reason and listed in the summary line; the deploy never fails
  on it. Alembic runs a revision once, so a table (or a late ``organization_id``
  column) that is not there when this revision runs is NOT picked up later: it
  needs its own new fencing revision, and the guard test in
  ``tests/test_row_level_security.py`` fails with "add a new fencing revision"
  when a ``TenantMixin`` / ``HybridTenantMixin`` table is in none of them.
* ``archie.platform_scope = 'on'`` is admitted by every policy. It is set only
  by ``app.jobs.tenant_safe_job.platform_scope`` for the few runtime paths that
  must read across organisations or resolve the organisation themselves.

Idempotent: every policy is dropped and recreated, so a re-run converges to
exactly these definitions. The migration imports nothing from ``app``.

Revision ID: 20261008_row_level_security
Revises: 20261008_uwp_element_unique
Create Date: 2026-10-08
"""
from alembic import op
from sqlalchemy import text

revision = "20261008_row_level_security"
down_revision = "20261008_uwp_element_unique"
branch_labels = None
depends_on = None


# The session organisation, built once and used in every policy. NULLIF turns
# the empty string a reused connection reports into NULL before the cast.
_ORG = "NULLIF(current_setting('archie.organization_id', true), '')::integer"
# Sanctioned no-organisation paths (platform_scope) are admitted by every policy.
_PLATFORM = "COALESCE(current_setting('archie.platform_scope', true), '') = 'on'"

_TENANT_RULE = f"organization_id = {_ORG} OR {_PLATFORM}"
_HYBRID_READ = f"organization_id IS NULL OR organization_id = {_ORG} OR {_PLATFORM}"
_HYBRID_WRITE = f"(organization_id IS NOT NULL AND organization_id = {_ORG}) OR {_PLATFORM}"

TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")
HYBRID_POLICIES = ("hybrid_select", "hybrid_insert", "hybrid_update", "hybrid_delete")

# (command, USING, WITH CHECK) per policy, in the order of the names above.
_TENANT_DEFINITIONS = (
    ("SELECT", _TENANT_RULE, None),
    ("INSERT", None, _TENANT_RULE),
    ("UPDATE", _TENANT_RULE, _TENANT_RULE),
    ("DELETE", _TENANT_RULE, None),
)
_HYBRID_DEFINITIONS = (
    ("SELECT", _HYBRID_READ, None),
    ("INSERT", None, _HYBRID_WRITE),
    ("UPDATE", _HYBRID_WRITE, _HYBRID_WRITE),
    ("DELETE", _HYBRID_WRITE, None),
)

# Every concrete model using TenantMixin: every row belongs to exactly one organisation.
# Tables that do not exist yet in a deployment are skipped (optional features).
TENANT_TABLES = (
    "account_tokens",
    "adm_board_portfolios",
    "adm_cross_board_dependencies",
    "adm_phase_approvals",
    "adm_rida_logs",
    "adm_transition_history",
    "agent_charters",
    "agent_oversight_state",
    "agent_registrations",
    "agent_run_records",
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
    "data_issues",
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
    "event_log",
    "evidence_claim_heads",
    "evidence_head_events",
    "evidence_records",
    "evidence_requests",
    "external_identity_crosswalk",
    "formula_registers",
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
    "org_connector_credentials",
    "organization_encryption_keys",
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
    "regulatory_change_impacts",
    "regulatory_changes",
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
    "unified_work_packages",
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
)

# Every concrete model using HybridTenantMixin: shared catalogue rows
# (organization_id IS NULL) readable by every organisation, plus per-organisation
# override rows. Tenants cannot insert, update or delete a shared row.
HYBRID_TABLES = (
    "capability_framework_configuration",
    "enterprise_architecture_frameworks",
    "framework_adoptions",
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
)

# Models that carry an organization_id but are deliberately not fenced here.
EXCLUDED = {
    "unified_capabilities": (
        "uses HybridCapabilityTenantMixin (a duplicate of HybridTenantMixin owned by "
        "R1-B02); fenced when that is merged into HybridTenantMixin"
    ),
}


def _skip_reason(bind, table: str, *, need_column: bool):
    """Return None when the table can be fenced, else the reason it is skipped."""
    present = bind.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}
    ).scalar()
    if not present:
        return "absent"
    if need_column:
        has_column = bind.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t "
                "AND column_name = 'organization_id')"
            ),
            {"t": table},
        ).scalar()
        if not has_column:
            print(f"WARNING row-level security skipped: {table} has no organization_id column")
            return "no organization_id column"
    may_alter = bind.execute(
        text(
            "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
            "FROM pg_class c WHERE c.oid = to_regclass(:t)"
        ),
        {"t": f"public.{table}"},
    ).scalar()
    if not may_alter:
        print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
        return "not owned by the migration role"
    return None


def _fence(bind, table: str, policies, definitions) -> None:
    # Table names come from the literal constants above and the policy text from
    # the module constants; nothing here is user input.
    for name, (command, using, check) in zip(policies, definitions):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(
            text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}")  # nosec B608 - table names from a literal constant
        )
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - table names from a literal constant


def upgrade():
    bind = op.get_bind()
    fenced = {"tenant": 0, "shared": 0}
    skipped = []

    for kind, tables, policies, definitions in (
        ("tenant", TENANT_TABLES, TENANT_POLICIES, _TENANT_DEFINITIONS),
        ("shared", HYBRID_TABLES, HYBRID_POLICIES, _HYBRID_DEFINITIONS),
    ):
        for table in tables:
            if table in EXCLUDED:
                continue
            reason = _skip_reason(bind, table, need_column=True)
            if reason is not None:
                skipped.append(f"{table} ({reason})")
                continue
            _fence(bind, table, policies, definitions)
            fenced[kind] += 1

    print(
        f"row-level security: {fenced['tenant']} tenant tables, "
        f"{fenced['shared']} shared tables fenced; "
        f"skipped: {', '.join(skipped) if skipped else 'none'}"
    )


def downgrade():
    bind = op.get_bind()
    for tables, policies in (
        (HYBRID_TABLES, HYBRID_POLICIES),
        (TENANT_TABLES, TENANT_POLICIES),
    ):
        for table in tables:
            if _skip_reason(bind, table, need_column=False) is not None:
                continue
            for name in policies:
                bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
            bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - table names from a literal constant
