"""
CLI command registration.
"""


def init_cli(app):
    """Register all CLI commands."""

    # Seed CLI commands
    try:
        from app.commands.seed_commands import register_commands
        register_commands(app)
        app.logger.info("\u2705 Seed CLI commands registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register seed CLI commands: {e}")

    # ArchiMate CLI commands
    try:
        from app.commands.archimate_commands import register_archimate_commands
        register_archimate_commands(app)
        app.logger.info("\u2705 ArchiMate CLI commands registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register ArchiMate CLI commands: {e}")

    # Traceability report CLI command (ARCH-126)
    try:
        from app.commands.traceability_report_command import register_traceability_report_command
        register_traceability_report_command(app)
        app.logger.info("✅ Traceability report CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register traceability report CLI command: {e}")

    # Lucidchart import CLI command
    try:
        from app.commands.lucid_import_commands import register_lucid_import_commands
        register_lucid_import_commands(app)
        app.logger.info("✅ Lucidchart import CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register Lucidchart import CLI command: {e}")

    # Capabilities seed CLI commands
    try:
        from app.commands.seed_capabilities import register_capabilities_commands
        register_capabilities_commands(app)
        app.logger.info("\u2705 Capabilities seed CLI commands registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register capabilities seed CLI commands: {e}")

    # ACM CLI commands
    try:
        from app.commands.acm_commands import register_commands as register_acm_commands
        register_acm_commands(app)
        app.logger.info("\u2705 ACM CLI commands registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register ACM CLI commands: {e}")

    # Feature Flags seed CLI command
    try:
        from app.commands import seed_feature_flags
        seed_feature_flags.init_app(app)
        app.logger.info("\u2705 Feature flags seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register feature flags seed CLI command: {e}")

    # ADM Deliverables seed CLI command
    try:
        from app.commands import seed_adm_deliverables
        seed_adm_deliverables.init_app(app)
        app.logger.info("\u2705 ADM deliverables seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register ADM deliverables seed CLI command: {e}")

    # ArchiMate backfill CLI command
    try:
        from app.commands import backfill_archimate_elements
        backfill_archimate_elements.init_app(app)
        app.logger.info("\u2705 ArchiMate backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register ArchiMate backfill CLI: {e}")

    # Demo tenant relationship seeder
    try:
        from app.commands import seed_demo_mappings
        seed_demo_mappings.init_app(app)
        app.logger.info("✅ Demo mapping seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register demo mapping seed CLI: {e}")

    # ADR-0003: layer-wide tenancy backfill/harden (runs on boot after reconcile-schema)
    try:
        from app.commands.backfill_layer_tenancy import init_app as init_layer_tenancy
        init_layer_tenancy(app)
        app.logger.info("✅ Layer tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register layer tenancy backfill CLI: {e}")

    # Duplicate-detection tenancy backfill
    try:
        from app.commands.backfill_dedupe_tenancy import init_app as init_dedupe_tenancy
        init_dedupe_tenancy(app)
        app.logger.info("✅ Duplicate-detection tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register duplicate-detection tenancy backfill CLI: {e}")

    # BIZBOK Strategy & Motivation backfill CLI command
    try:
        from scripts.backfill_strategy_motivation_elements import init_app as init_strat_backfill
        init_strat_backfill(app)
        app.logger.info("\u2705 Strategy/Motivation backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register Strategy/Motivation backfill CLI: {e}")

    # Vendor Seed Management CLI commands
    try:
        from app.commands.seed_vendors_cli import register_seed_commands
        register_seed_commands(app)
        app.logger.info("\u2705 Vendor seed management CLI commands registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register vendor seed CLI commands: {e}")

    # ArchiMate Viewpoint seed CLI command
    try:
        from app.commands import seed_viewpoints
        seed_viewpoints.init_app(app)
        app.logger.info("\u2705 ArchiMate viewpoint seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register viewpoint seed CLI command: {e}")

    # RATA-003: Rationalization scoring CLI commands
    try:
        from app.commands.rationalization_commands import register_rationalization_commands
        register_rationalization_commands(app)
        app.logger.info("\u2705 Rationalization CLI commands registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register rationalization CLI commands: {e}")

    # Data profile + read-only query CLI commands
    try:
        from app.commands.data_profile_commands import register_data_profile_commands
        register_data_profile_commands(app)
        app.logger.info("\u2705 Data profile CLI commands registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register data profile CLI commands: {e}")

    # PLT-009: Data maturity digest CLI command
    import click

    @app.cli.command("send-maturity-digest")
    def send_maturity_digest_cmd():
        """PLT-009: Send the weekly data maturity digest email now."""
        from app._bootstrap._digest_emails import send_data_maturity_digest
        from flask import current_app

        click.echo("Generating data maturity digest...")
        run = send_data_maturity_digest(current_app._get_current_object())
        # Per-tenant now (JobRun), not a single global dict: report the
        # organisations attempted and the recipients reached across them.
        recipients = sum((r.value or {}).get("recipients", 0)
                         for r in run.results if r.ok)
        click.echo(
            f"Done: sent to {run.succeeded} organisation(s), "
            f"{recipients} recipient(s); {run.failed} failed."
        )

    # PLT-031: Executive summary CLI command
    @app.cli.command("send-executive-summary")
    def send_executive_summary_cmd():
        """PLT-031: Send the weekly executive summary email now."""
        from app._bootstrap._digest_emails import send_executive_summary
        from flask import current_app

        click.echo("Generating executive summary...")
        run = send_executive_summary(current_app._get_current_object())
        recipients = sum((r.value or {}).get("recipients", 0)
                         for r in run.results if r.ok)
        click.echo(
            f"Done: sent to {run.succeeded} organisation(s), "
            f"{recipients} recipient(s); {run.failed} failed."
        )

    # Error digest CLI command: read-only, emails platform admins a summary
    # of new unresolved error_events rows since the last run.
    @app.cli.command("send-error-digest")
    def send_error_digest_cmd():
        """Send the unresolved-error digest email now (read-only)."""
        from app._bootstrap._digest_emails import send_error_digest
        from flask import current_app

        click.echo("Checking for new unresolved errors...")
        result = send_error_digest(current_app._get_current_object())
        click.echo(
            f"Done: {result['new_events']} new event(s), "
            f"{result['recipients']} recipient(s)."
        )

    # ACM-001: Cloud pricing API sync CLI commands
    try:
        from app.commands.cloud_pricing_commands import register_commands as register_cloud_pricing
        register_cloud_pricing(app)
        app.logger.info("\u2705 Cloud pricing CLI commands registered")
    except ImportError:
        pass

    # Solution maturity sync CLI commands
    try:
        from app.commands.solution_maturity_commands import register_solution_maturity_commands
        register_solution_maturity_commands(app)
        app.logger.info("\u2705 Solution maturity sync CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register solution maturity CLI: {e}")

    # SAP BTP: Vendor ArchiMate template seed CLI command + domain entity schema seeds
    try:
        from app.commands import seed_vendor_archimate_templates
        seed_vendor_archimate_templates.init_app(app)
        app.logger.info("\u2705 Vendor ArchiMate template seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register vendor ArchiMate template seed CLI: {e}")

    # Vendor seed column migration (add spec_data_seed to vendor_archimate_templates)
    try:
        from app.commands.add_vendor_seed_column import init_app as init_vendor_seed_col
        init_vendor_seed_col(app)
        app.logger.info("\u2705 Vendor seed column CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register vendor seed column CLI: {e}")

    # INTARCH-001: Integration pattern catalogue seed + schema extension commands
    try:
        from app.commands import seed_integration_patterns
        seed_integration_patterns.init_app(app)
        app.logger.info("\u2705 Integration pattern seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register integration pattern seed CLI: {e}")

    try:
        from app.commands.add_integration_flow_columns import init_app as init_flow_columns
        init_flow_columns(app)
        app.logger.info("\u2705 Integration flow columns CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register integration flow columns CLI: {e}")

    try:
        from app.commands.backfill_meaning_tenancy import init_app as init_backfill_meaning
        init_backfill_meaning(app)
        app.logger.info("Meaning tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register meaning tenancy backfill CLI: {e}")

    try:
        from app.commands.reconcile_schema import init_app as init_reconcile_schema
        init_reconcile_schema(app)
        app.logger.info("\u2705 Schema reconcile CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register schema reconcile CLI: {e}")

    try:
        from app.commands.backfill_ai_chat_approval_org import init_app as init_ai_approval_org
        init_ai_approval_org(app)
        app.logger.info("AI chat approval tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register AI chat approval tenancy backfill CLI: {e}")

    try:
        from app.commands.backfill_review_queue_org import init_app as init_review_queue_org
        init_review_queue_org(app)
        app.logger.info("Review queue tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register review queue tenancy backfill CLI: {e}")

    try:
        from app.commands.backfill_review_queue_approvals import init_app as init_review_queue_approvals
        init_review_queue_approvals(app)
        app.logger.info("approval-queue consolidation backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register approval-queue consolidation backfill CLI: {e}")

    try:
        from app.commands.dedupe_entities import init_app as init_dedupe_entities
        init_dedupe_entities(app)
        app.logger.info("\u2705 Dedupe entities CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register dedupe entities CLI: {e}")

    try:
        from app.commands.prune_oauth_clients import init_app as init_prune_oauth_clients
        init_prune_oauth_clients(app)
        app.logger.info("OAuth client pruning CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register OAuth client pruning CLI: {e}")

    try:
        from app.commands.clean_test_artefacts import init_app as init_clean_test_artefacts
        init_clean_test_artefacts(app)
        app.logger.info("\u2705 Clean test artefacts CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register clean test artefacts CLI: {e}")

    try:
        from app.commands.backfill_value_stream_tenancy import init_app as init_vs_tenancy
        init_vs_tenancy(app)
        app.logger.info("✅ Value-stream tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register value-stream tenancy backfill CLI: {e}")

    try:
        from app.commands.backfill_value_stream_archimate import init_app as init_vs_archimate
        init_vs_archimate(app)
        app.logger.info("✅ Value-stream ArchiMate backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register value-stream ArchiMate backfill CLI: {e}")

    try:
        from app.commands.backfill_data_archimate import init_app as init_data_archimate
        init_data_archimate(app)
        app.logger.info("✅ Data-entity ArchiMate backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register data-entity ArchiMate backfill CLI: {e}")

    try:
        from app.commands.backfill_archimate_layer_casing import init_app as init_layer_casing
        init_layer_casing(app)
        app.logger.info("✅ ArchiMate layer-casing backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register ArchiMate layer-casing backfill CLI: {e}")

    try:
        from app.commands.backfill_principle_org import init_app as init_principle_org
        init_principle_org(app)
        app.logger.info("✅ Principle tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register principle tenancy backfill CLI: {e}")

    try:
        from app.commands.backfill_outcome_org import init_app as init_outcome_org
        init_outcome_org(app)
        app.logger.info("✅ Outcome tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register outcome tenancy backfill CLI: {e}")

    try:
        from app.commands.backfill_architect_role import init_app as init_backfill_architect
        init_backfill_architect(app)
        app.logger.info("\u2705 Architect-role backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register architect-role backfill CLI: {e}")

    try:
        from app.commands.reconcile_admin_flags import init_app as init_reconcile_admin_flags
        init_reconcile_admin_flags(app)
        app.logger.info("reconcile-admin-flags CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register reconcile-admin-flags CLI: {e}")

    try:
        from app.commands.cutover_capability_tenancy import init_app as init_capability_cutover
        init_capability_cutover(app)
        app.logger.info("Capability tenancy cutover CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register capability tenancy cutover CLI: {e}")

    try:
        from app.commands.project_capabilities import init_app as init_capability_projection
        init_capability_projection(app)
        app.logger.info("Capability projection CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register capability projection CLI: {e}")

    try:
        from app.commands.apply_unified_capability_provenance_migration import (
            init_app as init_capability_provenance_migration,
        )
        init_capability_provenance_migration(app)
        app.logger.info("Capability provenance migration CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register capability provenance migration CLI: {e}")

    try:
        from app.commands.backfill_capability_catalogs import (
            init_app as init_capability_catalog_backfill,
        )
        init_capability_catalog_backfill(app)
        app.logger.info("Capability catalog backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register capability catalog backfill CLI: {e}")

    try:
        from app.commands.backfill_audit_trail import init_app as init_audit_trail_backfill
        init_audit_trail_backfill(app)
        app.logger.info("Audit trail backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register audit trail backfill CLI: {e}")

    try:
        from app.commands.indexnow_commands import init_app as init_indexnow
        init_indexnow(app)
        app.logger.info("IndexNow ping CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register IndexNow ping CLI: {e}")

    # CMP-01: SavedDiagram gained TenantMixin (runs on boot after reconcile-schema)
    try:
        from app.commands.backfill_saved_diagram_tenancy import init_app as init_saved_diagram_tenancy
        init_saved_diagram_tenancy(app)
        app.logger.info("\u2705 Saved-diagram tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register saved-diagram tenancy backfill CLI: {e}")

    # CMP-03: drop the wrong archimate_audit_logs.viewpoint_id FK
    try:
        from app.commands.drop_audit_log_viewpoint_fk import init_app as init_drop_audit_fk
        init_drop_audit_fk(app)
        app.logger.info("\u2705 Audit-log viewpoint-FK drop CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register audit-log viewpoint-FK drop CLI: {e}")

    # The entity_history trigger reconcile-schema cannot create
    try:
        from app.commands.apply_entity_history_trigger import init_app as init_entity_history_trigger
        init_entity_history_trigger(app)
        app.logger.info("\u2705 Entity-history trigger CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register entity-history trigger CLI: {e}")

    try:
        from app.commands.backfill_entity_history import init_app as init_entity_history_backfill
        init_entity_history_backfill(app)
        app.logger.info("\u2705 Entity-history backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register entity-history backfill CLI: {e}")

    try:
        from app.commands.seed_minimal_vendor_products import seed_minimal_vendor_products
        app.cli.add_command(seed_minimal_vendor_products)
        app.logger.info("\u2705 Minimal vendor products seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register minimal vendor products seed CLI: {e}")

    try:
        from app.commands.seed_sap_products import seed_sap_products
        app.cli.add_command(seed_sap_products)
        app.logger.info("\u2705 SAP products seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register SAP products seed CLI: {e}")

    try:
        from app.commands.codegen_drift_commands import register_codegen_drift_commands
        register_codegen_drift_commands(app)
        app.logger.info("\u2705 Codegen drift detection CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register codegen drift CLI: {e}")

    # Motivation bridge CLI command (journey Solution* motivation -> enterprise layer)
    try:
        from app.commands.bridge_motivation import init_app as init_bridge_motivation
        init_bridge_motivation(app)
        app.logger.info("\u2705 Motivation bridge CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register motivation bridge CLI: {e}")

    # Wave 4 Phase A: ARB/EA-workflow tenancy backfill (derives org from FK parents)
    try:
        from app.commands.backfill_arb_ea_tenancy import init_app as init_arb_ea_tenancy
        init_arb_ea_tenancy(app)
        app.logger.info("\u2705 ARB/EA tenancy backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"\u26a0\ufe0f  Failed to register ARB/EA tenancy backfill CLI: {e}")

    # Decision register consolidation (ADR records paired into
    # architecture_decisions; decision_ledger tenant-fenced)
    try:
        from app.commands.backfill_decision_register_consolidation import (
            init_app as init_decision_register_consolidation,
        )
        init_decision_register_consolidation(app)
        app.logger.info("\u2705 Decision register consolidation backfill CLI command registered")
    except Exception as e:
        app.logger.warning(
            f"\u26a0\ufe0f  Failed to register decision register consolidation backfill CLI: {e}"
        )

    try:
        from app.commands.process_arb_waiver_expiries import init_app as init_arb_expiry
        init_arb_expiry(app)
        app.logger.info("Typed ARB waiver expiry CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register typed ARB waiver expiry CLI: {e}")

    try:
        from app.commands.purge_sessions import init_app as init_purge_sessions
        init_purge_sessions(app)
        app.logger.info("✅ Session registry purge CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register session registry purge CLI: {e}")

    try:
        from app.commands.purge_copilot_insights import init_app as init_purge_copilot_insights
        init_purge_copilot_insights(app)
        app.logger.info("Copilot insights purge CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register copilot insights purge CLI: {e}")

    try:
        from app.commands.service_incident_commands import init_app as init_service_incident
        init_service_incident(app)
        app.logger.info("Service incident CLI command registered")
    except Exception as e:
        app.logger.warning(f"Failed to register service incident CLI: {e}")

    # T-S1: strategic surface demonstration data set (value streams at risk)
    try:
        from app.commands import seed_strategic_demo
        seed_strategic_demo.init_app(app)
        app.logger.info("✅ Strategic demo seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register strategic demo seed CLI: {e}")

    # T-DEMO-1: Lantern Quay demonstration company
    try:
        from app.commands import seed_demo_company
        seed_demo_company.init_app(app)
        app.logger.info("✅ Demo company seed CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register demo company seed CLI: {e}")

# Application owners backfill
    try:
        from app.commands import backfill_application_owners
        backfill_application_owners.init_app(app)
        app.logger.info("✅ Application owners backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register application owners backfill CLI: {e}")

    # Cost fact store backfill
    try:
        from app.commands import backfill_cost_facts
        backfill_cost_facts.init_app(app)
        app.logger.info("✅ Cost fact backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register cost fact backfill CLI: {e}")

    try:
        from app.commands.clear_foreign_assignees import init_app as init_clear_foreign_assignees
        init_clear_foreign_assignees(app)
        app.logger.info("✅ Clear foreign assignees CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register clear foreign assignees CLI: {e}")

    try:
        from app.commands.scan_eol_alerts import init_app as init_scan_eol_alerts
        init_scan_eol_alerts(app)
        app.logger.info("✅ End-of-support alert scan CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register end-of-support alert scan CLI: {e}")

# One risk register: copy solution_risks rows into the canonical risks table
    try:
        from app.commands.backfill_solution_risk_merge import init_app as init_solution_risk_merge
        init_solution_risk_merge(app)
        app.logger.info("✅ Solution risk merge backfill CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register solution risk merge backfill CLI: {e}")

    # Gap register consolidation: merge roadmap_gaps, implementation_gaps and compliance_gaps into gaps
    try:
        from app.commands.consolidate_gaps import init_app as init_consolidate_gaps
        init_consolidate_gaps(app)
        app.logger.info("✅ Gap register consolidation CLI command registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register gap register consolidation CLI: {e}")

    # unified_work_packages gained TenantMixin; four other stores merge into it
    try:
        from app.commands.consolidate_work_packages import init_app as init_consolidate_work_packages
        init_consolidate_work_packages(app)
        app.logger.info("✅ Work package consolidation CLI commands registered")
    except Exception as e:
        app.logger.warning(f"⚠️  Failed to register work package consolidation CLI: {e}")
