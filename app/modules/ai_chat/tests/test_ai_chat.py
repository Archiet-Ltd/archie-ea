"""
Tests for the ai_chat module Phase 4 decomposition.

Verifies:
- All 4 blueprints importable from app.modules.ai_chat
- unified_ai_chat monolith decomposed into 8 files sharing one blueprint
- Blueprint names and prefixes match legacy
- register() wires all blueprints to a Flask app
- Route parity: 67 unified_ai_chat routes + 14 data_interaction + 7 assistance + 9 gap_detection
"""
import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app


class TestAiChatModuleImports:
    """Test that all AI chat blueprints are importable from module paths."""

    def test_register_function_importable(self):
        from app.modules.ai_chat import register

        assert callable(register)

    def test_unified_ai_chat_blueprint(self):
        from app.modules.ai_chat.routes import unified_ai_chat_bp

        assert unified_ai_chat_bp.name == "unified_ai_chat"

    def test_ai_data_interaction_blueprint(self):
        from app.modules.ai_chat.routes.data_interaction_routes import ai_data_interaction

        assert ai_data_interaction.name == "ai_data_interaction"

    def test_ai_assistance_blueprint(self):
        from app.modules.ai_chat.routes.ai_assistance_routes import ai_assistance_bp

        assert ai_assistance_bp.name == "ai_assistance"

    def test_ai_gap_detection_blueprint(self):
        from app.modules.ai_chat.routes.ai_gap_detection_routes import ai_gap_detection_bp

        assert ai_gap_detection_bp.name == "ai_gap_detection"


class TestMonolithDecomposition:
    """Test that the 4,247-line monolith was correctly decomposed into 8 sub-modules."""

    def test_chat_views_importable(self):
        from app.modules.ai_chat.routes import chat_views

        assert hasattr(chat_views, "get_chat_service")
        assert hasattr(chat_views, "index")
        assert hasattr(chat_views, "document_upload_view")
        assert hasattr(chat_views, "business_output_view")
        assert hasattr(chat_views, "entity_matching_view")

    def test_chat_core_importable(self):
        from app.modules.ai_chat.routes import chat_core

        assert hasattr(chat_core, "send_message")
        assert hasattr(chat_core, "get_available_models")
        assert hasattr(chat_core, "get_available_domains")
        assert hasattr(chat_core, "get_available_personas")
        assert hasattr(chat_core, "get_prompt_templates")
        assert hasattr(chat_core, "get_chat_history")
        assert hasattr(chat_core, "clear_chat_history")
        assert hasattr(chat_core, "save_chat_session")
        assert hasattr(chat_core, "get_saved_sessions")
        assert hasattr(chat_core, "load_chat_session")
        assert hasattr(chat_core, "get_domain_context")

    def test_document_routes_importable(self):
        from app.modules.ai_chat.routes import document_routes

        assert hasattr(document_routes, "upload_document")
        assert hasattr(document_routes, "get_document_history")
        assert hasattr(document_routes, "get_document_details")
        assert hasattr(document_routes, "delete_document")
        assert hasattr(document_routes, "re_analyze_document")
        assert hasattr(document_routes, "create_elements")
        assert hasattr(document_routes, "record_feedback")
        assert hasattr(document_routes, "compare_document_versions")

    def test_workflow_routes_importable(self):
        from app.modules.ai_chat.routes import workflow_routes

        assert hasattr(workflow_routes, "generate_archimate")
        assert hasattr(workflow_routes, "apply_archimate")
        assert hasattr(workflow_routes, "map_apqc")
        assert hasattr(workflow_routes, "apply_apqc")
        assert hasattr(workflow_routes, "bulk_process")
        assert hasattr(workflow_routes, "suggest_entities")

    def test_chat_workflows_importable(self):
        from app.modules.ai_chat.routes import chat_workflows

        assert hasattr(chat_workflows, "generate_archimate_for_application")
        assert hasattr(chat_workflows, "map_apqc_for_application")
        assert hasattr(chat_workflows, "save_chat_insights_to_application")
        assert hasattr(chat_workflows, "bulk_process_applications")
        assert hasattr(chat_workflows, "actionable_gap_analysis")
        assert hasattr(chat_workflows, "discover_vendors_for_capability")

    def test_entity_routes_importable(self):
        from app.modules.ai_chat.routes import entity_routes

        assert hasattr(entity_routes, "transform_output")
        assert hasattr(entity_routes, "match_entities_ui")
        assert hasattr(entity_routes, "get_entity_types")
        assert hasattr(entity_routes, "generate_architecture")
        assert hasattr(entity_routes, "validate_architecture")
        assert hasattr(entity_routes, "match_entities_api")

    def test_analytics_routes_importable(self):
        from app.modules.ai_chat.routes import analytics_routes

        assert hasattr(analytics_routes, "get_usage_analytics")
        assert hasattr(analytics_routes, "get_domain_analytics")
        assert hasattr(analytics_routes, "get_quality_metrics")
        assert hasattr(analytics_routes, "natural_language_query")
        assert hasattr(analytics_routes, "get_recommendations")

    def test_legacy_compat_importable(self):
        from app.modules.ai_chat.routes import legacy_compat

        assert hasattr(legacy_compat, "llm_health")
        assert hasattr(legacy_compat, "legacy_chat_index")
        assert hasattr(legacy_compat, "legacy_send_message")


class TestAiChatModuleRegistration:
    """Test that the module registers all blueprints correctly."""

    def test_all_4_blueprints_registered(self, app):
        bp_names = list(app.blueprints.keys())
        expected = [
            "unified_ai_chat",
            "ai_data_interaction",
            "ai_assistance",
            "ai_gap_detection",
        ]
        for name in expected:
            assert name in bp_names, f"'{name}' blueprint missing from app"

    def test_unified_ai_chat_route_count(self, app):
        """Decomposed unified_ai_chat should register 60+ routes."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        ai_chat_rules = [r for r in rules if r.startswith("/ai-chat")]
        # unified_ai_chat routes at /ai-chat/* + ai_data_interaction at /ai-chat/data/*
        assert len(ai_chat_rules) >= 60, (
            f"Expected >= 60 /ai-chat URL rules, found {len(ai_chat_rules)}"
        )

    def test_ai_chat_total_route_count(self, app):
        """All AI chat routes combined should be 90+."""
        rules = [r.rule for r in app.url_map.iter_rules()]
        prefixes = [
            "/ai-chat",
            "/api/ai-assistance",
            "/api/ai-gap-detection",
        ]
        ai_rules = [r for r in rules if any(r.startswith(p) for p in prefixes)]
        assert len(ai_rules) >= 90, (
            f"Expected >= 90 total AI chat URL rules, found {len(ai_rules)}"
        )


class TestAiChatEndpointParity:
    """Test that key endpoint names are preserved for url_for() compatibility."""

    def test_unified_ai_chat_key_endpoints(self, app):
        """Key unified_ai_chat endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_ai_chat.index",
            "unified_ai_chat.send_message",
            "unified_ai_chat.get_available_models",
            "unified_ai_chat.get_available_domains",
            "unified_ai_chat.upload_document",
            "unified_ai_chat.generate_archimate",
            "unified_ai_chat.transform_output",
            "unified_ai_chat.get_usage_analytics",
            "unified_ai_chat.natural_language_query",
            "unified_ai_chat.get_recommendations",
            "unified_ai_chat.llm_health",
            "unified_ai_chat.get_chat_history",
            "unified_ai_chat.save_chat_session",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_other_blueprint_key_endpoints(self, app):
        """Key endpoints from other AI chat blueprints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            # Creating a capability through the assistant is served by the
            # unified chat blueprint; ai_data_interaction keeps the mapping.
            "unified_ai_chat.create_capability",
            "ai_data_interaction.create_capability_mapping",
            "ai_data_interaction.create_application",
            "ai_data_interaction.bulk_create_applications",
            "ai_assistance.suggest_field_value",
            "ai_assistance.validate_field",
            "ai_assistance.scan_content",
            "ai_gap_detection.natural_language_query",
            "ai_gap_detection.get_gap_summary",
            "ai_gap_detection.health_check",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"

    def test_chat_workflow_endpoints(self, app):
        """Chat-driven workflow endpoints must exist."""
        endpoints = set(app.view_functions.keys())
        must_have = [
            "unified_ai_chat.generate_archimate_for_application",
            "unified_ai_chat.map_apqc_for_application",
            "unified_ai_chat.save_chat_insights_to_application",
            "unified_ai_chat.bulk_process_applications",
            "unified_ai_chat.actionable_gap_analysis",
            "unified_ai_chat.discover_vendors_for_capability",
        ]
        for ep in must_have:
            assert ep in endpoints, f"Missing endpoint: {ep}"
