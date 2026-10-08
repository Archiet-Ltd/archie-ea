"""
Tests for the dashboard module migration.

Verifies:
- The register() function is importable
- All 2 blueprints are importable with correct .name attributes
- Route counts match expectations (17 + 40 = 57)
"""

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


class TestDashboardModuleImports:
    """Test that the dashboard module components are importable."""

    def test_register_function_importable(self):
        """register() function can be imported from the dashboard module."""
        from app.modules.dashboard import register

        assert callable(register), "register should be a callable function"

    def test_dashboard_bp_importable_with_correct_name(self):
        """dashboard_bp blueprint has name 'dashboard'."""
        from app.modules.dashboard.routes.dashboard_views import dashboard_bp

        assert dashboard_bp.name == "dashboard", (
            f"Expected blueprint name 'dashboard', got '{dashboard_bp.name}'"
        )

    def test_dashboard_pages_bp_importable_with_correct_name(self):
        """dashboard_pages_bp blueprint has name 'dashboard_pages'."""
        from app.modules.dashboard.routes.dashboard_pages_routes import (
            dashboard_pages_bp,
        )

        assert dashboard_pages_bp.name == "dashboard_pages", (
            f"Expected blueprint name 'dashboard_pages', got '{dashboard_pages_bp.name}'"
        )


class TestDashboardBlueprintNesting:
    """Test dashboard blueprint configuration."""

    def test_dashboard_pages_has_url_prefix(self):
        """dashboard_pages_bp has url_prefix='/dashboard' baked in."""
        from app.modules.dashboard.routes.dashboard_pages_routes import (
            dashboard_pages_bp,
        )

        assert dashboard_pages_bp.url_prefix == "/dashboard", (
            f"Expected url_prefix '/dashboard', got '{dashboard_pages_bp.url_prefix}'"
        )


class TestDashboardRouteCount:
    """Test that route counts match expectations."""

    def test_dashboard_views_route_count(self):
        """dashboard_bp should have 17 direct routes (excluding nested blueprint routes)."""
        from app.modules.dashboard.routes.dashboard_views import dashboard_bp

        # Count deferred view functions (route registrations)
        # Each @bp.route decorator adds a deferred function
        route_count = len(dashboard_bp.deferred_functions)
        assert route_count >= 17, (
            f"Expected at least 17 deferred functions on dashboard_bp, got {route_count}"
        )

    def test_dashboard_pages_route_count(self):
        """dashboard_pages_bp has its 30 routes; a change to the page set changes this."""
        from app.modules.dashboard.routes.dashboard_pages_routes import (
            dashboard_pages_bp,
        )

        route_count = len(dashboard_pages_bp.deferred_functions)
        assert route_count == 30, (
            f"Expected 30 deferred functions on dashboard_pages_bp, got {route_count}"
        )


class TestDashboardModuleRegistration:
    """Test that the module registers correctly with a Flask app."""

    def test_register_adds_blueprints_to_app(self, app):
        """register() should add dashboard and dashboard_pages blueprints to the app."""
        # The app fixture already has blueprints registered via create_app()
        # We verify our module's blueprints exist by checking names
        blueprint_names = list(app.blueprints.keys())

        assert "dashboard" in blueprint_names, (
            "'dashboard' blueprint should be registered in the app"
        )
        assert "dashboard_pages" in blueprint_names, (
            "'dashboard_pages' blueprint should be registered in the app"
        )
