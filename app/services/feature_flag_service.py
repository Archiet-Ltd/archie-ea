"""
Feature Flag Service for AI feature gating.
"""

import logging
from typing import Optional

from flask import current_app, jsonify
from flask_login import current_user

from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class LLMConfigurationError(Exception):
    def __init__(self, feature: str, message: str = None):
        self.feature = feature
        self.message = message or f"AI feature '{feature}' requires LLM configuration"
        super().__init__(self.message)


class FeatureFlagService:
    FEATURE_CHAT = "chat"
    FEATURE_ANALYSIS = "analysis"
    FEATURE_IMPORT = "import"
    FEATURE_IMPACT = "impact"
    FEATURE_DEDUPLICATION = "deduplication"
    FEATURE_SUGGESTIONS = "suggestions"
    FEATURE_ALL = "all"

    @staticmethod
    def is_ai_enabled(feature: str = "all") -> bool:
        if feature == FeatureFlagService.FEATURE_ALL:
            return all(
                [
                    FeatureFlagService.is_ai_enabled(FeatureFlagService.FEATURE_CHAT),
                ]
            )

        env_key = f"AI_{feature.upper()}_ENABLED"
        env_value = current_app.config.get(env_key)

        if env_value is not None:
            return env_value

        return FeatureFlagService._is_llm_configured()

    @staticmethod
    def _is_llm_configured() -> bool:
        try:
            provider, model = LLMService._get_configured_provider()
            return provider is not None and model is not None
        except ValueError as exc:
            from app.modules.ai_chat.services.llm_service_impl import AnswersPaused

            # Every provider in the platform's order is at its ceiling: the feature is configured and
            # every answer is the plain pause message, never a refusal the person cannot read.
            return isinstance(exc, AnswersPaused)

    @staticmethod
    def get_configured_provider_info() -> Optional[dict]:
        try:
            provider, model = LLMService._get_configured_provider()
            return {"provider": provider, "model": model, "configured": True, "paused": False}
        except ValueError as exc:
            from app.modules.ai_chat.services.llm_service_impl import AnswersPaused

            # Paused: the platform has providers, every one at its ceiling this month.
            return {"provider": None, "model": None, "configured": False, "paused": isinstance(exc, AnswersPaused)}

    @staticmethod
    def require_ai_for_route(
        feature: str, endpoint_name: str = None
    ) -> Optional[tuple]:
        if not FeatureFlagService.is_ai_enabled(feature):
            logger.warning(
                f"AI feature '{feature}' requested but not configured. Endpoint: {endpoint_name}"
            )
            from app.modules.ai_chat.services.failure_messages import (
                ai_unavailable_message,
            )

            is_admin = bool(getattr(current_user, "is_org_admin", False))
            return jsonify(
                {
                    "error": "service_unavailable",
                    "message": ai_unavailable_message(feature, is_admin),
                    "feature": feature,
                }
            ), 503
        return None

    @staticmethod
    def require_ai_raises(feature: str) -> None:
        if not FeatureFlagService.is_ai_enabled(feature):
            raise LLMConfigurationError(feature)

    @staticmethod
    def get_feature_status() -> dict:
        llm_info = FeatureFlagService.get_configured_provider_info()
        return {
            "llm_configured": llm_info["configured"],
            "provider": llm_info.get("provider"),
            "model": llm_info.get("model"),
            "features": {
                "chat": FeatureFlagService.is_ai_enabled(
                    FeatureFlagService.FEATURE_CHAT
                ),
                "analysis": FeatureFlagService.is_ai_enabled(
                    FeatureFlagService.FEATURE_ANALYSIS
                ),
                "import": FeatureFlagService.is_ai_enabled(
                    FeatureFlagService.FEATURE_IMPORT
                ),
                "impact": FeatureFlagService.is_ai_enabled(
                    FeatureFlagService.FEATURE_IMPACT
                ),
            },
        }
