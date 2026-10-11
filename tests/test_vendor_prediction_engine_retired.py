"""The vendor prediction engine is retired: it must no longer exist or be importable."""

import importlib

import pytest


def test_ai_recommendation_engine_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(
            "app.modules.ai_chat.services.ai_recommendation_engine"
        )


def test_ai_recommendation_engine_class_is_not_exported():
    import app.modules.ai_chat.services.ai_analysis_service as ai_analysis_service
    import app.modules.ai_chat.services as ai_chat_services

    assert not hasattr(ai_analysis_service, "AIRecommendationEngine")
    assert not hasattr(ai_chat_services, "AIRecommendationEngine")


def test_ai_chat_services_package_still_imports_cleanly():
    importlib.import_module("app.modules.ai_chat.services")
