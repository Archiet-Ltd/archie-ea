"""
Tests for the capabilities module Phase 4 decomposition.

Verifies:
- All 7 blueprints importable from app.modules.capabilities
- capability_map monolith decomposed into 9 files sharing one blueprint
- Blueprint names and prefixes match legacy
- register() wires all blueprints to a Flask app
- Route parity: 67 capability_map routes + other blueprint routes
"""
import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


class TestCapabilitiesModuleImports:
    """Test that all capability blueprints are importable from module paths."""

    def test_register_function_importable(self):
        from app.modules.capabilities import register

        assert callable(register)

    def test_capability_map_blueprint(self):
        from app.modules.capabilities.routes import capability_map

        assert capability_map.name == "capability_map"

    def test_enterprise_api_bp(self):
        from app.modules.capabilities.routes.enterprise_api_routes import enterprise_api_bp

        assert enterprise_api_bp.name == "enterprise_entity_api"

    def test_enterprise_crud_bp(self):
        from app.modules.capabilities.routes.enterprise_crud_routes import enterprise_crud_bp

        assert enterprise_crud_bp.name == "enterprise_crud"

    def test_abacus_consolidation_bp(self):
        from app.modules.capabilities.routes.abacus_consolidation import bp

        assert bp.name == "abacus_consolidation"

    def test_maturity_management_bp(self):
        from app.modules.capabilities.routes.maturity_routes import maturity_management

        assert maturity_management.name == "maturity_management"

    def test_acm_bp(self):
        from app.modules.capabilities.api.acm_routes import acm_bp

        assert acm_bp.name == "acm"


class TestCapabilityMapDecomposition:
    """Test that the monolith was correctly decomposed into 9 sub-modules."""

    def test_map_views_importable(self):
        from app.modules.capabilities.routes import map_views

        assert hasattr(map_views, "index")
        assert hasattr(map_views, "hierarchy")
        assert hasattr(map_views, "network")
        assert hasattr(map_views, "simple_view")
        assert hasattr(map_views, "dashboard")
        assert hasattr(map_views, "build_nodes_edges")

    def test_mapping_routes_importable(self):
        from app.modules.capabilities.routes import mapping_routes

        assert hasattr(mapping_routes, "api_nodes_edges")
        assert hasattr(mapping_routes, "api_create_mapping")
        assert hasattr(mapping_routes, "api_delete_mapping")
        assert hasattr(mapping_routes, "api_statistics")

    def test_export_routes_importable(self):
        from app.modules.capabilities.routes import export_routes

        assert hasattr(export_routes, "api_export_mappings")

    def test_process_routes_importable(self):
        from app.modules.capabilities.routes import process_routes

        assert hasattr(process_routes, "api_process_gaps")
        assert hasattr(process_routes, "api_capability_full_traceability")

    def test_domain_routes_importable(self):
        from app.modules.capabilities.routes import domain_routes

        assert hasattr(domain_routes, "api_unified_domains")
        assert hasattr(domain_routes, "api_manufacturing_domains")
        assert hasattr(domain_routes, "api_process_categories")

    def test_acm_map_routes_importable(self):
        from app.modules.capabilities.routes import acm_map_routes

        assert hasattr(acm_map_routes, "api_acm_domains")
        assert hasattr(acm_map_routes, "api_acm_bulk_mappings")

    def test_roadmap_routes_importable(self):
        from app.modules.capabilities.routes import roadmap_routes

        assert hasattr(roadmap_routes, "api_roadmap_gaps")
        assert hasattr(roadmap_routes, "api_roadmap_work_packages")

    def test_archimate_cap_routes_importable(self):
        from app.modules.capabilities.routes import archimate_cap_routes

        assert hasattr(archimate_cap_routes, "api_archimate_elements")
        assert hasattr(archimate_cap_routes, "api_save_apqc_mappings")

    def test_vendor_matrix_routes_importable(self):
        from app.modules.capabilities.routes import vendor_matrix_routes

        assert hasattr(vendor_matrix_routes, "api_vendor_capability_matrix")
        assert hasattr(vendor_matrix_routes, "api_vendor_capability_risks")


class TestCapabilitiesModuleRegistration:
    """Test that the module registers all blueprints correctly."""

    def test_all_7_blueprints_registered(self, app):
        bp_names = list(app.blueprints.keys())
        expected = [
            "capability_map",
            "enterprise_entity_api",
            "enterprise_crud",
            "abacus_consolidation",
            "maturity_management",
            "acm",
        ]
        for name in expected:
            assert name in bp_names, f"'{name}' blueprint missing from app"

    def test_capability_map_route_count(self, app):
        """Decomposed capability_map should register 67+ routes."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        cap_rules = [r for r in rules if r.startswith("/capability-map")]
        assert len(cap_rules) >= 67, (
            f"Expected >= 67 capability-map URL rules, found {len(cap_rules)}"
        )

    def test_capabilities_total_route_count(self, app):
        """All capability routes combined should be 100+."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        cap_prefixes = [
            "/capability-map", "/api/enterprise", "/enterprise",
            "/admin/abacus/consolidation", "/capability-maturity",
            "/api/acm",
        ]
        cap_rules = [r for r in rules if any(r.startswith(p) for p in cap_prefixes)]
        assert len(cap_rules) >= 100, (
            f"Expected >= 100 total capability URL rules, found {len(cap_rules)}"
        )


class TestCapabilitiesEndpointParity:
    """Test that key endpoint names are preserved for url_for() compatibility."""

    def test_capability_map_key_endpoints(self, app):
        """Key capability_map endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "capability_map.index",
            "capability_map.dashboard",
            "capability_map.api_statistics",
            "capability_map.api_mappings",
            "capability_map.api_create_mapping",
            "capability_map.api_export_mappings",
            "capability_map.api_process_gaps",
            "capability_map.api_roadmap_gaps",
            "capability_map.api_archimate_elements",
            "capability_map.api_vendor_capability_matrix",
            "capability_map.api_acm_domains",
            "capability_map.api_unified_domains",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_other_blueprint_key_endpoints(self, app):
        """Key endpoints from other capability blueprints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "enterprise_entity_api.get_applications",
            "enterprise_crud.list_capabilities",
            "acm.get_domains",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"


class TestCapabilitiesServicesImportable:
    """Test that module services are importable."""

    def test_capability_service(self):
        from app.modules.capabilities.services.capability_service import CapabilityTaxonomyService

        assert CapabilityTaxonomyService is not None

    def test_analysis_service(self):
        from app.modules.capabilities.services.analysis_service import CapabilityGapAnalysisService

        assert CapabilityGapAnalysisService is not None

    def test_seeder_service(self):
        from app.modules.capabilities.services.seeder_service import ACMTechnicalCapabilityService

        assert ACMTechnicalCapabilityService is not None
