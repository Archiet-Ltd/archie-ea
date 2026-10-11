"""
Tests for the import_batch module migration.

Verifies:
- The register() function is importable
- All 4 blueprints are importable with correct .name attributes
- Blueprint URL prefixes are correct
- Route counts match expectations (46 routes total)
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


class TestImportBatchModuleImports:
    """Test that the import_batch module components are importable."""

    def test_register_function_importable(self):
        """register() function can be imported from the import_batch module."""
        from app.modules.import_batch import register

        assert callable(register), "register should be a callable function"

    def test_batch_import_bp_importable(self):
        """batch_import_bp has name 'batch_import_api'."""
        from app.modules.import_batch.routes.batch_import_routes import batch_import_bp

        assert batch_import_bp.name == "batch_import_api", (
            f"Expected 'batch_import_api', got '{batch_import_bp.name}'"
        )

    def test_batch_import_view_bp_importable(self):
        """batch_import_view_bp has name 'batch_import_view'."""
        from app.modules.import_batch.routes.batch_import_view_routes import batch_import_view_bp

        assert batch_import_view_bp.name == "batch_import_view", (
            f"Expected 'batch_import_view', got '{batch_import_view_bp.name}'"
        )

    def test_batch_processing_bp_importable(self):
        """batch_processing_bp has name 'batch_processing'."""
        from app.modules.import_batch.routes.batch_processing_routes import batch_processing_bp

        assert batch_processing_bp.name == "batch_processing", (
            f"Expected 'batch_processing', got '{batch_processing_bp.name}'"
        )

    def test_unified_import_bp_importable(self):
        """unified_import bp has name 'unified_import'."""
        from app.modules.import_batch.routes.unified_import_routes import bp as unified_import_bp

        assert unified_import_bp.name == "unified_import", (
            f"Expected 'unified_import', got '{unified_import_bp.name}'"
        )


class TestImportBatchBlueprintConfig:
    """Test that blueprints have correct URL prefixes."""

    def test_batch_import_api_prefix(self):
        """batch_import_bp has url_prefix='/api/batch-import'."""
        from app.modules.import_batch.routes.batch_import_routes import batch_import_bp

        assert batch_import_bp.url_prefix == "/api/batch-import"

    def test_batch_import_view_prefix(self):
        """batch_import_view_bp has url_prefix='/batch-import'."""
        from app.modules.import_batch.routes.batch_import_view_routes import batch_import_view_bp

        assert batch_import_view_bp.url_prefix == "/batch-import"

    def test_batch_processing_prefix(self):
        """batch_processing_bp has url_prefix='/api/batch'."""
        from app.modules.import_batch.routes.batch_processing_routes import batch_processing_bp

        assert batch_processing_bp.url_prefix == "/api/batch"


class TestImportBatchRouteCount:
    """Test that route counts match expectations."""

    def test_batch_import_api_route_count(self):
        """batch_import_bp should have at least 26 routes."""
        from app.modules.import_batch.routes.batch_import_routes import batch_import_bp

        count = len(batch_import_bp.deferred_functions)
        assert count >= 26, (
            f"Expected >= 26 deferred functions on batch_import_bp, got {count}"
        )

    def test_batch_import_view_route_count(self):
        """batch_import_view_bp should have at least 4 routes."""
        from app.modules.import_batch.routes.batch_import_view_routes import batch_import_view_bp

        count = len(batch_import_view_bp.deferred_functions)
        assert count >= 4, (
            f"Expected >= 4 deferred functions on batch_import_view_bp, got {count}"
        )

    def test_batch_processing_route_count(self):
        """batch_processing_bp should have at least 13 routes."""
        from app.modules.import_batch.routes.batch_processing_routes import batch_processing_bp

        count = len(batch_processing_bp.deferred_functions)
        assert count >= 13, (
            f"Expected >= 13 deferred functions on batch_processing_bp, got {count}"
        )


class TestImportBatchModuleRegistration:
    """Test that the module registers correctly with a Flask app."""

    def test_all_import_blueprints_in_app(self, app):
        """All import/batch blueprints should be registered in the app."""
        bp_names = list(app.blueprints.keys())

        expected = [
            "batch_import_api",
            "batch_import_view",
            "batch_processing",
            "unified_import",
        ]
        for name in expected:
            assert name in bp_names, (
                f"'{name}' blueprint should be registered. Found: {bp_names}"
            )

    def test_batch_import_url_rules_exist(self, app):
        """App should have batch-import URL rules."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        batch_rules = [r for r in rules if "/batch-import" in r or "/api/batch" in r]
        assert len(batch_rules) >= 30, (
            f"Expected >= 30 batch/import URL rules, found {len(batch_rules)}"
        )
