"""
Tests for the vendors module migration.

Verifies:
- The register() function is importable and callable
- All 9 vendor blueprints are importable with correct .name attributes
- Blueprint URL prefixes are correct
- Route counts match expectations
- Module registers correctly with a Flask app
"""
import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


class TestVendorsModuleImports:
    """Test that the vendors module components are importable."""

    def test_register_function_importable(self):
        """register() function can be imported from the vendors module."""
        from app.modules.vendors import register

        assert callable(register), "register should be a callable function"

    def test_vendors_api_bp_importable(self):
        """vendors_api_bp has name 'vendors_api'."""
        from app.modules.vendors.api.api_vendors import vendors_api_bp

        assert vendors_api_bp.name == "vendors_api", (
            f"Expected 'vendors_api', got '{vendors_api_bp.name}'"
        )

    def test_vendor_analysis_bp_importable(self):
        """vendor_analysis_bp has name 'vendor_analysis'."""
        from app.modules.vendors.routes.vendor_analysis_routes import vendor_analysis_bp

        assert vendor_analysis_bp.name == "vendor_analysis", (
            f"Expected 'vendor_analysis', got '{vendor_analysis_bp.name}'"
        )

    def test_vendor_mdm_bp_importable(self):
        """vendor_mdm_bp has name 'vendor_mdm'."""
        from app.modules.vendors.routes.vendor_mdm_api import vendor_mdm_bp

        assert vendor_mdm_bp.name == "vendor_mdm", (
            f"Expected 'vendor_mdm', got '{vendor_mdm_bp.name}'"
        )

    def test_vendor_management_bp_importable(self):
        """vendor_management_bp has name 'vendor_management'."""
        from app.modules.vendors.routes.vendor_management_routes import vendor_management_bp

        assert vendor_management_bp.name == "vendor_management", (
            f"Expected 'vendor_management', got '{vendor_management_bp.name}'"
        )

    def test_unified_vendors_bp_importable(self):
        """unified_vendors_bp has name 'unified_vendors'."""
        from app.modules.vendors.routes.unified_vendor_views import unified_vendors_bp

        assert unified_vendors_bp.name == "unified_vendors", (
            f"Expected 'unified_vendors', got '{unified_vendors_bp.name}'"
        )

    def test_unified_vendors_api_bp_importable(self):
        """unified_vendors_api_bp has name 'unified_vendors_api'."""
        from app.modules.vendors.routes.unified_vendor_api import unified_vendors_api_bp

        assert unified_vendors_api_bp.name == "unified_vendors_api", (
            f"Expected 'unified_vendors_api', got '{unified_vendors_api_bp.name}'"
        )



    def test_vendor_product_bp_importable(self):
        """vendor_product_bp has name 'vendor_product'."""
        from app.modules.vendors.api.vendor_product_routes import vendor_product_bp

        assert vendor_product_bp.name == "vendor_product", (
            f"Expected 'vendor_product', got '{vendor_product_bp.name}'"
        )

    def test_vendor_catalog_bp_importable(self):
        """vendor_bp (catalog) has name 'vendor'."""
        from app.modules.vendors.api.vendor_catalog_routes import vendor_bp

        assert vendor_bp.name == "vendor", (
            f"Expected 'vendor', got '{vendor_bp.name}'"
        )

    def test_vendor_discovery_bp_importable(self):
        """vendor_discovery_bp has name 'vendor_discovery'."""
        from app.modules.vendors.api.vendor_discovery_routes import vendor_discovery_bp

        assert vendor_discovery_bp.name == "vendor_discovery", (
            f"Expected 'vendor_discovery', got '{vendor_discovery_bp.name}'"
        )




class TestVendorsBlueprintConfig:
    """Test that blueprints have correct URL prefixes."""

    def test_vendor_management_prefix(self):
        """vendor_management_bp has url_prefix='/vendor-management'."""
        from app.modules.vendors.routes.vendor_management_routes import vendor_management_bp

        assert vendor_management_bp.url_prefix == "/vendor-management"

    def test_vendor_analysis_prefix(self):
        """vendor_analysis_bp has url_prefix='/vendor-analysis'."""
        from app.modules.vendors.routes.vendor_analysis_routes import vendor_analysis_bp

        assert vendor_analysis_bp.url_prefix == "/vendor-analysis"

    def test_vendor_mdm_prefix(self):
        """vendor_mdm_bp has url_prefix='/api/vendor-mdm'."""
        from app.modules.vendors.routes.vendor_mdm_api import vendor_mdm_bp

        assert vendor_mdm_bp.url_prefix == "/api/vendor-mdm"

    def test_unified_vendors_prefix(self):
        """unified_vendors_bp has url_prefix='/vendors'."""
        from app.modules.vendors.routes.unified_vendor_views import unified_vendors_bp

        assert unified_vendors_bp.url_prefix == "/vendors"

    def test_unified_vendors_api_prefix(self):
        """unified_vendors_api_bp has url_prefix='/api/vendors'."""
        from app.modules.vendors.routes.unified_vendor_api import unified_vendors_api_bp

        assert unified_vendors_api_bp.url_prefix == "/api/vendors"


    def test_vendor_product_prefix(self):
        """vendor_product_bp has url_prefix='/api/vendor'."""
        from app.modules.vendors.api.vendor_product_routes import vendor_product_bp

        assert vendor_product_bp.url_prefix == "/api/vendor"

    def test_vendor_catalog_prefix(self):
        """vendor_bp (catalog) has url_prefix='/api/vendors'."""
        from app.modules.vendors.api.vendor_catalog_routes import vendor_bp

        assert vendor_bp.url_prefix == "/api/vendors"

    def test_vendor_discovery_prefix(self):
        """vendor_discovery_bp has url_prefix='/api/vendor-discovery'."""
        from app.modules.vendors.api.vendor_discovery_routes import vendor_discovery_bp

        assert vendor_discovery_bp.url_prefix == "/api/vendor-discovery"



class TestVendorsRouteCount:
    """Test that route counts match expectations for key blueprints."""

    def test_vendor_management_route_count(self):
        """vendor_management_bp should have routes (deferred functions)."""
        from app.modules.vendors.routes.vendor_management_routes import vendor_management_bp

        count = len(vendor_management_bp.deferred_functions)
        assert count >= 9, (
            f"Expected >= 9 deferred functions on vendor_management_bp, got {count}"
        )


    def test_vendor_product_route_count(self):
        """vendor_product_bp should have routes."""
        from app.modules.vendors.api.vendor_product_routes import vendor_product_bp

        count = len(vendor_product_bp.deferred_functions)
        assert count >= 9, (
            f"Expected >= 9 deferred functions on vendor_product_bp, got {count}"
        )

    def test_vendor_catalog_route_count(self):
        """vendor_bp (catalog) should have routes."""
        from app.modules.vendors.api.vendor_catalog_routes import vendor_bp

        count = len(vendor_bp.deferred_functions)
        assert count >= 13, (
            f"Expected >= 13 deferred functions on vendor_bp, got {count}"
        )

    def test_vendor_discovery_route_count(self):
        """vendor_discovery_bp should have routes."""
        from app.modules.vendors.api.vendor_discovery_routes import vendor_discovery_bp

        count = len(vendor_discovery_bp.deferred_functions)
        assert count >= 5, (
            f"Expected >= 5 deferred functions on vendor_discovery_bp, got {count}"
        )



    def test_unified_vendors_route_count(self):
        """unified_vendors_bp should have routes."""
        from app.modules.vendors.routes.unified_vendor_views import unified_vendors_bp

        count = len(unified_vendors_bp.deferred_functions)
        assert count >= 20, (
            f"Expected >= 20 deferred functions on unified_vendors_bp, got {count}"
        )

    def test_unified_vendors_api_route_count(self):
        """unified_vendors_api_bp should have routes."""
        from app.modules.vendors.routes.unified_vendor_api import unified_vendors_api_bp

        count = len(unified_vendors_api_bp.deferred_functions)
        assert count >= 20, (
            f"Expected >= 20 deferred functions on unified_vendors_api_bp, got {count}"
        )


class TestFunctionBasedRegistration:
    """Test that function-based registration exports exist."""

    def test_register_vendor_product_routes_callable(self):
        """register_vendor_product_routes is callable."""
        from app.modules.vendors.api.vendor_product_routes import register_vendor_product_routes

        assert callable(register_vendor_product_routes)

    def test_register_vendor_catalog_routes_callable(self):
        """register_vendor_catalog_routes is callable."""
        from app.modules.vendors.api.vendor_catalog_routes import register_vendor_catalog_routes

        assert callable(register_vendor_catalog_routes)

    def test_register_vendor_discovery_routes_callable(self):
        """register_vendor_discovery_routes is callable."""
        from app.modules.vendors.api.vendor_discovery_routes import register_vendor_discovery_routes

        assert callable(register_vendor_discovery_routes)


class TestVendorsModuleRegistration:
    """Test that the module registers correctly with a Flask app."""

    def test_all_vendor_blueprints_in_app(self, app):
        """All 9 vendor blueprints should be registered in the app."""
        bp_names = list(app.blueprints.keys())

        expected = [
            "vendors_api",
            "vendor_analysis",
            "vendor_mdm",
            "vendor_management",
            "unified_vendors",
            "unified_vendors_api",


            "vendor_product",
            "vendor",
            "vendor_discovery",


        ]
        for name in expected:
            assert name in bp_names, (
                f"'{name}' blueprint should be registered. Found: {bp_names}"
            )

    def test_vendor_url_rules_exist(self, app):
        """App should have vendor-prefixed URL rules."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        vendor_rules = [r for r in rules if "/vendors" in r or "/vendor" in r]
        assert len(vendor_rules) >= 50, (
            f"Expected >= 50 vendor URL rules, found {len(vendor_rules)}"
        )

    def test_all_9_blueprints_present(self, app):
        """Exactly 9 vendor-related blueprints should be present."""
        vendor_bp_names = [
            "vendors_api",
            "vendor_analysis",
            "vendor_mdm",
            "vendor_management",
            "unified_vendors",
            "unified_vendors_api",


            "vendor_product",
            "vendor",
            "vendor_discovery",


        ]
        registered = [name for name in vendor_bp_names if name in app.blueprints]
        assert len(registered) == 9, (
            f"Expected 9 vendor blueprints registered, found {len(registered)}: {registered}"
        )
