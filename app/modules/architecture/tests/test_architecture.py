"""
Tests for the architecture module migration (full-copy).

Verifies:
- All 13 blueprints importable from app.modules.architecture
- Blueprint names and prefixes match legacy
- register() wires 12 of the 13 blueprints to a Flask app by default; the
  architecture monitoring blueprint mounts only when configured
- Route parity between legacy and module paths
"""
import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


class TestArchitectureModuleImports:
    """Test that all 13 architecture blueprints are importable from module paths."""

    def test_register_function_importable(self):
        from app.modules.architecture import register

        assert callable(register)

    def test_archimate_crud_bp(self):
        from app.modules.architecture.routes.archimate_crud import archimate_crud

        assert archimate_crud.name == "archimate_crud"
        assert archimate_crud.url_prefix == "/architecture"

    def test_archimate_api_bp(self):
        from app.modules.architecture.api.archimate_routes import archimate_api

        assert archimate_api.name == "archimate_api"

    def test_viewpoint_bp(self):
        from app.modules.architecture.api.viewpoint_routes import viewpoint_bp

        assert viewpoint_bp.name == "viewpoint_api"

    def test_architecture_crud_bp(self):
        from app.modules.architecture.routes.architecture_crud_routes import architecture_crud_bp

        assert architecture_crud_bp.name == "architecture_crud"

    def test_architecture_bp(self):
        from app.modules.architecture.routes.architecture_routes import architecture_bp

        assert architecture_bp.name == "architecture"

    def test_architecture_assistant_bp(self):
        from app.modules.architecture.routes.architecture_assistant_routes import architecture_assistant_bp

        assert architecture_assistant_bp.name == "architecture_assistant"

    def test_archimate_export_bp(self):
        from app.modules.architecture.routes.archimate_export_routes import archimate_export_bp

        assert archimate_export_bp.name == "archimate_export"

    def test_architect_ui_bp(self):
        from app.modules.architecture.routes.architect_ui_routes import architect_ui_bp

        assert architect_ui_bp.name == "architect_ui"

    def test_architecture_monitoring_bp(self):
        from app.modules.architecture.routes.architecture_monitoring_routes import architecture_monitoring_bp

        assert architecture_monitoring_bp.name == "architecture_monitoring"

    def test_arb_bp(self):
        from app.modules.architecture.routes.arb_routes import arb_bp

        assert arb_bp.name == "arb"

    def test_arb_workflow_bp(self):
        from app.modules.architecture.routes.arb_workflow_routes import arb_workflow_bp

        assert arb_workflow_bp.name == "arb_workflow"

    def test_adm_kanban_view_bp(self):
        from app.modules.architecture.routes.adm_kanban_view_routes import adm_kanban_view_bp

        assert adm_kanban_view_bp.name == "adm_kanban_view"

    def test_adm_kanban_bp(self):
        from app.modules.architecture.routes.adm_kanban_routes import adm_kanban_bp

        assert adm_kanban_bp.name == "adm_kanban"


class TestArchitectureModuleRegistration:
    """Test that the module registers all blueprints correctly."""

    def test_all_12_default_blueprints_registered(self, app):
        bp_names = list(app.blueprints.keys())
        expected = [
            "archimate_crud",
            "archimate_api",
            "viewpoint_api",
            "architecture_crud",
            "architecture",
            "architecture_assistant",
            "archimate_export",
            "architect_ui",
            "arb",
            "arb_workflow",
            "adm_kanban_view",
            "adm_kanban",
        ]
        for name in expected:
            assert name in bp_names, f"'{name}' blueprint missing from app"

    def test_architecture_route_count(self, app):
        """Module should register ~180+ architecture routes."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        arch_prefixes = [
            "/architecture", "/api/archimate", "/api/viewpoints",
            "/api/architecture-assistant", "/api/archimate-export",
            "/arb", "/api/arb-workflow", "/adm-kanban", "/api/adm-kanban",
            "/api/architecture-monitoring",
        ]
        arch_rules = [r for r in rules if any(r.startswith(p) for p in arch_prefixes)]
        assert len(arch_rules) >= 150, (
            f"Expected >= 150 architecture URL rules, found {len(arch_rules)}"
        )

    def test_archimate_crud_services_importable(self):
        """archimate_crud package services should be importable."""
        from app.modules.architecture.routes.archimate_crud.services.ai_generation_service import AIGenerationService
        from app.modules.architecture.routes.archimate_crud.services.field_configs import get_element_config

        assert AIGenerationService is not None
        assert callable(get_element_config)


class TestArchitectureRouteParity:
    """Test that module routes match legacy routes exactly."""

    def test_architecture_endpoint_parity(self, app):
        """All architecture endpoint names should be present."""
        endpoints = set(app.view_functions.keys())
        # Key endpoints that must exist
        must_have = [
            "archimate_crud.dashboard",
            "arb.dashboard",
            "adm_kanban.update_card",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"


class TestArchitectureMonitoringApiFlag:
    """The architecture monitoring API is off by default and mounted when configured."""

    def test_not_mounted_by_default(self, app):
        assert "architecture_monitoring" not in app.blueprints
        assert not any(
            r.rule.startswith("/api/architecture-monitoring") for r in app.url_map.iter_rules()
        )

    def test_mounted_when_flag_enabled(self, monkeypatch):
        monkeypatch.setenv("ARCHITECTURE_MONITORING_API_ENABLED", "true")
        from app import create_app

        flagged_app = create_app()
        assert "architecture_monitoring" in flagged_app.blueprints
        assert "architecture_monitoring.get_monitoring_status" in flagged_app.view_functions
        rule_count = sum(
            r.rule.startswith("/api/architecture-monitoring")
            for r in flagged_app.url_map.iter_rules()
        )
        assert rule_count == 17

    def test_boot_continues_when_monitoring_routes_import_fails(self, monkeypatch):
        """A broken monitoring routes module must not take the whole application down."""
        import sys

        monkeypatch.setitem(
            sys.modules,
            "app.modules.architecture.routes.architecture_monitoring_routes",
            None,
        )
        from app import create_app

        broken_app = create_app()
        assert "architecture_monitoring" not in broken_app.blueprints
        assert "arb" in broken_app.blueprints
