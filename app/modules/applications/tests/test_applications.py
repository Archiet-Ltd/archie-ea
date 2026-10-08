"""
Tests for the applications module Phase 4 decomposition.

Verifies:
- All 3 blueprints importable from app.modules.applications
- unified_applications monolith decomposed into 11 files sharing one blueprint
- Blueprint names and prefixes match legacy
- register() wires all blueprints to a Flask app
- Route parity: 98 unified_applications + 5 merging + 26 implementation_planning
"""
import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


class TestApplicationsModuleImports:
    """Test that all application blueprints are importable from module paths."""

    def test_register_function_importable(self):
        from app.modules.applications import register

        assert callable(register)

    def test_unified_applications_blueprint_from_module(self):
        from app.modules.applications.routes import unified_applications_bp

        assert unified_applications_bp.name == "unified_applications"

    def test_application_merging_blueprint(self):
        from app.api.application_merging_routes import merging_bp

        assert merging_bp.name == "application_merging"

    def test_implementation_planning_blueprint(self):
        from app.implementation_planning import implementation_planning

        assert implementation_planning.name == "implementation_planning"

    def test_constants_importable(self):
        from app.modules.applications.routes._constants import (
            ARCHIMATE_RELATIONSHIP_CHOICES,
            CAPABILITY_MATURITY_CHOICES,
            CAPABILITY_SUPPORT_LEVEL_CHOICES,
            DEFAULT_TOKEN_RATE_DIVISOR,
            VENDOR_CRITICALITY_CHOICES,
            VENDOR_DEPLOYMENT_CHOICES,
            VENDOR_HOSTING_CHOICES,
        )

        assert DEFAULT_TOKEN_RATE_DIVISOR == 1_000_000
        assert len(ARCHIMATE_RELATIONSHIP_CHOICES) == 10
        assert len(CAPABILITY_SUPPORT_LEVEL_CHOICES) == 6
        assert len(CAPABILITY_MATURITY_CHOICES) == 5
        assert len(VENDOR_DEPLOYMENT_CHOICES) == 5
        assert len(VENDOR_CRITICALITY_CHOICES) == 4
        assert len(VENDOR_HOSTING_CHOICES) == 4

    def test_helpers_importable(self):
        from app.modules.applications.routes._helpers import (
            _cleanup_application_relationships,
            _format_date,
            _load_domain_model_elements,
            _query_archimate_by_layer,
            _sanitize_csv_value,
            _vendors_impl,
            calculate_match_confidence,
            get_matching_reason,
        )

        assert callable(_query_archimate_by_layer)
        assert callable(_load_domain_model_elements)
        assert callable(_format_date)
        assert callable(_cleanup_application_relationships)
        assert callable(_vendors_impl)
        assert callable(_sanitize_csv_value)
        assert callable(calculate_match_confidence)
        assert callable(get_matching_reason)


class TestMonolithDecomposition:
    """Test that the 10,736-line monolith was correctly decomposed into 11 sub-modules."""

    def test_list_views_importable(self):
        from app.modules.applications.routes import list_views

        assert hasattr(list_views, "application_list")
        assert hasattr(list_views, "api_list")
        assert hasattr(list_views, "api_table_data")
        assert hasattr(list_views, "dashboard")
        assert hasattr(list_views, "model_dashboard")

    def test_crud_routes_importable(self):
        from app.modules.applications.routes import crud_routes

        assert hasattr(crud_routes, "application_create")
        assert hasattr(crud_routes, "application_detail")
        assert hasattr(crud_routes, "application_edit")
        assert hasattr(crud_routes, "application_delete")
        assert hasattr(crud_routes, "bulk_delete_applications")
        assert hasattr(crud_routes, "generate_application_archimate")
        assert hasattr(crud_routes, "api_delete_application")
        assert hasattr(crud_routes, "api_bulk_consolidate")

    def test_update_routes_importable(self):
        from app.modules.applications.routes import update_routes

        assert hasattr(update_routes, "update_overview")
        assert hasattr(update_routes, "update_health_quality")
        assert hasattr(update_routes, "update_governance")
        assert hasattr(update_routes, "update_resources")
        assert hasattr(update_routes, "update_strategy_layer")

    def test_vendor_display_routes_importable(self):
        from app.modules.applications.routes import vendor_display_routes

        assert hasattr(vendor_display_routes, "vendors")
        assert hasattr(vendor_display_routes, "vendors_create")
        assert hasattr(vendor_display_routes, "vendor_detail")

    def test_import_export_routes_importable(self):
        from app.modules.applications.routes import import_export_routes

        assert hasattr(import_export_routes, "application_import_page")
        assert hasattr(import_export_routes, "application_import")
        assert hasattr(import_export_routes, "import_with_ai_review")
        assert hasattr(import_export_routes, "export_csv")
        assert hasattr(import_export_routes, "analyze_import")
        assert hasattr(import_export_routes, "preview_excel")
        assert hasattr(import_export_routes, "rollback_import")
        assert hasattr(import_export_routes, "analyze_import_stream")

    def test_import_sophisticated_routes_importable(self):
        from app.modules.applications.routes import import_sophisticated_routes

        assert hasattr(import_sophisticated_routes, "get_import_fields")
        assert hasattr(import_sophisticated_routes, "analyze_import_duplicates")
        assert hasattr(import_sophisticated_routes, "upload_excel_applications")
        assert hasattr(import_sophisticated_routes, "preview_ai_analysis")
        assert hasattr(import_sophisticated_routes, "import_manual_applications")
        assert hasattr(import_sophisticated_routes, "import_history")
        assert hasattr(import_sophisticated_routes, "rollback_import_by_session")
        assert hasattr(import_sophisticated_routes, "download_application_template")

    def test_document_routes_importable(self):
        from app.modules.applications.routes import document_routes

        assert hasattr(document_routes, "update_document_file")
        assert hasattr(document_routes, "upload_document_file")
        assert hasattr(document_routes, "download_document_file")
        assert hasattr(document_routes, "delete_document_file")
        assert hasattr(document_routes, "application_capability_mapping_create")
        assert hasattr(document_routes, "application_capability_mapping_delete")

    def test_vendor_api_routes_importable(self):
        from app.modules.applications.routes import vendor_api_routes

        assert hasattr(vendor_api_routes, "match_applications_to_vendors")
        assert hasattr(vendor_api_routes, "confirm_vendor_matches")
        assert hasattr(vendor_api_routes, "application_architecture_detail")
        assert hasattr(vendor_api_routes, "vendor_analyze_api")
        assert hasattr(vendor_api_routes, "api_get_vendor_results")
        assert hasattr(vendor_api_routes, "api_export_vendor_analysis")
        assert hasattr(vendor_api_routes, "generate_vendor_process_mappings")
        assert hasattr(vendor_api_routes, "save_vendor_process_mappings")
        assert hasattr(vendor_api_routes, "get_vendor_process_coverage")
        assert hasattr(vendor_api_routes, "get_vendor_capability_analysis")
        assert hasattr(vendor_api_routes, "get_vendor_organizations")
        assert hasattr(vendor_api_routes, "get_capabilities")
        assert hasattr(vendor_api_routes, "create_vendor_analysis")
        assert hasattr(vendor_api_routes, "confirm_vendor_mapping")
        assert hasattr(vendor_api_routes, "get_architectural_analysis")

    def test_element_routes_importable(self):
        from app.modules.applications.routes import element_routes

        assert hasattr(element_routes, "application_requirement_add")
        assert hasattr(element_routes, "application_business_process_add")
        assert hasattr(element_routes, "application_business_actor_add")
        assert hasattr(element_routes, "application_business_service_add")
        assert hasattr(element_routes, "application_business_role_add")
        assert hasattr(element_routes, "application_interface_add")
        assert hasattr(element_routes, "application_service_add")
        assert hasattr(element_routes, "data_object_add")
        assert hasattr(element_routes, "technology_node_add")
        assert hasattr(element_routes, "technology_device_add")
        assert hasattr(element_routes, "system_software_add")
        assert hasattr(element_routes, "goal_add")
        assert hasattr(element_routes, "driver_add")
        assert hasattr(element_routes, "work_package_add")
        assert hasattr(element_routes, "deliverable_add")
        assert hasattr(element_routes, "plateau_add")

    def test_auto_mapping_routes_importable(self):
        from app.modules.applications.routes import auto_mapping_routes

        assert hasattr(auto_mapping_routes, "semantic_link_preview")
        assert hasattr(auto_mapping_routes, "apply_semantic_links")
        assert hasattr(auto_mapping_routes, "apqc_vendor_enriched_archimate")
        assert hasattr(auto_mapping_routes, "bulk_apqc_vendor_enriched_archimate")
        assert hasattr(auto_mapping_routes, "comprehensive_auto_map")
        assert hasattr(auto_mapping_routes, "comprehensive_auto_map_stream")
        assert hasattr(auto_mapping_routes, "accept_auto_map_results")
        assert hasattr(auto_mapping_routes, "estimate_auto_map_cost")

    def test_rationalization_api_routes_importable(self):
        from app.modules.applications.routes import rationalization_api_routes

        assert hasattr(rationalization_api_routes, "rationalization_dashboard")
        assert hasattr(rationalization_api_routes, "rationalization_run_detection")
        assert hasattr(rationalization_api_routes, "rationalization_get_groups")
        assert hasattr(rationalization_api_routes, "rationalization_get_runs")
        assert hasattr(rationalization_api_routes, "rationalization_auto_resolve")
        assert hasattr(rationalization_api_routes, "rationalization_ignore_merge_candidate")
        assert hasattr(rationalization_api_routes, "api_get_element")
        assert hasattr(rationalization_api_routes, "api_delete_element")
        assert hasattr(rationalization_api_routes, "api_list_requirements")
        assert hasattr(rationalization_api_routes, "api_template_frameworks")
        assert hasattr(rationalization_api_routes, "api_template_categories")
        assert hasattr(rationalization_api_routes, "api_list_templates")
        assert hasattr(rationalization_api_routes, "api_template_element_types")
        assert hasattr(rationalization_api_routes, "api_template_recommendations")
        assert hasattr(rationalization_api_routes, "api_check_duplicate_element")


class TestApplicationsModuleRegistration:
    """Test that the module registers all blueprints correctly."""

    def test_all_3_blueprints_registered(self, app):
        bp_names = list(app.blueprints.keys())
        expected = [
            "unified_applications",
            "application_merging",
            "implementation_planning",
        ]
        for name in expected:
            assert name in bp_names, f"'{name}' blueprint missing from app"

    def test_unified_applications_route_count(self, app):
        """Decomposed unified_applications should register 90+ URL rules."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        app_rules = [r for r in rules if r.startswith("/applications")]
        assert len(app_rules) >= 90, (
            f"Expected >= 90 /applications URL rules, found {len(app_rules)}"
        )

    def test_total_route_count(self, app):
        """All application-related routes combined should be 120+."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        prefixes = ["/applications", "/implementation", "/dashboard/api/applications/merging"]
        app_rules = [r for r in rules if any(r.startswith(p) for p in prefixes)]
        assert len(app_rules) >= 120, (
            f"Expected >= 120 total application URL rules, found {len(app_rules)}"
        )


class TestApplicationsEndpointParity:
    """Test that key endpoint names are preserved for url_for() compatibility."""

    def test_list_view_endpoints(self, app):
        """Key list/dashboard endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.application_list",
            "unified_applications.api_list",
            "unified_applications.api_table_data",
            "unified_applications.dashboard",
            "unified_applications.model_dashboard",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_crud_endpoints(self, app):
        """Key CRUD endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.application_create",
            "unified_applications.application_detail",
            "unified_applications.application_edit",
            "unified_applications.application_delete",
            "unified_applications.bulk_delete_applications",
            "unified_applications.generate_application_archimate",
            "unified_applications.api_delete_application",
            "unified_applications.api_bulk_consolidate",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_update_endpoints(self, app):
        """AJAX update endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.update_overview",
            "unified_applications.update_health_quality",
            "unified_applications.update_governance",
            "unified_applications.update_resources",
            "unified_applications.update_strategy_layer",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_import_export_endpoints(self, app):
        """Import/export endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.application_import_page",
            "unified_applications.application_import",
            "unified_applications.import_with_ai_review",
            "unified_applications.export_csv",
            "unified_applications.analyze_import",
            "unified_applications.preview_excel",
            "unified_applications.rollback_import",
            "unified_applications.analyze_import_stream",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_sophisticated_import_endpoints(self, app):
        """Sophisticated import endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.get_import_fields",
            "unified_applications.analyze_import_duplicates",
            "unified_applications.upload_excel_applications",
            "unified_applications.preview_ai_analysis",
            "unified_applications.import_manual_applications",
            "unified_applications.import_history",
            "unified_applications.rollback_import_by_session",
            "unified_applications.download_application_template",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_document_endpoints(self, app):
        """Document and capability mapping endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.update_document_file",
            "unified_applications.upload_document_file",
            "unified_applications.download_document_file",
            "unified_applications.delete_document_file",
            "unified_applications.application_capability_mapping_create",
            "unified_applications.application_capability_mapping_delete",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_vendor_api_endpoints(self, app):
        """Vendor matching and analysis API endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.match_applications_to_vendors",
            "unified_applications.confirm_vendor_matches",
            "unified_applications.vendor_analyze_api",
            "unified_applications.generate_vendor_process_mappings",
            "unified_applications.get_vendor_process_coverage",
            "unified_applications.get_vendor_capability_analysis",
            "unified_applications.create_vendor_analysis",
            "unified_applications.get_architectural_analysis",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_element_endpoints(self, app):
        """ArchiMate element addition endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.application_requirement_add",
            "unified_applications.application_business_process_add",
            "unified_applications.application_business_actor_add",
            "unified_applications.application_service_add",
            "unified_applications.data_object_add",
            "unified_applications.technology_node_add",
            "unified_applications.goal_add",
            "unified_applications.work_package_add",
            "unified_applications.plateau_add",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_auto_mapping_endpoints(self, app):
        """Auto-mapping and semantic linking endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.semantic_link_preview",
            "unified_applications.apply_semantic_links",
            "unified_applications.apqc_vendor_enriched_archimate",
            "unified_applications.comprehensive_auto_map",
            "unified_applications.comprehensive_auto_map_stream",
            "unified_applications.accept_auto_map_results",
            "unified_applications.estimate_auto_map_cost",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_rationalization_endpoints(self, app):
        """Rationalization dashboard and API endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_applications.rationalization_dashboard",
            "unified_applications.rationalization_run_detection",
            "unified_applications.rationalization_get_groups",
            "unified_applications.api_get_element",
            "unified_applications.api_delete_element",
            "unified_applications.api_list_templates",
            "unified_applications.api_template_recommendations",
            "unified_applications.api_check_duplicate_element",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_other_blueprint_endpoints(self, app):
        """Key endpoints from merging and implementation planning must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "application_merging.execute_merge",
            "application_merging.get_merge_candidates",
            "implementation_planning.implementation_dashboard",
            "implementation_planning.work_packages_list",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"
