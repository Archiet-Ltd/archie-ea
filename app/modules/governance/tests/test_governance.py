"""
Tests for the governance module migration.

Verifies:
- The register() function is importable
- All 4 blueprints are importable with correct .name attributes
- Blueprint URL prefixes are correct
- Route counts match expectations (28 routes total)
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


class TestGovernanceModuleImports:
    """Test that the governance module components are importable."""

    def test_register_function_importable(self):
        """register() function can be imported from the governance module."""
        from app.modules.governance import register

        assert callable(register), "register should be a callable function"

    def test_consolidation_list_bp_importable(self):
        """consolidation_list_bp has name 'consolidation_list'."""
        from app.modules.governance.routes.consolidation_list_routes import consolidation_list_bp

        assert consolidation_list_bp.name == "consolidation_list"

    def test_policy_monitoring_bp_importable(self):
        """policy_monitoring_bp has name 'policy_monitoring'."""
        from app.modules.governance.routes.policy_monitoring_routes import policy_monitoring_bp

        assert policy_monitoring_bp.name == "policy_monitoring"

    def test_capability_management_importable(self):
        """capability_management has name 'capability_management'."""
        from app.modules.governance.routes.capability_management_routes import capability_management

        assert capability_management.name == "capability_management"

    def test_capability_governance_importable(self):
        """capability_governance has name 'capability_governance'."""
        from app.modules.governance.routes.capability_governance_routes import capability_governance

        assert capability_governance.name == "capability_governance"


class TestGovernanceBlueprintConfig:
    """Test that blueprints have correct URL prefixes."""

    def test_consolidation_list_prefix(self):
        """consolidation_list_bp has url_prefix='/consolidation-list'."""
        from app.modules.governance.routes.consolidation_list_routes import consolidation_list_bp

        assert consolidation_list_bp.url_prefix == "/consolidation-list"

    def test_policy_monitoring_prefix(self):
        """policy_monitoring_bp has url_prefix='/policy-monitoring'."""
        from app.modules.governance.routes.policy_monitoring_routes import policy_monitoring_bp

        assert policy_monitoring_bp.url_prefix == "/policy-monitoring"


class TestGovernanceRouteCount:
    """Test that route counts match expectations."""

    def test_consolidation_list_route_count(self):
        """consolidation_list_bp should have at least 8 routes."""
        from app.modules.governance.routes.consolidation_list_routes import consolidation_list_bp

        count = len(consolidation_list_bp.deferred_functions)
        assert count >= 8, (
            f"Expected >= 8 deferred functions on consolidation_list_bp, got {count}"
        )

    def test_policy_monitoring_route_count(self):
        """policy_monitoring_bp should have at least 7 routes."""
        from app.modules.governance.routes.policy_monitoring_routes import policy_monitoring_bp

        count = len(policy_monitoring_bp.deferred_functions)
        assert count >= 7, (
            f"Expected >= 7 deferred functions on policy_monitoring_bp, got {count}"
        )

    def test_capability_governance_route_count(self):
        """capability_governance should have at least 6 routes."""
        from app.modules.governance.routes.capability_governance_routes import capability_governance

        count = len(capability_governance.deferred_functions)
        assert count >= 6, (
            f"Expected >= 6 deferred functions on capability_governance, got {count}"
        )

    def test_capability_management_route_count(self):
        """capability_management should have at least 7 routes."""
        from app.modules.governance.routes.capability_management_routes import capability_management

        count = len(capability_management.deferred_functions)
        assert count >= 7, (
            f"Expected >= 7 deferred functions on capability_management, got {count}"
        )


class TestGovernanceModuleRegistration:
    """Test that the module registers correctly with a Flask app."""

    def test_all_governance_blueprints_in_app(self, app):
        """All governance blueprints should be registered in the app."""
        bp_names = list(app.blueprints.keys())

        expected = [
            "consolidation_list",
            "policy_monitoring",
            "capability_management",
            "capability_governance",
        ]
        for name in expected:
            assert name in bp_names, (
                f"'{name}' blueprint should be registered. Found: {bp_names}"
            )

    def test_governance_url_rules_exist(self, app):
        """App should have governance-related URL rules."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        gov_rules = [
            r for r in rules
            if "/consolidation-list" in r
            or "/policy-monitoring" in r
            or "/capability-management" in r
            or "/capability-governance" in r
        ]
        assert len(gov_rules) >= 25, (
            f"Expected >= 25 governance URL rules, found {len(gov_rules)}"
        )
