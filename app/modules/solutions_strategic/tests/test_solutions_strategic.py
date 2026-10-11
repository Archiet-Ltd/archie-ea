"""
Tests for the solutions_strategic module migration.

Verifies:
- The register() function is importable
- Key blueprints are importable with correct .name attributes
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


class TestSolutionsStrategicModuleImports:
    def test_register_function_importable(self):
        from app.modules.solutions_strategic import register

        assert callable(register)

    def test_strategic_bp_importable(self):
        from app.modules.solutions_strategic.v2.routes.strategic_routes import strategic_bp

        assert strategic_bp.name == "strategic"

    def test_solution_design_importable(self):
        from app.modules.solutions_strategic.v2.routes.solution_design_routes import solution_design_bp

        assert solution_design_bp.name == "solution_design"

    def test_roadmap_api_importable(self):
        from app.modules.solutions_strategic.v2.routes.roadmap_api import roadmap_bp

        assert roadmap_bp.name == "roadmap_api"


class TestSolutionsStrategicModuleRegistration:
    def test_key_blueprints_in_app(self, app):
        bp_names = list(app.blueprints.keys())
        expected = ["strategic", "solution_design", "roadmap_api", "solution_composer"]
        for name in expected:
            assert name in bp_names, f"'{name}' should be registered"

    def test_url_rules_exist(self, app):
        rules = [r.rule for r in app.url_map.iter_rules()]
        sol_rules = [
            r for r in rules
            if "/strategic" in r
            or "/solutions" in r
            or "/api/roadmap" in r
            or "/api/solution-composer" in r
            or "/api/roadmap-builder" in r
            or "/solution-architect" in r
            or "/api/market-intelligence" in r
        ]
        assert len(sol_rules) >= 100, (
            f"Expected >= 100 solutions/strategic URL rules, found {len(sol_rules)}"
        )
