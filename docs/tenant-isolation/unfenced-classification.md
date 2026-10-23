# Unprotected tables, classified

Produced by `scripts/classify_unfenced_tables.py` from the model metadata. Analysis only: no change to app/, migrations or the table lists.

## Counts per class

| Class | Tables |
|---|---|
| customer_owned | 348 |
| shared_reference | 51 |
| tenant_root_or_identity | 15 |
| unused | 55 |
| **total** | **469** |

Low-confidence calls: 63. Medium-confidence calls: 64.

Protected parents used as owners that are fenced in code but missing from `scripts/tenant_tables.txt`: analysis_audit_logs, analysis_recommendations, analysis_scenarios, capability_framework_configuration, compliance_requirements, enterprise_architecture_frameworks, framework_adoptions, framework_configuration_templates, framework_extensions, framework_migration_mappings, framework_validation_rules, industry_apqc_framework, industry_apqc_process, industry_frameworks, permission_grants, portfolio_initiatives, quality_frameworks, reference_model, reference_model_capability, required_capabilities, system_boundaries, tasks, unified_capabilities, vendor_capability_hierarchy, vendor_options, vendor_process_hierarchy. Hybrid mixins leave `organization_id` nullable, so a null there means a shared row.

## Tenant root and identity (not batched)

- **feature_flags**: platform-level flags, evaluated per organisation but the rows are global; not tenant data
- **oauth_authorization_codes**: OAuth codes: carry organization_id and user_id; resolved by code, which fixes the organisation
- **oauth_clients**: OAuth client registrations; looked up by client id before any organisation is known, so isolation is by token, not by filter
- **oauth_tokens**: OAuth access tokens: carry organization_id and user_id; resolved by token, which fixes the organisation
- **org_roles**: an organisation's membership roles: carries organization_id; scoped by membership
- **organizations**: the tenant root: each row is an organisation; access is by membership, not by an organization_id filter
- **pending_invitations**: invitations into an organisation: carries organization_id; scoped by membership
- **permissions**: platform permission catalogue shared by every organisation; read by name
- **role_permissions**: links platform roles to platform permissions; same rows for every organisation
- **roles**: platform role definitions shared by every organisation; not tenant data, read by role name
- **sso_configs**: an organisation's sign-in provider settings: carries organization_id; scoped by membership
- **user_preferences**: per-user settings; scoped by user_id, which belongs to one organisation through users
- **user_roles**: links users to roles; isolated through users.organization_id (scoped by membership)
- **user_sessions**: sign-in sessions: carries organization_id and user_id; scoped by the signed-in user
- **users**: users carry organization_id; scoped by membership (the signed-in user's active organisation)

## Shared reference: writer check

- **acm_cross_domain_rules** (AcmCrossDomainRule), reads 3, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **acm_domain_templates** (AcmDomainTemplate), reads 7, writes 0: no organization_id and no foreign key to an owner; no request route writes it; writers: app/modules/architecture_assistant/acm_domain_service.py:AcmDomainService.populate_domains (seeder), app/modules/architecture_assistant/journey_orchestrator.py:JourneyOrchestrator.backfill_domain_elements (helper)
  - writer app/modules/architecture_assistant/acm_domain_service.py:AcmDomainService.populate_domains (seeder, edits rows (heuristic))
  - writer app/modules/architecture_assistant/journey_orchestrator.py:JourneyOrchestrator.backfill_domain_elements (helper, edits rows (heuristic)); reached from routes app/modules/ai_chat/routes/chat_core.py:send_message, app/modules/codegen/routes/codegen_routes.py:generate_code, app/modules/codegen/routes/codegen_routes.py:generate_stream, app/modules/codegen/routes/codegen_routes.py:verify_code
- **adm_deliverables** (ADMDeliverable), reads 5, writes 1: no organization_id and no foreign key to an owner; no request route writes it; writers: app/commands/seed_adm_deliverables.py:seed_adm_deliverables_command (cli), app/services/adm_deliverable_service.py:seed_deliverables (seeder), app/services/adm_deliverable_service.py:toggle_deliverable (helper), app/services/kanban_projection_service.py:KanbanProjectionService._move_deliverable (helper)
  - writer app/commands/seed_adm_deliverables.py:seed_adm_deliverables_command (cli, creates rows)
  - writer app/services/adm_deliverable_service.py:seed_deliverables (seeder, creates rows)
  - writer app/services/adm_deliverable_service.py:toggle_deliverable (helper, edits rows (heuristic)); reached from routes app/modules/architecture/routes/adm_kanban_v2_routes.py:check_deliverable
  - writer app/services/kanban_projection_service.py:KanbanProjectionService._move_deliverable (helper, edits rows (heuristic)); reached from routes app/modules/architecture/routes/adm_kanban_v2_routes.py:create_task, app/modules/architecture/routes/adm_kanban_v2_routes.py:get_cards, app/modules/architecture/routes/adm_kanban_v2_routes.py:get_metrics, app/modules/architecture/routes/adm_kanban_v2_routes.py:move_card
- **ai_service_configs** (AIServiceConfig), reads 1, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **application_metrics_snapshots** (ApplicationMetricsSnapshot), reads 0, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **application_portfolio_summary** (ApplicationPortfolio), reads 1, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **apqc_process** (APQCProcess), reads 109, writes 2: no organization_id and no foreign key to an owner; no request route writes it; writers: app/commands/seed_capabilities.py:seed_apqc_processes (cli), app/modules/industry_apqc/routes/apqc_api_routes.py:save_process_mappings (route), app/modules/vendors/services/seed_apqc_vendor_mapping.py:APQCVendorSeedingService.seed_apqc_processes (seeder), app/modules/vendors/services/vendor_process_mapping_service.py:VendorProcessMappingService.seed_apqc_processes (seeder)
  - writer app/commands/seed_capabilities.py:seed_apqc_processes (cli, creates rows)
  - writer app/modules/industry_apqc/routes/apqc_api_routes.py:save_process_mappings (route, edits rows (heuristic)); decorators: login_required
  - writer app/modules/vendors/services/seed_apqc_vendor_mapping.py:APQCVendorSeedingService.seed_apqc_processes (seeder, creates rows)
  - writer app/modules/vendors/services/vendor_process_mapping_service.py:VendorProcessMappingService.seed_apqc_processes (seeder, raw SQL write)
  - writer app/services/faiss_apqc_service.py:FAISSAPQCService._build_faiss_index (helper, edits rows (heuristic)); reached from routes app/api/ba_phase_b_routes.py:run_phase_b, app/api/coverage_matrix_routes.py:estimate_coverage, app/api/coverage_matrix_routes.py:generate_matrix, app/api/coverage_matrix_routes.py:get_gap_analysis
  - writer app/services/faiss_apqc_service.py:FAISSAPQCService._build_simple_index (helper, edits rows (heuristic)); reached from routes app/api/ba_phase_b_routes.py:run_phase_b, app/api/coverage_matrix_routes.py:estimate_coverage, app/api/coverage_matrix_routes.py:generate_matrix, app/api/coverage_matrix_routes.py:get_gap_analysis
- **apqc_process_hierarchy** (ApqcProcessHierarchy), reads 5, writes 1: no organization_id and no foreign key to an owner; no request route writes it; writers: app/seeds/apqc_process_hierarchy_seed.py:seed_hierarchy (seeder)
  - writer app/seeds/apqc_process_hierarchy_seed.py:seed_hierarchy (seeder, creates rows)
- **arb_waiver_expiry_checkpoints** (ARBWaiverExpiryCheckpoint), reads 1, writes 1: checkpoint rows for the scheduled waiver-expiry job, written by the job (app bootstrap scheduler) and the process_arb_waiver_expiries CLI, not by requests
  - writer app/modules/transformation_room/arb_waiver_expiry_batch_service.py:ARBWaiverExpiryBatchService._select_due (helper, creates rows)
- **archimate_relationship_rules** (ArchiMateRelationshipRule), reads 7, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **capability_domain_definitions** (CapabilityDomainDefinition), reads 2, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **capability_gap_dashboard** (CapabilityGapDashboard), reads 0, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **capability_level_definitions** (CapabilityLevelDefinition), reads 2, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **capability_vendor_product_mapping** (CapabilityVendorProductMapping), reads 0, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **cobit_domains** (COBITDomain), reads 2, writes 2: no organization_id and no foreign key to an owner; no request route writes it; writers: app/seed_data/catalogue_importer.py:VendorCatalogueImporter._ensure_cobit (seeder)
  - writer app/seed_data/catalogue_importer.py:VendorCatalogueImporter._ensure_cobit (seeder, creates rows)
- **cobit_processes** (COBITProcess), reads 4, writes 1: no organization_id and no foreign key to an owner; no request route writes it; writers: app/seed_data/catalogue_importer.py:VendorCatalogueImporter._ensure_cobit (seeder)
  - writer app/seed_data/catalogue_importer.py:VendorCatalogueImporter._ensure_cobit (seeder, creates rows)
- **compliance_controls** (ComplianceControl), reads 18, writes 6: no organization_id and no link to an owner except an audit user column; no request route writes it; writers: app/modules/compliance/services/applicability_service.py:ApplicabilityService.confirm_harmonization (helper), app/modules/compliance/services/applicability_service.py:ApplicabilityService.propose_harmonization (helper), app/modules/enterprise_genome/seeds/seed_security_demo.py:seed_security_demo (seeder), app/services/compliance/regulatory_framework_service.py:RegulatoryFrameworkService.seed_manufacturing_frameworks (seeder)
  - writer app/modules/compliance/services/applicability_service.py:ApplicabilityService.confirm_harmonization (helper, edits rows (heuristic)); reached from routes app/application_mgmt/framework_catalogue_routes.py:adopt_framework, app/application_mgmt/framework_catalogue_routes.py:confirm_harmonization, app/application_mgmt/framework_catalogue_routes.py:get_framework_scope, app/application_mgmt/framework_catalogue_routes.py:list_adopted_controls
  - writer app/modules/compliance/services/applicability_service.py:ApplicabilityService.propose_harmonization (helper, edits rows (heuristic)); reached from routes app/application_mgmt/framework_catalogue_routes.py:adopt_framework, app/application_mgmt/framework_catalogue_routes.py:confirm_harmonization, app/application_mgmt/framework_catalogue_routes.py:get_framework_scope, app/application_mgmt/framework_catalogue_routes.py:list_adopted_controls
  - writer app/modules/enterprise_genome/seeds/seed_security_demo.py:seed_security_demo (seeder, creates rows)
  - writer app/services/compliance/regulatory_framework_service.py:RegulatoryFrameworkService.seed_manufacturing_frameworks (seeder, creates rows)
  - writer scripts/capture_screenshots.py:_seed_module_screen_data (seeder, creates rows)
- **consulting_partners** (ConsultingPartner), reads 0, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it; writers: app/commands/seed_consulting_partners.py:seed_consulting_partners (cli)
  - writer app/commands/seed_consulting_partners.py:seed_consulting_partners (cli, creates rows)
- **decision_authority** (DecisionAuthority), reads 0, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **dod_templates** (DoDTemplate), reads 6, writes 1: no organization_id and no foreign key to an owner; no request route writes it; writers: app/services/dod_service.py:seed_default_templates (seeder), app/services/dod_service.py:update_checks (helper)
  - writer app/services/dod_service.py:seed_default_templates (seeder, creates rows)
  - writer app/services/dod_service.py:update_checks (helper, edits rows (heuristic)); reached from routes app/modules/architecture/routes/dod_routes.py:update_criterion
- **element_template_recommendations** (ElementTemplateRecommendation), reads 1, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **exception_process** (ExceptionProcess), reads 0, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **exchange_rates** (ExchangeRate), reads 2, writes 1: record_exchange_rate raises PermissionError unless the actor is a platform administrator and nothing in app/ calls it
  - writer app/services/currency_service.py:record_exchange_rate (helper, creates rows)
- **import_history** (ImportHistory), reads 1, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **integration_patterns** (IntegrationPattern), reads 11, writes 0: no organization_id and no foreign key to an owner; no request route writes it; writers: app/commands/seed_integration_patterns.py:seed_integration_patterns (cli)
  - writer app/commands/seed_integration_patterns.py:seed_integration_patterns (cli, creates rows)
- **itil_practices** (ITILPractice), reads 2, writes 1: no organization_id and no foreign key to an owner; no request route writes it; writers: app/seed_data/catalogue_importer.py:VendorCatalogueImporter._ensure_itil (seeder)
  - writer app/seed_data/catalogue_importer.py:VendorCatalogueImporter._ensure_itil (seeder, creates rows)
- **manufacturing_domain_hierarchy** (ManufacturingDomainHierarchy), reads 6, writes 2: no organization_id and no foreign key to an owner; no request route writes it; writers: app/modules/admin/v2/services/manufacturing_domain_hierarchy_seeder_v2.py:ManufacturingDomainHierarchySeeder._create_or_update_domain (seeder), app/services/manufacturing_domain_hierarchy_seeder.py:ManufacturingDomainHierarchySeeder._create_or_update_domain (seeder)
  - writer app/modules/admin/v2/services/manufacturing_domain_hierarchy_seeder_v2.py:ManufacturingDomainHierarchySeeder._create_or_update_domain (seeder, creates rows)
  - writer app/services/manufacturing_domain_hierarchy_seeder.py:ManufacturingDomainHierarchySeeder._create_or_update_domain (seeder, creates rows)
- **mapping_metrics** (MappingMetric), reads 0, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **platform_configurations** (PlatformConfiguration), reads 3, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **product_taxonomy** (ProductTaxonomy), reads 4, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **rationalization_policies** (RationalizationPolicy), reads 2, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **regulatory_frameworks** (RegulatoryFramework), reads 14, writes 5: framework catalogue (for example SOC 2 and ISO); only seeders create it (seed_manufacturing_frameworks, seed_security_demo)
  - writer app/modules/enterprise_genome/seeds/seed_security_demo.py:_get_or_create_framework (seeder, creates rows)
  - writer app/services/compliance/regulatory_framework_service.py:RegulatoryFrameworkService.seed_manufacturing_frameworks (seeder, creates rows)
  - writer scripts/capture_screenshots.py:_seed_module_screen_data (seeder, creates rows)
- **requirement_template** (RequirementTemplate), reads 8, writes 0: no organization_id and no foreign key to an owner; no request route writes it; writers: app/modules/capabilities/routes/enterprise_api_routes.py:auto_classify_requirement (route), app/modules/capabilities/routes/enterprise_api_routes.py:batch_enrich_requirements (route), app/modules/capabilities/routes/enterprise_api_routes.py:create_requirement (route), app/modules/capabilities/routes/enterprise_api_routes.py:generate_requirement_ac (route)
  - writer app/modules/capabilities/routes/enterprise_api_routes.py:auto_classify_requirement (route, edits rows (heuristic)); decorators: login_required
  - writer app/modules/capabilities/routes/enterprise_api_routes.py:batch_enrich_requirements (route, edits rows (heuristic)); decorators: login_required
  - writer app/modules/capabilities/routes/enterprise_api_routes.py:create_requirement (route, edits rows (heuristic)); decorators: login_required
  - writer app/modules/capabilities/routes/enterprise_api_routes.py:generate_requirement_ac (route, edits rows (heuristic)); decorators: login_required
  - writer manage.py:_seed_requirement_templates (seeder, creates rows)
- **resources** (RoadmapResource), reads 0, writes 0: no organization_id and no foreign key to an owner; no request route writes it; writers: app/_bootstrap/services.py:_init_opentelemetry (seeder), app/application_mgmt/detail_layer_routes.py:update_strategy_layer (route), app/modules/applications/routes/update_routes.py:update_strategy_layer (route)
  - writer app/_bootstrap/services.py:_init_opentelemetry (seeder, creates rows)
  - writer app/application_mgmt/detail_layer_routes.py:update_strategy_layer (route, edits rows (heuristic)); decorators: login_required
  - writer app/modules/applications/routes/update_routes.py:update_strategy_layer (route, edits rows (heuristic)); decorators: login_required
- **roadmap_scenarios** (RoadmapScenario), reads 0, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **scoring_configs** (ScoringConfig), reads 6, writes 2: platform scoring weights; seed_defaults is a seeder and update_config has no caller in app/
  - writer app/services/scoring_engine_service.py:ScoringEngineService.seed_defaults (seeder, creates rows)
  - writer app/services/scoring_engine_service.py:ScoringEngineService.update_config (helper, creates rows)
- **service_incidents** (ServiceIncident), reads 5, writes 1: platform status incidents; open_incident and resolve_incident are called only from the service_incident CLI commands
  - writer app/modules/monitoring/services/service_status.py:open_incident (helper, creates rows)
  - writer app/modules/monitoring/services/service_status.py:resolve_incident (helper, edits rows (heuristic))
- **sidebar_menu_items** (SidebarMenuItem), reads 14, writes 0: no organization_id and no foreign key to an owner; no request route writes it; writers: app/modules/admin/routes/sidebar_mgmt_routes.py:reset_all_items (route), app/modules/admin/routes/sidebar_mgmt_routes.py:toggle_section (route), app/modules/admin/routes/sidebar_mgmt_routes.py:toggle_sidebar_item (route), app/modules/admin/routes/sidebar_mgmt_routes.py:toggle_subsection (route)
  - writer app/modules/admin/routes/sidebar_mgmt_routes.py:reset_all_items (route, edits rows (heuristic)); decorators: login_required,platform_admin_required
  - writer app/modules/admin/routes/sidebar_mgmt_routes.py:toggle_section (route, edits rows (heuristic)); decorators: login_required,platform_admin_required
  - writer app/modules/admin/routes/sidebar_mgmt_routes.py:toggle_sidebar_item (route, edits rows (heuristic)); decorators: login_required,platform_admin_required
  - writer app/modules/admin/routes/sidebar_mgmt_routes.py:toggle_subsection (route, edits rows (heuristic)); decorators: login_required,platform_admin_required
  - writer app/modules/admin/v2/routes/sidebar_mgmt_routes.py:reset_all_items (route, edits rows (heuristic)); decorators: platform_admin_required
  - writer app/modules/admin/v2/routes/sidebar_mgmt_routes.py:toggle_section (route, edits rows (heuristic)); decorators: platform_admin_required
  - writer app/modules/admin/v2/routes/sidebar_mgmt_routes.py:toggle_sidebar_item (route, edits rows (heuristic)); decorators: platform_admin_required
  - writer app/modules/admin/v2/routes/sidebar_mgmt_routes.py:toggle_subsection (route, edits rows (heuristic)); decorators: platform_admin_required
- **taxonomy_mappings** (TaxonomyMapping), reads 3, writes 1: vendor taxonomy mappings; create_taxonomy_mapping and validate_mapping have no caller in app/
  - writer app/modules/vendors/services/vendor_mdm.py:VendorMDMService.create_taxonomy_mapping (helper, creates rows); reached from routes app/modules/vendors/routes/vendor_mdm_api.py:bulk_normalize, app/modules/vendors/routes/vendor_mdm_api.py:find_duplicates, app/modules/vendors/routes/vendor_mdm_api.py:get_reconciliation_candidates, app/modules/vendors/routes/vendor_mdm_api.py:import_external_data
  - writer app/modules/vendors/services/vendor_mdm.py:VendorMDMService.validate_mapping (helper, edits rows (heuristic)); reached from routes app/application_mgmt/analytics_routes.py:ai_portfolio_analysis, app/application_mgmt/archimate_routes.py:archimate_element_generation, app/application_mgmt/vendor_analysis_routes.py:api_export_vendor_analysis, app/modules/applications/routes/vendor_api_routes.py:api_export_vendor_analysis
- **tco_benchmarks** (TCOBenchmark), reads 2, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **technical_capabilities** (TechnicalCapability), reads 68, writes 2: the ACM technical capability catalogue; the update and delete routes carry platform_admin_required, creation is by the seed_capabilities CLI and seeders; the foreign key to unified_capabilities is a retirement redirect, not an owner; the helpers the trace lists as editing rows write application-to-capability mappings, not this table
  - 9 writer sites: 1 request routes (1 with platform_admin_required, 0 creating or deleting rows without it), 3 seeders or CLI, 5 helpers
  - 'edits rows' sites are a heuristic (the function names the model and sets attributes) and are counted but not listed
- **technology_capabilities** (TechnologyCapability), reads 0, writes 0: no organization_id and no foreign key to an owner; no request route writes it
  - no writer found in app/
- **threshold_configurations** (ThresholdConfiguration), reads 0, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **usage_analytics** (UsageAnalytics), reads 4, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **vendor_comparison_criteria** (VendorComparisonCriteria), reads 0, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **vendor_data_sources** (VendorDataSource), reads 0, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **vendor_organization_embeddings** (VendorOrganizationEmbedding), reads 1, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **vendor_organizations** (VendorOrganization), reads 259, writes 29: writes limited to platform administrators (PR 867); private vendors planned
  - 69 writer sites: 28 request routes (17 with platform_admin_required, 1 creating or deleting rows without it), 14 seeders or CLI, 27 helpers
  - check: on this base 17 request routes carry platform_admin_required, but import_routes.auto_create_vendors (login_required only) still creates vendor rows, and the AI chat create_vendor helper and the vendor catalogue service create/update are reached from login_required routes; confirm PR 867 is in the base before relying on the note
  - 'edits rows' sites are a heuristic (the function names the model and sets attributes) and are counted but not listed
  - route without platform_admin_required: app/application_mgmt/import_routes.py:auto_create_vendors (creates rows); decorators: login_required
- **vendor_product_details** (VendorProductDetail), reads 2, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/
- **vendor_product_families** (VendorProductFamily), reads 15, writes 1: vendor product catalogue; written by the end-of-life sync (sync_all, no caller in app/) and the standardize_vendor_domains CLI
  - writer app/services/eol_sync_service.py:sync_product_eol (helper, edits rows (heuristic))
  - writer manage.py:register_cli_commands.standardize_vendor_domains (cli, creates rows)
- **workflow_templates** (WorkflowTemplate), reads 5, writes 0: no organization_id and no link to an owner except an audit user column; no request route writes it
  - no writer found in app/

## Batches (customer-owned, most used first)

Batch 1 and the batches that follow it hold the application and capability tables (owner read from application_components or the capability tables), mapping and coverage tables first. The remaining parents follow, the most read and written first. A batch holds 20 to 40 tables; a large parent is split, so usage falls within a parent but a small parent can fill the end of a batch.

### Batch 1 (27 tables, 660 reads+writes)

- **application_business_actor_mapping** (ApplicationBusinessActorMapping) reads 3, writes 2; owner path: application_component_id -> application_components.organization_id; high
- **application_capability_coverage** (ApplicationCapabilityCoverage) reads 23, writes 3; owner path: application_component_id -> application_components.organization_id; high
- **application_capability_mapping** (ApplicationCapabilityMapping) reads 82, writes 14; owner path: own organization_id column; high
- **application_interface_mapping** (ApplicationInterfaceMapping) reads 0, writes 0; owner path: application_component_id -> application_components.organization_id; high
- **application_owners** (ApplicationOwner) reads 24, writes 5; owner path: own organization_id column; high
- **application_process_support** (ApplicationProcessSupport) reads 18, writes 10; owner path: application_component_id -> application_components.organization_id; high
- **application_requirement_mapping** (ApplicationRequirementMapping) reads 1, writes 1; owner path: application_component_id -> application_components.organization_id; high
- **application_services** (ApplicationService) reads 11, writes 13; owner path: own organization_id column; high
- **application_technology_mapping** (ApplicationTechnologyMapping) reads 6, writes 0; owner path: application_component_id -> application_components.organization_id; high
- **application_vendor_product_mappings** (ApplicationVendorProductMapping) reads 4, writes 3; owner path: application_component_id -> application_components.organization_id; high
- **batch_import_element** (BatchImportElement) reads 20, writes 2; owner path: application_id -> batch_import_application.committed_application_id -> application_components.organization_id; medium
- **capability_process_mapping** (CapabilityProcessMapping) reads 12, writes 5; owner path: capability_id -> business_capability.organization_id; high
- **cobit_capability_mapping** (COBITCapabilityMapping) reads 0, writes 0; owner path: capability_id -> enterprise_capabilities.business_capability_id -> business_capability.organization_id; medium
- **consolidation_list_entries** (ConsolidationListEntry) reads 18, writes 6; owner path: application_id -> application_components.organization_id; high
- **consolidation_opportunities** (ConsolidationOpportunity) reads 11, writes 1; owner path: target_application_id -> application_components.organization_id; high
- **deliverables** (Deliverable) reads 23, writes 9; owner path: own organization_id column; high
- **duplicate_app_process_mapping** (ApplicationProcessMapping) reads 5, writes 0; owner path: application_id -> application_components.organization_id; high
- **implementation_work_packages** (ImplementationWorkPackage) reads 58, writes 9; owner path: application_component_id -> application_components.organization_id; high
- **itil_capability_mapping** (ITILCapabilityMapping) reads 0, writes 0; owner path: capability_id -> enterprise_capabilities.business_capability_id -> business_capability.organization_id; medium
- **options_analysis** (OptionsAnalysis) reads 37, writes 3; owner path: own organization_id column; high
- **solution_capability_mappings** (SolutionCapabilityMapping) reads 63, writes 28; owner path: capability_id -> business_capability.organization_id; high
- **solution_tco_items** (SolutionTCOItem) reads 10, writes 6; owner path: solution_id -> solutions.organization_id; high
- **unified_application_capability_mapping** (UnifiedApplicationCapabilityMapping) reads 58, writes 15; owner path: application_component_id -> application_components.organization_id; high
- **unified_capability_application_mappings** (UnifiedCapabilityApplicationMapping) reads 3, writes 2; owner path: application_component_id -> application_components.organization_id; high
- **unified_capability_process_mapping** (UnifiedCapabilityProcessMapping) reads 0, writes 0; owner path: capability_id -> unified_capabilities.organization_id; high
- **unified_capability_vendor_organization_mappings** (UnifiedCapabilityVendorOrganizationMapping) reads 2, writes 2; owner path: unified_capability_id -> unified_capabilities.organization_id; high
- **unified_work_packages** (UnifiedWorkPackage) reads 19, writes 10; owner path: own organization_id column; high

### Batch 2 (26 tables, 148 reads+writes)

- **application_collaborations** (ApplicationCollaboration) reads 3, writes 2; owner path: own organization_id column; high
- **application_costs** (ApplicationCost) reads 2, writes 1; owner path: application_id -> application_components.organization_id; high
- **application_documents** (ApplicationDocument) reads 9, writes 2; owner path: own organization_id column; high
- **application_interactions** (ApplicationInteraction) reads 3, writes 2; owner path: own organization_id column; high
- **application_similarity_analysis** (ApplicationSimilarityAnalysis) reads 4, writes 2; owner path: app_1_id -> application_components.organization_id; high
- **arb_submission_packs** (ARBSubmissionPack) reads 1, writes 1; owner path: solution_id -> application_components.organization_id; high
- **architecture_documents** (ArchitectureDocument) reads 3, writes 1; owner path: application_id -> application_components.organization_id; high
- **batch_import_application** (BatchImportApplication) reads 6, writes 1; owner path: committed_application_id -> application_components.organization_id; high
- **batch_import_checkpoint** (BatchImportCheckpoint) reads 1, writes 5; owner path: application_id -> batch_import_application.committed_application_id -> application_components.organization_id; medium
- **business_capability_embeddings** (BusinessCapabilityEmbedding) reads 3, writes 1; owner path: business_capability_id -> business_capability.organization_id; high
- **capability_tag_associations** (CapabilityTagAssociation) reads 3, writes 1; owner path: capability_id -> unified_capabilities.organization_id; high
- **capability_taxonomy_audit** (CapabilityTaxonomyAudit) reads 2, writes 2; owner path: capability_id -> unified_capabilities.organization_id; high
- **capability_taxonomy_violations** (CapabilityTaxonomyViolation) reads 5, writes 0; owner path: capability_id -> unified_capabilities.organization_id; high
- **change_impact_assessments** (ChangeImpactAssessment) reads 2, writes 1; owner path: affected_capability_id -> capabilities.organization_id; high
- **consolidation_candidates** (ConsolidationCandidate) reads 6, writes 1; owner path: duplicate_application_id -> application_components.organization_id; high
- **decision_capability_links** (DecisionCapabilityLink) reads 6, writes 1; owner path: capability_id -> business_capability.organization_id; high
- **decommission_plans** (DecommissionPlan) reads 2, writes 1; owner path: application_id -> application_components.organization_id; high
- **duplicate_analyses** (DuplicateAnalysis) reads 2, writes 2; owner path: application_1_id -> application_components.organization_id; high
- **element_template_usage** (ElementTemplateUsage) reads 6, writes 3; owner path: application_id -> application_components.organization_id; high
- **integration_contracts** (IntegrationContract) reads 6, writes 2; owner path: application_id -> application_components.organization_id; high
- **jira_sync_tracking** (JiraSyncTracking) reads 2, writes 1; owner path: application_id -> application_components.organization_id; high
- **manufacturing_capabilities** (ManufacturingCapability) reads 8, writes 1; owner path: unified_capability_id -> unified_capabilities.organization_id; high
- **rationalization_benefits** (RationalizationBenefitsTracker) reads 5, writes 3; owner path: application_id -> application_components.organization_id; high
- **replacement_plans** (ReplacementPlan) reads 3, writes 1; owner path: source_app_id -> application_components.organization_id; high
- **savings_realizations** (SavingsRealization) reads 4, writes 1; owner path: opportunity_id -> consolidation_opportunities.target_application_id -> application_components.organization_id; medium
- **solution_fit_gap_entries** (SolutionFitGapEntry) reads 11, writes 1; owner path: capability_id -> capabilities.organization_id; high

### Batch 3 (25 tables, 15 reads+writes)

- **application_business_metrics** (ApplicationBusinessMetric) reads 0, writes 0; owner path: application_id -> application_components.organization_id; high
- **application_component_embeddings** (ApplicationComponentEmbedding) reads 1, writes 0; owner path: application_component_id -> application_components.organization_id; high
- **application_performance_metrics** (ApplicationPerformanceMetrics) reads 0, writes 0; owner path: own organization_id column; medium
- **application_roi** (ApplicationROI) reads 0, writes 0; owner path: application_id -> application_components.organization_id; high
- **application_technology_instances** (ApplicationTechnologyInstance) reads 0, writes 0; owner path: application_id -> application_components.organization_id; high
- **application_usage** (ApplicationUsage) reads 0, writes 0; owner path: application_id -> application_components.organization_id; high
- **application_versioning** (ApplicationVersioning) reads 0, writes 0; owner path: own organization_id column; medium
- **archimate_capabilities** (ArchiMateCapability) reads 0, writes 1; owner path: business_capability_id -> business_capability.organization_id; high
- **consolidation_recommendations** (ConsolidationRecommendation) reads 0, writes 1; owner path: target_application_id -> application_components.organization_id; high
- **contract_applications** (ContractApplication) reads 1, writes 0; owner path: own organization_id column; high
- **deployment_pipelines** (DeploymentPipeline) reads 0, writes 0; owner path: own organization_id column; medium
- **enterprise_capabilities** (EnterpriseCapability) reads 0, writes 0; owner path: business_capability_id -> business_capability.organization_id; high
- **governance_system_links** (GovernanceSystemLink) reads 0, writes 0; owner path: application_id -> application_components.organization_id; high
- **governance_workflows** (GovernanceWorkflow) reads 0, writes 1; owner path: capability_id -> unified_capabilities.organization_id; high
- **integration_impact_registers** (IntegrationImpactRegister) reads 1, writes 1; owner path: target_app_id -> application_components.organization_id; high
- **platform_migration_scopes** (PlatformMigrationScope) reads 1, writes 1; owner path: source_platform_app_id -> application_components.organization_id; high
- **project_constraints** (ProjectConstraint) reads 1, writes 1; owner path: applies_to_capability_id -> business_capability.organization_id; high
- **quality_attributes** (QualityAttribute) reads 0, writes 1; owner path: applies_to_capability_id -> business_capability.organization_id; high
- **rationalization_audit_entries** (RationalizationAuditEntry) reads 1, writes 1; owner path: application_id -> application_components.organization_id; high
- **refactoring_tracking** (RefactoringTracking) reads 0, writes 0; owner path: application_component_id -> application_components.organization_id; high
- **rfp_templates** (RFPTemplate) reads 0, writes 0; owner path: capability_id -> business_capability.organization_id; high
- **roadmap_gaps** (RoadmapGap) reads 0, writes 1; owner path: source_application_id -> application_components.organization_id; high
- **service_level_agreements** (ServiceLevelAgreement) reads 0, writes 0; owner path: capability_id -> business_capability.organization_id; high
- **sla_violations** (SLAViolation) reads 0, writes 0; owner path: sla_id -> service_level_agreements.capability_id -> business_capability.organization_id; medium
- **technical_debt** (TechnicalDebt) reads 0, writes 0; owner path: own organization_id column; medium

### Batch 4 (25 tables, 640 reads+writes)

- **codegen_drift_reports** (CodegenDriftReport) reads 2, writes 3; owner path: solution_id -> solutions.organization_id; high
- **codegen_generation_history** (CodegenGenerationHistory) reads 15, writes 4; owner path: codegen_generation_id -> codegen_generations.solution_id -> solutions.organization_id; medium
- **codegen_generations** (CodegenGeneration) reads 94, writes 8; owner path: solution_id -> solutions.organization_id; high
- **codegen_solution_connectors** (SolutionConnector) reads 8, writes 2; owner path: solution_id -> solutions.organization_id; high
- **codegen_solution_instances** (SolutionInstance) reads 15, writes 2; owner path: solution_id -> solutions.organization_id; high
- **codegen_solution_rules** (SolutionRule) reads 9, writes 2; owner path: solution_id -> solutions.organization_id; high
- **codegen_test_runs** (TestRun) reads 3, writes 2; owner path: solution_id -> solutions.organization_id; high
- **copilot_insights** (CopilotInsight) reads 2, writes 5; owner path: solution_id -> solutions.organization_id; high
- **published_api_specs** (PublishedAPISpec) reads 13, writes 2; owner path: solution_id -> solutions.organization_id; high
- **solution_ai_reasoning_states** (SolutionAIReasoningState) reads 16, writes 8; owner path: solution_id -> solutions.organization_id; high
- **solution_app_elements** (SolutionAppElement) reads 11, writes 4; owner path: solution_id -> solutions.organization_id; high
- **solution_apqc_processes** (SolutionAPQCProcess) reads 11, writes 3; owner path: solution_id -> solutions.organization_id; high
- **solution_archimate_elements** (SolutionArchiMateElement) reads 195, writes 68; owner path: solution_id -> solutions.organization_id; high
- **solution_benefit_realizations** (SolutionBenefitRealization) reads 5, writes 2; owner path: solution_id -> solutions.organization_id; high
- **solution_business_elements** (SolutionBusinessElement) reads 7, writes 3; owner path: solution_id -> solutions.organization_id; high
- **solution_code_bundles** (SolutionCodeBundle) reads 7, writes 2; owner path: solution_id -> solutions.organization_id; high
- **solution_comments** (SolutionComment) reads 7, writes 4; owner path: solution_id -> solutions.organization_id; high
- **solution_notifications** (SolutionNotification) reads 3, writes 6; owner path: solution_id -> solutions.organization_id; high
- **solution_outcome_tracking** (SolutionOutcomeTracking) reads 11, writes 1; owner path: solution_id -> solutions.organization_id; high
- **solution_plateaus** (SolutionPlateau) reads 12, writes 15; owner path: solution_id -> solutions.organization_id; high
- **solution_risk_snapshots** (RiskSnapshot) reads 5, writes 2; owner path: solution_id -> solutions.organization_id; high
- **solution_slas** (SolutionSLA) reads 13, writes 3; owner path: solution_id -> solutions.organization_id; high
- **solution_stakeholders_sad** (SolutionStakeholderSAD) reads 5, writes 3; owner path: solution_id -> solutions.organization_id; high
- **solution_tech_elements** (SolutionTechElement) reads 8, writes 3; owner path: solution_id -> solutions.organization_id; high
- **spec_webhooks** (SpecWebhook) reads 5, writes 1; owner path: solution_id -> solutions.organization_id; high

### Batch 5 (25 tables, 79 reads+writes)

- **ai_audit_logs** (AIAuditLog) reads 2, writes 1; owner path: solution_id -> solutions.organization_id; high
- **architecture_generation_runs** (ArchitectureGenerationRun) reads 0, writes 1; owner path: solution_id -> solutions.organization_id; high
- **codegen_chat_messages** (CodegenChatMessage) reads 0, writes 1; owner path: solution_id -> solutions.organization_id; high
- **codegen_data_imports** (DataImport) reads 0, writes 2; owner path: solution_id -> solutions.organization_id; high
- **codegen_scenario_results** (ScenarioResult) reads 2, writes 2; owner path: solution_id (no foreign key declared) -> solutions.organization_id; medium
- **codegen_system_boundary_solutions** (SystemBoundarySolution) reads 2, writes 2; owner path: solution_id -> solutions.organization_id; high
- **codegen_test_run_steps** (TestRunStep) reads 0, writes 2; owner path: test_run_id -> codegen_test_runs.solution_id -> solutions.organization_id; medium
- **codegen_workflow_designs** (WorkflowDesign) reads 3, writes 2; owner path: solution_id -> solutions.organization_id; high
- **cross_layer_relationships** (CrossLayerRelationship) reads 1, writes 2; owner path: solution_id -> solutions.organization_id; high
- **experience_elements** (ExperienceElement) reads 0, writes 1; owner path: solution_id -> solutions.organization_id; high
- **runtime_compliance_checks** (RuntimeComplianceCheck) reads 2, writes 2; owner path: solution_id -> solutions.organization_id; high
- **solution_ai_backtesting** (SolutionAIBacktesting) reads 4, writes 1; owner path: solution_id -> solutions.organization_id; high
- **solution_assessments_sad** (SolutionAssessmentSAD) reads 2, writes 1; owner path: solution_id -> solutions.organization_id; high
- **solution_change_requests** (SolutionChangeRequest) reads 4, writes 0; owner path: solution_id -> solutions.organization_id; high
- **solution_compositions** (SolutionComposition) reads 4, writes 0; owner path: solution_id -> solutions.organization_id; high
- **solution_embeddings** (SolutionEmbedding) reads 3, writes 0; owner path: solution_id -> solutions.organization_id; high
- **solution_feasibility_reviews** (SolutionFeasibilityReview) reads 2, writes 1; owner path: solution_id -> solutions.organization_id; high
- **solution_investment_phases** (SolutionInvestmentPhase) reads 4, writes 0; owner path: solution_id -> solutions.organization_id; high
- **solution_layer_elements** (SolutionLayerElement) reads 1, writes 2; owner path: solution_id -> solutions.organization_id; high
- **solution_lessons_learned** (SolutionLessonLearned) reads 2, writes 0; owner path: solution_id -> solutions.organization_id; high
- **solution_migration_dependencies** (MigrationDependency) reads 2, writes 0; owner path: solution_id -> solutions.organization_id; high
- **solution_org_impacts** (SolutionOrgImpact) reads 2, writes 1; owner path: solution_id -> solutions.organization_id; high
- **solution_principles_sad** (SolutionPrincipleSAD) reads 2, writes 1; owner path: solution_id -> solutions.organization_id; high
- **solution_templates** (SolutionTemplate) reads 4, writes 1; owner path: source_solution_id -> solutions.organization_id; high
- **traceability_links** (TraceabilityLink) reads 4, writes 1; owner path: solution_id -> solutions.organization_id; high

### Batch 6 (38 tables, 430 reads+writes)

- **agent_configurations** (AgentConfiguration) reads 4, writes 1; owner path: updated_by_id -> users.organization_id; low
- **agent_execution_history** (AgentExecutionHistory) reads 9, writes 3; owner path: executed_by_id -> users.organization_id; low
- **agent_schedules** (AgentSchedule) reads 2, writes 1; owner path: created_by_id -> users.organization_id; low
- **ai_interaction_logs** (AIInteractionLog) reads 0, writes 1; owner path: user_id -> users.organization_id; medium
- **ai_recommendations** (AIRecommendation) reads 6, writes 1; owner path: user_id -> users.organization_id; medium
- **ai_suggestions** (AISuggestion) reads 18, writes 2; owner path: reviewed_by_id -> users.organization_id; low
- **archimate_audit_logs** (ArchimateAuditLog) reads 1, writes 1; owner path: user_id -> users.organization_id; medium
- **archimate_patterns** (ArchimatePattern) reads 3, writes 1; owner path: created_by -> users.organization_id; low
- **archimate_viewpoint_templates** (ArchimateViewpointTemplate) reads 2, writes 1; owner path: created_by -> users.organization_id; low
- **audit_events** (AuditEvent) reads 4, writes 3; owner path: user_id (no foreign key declared) -> users.organization_id; low
- **audit_trail** (AuditTrail) reads 0, writes 2; owner path: user_id -> users.organization_id; medium
- **batch_import_batch** (BatchImportBatch) reads 27, writes 1; owner path: job_id -> batch_import_job.user_id -> users.organization_id; medium
- **batch_import_job** (BatchImportJob) reads 26, writes 1; owner path: user_id -> users.organization_id; medium
- **batch_job_checkpoints** (BatchJobCheckpoint) reads 2, writes 1; owner path: batch_job_id -> batch_jobs.created_by_id -> users.organization_id; low
- **batch_job_errors** (BatchJobError) reads 2, writes 1; owner path: batch_job_id -> batch_jobs.created_by_id -> users.organization_id; low
- **batch_job_items** (BatchJobItem) reads 5, writes 1; owner path: batch_job_id -> batch_jobs.created_by_id -> users.organization_id; low
- **batch_jobs** (BatchJob) reads 13, writes 1; owner path: created_by_id -> users.organization_id; low
- **capability_sets** (CapabilitySet) reads 2, writes 1; owner path: user_id -> users.organization_id; medium
- **chat_message_embeddings** (ChatMessageEmbedding) reads 14, writes 4; owner path: user_id -> users.organization_id; medium
- **codegen_system_boundaries** (CodegenSystemBoundary) reads 0, writes 2; owner path: created_by_id -> users.organization_id; low
- **codegen_template_files** (CodegenTemplateFile) reads 4, writes 3; owner path: set_id -> codegen_template_sets.created_by_id -> users.organization_id; low
- **codegen_template_sets** (CodegenTemplateSet) reads 8, writes 3; owner path: created_by_id -> users.organization_id; low
- **confidence_thresholds** (ConfidenceThreshold) reads 5, writes 6; owner path: created_by_id -> users.organization_id; low
- **conversation_messages** (ConversationMessageRecord) reads 0, writes 0; owner path: thread_id -> conversation_threads.user_id -> users.organization_id; medium
- **conversation_threads** (ConversationThreadRecord) reads 0, writes 0; owner path: user_id -> users.organization_id; medium
- **custom_field_definitions** (CustomFieldDefinition) reads 7, writes 1; owner path: created_by -> users.organization_id; low
- **element_templates** (ElementTemplate) reads 56, writes 0; owner path: created_by -> users.organization_id; low
- **external_systems** (ExternalSystem) reads 36, writes 7; owner path: updated_by_id -> users.organization_id; low
- **impact_analysis_results** (ImpactAnalysisResult) reads 4, writes 4; owner path: created_by_id -> users.organization_id; low
- **import_checkpoints** (ImportCheckpoint) reads 0, writes 1; owner path: import_session_id -> import_sessions.user_id -> users.organization_id; medium
- **import_sessions** (ImportSession) reads 3, writes 1; owner path: user_id -> users.organization_id; medium
- **roadmap_audit** (RoadmapAudit) reads 0, writes 2; owner path: user_id -> users.organization_id; medium
- **roadmap_work_packages** (RoadmapWorkPackage) reads 11, writes 6; owner path: created_by -> users.organization_id; low
- **scoring_configurations** (ScoringConfiguration) reads 11, writes 3; owner path: created_by_id -> users.organization_id; low
- **staging_elements** (StagingElement) reads 1, writes 1; owner path: import_session_id -> import_sessions.user_id -> users.organization_id; medium
- **unified_detection_runs** (UnifiedDetectionRun) reads 16, writes 5; owner path: user_id -> users.organization_id; medium
- **unified_duplicate_groups** (UnifiedDuplicateGroup) reads 41, writes 6; owner path: detection_run_id -> unified_detection_runs.user_id -> users.organization_id; medium
- **validation_violations** (ValidationViolation) reads 0, writes 8; owner path: resolved_by_id -> users.organization_id; low

### Batch 7 (21 tables, 347 reads+writes)

- **application_capability** (ApplicationCapability) reads 3, writes 0; owner path: archimate_application_id -> archimate_elements.organization_id; high
- **application_interface_metadata** (ApplicationInterfaceMetadata) reads 4, writes 2; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **archimate_element_comments** (ArchimateElementComment) reads 1, writes 1; owner path: element_id -> archimate_elements.organization_id; high
- **business_interactions** (BusinessInteraction) reads 2, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **capability_archimate_classifications** (CapabilityArchiMateClassification) reads 3, writes 1; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **interface_consumer** (InterfaceConsumer) reads 4, writes 0; owner path: consumer_application_id -> archimate_elements.organization_id; high
- **relationship_suggestions** (RelationshipSuggestion) reads 4, writes 1; owner path: source_element_id -> archimate_elements.organization_id; high
- **software_dependencies** (SoftwareDependency) reads 3, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **software_modules** (SoftwareModule) reads 3, writes 1; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **solution_compliance_mappings** (SolutionComplianceMapping) reads 3, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **solution_elements** (SolutionElement) reads 16, writes 10; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **solution_quality_attributes** (SolutionQualityAttribute) reads 10, writes 3; owner path: constraint_id -> archimate_elements.organization_id; high
- **system_dependencies** (SystemDependency) reads 4, writes 4; owner path: interface_id -> archimate_elements.organization_id; high
- **tco_calculations** (TCOCalculation) reads 6, writes 2; owner path: vendor_product_id -> vendor_products.archimate_product_element_id -> archimate_elements.organization_id; medium
- **technical_capability_vendor_mappings** (TechnicalCapabilityVendorMapping) reads 10, writes 3; owner path: vendor_product_id -> vendor_products.archimate_product_element_id -> archimate_elements.organization_id; medium
- **uml_elements** (UMLElement) reads 0, writes 3; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **vendor_archimate_templates** (VendorArchiMateTemplate) reads 4, writes 0; owner path: element_id -> archimate_elements.organization_id; high
- **vendor_product_apqc_mapping** (VendorProductAPQCMapping) reads 12, writes 3; owner path: vendor_product_id -> vendor_products.archimate_product_element_id -> archimate_elements.organization_id; medium
- **vendor_product_embeddings** (VendorProductEmbedding) reads 2, writes 1; owner path: vendor_product_id -> vendor_products.archimate_product_element_id -> archimate_elements.organization_id; medium
- **vendor_products** (VendorProduct) reads 194, writes 17; owner path: archimate_product_element_id -> archimate_elements.organization_id; high
- **vendor_risk_assessments** (VendorRiskAssessment) reads 5, writes 2; owner path: vendor_product_id -> vendor_products.archimate_product_element_id -> archimate_elements.organization_id; medium

### Batch 8 (21 tables, 16 reads+writes)

- **architecture_review_findings** (ArchitectureReviewFinding) reads 1, writes 0; owner path: element_id -> archimate_elements.organization_id; high
- **code_quality_metrics** (CodeQualityMetrics) reads 0, writes 0; owner path: software_module_id -> software_modules.archimate_element_id -> archimate_elements.organization_id; medium
- **composite_structures** (CompositeStructure) reads 0, writes 0; owner path: child_element_id -> archimate_elements.organization_id; high
- **design_patterns** (DesignPattern) reads 2, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **equipment** (Equipment) reads 0, writes 1; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **functional_requirement** (FunctionalRequirement) reads 0, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **manufacturing_plants** (ManufacturingPlant) reads 0, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **motivation_drivers** (MotivationDriver) reads 0, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **non_functional_requirement** (NonFunctionalRequirement) reads 0, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **other_relationships** (OtherRelationship) reads 0, writes 0; owner path: source_id -> archimate_elements.organization_id; high
- **production_lines** (ProductionLine) reads 0, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **solution_governance_exceptions** (SolutionGovernanceException) reads 2, writes 0; owner path: principle_id -> archimate_elements.organization_id; high
- **structural_groupings** (Grouping) reads 0, writes 2; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **structural_junctions** (Junction) reads 0, writes 2; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **structural_locations** (Location) reads 1, writes 1; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **system_hierarchies** (SystemHierarchy) reads 0, writes 0; owner path: child_system_id -> archimate_elements.organization_id; high
- **system_interfaces** (SystemInterface) reads 0, writes 0; owner path: archimate_element_id -> archimate_elements.organization_id; high
- **system_lifecycles** (SystemLifecycle) reads 0, writes 0; owner path: system_id -> archimate_elements.organization_id; high
- **uml_attributes** (UMLAttribute) reads 2, writes 0; owner path: uml_element_id -> uml_elements.archimate_element_id -> archimate_elements.organization_id; medium
- **uml_methods** (UMLMethod) reads 2, writes 0; owner path: uml_element_id -> uml_elements.archimate_element_id -> archimate_elements.organization_id; medium
- **workflow_instance_archimate_elements** (WorkflowInstanceArchiMateElement) reads 0, writes 0; owner path: element_id -> archimate_elements.organization_id; high

### Batch 9 (25 tables, 189 reads+writes)

- **acm_property_templates** (AcmPropertyTemplate) reads 6, writes 1; owner path: own organization_id column; high
- **agent_charters** (AgentCharter) reads 1, writes 1; owner path: own organization_id column; high
- **agent_run_records** (AgentRunRecord) reads 3, writes 1; owner path: own organization_id column; high
- **arb_change_requests** (ChangeRequest) reads 3, writes 2; owner path: own organization_id column; high
- **arb_governance_standards** (ARBGovernanceStandard) reads 12, writes 2; owner path: own organization_id column; high
- **arb_workflow_stages** (ARBWorkflowStage) reads 17, writes 1; owner path: own organization_id column; high
- **artefact_share_links** (ArtefactShareLink) reads 6, writes 1; owner path: own organization_id column; high
- **constraints** (ConstraintElement) reads 6, writes 1; owner path: own organization_id column; high
- **decision_ledger** (DecisionLedger) reads 0, writes 0; owner path: own organization_id column; medium
- **devops_connector_configs** (DevOpsConnectorConfig) reads 2, writes 0; owner path: own organization_id column; high
- **ea_workflow_definitions** (EAWorkflowDefinition) reads 11, writes 2; owner path: own organization_id column; high
- **error_events** (ErrorEvent) reads 3, writes 2; owner path: own organization_id column; high
- **import_audit_log** (ImportSessionLog) reads 5, writes 2; owner path: own organization_id column; high
- **llm_interactions** (LLMInteraction) reads 14, writes 14; owner path: own organization_id column; high
- **lucidchart_connector_configs** (LucidchartConnectorConfig) reads 1, writes 1; owner path: own organization_id column; high
- **model_providers** (ModelProvider) reads 1, writes 0; owner path: own organization_id column; high
- **notifications** (Notification) reads 3, writes 4; owner path: own organization_id column; high
- **org_connector_configs** (OrgConnectorConfig) reads 0, writes 0; owner path: own organization_id column; medium
- **soc2_audit_log** (AuditLog) reads 15, writes 7; owner path: own organization_id column; high
- **solution_scoring_configs** (SolutionScoringConfig) reads 0, writes 0; owner path: own organization_id column; medium
- **stakeholder_inputs** (StakeholderInput) reads 3, writes 1; owner path: own organization_id column; high
- **strategy_resources** (StrategyResource) reads 0, writes 1; owner path: own organization_id column; high
- **subscriptions** (Subscription) reads 2, writes 2; owner path: own organization_id column; high
- **usage_events** (UsageEvent) reads 6, writes 1; owner path: own organization_id column; high
- **vendor_product_pricing** (VendorProductPricing) reads 16, writes 6; owner path: own organization_id column; high

### Batch 10 (34 tables, 168 reads+writes)

- **archimate_viewpoint_snapshots** (ArchimateViewpointSnapshot) reads 4, writes 1; owner path: viewpoint_id -> saved_diagrams.organization_id; high
- **architecture_vision_documents** (ArchitectureVisionDocument) reads 2, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **capability_investment_plans** (CapabilityInvestmentPlan) reads 1, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **change_management_records** (ChangeManagementRecord) reads 0, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **code_artifacts** (CodeArtifact) reads 0, writes 9; owner path: architecture_id -> architecture_models.organization_id; high
- **compliance_governance_reports** (ComplianceGovernanceReport) reads 0, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **compliance_scan_reports** (ComplianceScanReport) reads 3, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **document_analyses** (DocumentAnalysis) reads 10, writes 2; owner path: llm_interaction_id -> llm_interactions.pipeline_stage_id -> pipeline_stages.pipeline_id -> generation_pipelines.architecture_id -> architecture_models.organization_id; medium
- **document_analysis_edits** (DocumentAnalysisEdit) reads 0, writes 0; owner path: analysis_id -> document_analyses.llm_interaction_id -> llm_interactions.pipeline_stage_id -> pipeline_stages.pipeline_id -> generation_pipelines.architecture_id -> architecture_models.organization_id; medium
- **gap_remediation_reports** (GapRemediationReport) reads 1, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **generation_pipelines** (GenerationPipeline) reads 2, writes 0; owner path: architecture_id -> architecture_models.organization_id; high
- **implementation_gaps** (ImplementationGap) reads 37, writes 1; owner path: architecture_id -> architecture_models.organization_id; high
- **implementation_plateaus** (ImplementationPlateau) reads 28, writes 3; owner path: architecture_id -> architecture_models.organization_id; high
- **jira_issues** (JiraIssue) reads 0, writes 0; owner path: architecture_id -> architecture_models.organization_id; high
- **jira_projects** (JiraProject) reads 0, writes 0; owner path: architecture_id -> architecture_models.organization_id; high
- **migration_plan_documents** (MigrationPlanDocument) reads 1, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **motivation_assessments** (MotivationAssessment) reads 1, writes 1; owner path: model_id -> architecture_models.organization_id; high
- **motivation_meanings** (MotivationMeaning) reads 0, writes 0; owner path: model_id -> architecture_models.organization_id; high
- **motivation_stakeholders** (MotivationStakeholder) reads 1, writes 1; owner path: model_id -> architecture_models.organization_id; high
- **motivation_values** (MotivationValue) reads 0, writes 0; owner path: model_id -> architecture_models.organization_id; high
- **pipeline_stages** (PipelineStage) reads 0, writes 0; owner path: pipeline_id -> generation_pipelines.architecture_id -> architecture_models.organization_id; medium
- **planning_deliverables** (PlanningDeliverable) reads 0, writes 0; owner path: architecture_id -> architecture_models.organization_id; high
- **requirements_traceability_matrices** (RequirementsTraceabilityMatrix) reads 0, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **saved_diagram_elements** (SavedDiagramElement) reads 7, writes 13; owner path: diagram_id -> saved_diagrams.organization_id; high
- **saved_diagram_relationships** (SavedDiagramRelationship) reads 5, writes 6; owner path: diagram_id -> saved_diagrams.organization_id; high
- **security_scans** (SecurityScan) reads 2, writes 0; owner path: code_artifact_id -> code_artifacts.architecture_id -> architecture_models.organization_id; medium
- **technology_roadmap_initiatives** (TechnologyRoadmapInitiative) reads 6, writes 1; owner path: architecture_id -> architecture_models.organization_id; high
- **vendor_selection_reports** (VendorSelectionReport) reads 1, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **viewpoint_views** (ViewpointView) reads 0, writes 0; owner path: architecture_model_id -> architecture_models.organization_id; high
- **workflow_completion_summaries** (WorkflowCompletionSummary) reads 1, writes 2; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high
- **workflow_executions** (WorkflowExecution) reads 0, writes 0; owner path: pipeline_id -> workflow_pipelines.architecture_id -> architecture_models.organization_id; medium
- **workflow_nodes** (WorkflowNode) reads 0, writes 0; owner path: pipeline_id -> workflow_pipelines.architecture_id -> architecture_models.organization_id; medium
- **workflow_pipelines** (WorkflowPipeline) reads 0, writes 1; owner path: architecture_id -> architecture_models.organization_id; high
- **workflow_run_watchers** (WorkflowRunWatcher) reads 4, writes 1; owner path: workflow_instance_id -> ea_workflow_instances.organization_id; high

### Batch 11 (29 tables, 182 reads+writes)

- **acceptance_criteria** (AcceptanceCriteria) reads 2, writes 1; owner path: requirement_id -> requirements.organization_id; high
- **adm_audit_logs** (ADMAuditLog) reads 6, writes 1; owner path: approval_id -> adm_phase_approvals.organization_id; high
- **adm_board_portfolio_links** (ADMBoardPortfolioLink) reads 0, writes 0; owner path: board_id -> kanban_boards.organization_id; high
- **adm_compliance_checkpoints** (ADMComplianceCheckpoint) reads 4, writes 2; owner path: approval_id -> adm_phase_approvals.organization_id; high
- **adm_stakeholder_concurrences** (ADMStakeholderConcurrence) reads 3, writes 1; owner path: approval_id -> adm_phase_approvals.organization_id; high
- **adr_capability_links** (ADRCapabilityLink) reads 0, writes 0; owner path: adr_id -> architecture_decision_records.organization_id; high
- **ai_chat_approval_audit_log** (AIChatApprovalAuditLog) reads 0, writes 4; owner path: approval_id -> ai_chat_crud_approvals.organization_id; high
- **ai_chat_audit_logs** (AIChatAuditLog) reads 2, writes 1; owner path: approval_id -> ai_chat_crud_approvals.organization_id; high
- **arb_adversarial_reviews** (ARBAdversarialReview) reads 10, writes 2; owner path: review_item_id -> arb_review_items.organization_id; high
- **arb_compliance_checks** (ARBComplianceCheck) reads 3, writes 2; owner path: review_item_id -> arb_review_items.organization_id; high
- **arb_derogations** (Derogation) reads 2, writes 0; owner path: arb_review_item_id -> arb_review_items.organization_id; high
- **capability_gap_details** (CapabilityGapDetail) reads 8, writes 2; owner path: analysis_id -> capability_gap_analysis.organization_id; high
- **compliance_checks** (ComplianceCheck) reads 2, writes 0; owner path: requirement_id -> requirements.organization_id; high
- **compliance_gaps** (ComplianceGap) reads 4, writes 2; owner path: compliance_requirement_id -> compliance_requirements.organization_id; high
- **duplicate_groups** (DuplicateGroup) reads 17, writes 2; owner path: primary_business_process_id -> business_processes.organization_id; high
- **gap_solution_options** (GapSolutionOption) reads 5, writes 0; owner path: gap_detail_id -> capability_gap_details.analysis_id -> capability_gap_analysis.organization_id; medium
- **policy_exemptions** (PolicyExemption) reads 10, writes 2; owner path: violation_id -> policy_violations.organization_id; high
- **process_role_raci** (ProcessRoleRaci) reads 0, writes 0; owner path: process_id -> business_processes.organization_id; high
- **requirement_change_log** (RequirementChangeLog) reads 2, writes 1; owner path: req_id -> solution_requirements.organization_id; high
- **requirement_dependencies** (RequirementDependency) reads 4, writes 1; owner path: depends_on_id -> solution_requirements.organization_id; high
- **service_realization** (ServiceRealization) reads 4, writes 1; owner path: process_id -> business_processes.organization_id; high
- **solution_adr_direct** (SolutionADRDirect) reads 8, writes 3; owner path: adr_id -> architecture_decision_records.organization_id; high
- **solution_cost_comparisons** (SolutionCostComparison) reads 0, writes 1; owner path: session_id -> solution_analysis_sessions.organization_id; high
- **solution_integration_flows** (SolutionIntegrationFlow) reads 18, writes 1; owner path: archimate_flow_id -> archimate_relationships.organization_id; high
- **solution_session_versions** (SolutionSessionVersion) reads 3, writes 1; owner path: session_id -> solution_analysis_sessions.organization_id; high
- **solution_stakeholder_mappings** (SolutionStakeholderMapping) reads 9, writes 6; owner path: programme_id -> strategic_initiatives.organization_id; high
- **sprints** (Sprint) reads 8, writes 1; owner path: board_id -> kanban_boards.organization_id; high
- **sync_logs** (SyncLog) reads 4, writes 6; owner path: connector_id -> connector_configs.organization_id; high
- **test_cases** (TestCase) reads 0, writes 0; owner path: requirement_id -> requirements.organization_id; high

### Batch 12 (20 tables, 22 reads+writes)

- **approval_workflow** (ApprovalWorkflow) reads 0, writes 0; owner path: decision_id -> capability_governance_decision.organization_id; high
- **architecture_change_notices** (ArchitectureChangeNotice) reads 0, writes 1; owner path: change_request_id -> architecture_change_requests.organization_id; high
- **card_dependencies** (CardDependency) reads 1, writes 0; owner path: source_card_id -> kanban_cards.organization_id; high
- **initiative_success_metrics** (InitiativeSuccessMetric) reads 0, writes 1; owner path: initiative_id -> portfolio_initiatives.organization_id; high
- **kanban_card_attachments** (KanbanCardAttachment) reads 0, writes 0; owner path: card_id -> kanban_cards.organization_id; high
- **kanban_card_comments** (KanbanCardComment) reads 0, writes 0; owner path: card_id -> kanban_cards.organization_id; high
- **kanban_card_history** (KanbanCardHistory) reads 0, writes 0; owner path: card_id -> kanban_cards.organization_id; high
- **milestones** (Milestone) reads 0, writes 0; owner path: project_id -> projects.organization_id; high
- **motivation_constraints** (MotivationConstraint) reads 1, writes 1; owner path: goal_id -> goals.organization_id; high
- **motivation_outcomes** (MotivationOutcome) reads 1, writes 1; owner path: goal_id -> goals.organization_id; high
- **platform_capabilities** (PlatformCapability) reads 1, writes 0; owner path: technology_stack_id -> technology_stacks.vendor_template_id -> vendor_stack_templates.organization_id; medium
- **process_embeddings** (ProcessEmbedding) reads 1, writes 0; owner path: process_id -> industry_apqc_process.organization_id; high
- **review_decisions** (ReviewDecision) reads 1, writes 1; owner path: review_item_id -> review_queue_items.organization_id; high
- **service_dependency** (ServiceDependency) reads 4, writes 0; owner path: dependent_service_id -> business_services.organization_id; high
- **solution_cost_yearly_projections** (SolutionCostYearlyProjection) reads 2, writes 1; owner path: cost_model_id -> solution_cost_models.organization_id; high
- **solution_workflow_tasks** (SolutionWorkflowTask) reads 0, writes 1; owner path: workflow_id -> solution_workflows.organization_id; high
- **system_deployments** (SystemDeployment) reads 0, writes 0; owner path: facility_id -> physical_facilities.organization_id; high
- **technology_stacks** (TechnologyStack) reads 2, writes 1; owner path: vendor_template_id -> vendor_stack_templates.organization_id; high
- **vendor_proof_points** (VendorProofPoint) reads 0, writes 0; owner path: vendor_option_id -> vendor_options.organization_id; high
- **vendor_responses** (VendorResponse) reads 0, writes 0; owner path: vendor_option_id -> vendor_options.organization_id; high

### Customer-owned, not batched (no derivable owner; a person decides)

- **adm_deliverable_checks** (ADMDeliverableCheck) reads 2, writes 1: written on request paths: app/services/adm_deliverable_service.py:toggle_deliverable (helper, reached by name match from app/modules/architecture/routes/adm_kanban_v2_routes.py:check_deliverable); but no organization_id and no foreign key to an owner
- **adm_phases** (ADMPhase) reads 32, writes 0: written on request paths: app/models/adm_kanban.py:create_adm_phases (helper, reached by name match from app/modules/architecture/routes/adm_kanban_routes.py:init_adm_phases); but no organization_id and no foreign key to an owner
- **ai_prompt_template_versions** (AIPromptTemplateVersion) reads 5, writes 6: written on request paths: app/services/solution_prompt_override_service.py:reset_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_reset); app/services/solution_prompt_override_service.py:rollback_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_rollback); app/services/solution_prompt_override_service.py:update_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_update); but no organization_id and no foreign key to an owner
- **ai_prompt_templates** (AIPromptTemplate) reads 20, writes 5: written on request paths: app/modules/ai_chat/routes/chat_admin_routes.py:admin_prompt_update (a request route); app/services/solution_prompt_override_service.py:rollback_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_rollback); app/services/solution_prompt_override_service.py:update_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_update); but no organization_id and no foreign key to an owner
- **archimate_metamodel_violations** (MetamodelViolation) reads 5, writes 1: written on request paths: app/modules/architecture/services/archimate_validation_service.py:ArchiMateValidationService.log_violation (helper, reached by name match from app/api/coverage_matrix_routes.py:estimate_coverage); but no organization_id and no foreign key to an owner
- **architecture_elements** (ArchitectureElement) reads 32, writes 3: written on request paths: app/modules/architecture/routes/architecture_crud_routes.py:create_element (a request route); app/modules/architecture/services/architecture_import_export_service.py:ArchitectureImportExportService.import_from_csv (helper, reached by name match from app/modules/architecture/routes/architecture_crud_routes.py:export_architecture); app/modules/architecture/services/architecture_import_export_service.py:ArchitectureImportExportService.import_from_json (helper, reached by name match from app/modules/architecture/routes/architecture_crud_routes.py:export_architecture); but no organization_id and no foreign key to an owner
- **architecture_inference_relationship** (ArchitectureInferenceRelationship) reads 20, writes 6: written on request paths: app/modules/architecture/services/architecture_graph_facade.py:ArchitectureGraphFacade.get_or_create_relationship (helper, reached by name match from app/modules/ai_chat/routes/approval_routes.py:approval_impact); app/modules/architecture_assistant/journey_orchestrator.py:JourneyOrchestrator.generate_architecture (helper, reached by name match from app/modules/ai_chat/routes/chat_core.py:send_message); app/modules/solutions_strategic/v2/routes/journey_v2_routes.py:_sync_capability_realization_links (helper, reached by name match from app/modules/solutions_strategic/v2/routes/journey_v2_routes.py:derive_capabilities); but no organization_id and no foreign key to an owner
- **architecture_relationships** (Relationship) reads 12, writes 1: written on request paths: app/modules/architecture/routes/architecture_crud_routes.py:create_relationship (a request route); app/modules/architecture/routes/architecture_crud_routes.py:delete_element (a request route); but no organization_id and no foreign key to an owner
- **autogen_override** (AutoGenOverride) reads 3, writes 1: written on request paths: app/services/autogen_service.py:create_override (helper, reached by name match from app/main/autogen.py:overrides); but no organization_id and no foreign key to an owner
- **business_domains** (BusinessDomain) reads 33, writes 11: written on request paths: app/modules/applications/services/application_capability_catalog.py:ensure_capabilities_seeded.get_or_create_capability (helper, reached by name match from app/modules/codegen/routes/codegen_routes.py:generate_code); but no organization_id and no foreign key to an owner
- **capability_taxonomy_rules** (CapabilityTaxonomyRule) reads 3, writes 1: written on request paths: app/modules/capabilities/services/capability_taxonomy_service.py:CapabilityTaxonomyService.create_taxonomy_rule (helper, reached by name match from app/api/import_history_routes.py:export_job_data); but no organization_id and no foreign key to an owner
- **codegen_connector_credentials** (ConnectorCredential) reads 3, writes 4: written on request paths: app/modules/codegen/services/credential_vault.py:CredentialVault.delete (helper, reached by name match from app/modules/codegen/routes/connector_routes.py:connector_sync_history); app/modules/codegen/services/credential_vault.py:CredentialVault.store (helper, reached by name match from app/modules/admin/connector_routes.py:connectors_delete); but no organization_id and no foreign key to an owner
- **dod_checks** (DoDCheck) reads 2, writes 1: written on request paths: app/services/dod_service.py:create_dod_check (helper, reached by name match from app/modules/architecture/routes/dod_routes.py:create_check); but no organization_id and no foreign key to an owner
- **duplicate_detection_runs** (DuplicateDetectionRun) reads 9, writes 2: written on request paths: app/modules/duplicate_detection/services/duplicate_detection_service.py:DuplicateDetectionService.run_duplicate_detection (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/modules/duplicate_detection/services/unified_duplicate_detection_service.py:UnifiedDuplicateDetectionService.run_duplicate_detection (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); but no organization_id and no foreign key to an owner
- **extraction_feedback** (ExtractionFeedback) reads 7, writes 2: written on request paths: app/modules/architecture/services/feedback_learning_service.py:FeedbackLearningService.record_correction (helper, reached by name match from app/application_mgmt/api_data_routes.py:analyze_document_for_application); but no organization_id and no foreign key to an owner
- **generation_event** (GenerationEvent) reads 2, writes 1: autogen event log written by autogen_service.log_event, used by the autogen routes and the ArchiMate LLM service
- **import_audit_logs** (ImportAuditLog) reads 3, writes 2: written on request paths: app/modules/applications/routes/import_export_routes.py:rollback_import (a request route); app/modules/import_batch/services/import_audit_service.py:ImportAuditService.log_import_operation (helper, reached by name match from app/modules/applications/routes/import_export_routes.py:application_import); but no organization_id and no foreign key to an owner
- **jobs** (Job) reads 12, writes 3: written on request paths: app/modules/admin/routes/admin_routes.py:jira_trigger_push (a request route); app/modules/admin/v2/routes/admin_routes.py:jira_trigger_push (a request route); app/services/job_queue_service.py:JobQueueService.create_job (helper, reached by name match from app/modules/admin/routes/admin_routes.py:cancel_abacus_job); but no organization_id and no foreign key to an owner
- **llm_metrics** (LLMMetrics) reads 2, writes 1: provider latency, throughput and cost samples written by the LLM router on each call
- **llm_requests** (LLMRequest) reads 0, writes 2: written on request paths: app/modules/ai_chat/services/llm_router.py:LLMRouter._record_request (helper, reached by name match from app/api/v1/llm.py:get_available_llms); but no organization_id and no foreign key to an owner
- **llm_responses** (LLMResponse) reads 0, writes 2: written on request paths: app/modules/ai_chat/services/llm_router.py:LLMRouter._record_request (helper, reached by name match from app/api/v1/llm.py:get_available_llms); but no organization_id and no foreign key to an owner
- **process_application_mapping** (ProcessApplicationMapping) reads 18, writes 15: written on request paths: app/modules/ai_chat/services/ai_import_service.py:AIImportService.create_ai_mappings (helper, reached by name match from app/api/import_history_routes.py:export_job_data); app/modules/ai_chat/services/ai_import_service.py:AIImportService.import_with_ai_analysis (helper, reached by name match from app/api/import_history_routes.py:export_job_data); app/modules/applications/routes/_helpers.py:_cascade_delete_application (helper, reached by name match from app/modules/applications/routes/crud_routes.py:api_delete_application); but no organization_id and no foreign key to an owner
- **product_inquiries** (ProductInquiry) reads 2, writes 1: written on request paths: app/main/views.py:product_inquiry_submit (a request route); but no organization_id and no foreign key to an owner
- **public_visitor_events** (PublicVisitorEvent) reads 0, writes 1: written on request paths: app/services/public_analytics_service.py:_record (helper, reached by name match from app/_bootstrap/routes.py:_register_api_auth.api_logout); but no organization_id and no foreign key to an owner
- **reference_number_counters** (ReferenceNumberCounter) reads 0, writes 0: written on request paths: app/utils/reference_numbers.py:next_reference (helper, reached by name match from app/api/ba_phase_b_routes.py:run_phase_b); but no organization_id and no foreign key to an owner
- **simple_detection_runs** (SimpleDetectionRun) reads 4, writes 2: written on request paths: app/services/simple_duplicate_service.py:SimpleDuplicateService.cleanup_stale_data (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/services/simple_duplicate_service.py:SimpleDuplicateService.run_detection (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/services/simple_duplicate_service.py:SimpleDuplicateService.run_detection_hybrid (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); but no organization_id and no foreign key to an owner
- **simple_duplicate_groups** (SimpleDuplicateGroup) reads 8, writes 3: written on request paths: app/services/simple_duplicate_service.py:SimpleDuplicateService.cleanup_stale_data (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/services/simple_duplicate_service.py:SimpleDuplicateService.run_detection (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/services/simple_duplicate_service.py:SimpleDuplicateService.run_detection_hybrid (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); but no organization_id and no foreign key to an owner
- **stakeholder_communications** (StakeholderCommunication) reads 2, writes 1: written on request paths: app/services/communication_log_service.py:log_communication (helper, reached by name match from app/modules/architecture/routes/communication_routes.py:create_communication); but no organization_id and no foreign key to an owner
- **system_settings** (SystemSetting) reads 0, writes 0: written on request paths: app/main/views.py:save_system_settings (a request route); app/modules/ai_chat/services/llm_service_impl.py:LLMService.set_user_llm_preference (helper, reached by name match from app/api/archimate_generation_routes.py:create_model); but no organization_id and no foreign key to an owner
- **uml_models** (UMLModel) reads 0, writes 0: written on request paths: app/modules/solutions_strategic/v2/routes/solution_design_routes.py:_engine_archimate_cleanup (helper, reached by name match from app/modules/solutions_strategic/v2/routes/solution_design_routes.py:api_delete_solution); but no organization_id and no foreign key to an owner
- **vendor_concentration_analysis** (VendorConcentrationAnalysis) reads 4, writes 1: aggregates the applications that use a vendor's products, then stores one row per vendor
- **waitlist_signups** (WaitlistSignup) reads 2, writes 1: written on request paths: app/main/views.py:index (a request route); but no organization_id and no foreign key to an owner

## Low-confidence calls to check

- **acm_domain_templates** (shared_reference): no organization_id and no owner link; request code that may edit rows (it references the model and sets attributes): app/modules/architecture_assistant/journey_orchestrator.py:JourneyOrchestrator.backfill_domain_elements; check whether customers can edit these rows
- **adm_deliverable_checks** (customer_owned): written on request paths: app/services/adm_deliverable_service.py:toggle_deliverable (helper, reached by name match from app/modules/architecture/routes/adm_kanban_v2_routes.py:check_deliverable); but no organization_id and no foreign key to an owner
- **adm_deliverables** (shared_reference): no organization_id and no owner link; request code that may edit rows (it references the model and sets attributes): app/services/adm_deliverable_service.py:toggle_deliverable, app/services/kanban_projection_service.py:KanbanProjectionService._move_deliverable; check whether customers can edit these rows
- **adm_phases** (customer_owned): written on request paths: app/models/adm_kanban.py:create_adm_phases (helper, reached by name match from app/modules/architecture/routes/adm_kanban_routes.py:init_adm_phases); but no organization_id and no foreign key to an owner
- **agent_configurations** (customer_owned): written on request paths: app/main/routes_agentic_gaps.py:agent_configuration (a request route); but no organization_id and no foreign key to an owner; only link is an audit user column
- **agent_execution_history** (customer_owned): written on request paths: app/modules/architecture/services/agentic_gap_implementation_service.py:AgenticGapImplementationService._create_workflow_execution_record (helper, reached by name match from app/main/routes_agentic_gaps.py:auto_implement_from_gaps); app/modules/architecture/services/agentic_gap_implementation_service.py:AgenticGapImplementationService._run_agent_with_tracking (helper, reached by name match from app/main/routes_agentic_gaps.py:auto_implement_from_gaps); app/modules/architecture/services/agentic_gap_implementation_service.py:AgenticGapImplementationService.implement_all_gaps (helper, reached by name match from app/main/routes_agentic_gaps.py:auto_implement_from_gaps); but no organization_id and no foreign key to an owner; only link is an audit user column
- **agent_schedules** (customer_owned): written on request paths: app/main/routes_agentic_gaps.py:manage_schedules (a request route); but no organization_id and no foreign key to an owner; only link is an audit user column
- **ai_prompt_template_versions** (customer_owned): written on request paths: app/services/solution_prompt_override_service.py:reset_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_reset); app/services/solution_prompt_override_service.py:rollback_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_rollback); app/services/solution_prompt_override_service.py:update_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_update); but no organization_id and no foreign key to an owner
- **ai_prompt_templates** (customer_owned): written on request paths: app/modules/ai_chat/routes/chat_admin_routes.py:admin_prompt_update (a request route); app/services/solution_prompt_override_service.py:rollback_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_rollback); app/services/solution_prompt_override_service.py:update_override (helper, reached by name match from app/modules/admin/routes/solution_prompt_admin.py:solution_prompt_update); but no organization_id and no foreign key to an owner
- **ai_suggestions** (customer_owned): written on request paths: app/modules/ai_chat/services/ai_suggestion_service.py:AISuggestionService.create_batch_suggestions (helper, reached by name match from app/api/ba_phase_b_routes.py:run_phase_b); app/modules/ai_chat/services/ai_suggestion_service.py:AISuggestionService.create_suggestion (helper, reached by name match from app/api/ba_phase_b_routes.py:run_phase_b); but no organization_id and no foreign key to an owner; only link is an audit user column
- **apqc_process** (shared_reference): no organization_id and no owner link; request code that may edit rows (it references the model and sets attributes): app/modules/industry_apqc/routes/apqc_api_routes.py:save_process_mappings, app/services/faiss_apqc_service.py:FAISSAPQCService._build_faiss_index, app/services/faiss_apqc_service.py:FAISSAPQCService._build_simple_index; check whether customers can edit these rows
- **arb_waiver_expiry_checkpoints** (shared_reference): not reference data in the strict sense: it is platform job state with no organisation column; check whether the checkpoints record organisation-specific waivers
- **archimate_metamodel_violations** (customer_owned): written on request paths: app/modules/architecture/services/archimate_validation_service.py:ArchiMateValidationService.log_violation (helper, reached by name match from app/api/coverage_matrix_routes.py:estimate_coverage); but no organization_id and no foreign key to an owner
- **archimate_patterns** (customer_owned): written on request paths: app/modules/architecture/routes/archimate_routes.py:api_create_pattern (a request route); but no organization_id and no foreign key to an owner; only link is an audit user column
- **archimate_viewpoint_templates** (customer_owned): written on request paths: app/modules/architecture/routes/archimate_routes.py:api_create_template (a request route); but no organization_id and no foreign key to an owner; only link is an audit user column
- **architecture_elements** (customer_owned): written on request paths: app/modules/architecture/routes/architecture_crud_routes.py:create_element (a request route); app/modules/architecture/services/architecture_import_export_service.py:ArchitectureImportExportService.import_from_csv (helper, reached by name match from app/modules/architecture/routes/architecture_crud_routes.py:export_architecture); app/modules/architecture/services/architecture_import_export_service.py:ArchitectureImportExportService.import_from_json (helper, reached by name match from app/modules/architecture/routes/architecture_crud_routes.py:export_architecture); but no organization_id and no foreign key to an owner
- **architecture_inference_relationship** (customer_owned): written on request paths: app/modules/architecture/services/architecture_graph_facade.py:ArchitectureGraphFacade.get_or_create_relationship (helper, reached by name match from app/modules/ai_chat/routes/approval_routes.py:approval_impact); app/modules/architecture_assistant/journey_orchestrator.py:JourneyOrchestrator.generate_architecture (helper, reached by name match from app/modules/ai_chat/routes/chat_core.py:send_message); app/modules/solutions_strategic/v2/routes/journey_v2_routes.py:_sync_capability_realization_links (helper, reached by name match from app/modules/solutions_strategic/v2/routes/journey_v2_routes.py:derive_capabilities); but no organization_id and no foreign key to an owner
- **architecture_relationships** (customer_owned): written on request paths: app/modules/architecture/routes/architecture_crud_routes.py:create_relationship (a request route); app/modules/architecture/routes/architecture_crud_routes.py:delete_element (a request route); but no organization_id and no foreign key to an owner
- **audit_events** (customer_owned): written by the audit logger during requests and holds user email, resource and old/new values, but has no organization_id; user_id is a plain integer with no foreign key and system events carry no user
- **autogen_override** (customer_owned): written on request paths: app/services/autogen_service.py:create_override (helper, reached by name match from app/main/autogen.py:overrides); but no organization_id and no foreign key to an owner
- **batch_job_checkpoints** (customer_owned): written on request paths: app/modules/import_batch/services/batch_processing_service.py:BatchProcessingService._create_checkpoint (helper, reached by name match from app/api/import_history_routes.py:export_job_data); but no organization_id and no foreign key to an owner; only link is an audit user column
- **batch_job_errors** (customer_owned): written from request paths (import routes); the owner is reachable only through the creator of the job (an audit column)
- **batch_job_items** (customer_owned): written on request paths: app/modules/import_batch/services/batch_processing_service.py:BatchProcessingService.create_batch_job (helper, reached by name match from app/api/import_history_routes.py:export_job_data); but no organization_id and no foreign key to an owner; only link is an audit user column
- **batch_jobs** (customer_owned): written on request paths: app/modules/import_batch/services/batch_processing_service.py:BatchProcessingService.create_batch_job (helper, reached by name match from app/api/import_history_routes.py:export_job_data); but no organization_id and no foreign key to an owner; only link is an audit user column
- **business_domains** (customer_owned): written on request paths: app/modules/applications/services/application_capability_catalog.py:ensure_capabilities_seeded.get_or_create_capability (helper, reached by name match from app/modules/codegen/routes/codegen_routes.py:generate_code); but no organization_id and no foreign key to an owner
- **capability_taxonomy_rules** (customer_owned): written on request paths: app/modules/capabilities/services/capability_taxonomy_service.py:CapabilityTaxonomyService.create_taxonomy_rule (helper, reached by name match from app/api/import_history_routes.py:export_job_data); but no organization_id and no foreign key to an owner
- **codegen_connector_credentials** (customer_owned): written on request paths: app/modules/codegen/services/credential_vault.py:CredentialVault.delete (helper, reached by name match from app/modules/codegen/routes/connector_routes.py:connector_sync_history); app/modules/codegen/services/credential_vault.py:CredentialVault.store (helper, reached by name match from app/modules/admin/connector_routes.py:connectors_delete); but no organization_id and no foreign key to an owner
- **codegen_system_boundaries** (customer_owned): created from a request route; the only direct link is the creator (an audit column); the owner could instead be read from codegen_system_boundary_solutions.solution_id
- **codegen_template_files** (customer_owned): written on request paths: app/modules/codegen/routes/template_set_routes.py:save_generation_as_template (a request route); app/modules/codegen/routes/template_set_routes.py:upsert_template_file (a request route); but no organization_id and no foreign key to an owner; only link is an audit user column
- **codegen_template_sets** (customer_owned): written on request paths: app/modules/codegen/routes/template_set_routes.py:create_template_set (a request route); app/modules/codegen/routes/template_set_routes.py:save_generation_as_template (a request route); but no organization_id and no foreign key to an owner; only link is an audit user column
- **compliance_controls** (shared_reference): no organization_id and no owner link; request code that may edit rows (it references the model and sets attributes): app/modules/compliance/services/applicability_service.py:ApplicabilityService.confirm_harmonization, app/modules/compliance/services/applicability_service.py:ApplicabilityService.propose_harmonization; check whether customers can edit these rows
- **confidence_thresholds** (customer_owned): written on request paths: app/routes/confidence_review_routes.py:api_save_thresholds (a request route); app/services/confidence_review_service.py:ConfidenceReviewService.create_confidence_threshold (helper, reached by name match from app/api/confidence_review_routes.py:create_confidence_threshold); but no organization_id and no foreign key to an owner; only link is an audit user column
- **custom_field_definitions** (customer_owned): written on request paths: app/application_mgmt/custom_field_routes.py:custom_field_create (a request route); but no organization_id and no foreign key to an owner; only link is an audit user column
- **dod_checks** (customer_owned): written on request paths: app/services/dod_service.py:create_dod_check (helper, reached by name match from app/modules/architecture/routes/dod_routes.py:create_check); but no organization_id and no foreign key to an owner
- **dod_templates** (shared_reference): no organization_id and no owner link; request code that may edit rows (it references the model and sets attributes): app/services/dod_service.py:update_checks; check whether customers can edit these rows
- **duplicate_detection_runs** (customer_owned): written on request paths: app/modules/duplicate_detection/services/duplicate_detection_service.py:DuplicateDetectionService.run_duplicate_detection (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/modules/duplicate_detection/services/unified_duplicate_detection_service.py:UnifiedDuplicateDetectionService.run_duplicate_detection (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); but no organization_id and no foreign key to an owner
- **element_templates** (customer_owned): written on request paths: app/repositories/template_repository.py:ElementTemplateRepository.increment_usage_count (helper, reached by name match from app/application_mgmt/template_api_routes.py:add_from_template_page); app/services/search_service.py:TemplateSearchService.rebuild_search_vectors (helper, reached by name match from app/application_mgmt/template_api_routes.py:add_from_template_page); app/services/template_performance_optimizer.py:TemplatePerformanceOptimizer.increment_usage_count (helper, reached by name match from app/api/coverage_matrix_routes.py:estimate_coverage); but no organization_id and no foreign key to an owner; only link is an audit user column
- **external_systems** (customer_owned): written on request paths: app/modules/admin/routes/admin_routes.py:abacus_settings (a request route); app/modules/admin/routes/admin_routes.py:jira_settings (a request route); app/modules/admin/routes/admin_routes.py:save_env_jira_config (a request route); but no organization_id and no foreign key to an owner; only link is an audit user column
- **extraction_feedback** (customer_owned): written on request paths: app/modules/architecture/services/feedback_learning_service.py:FeedbackLearningService.record_correction (helper, reached by name match from app/application_mgmt/api_data_routes.py:analyze_document_for_application); but no organization_id and no foreign key to an owner
- **generation_event** (customer_owned): written from request paths; points at its target by target_type and target_id only, so there is no organization_id and no foreign key to derive an owner from; needs a column
- **impact_analysis_results** (customer_owned): written on request paths: app/modules/solutions_strategic/v2/services/impact_analysis_service.py:ImpactAnalysisService.analyze_change_impact (helper, reached by name match from app/api/coverage_matrix_routes.py:estimate_coverage); app/services/agents/gap_analysis_agent.py:GapAnalysisAgent.analyze_impact (helper, reached by name match from app/modules/architecture/routes/arb_routes.py:api_decision_analytics); but no organization_id and no foreign key to an owner; only link is an audit user column
- **import_audit_logs** (customer_owned): written on request paths: app/modules/applications/routes/import_export_routes.py:rollback_import (a request route); app/modules/import_batch/services/import_audit_service.py:ImportAuditService.log_import_operation (helper, reached by name match from app/modules/applications/routes/import_export_routes.py:application_import); but no organization_id and no foreign key to an owner
- **jobs** (customer_owned): written on request paths: app/modules/admin/routes/admin_routes.py:jira_trigger_push (a request route); app/modules/admin/v2/routes/admin_routes.py:jira_trigger_push (a request route); app/services/job_queue_service.py:JobQueueService.create_job (helper, reached by name match from app/modules/admin/routes/admin_routes.py:cancel_abacus_job); but no organization_id and no foreign key to an owner
- **llm_metrics** (customer_owned): written during requests with free-form labels (JSON) and no organization_id; the values look like platform telemetry, so a person should decide between adding a column and treating it as platform data
- **llm_requests** (customer_owned): written on request paths: app/modules/ai_chat/services/llm_router.py:LLMRouter._record_request (helper, reached by name match from app/api/v1/llm.py:get_available_llms); but no organization_id and no foreign key to an owner
- **llm_responses** (customer_owned): written on request paths: app/modules/ai_chat/services/llm_router.py:LLMRouter._record_request (helper, reached by name match from app/api/v1/llm.py:get_available_llms); but no organization_id and no foreign key to an owner
- **process_application_mapping** (customer_owned): written on request paths: app/modules/ai_chat/services/ai_import_service.py:AIImportService.create_ai_mappings (helper, reached by name match from app/api/import_history_routes.py:export_job_data); app/modules/ai_chat/services/ai_import_service.py:AIImportService.import_with_ai_analysis (helper, reached by name match from app/api/import_history_routes.py:export_job_data); app/modules/applications/routes/_helpers.py:_cascade_delete_application (helper, reached by name match from app/modules/applications/routes/crud_routes.py:api_delete_application); but no organization_id and no foreign key to an owner
- **product_inquiries** (customer_owned): written on request paths: app/main/views.py:product_inquiry_submit (a request route); but no organization_id and no foreign key to an owner
- **public_visitor_events** (customer_owned): written on request paths: app/services/public_analytics_service.py:_record (helper, reached by name match from app/_bootstrap/routes.py:_register_api_auth.api_logout); but no organization_id and no foreign key to an owner
- **reference_number_counters** (customer_owned): written on request paths: app/utils/reference_numbers.py:next_reference (helper, reached by name match from app/api/ba_phase_b_routes.py:run_phase_b); but no organization_id and no foreign key to an owner
- **requirement_template** (shared_reference): no organization_id and no owner link; request code that may edit rows (it references the model and sets attributes): app/modules/capabilities/routes/enterprise_api_routes.py:auto_classify_requirement, app/modules/capabilities/routes/enterprise_api_routes.py:batch_enrich_requirements, app/modules/capabilities/routes/enterprise_api_routes.py:create_requirement; check whether customers can edit these rows
- **resources** (shared_reference): no organization_id and no owner link; request code that may edit rows (it references the model and sets attributes): app/application_mgmt/detail_layer_routes.py:update_strategy_layer, app/modules/applications/routes/update_routes.py:update_strategy_layer; check whether customers can edit these rows
- **roadmap_work_packages** (customer_owned): written on request paths: app/modules/solutions_strategic/v2/routes/solution_design_routes.py:api_solution_work_packages (a request route); app/modules/solutions_strategic/v2/services/roadmap_sync.py:RoadmapDataSync._create_application_deliverable (helper, reached by name match from app/modules/solutions_strategic/v2/routes/roadmap_api.py:sync_applications); app/modules/solutions_strategic/v2/services/roadmap_sync.py:RoadmapDataSync._create_capability_work_package (helper, reached by name match from app/main/routes_agentic_gaps.py:auto_implement_from_gaps); but no organization_id and no foreign key to an owner; only link is an audit user column
- **scoring_configurations** (customer_owned): written on request paths: app/services/rationalization_scoring_service.py:RationalizationScoringService.get_scoring_configuration (helper, reached by name match from app/api/ba_phase_b_routes.py:run_phase_b); app/services/scoring_configuration_service.py:create_scoring_configuration (helper, reached by name match from app/api/dashboard_routes.py:create_scoring_configuration); app/services/scoring_configuration_service.py:update_scoring_configuration (helper, reached by name match from app/api/dashboard_routes.py:update_scoring_configuration); but no organization_id and no foreign key to an owner; only link is an audit user column
- **sidebar_menu_items** (shared_reference): no organization_id and no owner link; request code that may edit rows (it references the model and sets attributes): app/modules/admin/routes/sidebar_mgmt_routes.py:reset_all_items, app/modules/admin/routes/sidebar_mgmt_routes.py:toggle_section, app/modules/admin/routes/sidebar_mgmt_routes.py:toggle_sidebar_item; check whether customers can edit these rows
- **simple_detection_runs** (customer_owned): written on request paths: app/services/simple_duplicate_service.py:SimpleDuplicateService.cleanup_stale_data (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/services/simple_duplicate_service.py:SimpleDuplicateService.run_detection (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/services/simple_duplicate_service.py:SimpleDuplicateService.run_detection_hybrid (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); but no organization_id and no foreign key to an owner
- **simple_duplicate_groups** (customer_owned): written on request paths: app/services/simple_duplicate_service.py:SimpleDuplicateService.cleanup_stale_data (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/services/simple_duplicate_service.py:SimpleDuplicateService.run_detection (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); app/services/simple_duplicate_service.py:SimpleDuplicateService.run_detection_hybrid (helper, reached by name match from app/api/dashboard_routes.py:rationalization_dashboard); but no organization_id and no foreign key to an owner
- **stakeholder_communications** (customer_owned): written on request paths: app/services/communication_log_service.py:log_communication (helper, reached by name match from app/modules/architecture/routes/communication_routes.py:create_communication); but no organization_id and no foreign key to an owner
- **system_settings** (customer_owned): written on request paths: app/main/views.py:save_system_settings (a request route); app/modules/ai_chat/services/llm_service_impl.py:LLMService.set_user_llm_preference (helper, reached by name match from app/api/archimate_generation_routes.py:create_model); but no organization_id and no foreign key to an owner
- **uml_models** (customer_owned): written on request paths: app/modules/solutions_strategic/v2/routes/solution_design_routes.py:_engine_archimate_cleanup (helper, reached by name match from app/modules/solutions_strategic/v2/routes/solution_design_routes.py:api_delete_solution); but no organization_id and no foreign key to an owner
- **validation_violations** (customer_owned): written on request paths: app/modules/capabilities/services/capability_taxonomy_service.py:CapabilityTaxonomyService._validate_domain_compliance (helper, reached by name match from app/api/acm_hybrid_routes.py:validate_capabilities); app/modules/capabilities/services/capability_taxonomy_service.py:CapabilityTaxonomyService._validate_length_requirements (helper, reached by name match from app/api/import_history_routes.py:export_job_data); app/modules/capabilities/services/capability_taxonomy_service.py:CapabilityTaxonomyService._validate_level_hierarchy (helper, reached by name match from app/api/import_history_routes.py:export_job_data); but no organization_id and no foreign key to an owner; only link is an audit user column
- **vendor_concentration_analysis** (customer_owned): the row is computed from application components (customer data) but keyed only by vendor_organization_id, so two organisations would share one row; needs an organization_id
- **waitlist_signups** (customer_owned): written on request paths: app/main/views.py:index (a request route); but no organization_id and no foreign key to an owner

## Unused (product-manager decides)

- adm_audit_summaries (ADMAuditSummary)
- adm_data_retention_policies (ADMDataRetentionPolicy): created_by_id -> users.organization_id
- adm_phase_steps (ADMPhaseStep)
- adr_process_links (ADRProcessLink): adr_id -> architecture_decision_records.organization_id
- application_duplication_reports (ApplicationDuplicationReport)
- archimate_capability_mapping (ArchiMateCapabilityMapping): archimate_capability_id -> archimate_capabilities.business_capability_id -> business_capability.organization_id
- batch_job_statistics (BatchJobStatistics)
- business_initiatives (BusinessInitiative)
- capability_actor_raci (CapabilityActorRaci): actor_id -> business_actors.organization_id
- capability_maturity_level (CapabilityMaturityLevel)
- capability_prioritization (CapabilityPrioritization): capability_id -> business_capability.organization_id
- card_applications (CardApplication): application_id -> application_components.organization_id
- card_archimate_elements (CardArchiMateElement): archimate_element_id -> archimate_elements.organization_id
- card_capabilities (CardCapability): capability_id -> unified_capabilities.organization_id
- card_initiatives (CardInitiative): card_id -> kanban_cards.organization_id
- card_systems (CardSystem): card_id -> kanban_cards.organization_id
- compliance_requirements_model (ComplianceRequirementModel)
- dashboard_edits (DashboardEdit)
- detection_schedules (DetectionSchedule): created_by -> users.organization_id
- dynamics_metadata (DynamicsMetadata): configuration_id -> platform_configurations.created_by_id -> users.organization_id
- frontend_configuration (FrontendConfiguration)
- gap_analysis_recommendations (GapAnalysisRecommendation): analysis_id -> capability_gap_analysis.organization_id
- governance_capability_links (GovernanceCapabilityLink): capability_id -> business_capability.organization_id
- llm_prompt_version (LLMPromptVersion)
- manufacturing_benchmarks (ManufacturingBenchmark)
- manufacturing_improvement_initiatives (ManufacturingImprovementInitiative): manufacturing_capability_id -> manufacturing_capabilities.unified_capability_id -> unified_capabilities.organization_id
- manufacturing_processes (ManufacturingProcess)
- manufacturing_value_stream_stages (ManufacturingValueStreamStage)
- manufacturing_value_streams (ManufacturingValueStream)
- optimization_runs (OptimizationRun): created_by_id -> users.organization_id
- platform_ai_interactions (PlatformAIInteraction): pipeline_id -> generation_pipelines.architecture_id -> architecture_models.organization_id
- platform_types (PlatformType)
- policy_enforcement (PolicyEnforcement)
- process_actor_raci (ProcessActorRaci): actor_id -> business_actors.organization_id
- production_orders (ProductionOrder): production_line_id -> production_lines.archimate_element_id -> archimate_elements.organization_id
- project_notes (ProjectNote): project_id -> projects.organization_id
- project_resources (ProjectResource): project_id -> projects.organization_id
- rag_metrics (RAGMetrics)
- rag_queries (RAGQuery)
- rag_results (RAGResult)
- review_queue_statistics (ReviewQueueStatistics)
- salesforce_config_metadata (SalesforceConfigMetadata): configuration_id -> platform_configurations.created_by_id -> users.organization_id
- sap_metadata (SAPMetadata): configuration_id -> platform_configurations.created_by_id -> users.organization_id
- solution_stakeholder_concerns (SolutionStakeholderConcern): stakeholder_id -> solution_stakeholders.organization_id
- suggestion_feedback (SuggestionFeedback)
- systems (System)
- technology_capability (TechnologyCapabilityMapping): business_capability_id -> business_capability.organization_id
- unified_capability_technology_mapping (CapabilityTechnologyMapping): capability_id -> unified_capabilities.organization_id
- validation_gates (ValidationGate)
- vendor_integration_catalog (VendorIntegrationCatalog): template_id -> vendor_stack_templates.organization_id
- vendor_product_aliases (VendorProductAlias): created_by_id -> users.organization_id
- viewpoint_stakeholder_mappings (ViewpointStakeholderMapping): viewpoint_id -> archimate_viewpoints.organization_id
- workflow_cleanup_nodes (WorkflowCleanupNode): pipeline_id -> workflow_pipelines.architecture_id -> architecture_models.organization_id
- workflow_edges (WorkflowEdge): pipeline_id -> workflow_pipelines.architecture_id -> architecture_models.organization_id
- workflow_node_types (WorkflowNodeType)
