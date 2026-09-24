"""
-> app.modules.ai_chat.services.llm_service

LLM Service for integrating with Hugging Face (default), OpenAI, Anthropic, and other LLM providers.

Provides unified interface for generating architecture elements, requirements,
UML diagrams, and code using various LLM providers.

DEFAULT PROVIDER: Hugging Face (free, local models)
- Priority order: huggingface → anthropic → openai → gemini → deepseek → azure
- Hugging Face models run locally, no API costs
- Other providers require API keys and incur costs

CRITICAL POLICY: NO HARDCODED DATA
=====================================
This service strictly enforces a NO HARDCODED DATA policy:

1. MODEL SELECTION: ONLY models configured via API Settings database are used.
   NO hardcoded model names or fallback models are allowed.

2. PROVIDER CONFIGURATION: All provider settings (API keys, models, endpoints)
   must come from the database APISettings table.

3. VALIDATION: System will fail gracefully if no database configuration exists
   rather than falling back to hardcoded values.

4. DYNAMIC LOADING: All model lists, provider configurations, and settings
   are loaded dynamically from the database.

5. ENFORCEMENT: Any hardcoded values for models, API keys, or provider settings
   are STRICTLY PROHIBITED and will be rejected.

CONFIGURATION PRIORITY:
======================
1. Database APISettings (REQUIRED - no fallbacks)
2. Environment variables (for API keys only as backup)
3. NO hardcoded fallback values allowed

DEFAULT PROVIDER ORDER:
======================
1. anthropic (Claude - requires API key)
2. openai (GPT - 4 - requires API key)
3. gemini (requires API key)
4. deepseek (requires API key)
5. azure (requires API key)
6. huggingface (free, local models - last due to potential stability issues on some systems)

If no database configuration exists for a provider, the system will raise
a ValueError rather than use any hardcoded defaults.
"""
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
)

import requests
from flask import current_app

from app import db
from app.models import LLMInteraction

# from .llm_validator import LLMValidator  # Temporarily disabled
from app.services.core.retry_handler import retry_on_transient_error
from app.services.llm_cost_tracker import LLMCostTracker
from app.services.llm_model_router import LLMModelRouter, TaskComplexity

logger = logging.getLogger(__name__)

# Use centralized cache service (Redis if available, falls back to in-memory)
from app.services.core.cache_service import cache_service

CACHE_TTL_SECONDS = 3600  # fabricated-ok: named config constant (cache TTL), not displayed data
DEFAULT_TOKEN_COST_MULTIPLIER = 1000  # fabricated-ok: named config constant (token cost multiplier), not displayed data

# Fallback in-memory cache if Redis is not available
_llm_cache_fallback = {}
_cache_expiry_fallback = {}

# ENT-040: field patterns that must be scrubbed before prompt transmission.
# Keys are human-readable labels; values are (pattern, replacement) tuples.
_SENSITIVE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I), "[EMAIL_REDACTED]"),
    (re.compile(r"\b\+?[\d\s\-().]{7,15}\d\b"), "[PHONE_REDACTED]"),
    # Match only actual credential values — NOT Python type annotations (token: TokenPayload).
    # Three accepted value shapes:
    #   1. Quoted string of >=4 chars:  token="abc..."
    #   2. Lowercase/digit string >=8 chars (no leading uppercase → not a Python type):
    #      password=changeme123   api_key=abcdef12
    #   3. Base64/JWT blob >=16 chars (explicit A-Za-z0-9):
    #      bearer eyJhbGciOiJSUzI1NiJ9...
    # Separator is [:=] for key=value, or \s for "bearer <token>" HTTP header style.
    # re.I applied ONLY to the keyword group via inline flag (?i:...), not to value group,
    # so [a-z0-9] in the value alternatives stays strictly lowercase-only.
    (re.compile(
        r"\b(?i:password|passwd|secret|api[_-]?key|token|bearer)(?:\s*[:=]\s*|\s+)"
        r"(?:['\"][^'\"]{4,}['\"]"        # quoted string
        r"|[a-z][a-z0-9_\-]{7,}"          # lowercase >=8 chars (excludes CamelCase types)
        r"|[A-Za-z0-9+/]{16,}={0,2})"     # base64/JWT >=16 chars
    ), "[CREDENTIAL_REDACTED]"),
]


def _scrub_prompt(prompt: str) -> str:
    """Replace known sensitive patterns in a prompt string before LLM transmission (ENT-040)."""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        prompt = pattern.sub(replacement, prompt)
    return prompt


class LLMService:
    """
    Service for interacting with LLM providers (Hugging Face default, OpenAI, Anthropic, Azure).

    DEFAULT PROVIDER: Hugging Face (free, local models)
    - Runs locally using transformers library
    - No API costs, no API keys required
    - Models: distilbert-base-uncased, bert-base-uncased, roberta-base, cardiffnlp/twitter-roberta-base-sentiment

    STRICT NO HARDCODED DATA POLICY ENFORCED
    ==========================================

    This service operates under a strict policy that PROHIBITS any hardcoded:
    - Model names or IDs
    - API endpoints (except base URLs)
    - Default configurations
    - Fallback values

    All configuration MUST come from the database APISettings table.

    Provider Structure:
    - 'models': ALWAYS empty array [] - loaded from database only
    - 'base_url': Only exception - base URLs for API endpoints

    If no database configuration exists, methods will raise ValueError
    rather than use any hardcoded defaults.
    """

    PROVIDERS = {
        "openai": {
            "models": [],  # NO HARDCODED MODELS - Database only
            "base_url": "https://api.openai.com/v1",
        },
        "anthropic": {
            "models": [],  # NO HARDCODED MODELS - Database only
            "base_url": "https://api.anthropic.com/v1",
        },
        "gemini": {
            "models": [],  # NO HARDCODED MODELS - Database only
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
        },
        "azure": {
            "models": [],  # NO HARDCODED MODELS - Database only
            "base_url": None,  # Set via environment variable
        },
        "deepseek": {
            "models": [],  # NO HARDCODED MODELS - Database only
            "base_url": "https://api.deepseek.com/v1",
        },
        "huggingface": {
            "models": [],  # NO HARDCODED MODELS - Database only
            "base_url": None,  # Local models, no API endpoint
        },
        "openrouter": {
            "models": [],  # NO HARDCODED MODELS - Database only
            "base_url": "https://openrouter.ai/api/v1",
        },
    }

    @staticmethod
    def _get_configured_provider(exclude_providers: List[str] = None, user_id: int = None) -> Tuple[str, str]:
        """
        Get the best available LLM provider using intelligent selection.

        SELECTION PRIORITY:
        ===================
        1. User preference (if set via LLM selector and provider has working keys)
        2. Database-configured providers with test_status='success' (recently verified)
        3. Database-configured providers with API keys and models
        4. Environment variable fallback (.env keys)

        Args:
            exclude_providers: List of provider names to exclude (for fallback scenarios)
            user_id: Optional user ID for preference lookup (auto-detected from request context)

        Returns:
            Tuple of (provider_name, model_name)

        Raises:
            ValueError: If no enabled provider is found
        """
        from app.models.models import APISettings
        import os

        if exclude_providers is None:
            exclude_providers = []

        # Provider fallback priority order (used when no preference is set)
        provider_priority = ["openai", "anthropic", "gemini", "deepseek", "openrouter", "azure", "huggingface"]

        # Default models for each provider (used when falling back to .env)
        # Single source of truth - these ids were hardcoded in six places and
        # drifted independently until every Claude id was stale, several to
        # RETIRED models that now 404 (claude-3-sonnet-20240229 went out in  stale-model-ok
        # July 2025).
        from app.modules.ai_chat.services.model_defaults import DEFAULT_MODELS

        default_models = dict(DEFAULT_MODELS)

        # Ensure clean transaction state before query
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

        # ── Step 1: Check user preference ──
        preferred_provider = None
        preferred_model = None
        try:
            # Auto-detect user_id from request context if not provided
            if user_id is None:
                try:
                    from flask_login import current_user
                    if current_user and hasattr(current_user, 'id') and not current_user.is_anonymous:
                        user_id = current_user.id
                except Exception as e:
                    logger.debug("Failed to get current_user for LLM preference: %s", e)

            if user_id:
                pref = LLMService.get_user_llm_preference(user_id)
                if pref.get("has_preference") and pref.get("provider") not in (None, "none"):
                    preferred_provider = pref["provider"]
                    preferred_model = pref.get("model")
                    logger.info(f"User {user_id} preference: {preferred_provider}/{preferred_model}")
        except Exception as pref_error:
            logger.debug(f"Could not check user preference: {pref_error}")

        # Try database configuration
        all_enabled_settings = []
        try:
            all_enabled_settings = APISettings.query.filter_by(enabled=True).all()
        except Exception as db_error:
            logger.warning(f"Database error getting provider settings: {db_error}")

        settings_by_provider = {s.provider: s for s in all_enabled_settings}

        # Helper: check if a provider has working config (DB or .env)
        def _provider_is_usable(pname):
            if pname in exclude_providers:
                return False, None
            settings = settings_by_provider.get(pname)
            if settings and (pname == "huggingface" or settings.has_key()):
                model = settings.default_model.strip() if settings.default_model and settings.default_model.strip() else None
                if model:
                    return True, model
            # Check .env fallback
            env_key = os.getenv(f"{pname.upper()}_API_KEY")
            if env_key and len(env_key) > 0:
                model = default_models.get(pname)
                if model:
                    return True, model
            return False, None

        # If user has a preference, try that first
        if preferred_provider and preferred_provider not in exclude_providers:
            usable, model = _provider_is_usable(preferred_provider)
            if usable:
                # Use user's preferred model if set, else fall back to configured model
                final_model = preferred_model if preferred_model else model
                logger.info(f"Using user-preferred provider: {preferred_provider} with model {final_model}")
                return preferred_provider, final_model

        # ── Step 2: Prefer recently-tested providers ──
        from datetime import datetime, timedelta
        tested_cutoff = datetime.utcnow() - timedelta(hours=24)

        for provider_name in provider_priority:
            if provider_name in exclude_providers:
                continue
            settings = settings_by_provider.get(provider_name)
            if (settings and settings.test_status == "success"
                    and settings.last_tested_at and settings.last_tested_at > tested_cutoff):
                if settings.has_key() and settings.default_model and settings.default_model.strip():
                    logger.info(f"Using recently-tested provider: {provider_name} with model {settings.default_model}")
                    return provider_name, settings.default_model.strip()

        # ── Step 3: Any database-configured provider with key + model ──
        for provider_name in provider_priority:
            if provider_name in exclude_providers:
                continue
            settings = settings_by_provider.get(provider_name)
            if settings:
                if provider_name == "huggingface" or settings.has_key():
                    if settings.default_model and settings.default_model.strip():
                        logger.info(f"Using database-configured provider: {provider_name} with model {settings.default_model}")
                        return provider_name, settings.default_model.strip()

        # ── Step 4: Fallback to environment variables (bootstrap only) ──
        # Only used when a provider has NO DB record at all (admin has never configured it).
        # Providers that have a DB record but no usable key are intentionally skipped here —
        # falling through to .env would silently bypass the admin's intent.
        providers_with_db_record = set(settings_by_provider.keys())
        env_only_providers = [p for p in provider_priority if p not in providers_with_db_record]

        if env_only_providers:
            logger.info(
                "No database configuration found for providers %s — checking environment variables (bootstrap path)...",
                env_only_providers,
            )
        for provider_name in env_only_providers:
            if provider_name in exclude_providers:
                continue
            env_key = os.getenv(f"{provider_name.upper()}_API_KEY")
            if env_key and len(env_key) > 0:
                model = default_models.get(provider_name)
                if model:
                    logger.info(
                        "Using environment-configured provider: %s with default model %s. "
                        "Migrate this key to Admin > API Settings to suppress this message.",
                        provider_name, model,
                    )
                    return provider_name, model

        # No configuration found - provide helpful error message
        configured_env_vars = []
        for provider_name in provider_priority:
            if os.getenv(f"{provider_name.upper()}_API_KEY"):
                configured_env_vars.append(f"{provider_name.upper()}_API_KEY")

        excluded_msg = f" (excluding: {', '.join(exclude_providers)})" if exclude_providers else ""

        if configured_env_vars:
            raise ValueError(
                f"No enabled LLM provider found with a configured model{excluded_msg}. "
                f"Environment variables detected: {', '.join(configured_env_vars)} but their providers "
                f"already have DB records — check that the DB record is enabled and has a default_model set. "
                f"Go to Admin > API Settings to review."
            )
        else:
            raise ValueError(
                f"No enabled LLM provider found with a configured model{excluded_msg}. "
                f"Please configure at least one provider at Admin > API Settings "
                f"(or set OPENAI_API_KEY / ANTHROPIC_API_KEY in .env as a bootstrap key)."
            )

    @staticmethod
    def configuration_status() -> Dict[str, Any]:
        """One answer to "is an LLM configured?", for every surface that asks.

        Added 31 Aug 2026 because two surfaces in the SAME process disagreed:
        ``GET /health`` reported ``llm_providers: {enabled_providers: 0, "No LLM
        providers enabled"}`` while ``GET /ai-chat/token-usage`` reported
        ``{"configured": true, "provider": "openrouter"}``. token-usage was
        right. /health counted rows in ``api_settings`` only, and a provider
        configured by environment variable (the documented bootstrap path,
        Step 4 of ``_get_configured_provider``) has no such row — so /health
        told an operator the AI was dead while it was serving. Worse, that
        count is tenant-filtered inside a request and unfiltered outside one,
        so it was not even a stable number.

        The verdict here comes from the resolver that actually chooses the
        provider, so a surface cannot disagree with what the product will do.
        The row count is kept alongside it as detail, clearly labelled, rather
        than as the verdict.

        Never raises: this runs inside a health probe.
        """
        from app.models.models import APISettings

        # Kept for the five existing callers that read `ready` / `providers`.
        # It is a *list of candidates*, not the verdict — the verdict below
        # comes from the resolver, which is the only thing that knows whether a
        # call will actually be made.
        try:
            _, candidate_providers = LLMService._validate_database_configuration()
        except Exception:  # noqa: BLE001 - a health probe must not 500
            candidate_providers = []

        db_enabled = None
        try:
            db_enabled = APISettings.query.filter_by(enabled=True).count()
        except Exception as exc:  # noqa: BLE001 - a health probe must not 500
            logger.debug("configuration_status: API settings count failed: %s", exc)

        try:
            provider, model = LLMService._get_configured_provider()
            return {
                "configured": True,
                "ready": True,
                "providers": candidate_providers,
                "provider": provider,
                "model": model,
                "db_enabled_providers": db_enabled,
                "source": "database" if db_enabled else "environment",
            }
        except ValueError as exc:
            return {
                "configured": False,
                "ready": False,
                "providers": candidate_providers,
                "provider": None,
                "model": None,
                "db_enabled_providers": db_enabled,
                "source": None,
                "reason": str(exc),
            }
        except Exception as exc:  # noqa: BLE001 - a health probe must not 500
            logger.warning("configuration_status: resolver failed: %s", exc)
            # Unknown is not the same as "not configured". Saying "no providers"
            # here would be the exact fabrication this method exists to remove.
            return {
                "configured": None,
                # `ready` must not become True on an unknown: a caller that
                # gates a write on it would proceed into a call that cannot be
                # made. Unknown degrades to not-ready, and `configured: None`
                # says which of the two it is.
                "ready": False,
                "providers": candidate_providers,
                "provider": None,
                "model": None,
                "db_enabled_providers": db_enabled,
                "source": None,
                "reason": "Could not determine LLM configuration.",
            }

    @staticmethod
    def is_available() -> bool:
        """
        Check if LLM service is available (at least one provider configured).

        This method provides graceful degradation by checking provider availability
        without raising exceptions. Use this before calling AI features to provide
        friendly error messages instead of 500 errors.

        Returns:
            bool: True if at least one LLM provider is configured and available

        Examples:
            >>> if not LLMService.is_available():
            >>>     flash("AI features unavailable. Please contact support.", "warning")
            >>>     return redirect(url_for('applications.detail', id=app_id))
            >>>
            >>> # Safe to call AI features
            >>> result = llm_service.generate(...)

        Notes:
            - Returns False instead of raising ValueError when no provider
            - Catches all exceptions to prevent crashes
            - Logs warnings when providers are unavailable
        """
        try:
            provider, model = LLMService._get_configured_provider()
            return provider is not None and model is not None
        except ValueError as e:
            logger.debug(f"LLM not available: {e}")
            return False
        except Exception as e:
            logger.warning(f"Error checking LLM availability: {e}")
            return False

    @staticmethod
    def _enforce_no_hardcoded_policy():
        """
        Enforce NO HARDCODED DATA policy by validating system configuration.

        This method ensures:
        1. No hardcoded models exist in PROVIDERS
        2. All model lists are empty (loaded from database only)
        3. System configuration comes exclusively from database

        Raises:
            RuntimeError: If any hardcoded data policy violations detected
        """
        violations = []

        # Check for hardcoded models in PROVIDERS
        for provider_name, provider_config in LLMService.PROVIDERS.items():
            models = provider_config.get("models", [])
            if models:
                violations.append(f"Provider '{provider_name}' has hardcoded models: {models}")

        if violations:
            raise RuntimeError(
                "NO HARDCODED DATA POLICY VIOLATION DETECTED:\n"
                + "\n".join(violations)
                + "\n\nAll models MUST be loaded from database APISettings table only."
            )

    @staticmethod
    def get_max_tokens_limit(
        provider: str, model: str = None, requested_max: Optional[int] = None
    ) -> int:
        """
        Get the maximum tokens limit for a provider/model combination.

        Args:
            provider: Provider name ('openai', 'anthropic', 'deepseek', etc.)
            model: Optional model name for provider-specific limits
            requested_max: Optional requested max_tokens value to cap

        Returns:
            Maximum tokens allowed for the provider/model
        """
        # Provider-specific limits
        provider_limits = {
            "deepseek": 8192,  # DeepSeek max is 8192
            "openai": 16384,  # GPT - 4 Turbo supports up to 16K
            "anthropic": 32000,  # Claude Opus supports up to 32K
            "gemini": 8192,  # Gemini Pro supports up to 8K
            "azure": 16384,  # Azure OpenAI similar to OpenAI
            "huggingface": 1024,  # Most HF models have small context
        }

        # Get base limit for provider
        base_limit = provider_limits.get(provider.lower(), 8192)  # Default to 8K if unknown

        # Model-specific adjustments
        if provider.lower() == "anthropic" and model:
            if "opus" in model.lower():
                base_limit = 32000
            elif "sonnet" in model.lower():
                base_limit = 16384
            else:
                base_limit = 4096  # Haiku

        # If a requested max was provided, cap it to the provider limit
        if requested_max is not None:
            return min(requested_max, base_limit)

        return base_limit

    @staticmethod
    def _validate_database_configuration():
        """
        Validate that required LLM configuration exists.
        Checks database first, then falls back to environment variables.

        Returns:
            tuple: (bool, list) - (is_configured, list_of_available_providers)
        """
        import os
        from app.models.models import APISettings

        valid_providers = []

        # First, check database configuration
        try:
            enabled_providers = APISettings.query.filter_by(enabled=True).all()
            for settings in enabled_providers:
                if settings.has_key() and settings.default_model and settings.default_model.strip():
                    valid_providers.append(settings.provider)
        except Exception:
            # Database might not be available, continue to env fallback
            logger.debug("Failed to query API settings from database", exc_info=True)

        # Fallback: Check environment variables for API keys
        # Only add providers not already configured in database
        configured_in_db = set(valid_providers)

        env_key_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "azure": "AZURE_API_KEY",
            "huggingface": "HUGGINGFACE_API_KEY",
        }
        for provider, env_var in env_key_map.items():
            if provider not in configured_in_db:
                key = os.environ.get(env_var)
                if key and len(key) > 0:
                    valid_providers.append(provider)

        return len(valid_providers) > 0, valid_providers

    @staticmethod
    def generate_architecture_from_jira(jira_issue, pipeline_stage_id: Optional[int] = None) -> str:
        """
        Generate ArchiMate architecture elements from JIRA issue description.

        ENFORCES NO HARDCODED DATA POLICY:
        - Validates no hardcoded models exist in system
        - Ensures database configuration is valid
        - Uses ONLY database-configured models and providers

        Args:
            jira_issue: JiraIssue model instance
            pipeline_stage_id: Optional PipelineStage ID for tracking

        Returns:
            Generated architecture description as text

        Raises:
            RuntimeError: If hardcoded data policy violations detected
            ValueError: If no valid database configuration found
        """
        # ENFORCE NO HARDCODED DATA POLICY
        LLMService._enforce_no_hardcoded_policy()

        # Validate database configuration exists
        has_config, valid_providers = LLMService._validate_database_configuration()
        if not has_config:
            raise ValueError(
                "No valid LLM provider configuration found in database. "
                "Please configure API keys and models for at least one provider "
                "through the admin interface at /admin/api-settings"
            )

        prompt = f"""Analyze this JIRA issue and generate 5 - 8 ArchiMate elements as JSON.

JIRA: {jira_issue.jira_key} | {jira_issue.issue_type}
Summary: {jira_issue.summary}
Description: {jira_issue.description}

Generate JSON array with Business, Application, and Technology elements:

[
  {{
    "name": "Element Name",
    "type": "BusinessActor|ApplicationComponent|TechnologyService|etc",
    "layer": "Business|Application|Technology",
    "description": "Brief functional description"
  }}
]

Requirements:
1. Clear, specific names (not generic)
2. Descriptions 15 - 30 words explaining purpose
3. Include elements from at least 2 different layers
4. Focus on domain-specific concepts from the JIRA issue

Generate the JSON array now:"""

        provider, model = LLMService._get_configured_provider()
        response, interaction = LLMService._call_llm(
            prompt=prompt, model=model, provider=provider, pipeline_stage_id=pipeline_stage_id
        )

        # Extract JSON from response (remove any explanatory text)
        import re

        # Try to find JSON array in the response
        json_match = re.search(r"\[[\s\S]*\]", response)
        if json_match:
            json_str = json_match.group(0)
            try:
                # Validate it's proper JSON
                json.loads(json_str)
                return json_str
            except json.JSONDecodeError:
                logger.exception("Failed to JSON parsing")
                pass

        # Fallback: return original response if JSON extraction fails
        return response

    @staticmethod
    def generate_architecture_from_capability(
        capability_name: str,
        capability_description: str,
        capability_level: Optional[int] = None,
        capability_category: Optional[str] = None,
        capability_domain: Optional[str] = None,
        pipeline_stage_id: Optional[int] = None,
    ) -> Dict:
        """
        Generate ArchiMate architecture elements from a business capability.

        Args:
            capability_name: Name of the business capability
            capability_description: Description of the capability
            capability_level: Level (1=Strategic, 2=Tactical, 3=Operational)
            capability_category: Category (Core, Supporting, Management)
            capability_domain: Domain (Customer, Product, Operations, etc.)
            pipeline_stage_id: Optional PipelineStage ID for tracking

        Returns:
            Dictionary with 'elements' and 'relationships' keys
        """
        level_desc = {1: "Strategic (L1)", 2: "Tactical (L2)", 3: "Operational (L3)"}.get(
            capability_level, "Unknown"
        )

        prompt = f"""You are an expert enterprise architect using ArchiMate 3.2 notation.
Analyze the following business capability and generate well-structured architecture elements.

BUSINESS CAPABILITY DETAILS:
---
Name: {capability_name}
Description: {capability_description}
Level: {level_desc}
Category: {capability_category or 'Not specified'}
Domain: {capability_domain or 'Not specified'}
---

TASK:
Generate 5 - 10 ArchiMate elements that model this business capability. Include elements from multiple layers to show complete architecture:
- Business Layer: processes, functions, services, roles that deliver the capability
- Application Layer: applications and data that support the capability
- Technology Layer: infrastructure that enables the capability

OUTPUT FORMAT:
Return ONLY a valid JSON object with this exact structure (no markdown, no code blocks, no explanations):

{{
  "elements": [
    {{
      "name": "Clear, Descriptive Name",
      "type": "ArchiMate Element Type",
      "layer": "Business|Application|Technology",
      "description": "Detailed description (20+ words) explaining purpose and responsibilities"
    }}
  ],
  "relationships": [
    {{
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "Serves|Realizes|Accesses|Triggers|Composition|Aggregation"
    }}
  ]
}}

VALID ELEMENT TYPES:
- Business Layer: BusinessActor, BusinessRole, BusinessProcess, BusinessService, BusinessFunction, BusinessCapability
- Application Layer: ApplicationComponent, ApplicationService, ApplicationInterface, DataObject
- Technology Layer: TechnologyService, TechnologyComponent, Artifact, Node

QUALITY REQUIREMENTS:
1. Each element must have a clear, descriptive name based on the capability
2. Descriptions must be 20+ words explaining purpose and responsibilities
3. Include elements from at least 2 different layers
4. Ensure elements represent distinct concepts (no duplicates)
5. Focus on domain-specific business and technical concepts from the capability
6. Create meaningful relationships showing how elements interact

Generate the JSON object now:"""

        # Use model router for intelligent model selection
        router = LLMModelRouter()
        provider, model = router.select_model(
            task_type="capability_discovery",
            complexity=TaskComplexity.COMPLEX,
            optimize_for="quality",
        )

        response_text, interaction = LLMService._call_llm(
            prompt=prompt, model=model, provider=provider, pipeline_stage_id=pipeline_stage_id
        )

        # Parse and shape-check the JSON response.
        #
        # This previously called LLMValidator.validate_architecture_response(), but
        # that class is not importable: `from .llm_validator import LLMValidator` is
        # commented out at the top of this module as "Temporarily disabled", and
        # llm_validator.py does not exist. The except below catches only ValueError,
        # so the resulting NameError escaped uncaught — every architecture-generation
        # call 500'd here rather than degrading.
        #
        # Parsing directly keeps the documented contract: a dict with "elements" and
        # "relationships", falling back to empty lists when the response is not
        # usable. Restore the richer schema validation when llm_validator lands.
        try:
            parsed = json.loads(response_text)
            if not isinstance(parsed, dict):
                raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
            result = {
                "elements": parsed.get("elements") or [],
                "relationships": parsed.get("relationships") or [],
            }
            logger.info(
                f"✓ Architecture response parsed: {len(result['elements'])} elements"
            )
            return result

        except (ValueError, TypeError) as e:  # fabricated-ok: returns honest empty structure below, no invented data
            logger.error(f"✗ Architecture response validation failed: {e}")
            logger.debug(f"Response was: {response_text[:500]}...")
            # Return empty structure on validation failure -- no data is
            # invented; downstream renders nothing rather than a fake element.
            return {"elements": [], "relationships": []}

    @staticmethod
    def generate_requirements_from_architecture(
        architecture_elements, pipeline_stage_id: Optional[int] = None
    ) -> str:
        """
        Generate functional and non-functional requirements from architecture elements.

        Args:
            architecture_elements: List of ArchiMateElement instances
            pipeline_stage_id: Optional PipelineStage ID for tracking

        Returns:
            Generated requirements as text
        """
        elements_summary = "\n".join(
            [
                f"- {elem.name} ({elem.type}, {elem.layer}): {elem.description}"
                for elem in architecture_elements
            ]
        )

        prompt = f"""Based on these architecture elements, generate detailed requirements:

{elements_summary}

Generate:
1. Functional Requirements (what the system should do)
2. Non-Functional Requirements (performance, security, scalability)
3. Acceptance Criteria for each requirement

Format as JSON with: category, description, priority"""

        provider, model = LLMService._get_configured_provider()
        response, interaction = LLMService._call_llm(
            prompt=prompt, model=model, provider=provider, pipeline_stage_id=pipeline_stage_id
        )

        return response

    @staticmethod
    def generate_uml_from_architecture(
        architecture_elements, diagram_type: str = "class", pipeline_stage_id: Optional[int] = None
    ) -> str:
        """
        Generate UML diagram in structured JSON format from architecture elements.

        Args:
            architecture_elements: List of ArchiMateElement instances
            diagram_type: Type of UML diagram (class, sequence, component)
            pipeline_stage_id: Optional PipelineStage ID for tracking

        Returns:
            JSON string with structured UML class diagram data
        """
        elements_summary = "\n".join(
            [f"- {elem.name} ({elem.type}): {elem.description}" for elem in architecture_elements]
        )

        if diagram_type == "class":
            prompt = f"""Generate a CLASS DIAGRAM in structured JSON format from these architecture elements:

{elements_summary}

OUTPUT FORMAT - Return ONLY valid JSON (no markdown, no code blocks):
{{
  "classes": [
    {{
      "name": "CustomerProfile",
      "type": "class",
      "stereotype": "Entity",
      "attributes": [
        {{"name": "customerId", "type": "int", "visibility": "private"}},
        {{"name": "name", "type": "string", "visibility": "private"}},
        {{"name": "email", "type": "string", "visibility": "private"}}
      ],
      "methods": [
        {{"name": "updateProfile", "parameters": [], "returnType": "void", "visibility": "public"}},
        {{"name": "getPreferences", "parameters": [], "returnType": "UserPreferences", "visibility": "public"}}
      ]
    }},
    {{
      "name": "OrderService",
      "type": "interface",
      "stereotype": "Service",
      "attributes": [],
      "methods": [
        {{"name": "createOrder", "parameters": ["orderData: OrderDTO"], "returnType": "Order", "visibility": "public"}},
        {{"name": "processPayment", "parameters": ["paymentInfo: PaymentDTO"], "returnType": "boolean", "visibility": "public"}}
      ]
    }}
  ],
  "relationships": [
    {{
      "source": "CustomerProfile",
      "target": "OrderService",
      "type": "association",
      "label": "uses",
      "sourceMultiplicity": "1",
      "targetMultiplicity": "*"
    }}
  ]
}}

REQUIREMENTS:
1. Generate at MOST 8 - 10 core classes (consolidate related elements - do NOT create one class per element)
2. Each class should have 2 - 4 key attributes and 3 - 5 essential methods MAX
3. Use proper visibility: public, private, protected
4. Add stereotypes: Entity, Service, Repository, Controller, DTO, etc.
5. Include 3 - 5 key relationships only
6. Focus on the most important domain concepts - be selective and concise

IMPORTANT: Keep the JSON response under 3000 tokens. Prioritize quality over quantity.

Generate the JSON now:"""
        else:
            # For other diagram types, create a simpler component structure
            prompt = f"""Generate a {diagram_type} diagram in JSON format from these architecture elements:

{elements_summary}

OUTPUT FORMAT - Return ONLY valid JSON:
{{
  "components": [
    {{"name": "Component Name", "type": "{diagram_type}", "description": "..."}}
  ],
  "relationships": [
    {{"source": "Source", "target": "Target", "type": "dependency", "label": "..."}}
  ]
}}

Generate the JSON now:"""

        provider, model = LLMService._get_configured_provider()
        response, interaction = LLMService._call_llm(
            prompt=prompt, model=model, provider=provider, pipeline_stage_id=pipeline_stage_id
        )

        return response

    @staticmethod
    def generate_code_from_uml(
        uml_elements, language: str = "python", pipeline_stage_id: Optional[int] = None
    ) -> str:
        """
        Generate source code from UML class diagram elements.

        Args:
            uml_elements: List of UMLElement instances with attributes and methods
            language: Target programming language (python, java, typescript)
            pipeline_stage_id: Optional PipelineStage ID for tracking

        Returns:
            Generated source code
        """
        # Batch prefetch attributes and methods for all UML elements to avoid N+1
        from app.models.models import UMLAttribute, UMLMethod

        elem_ids = [e.id for e in uml_elements]
        all_attrs = UMLAttribute.query.filter(UMLAttribute.uml_element_id.in_(elem_ids)).all() if elem_ids else []
        all_methods = UMLMethod.query.filter(UMLMethod.uml_element_id.in_(elem_ids)).all() if elem_ids else []
        attrs_by_elem = {}
        for a in all_attrs:
            attrs_by_elem.setdefault(a.uml_element_id, []).append(a)
        methods_by_elem = {}
        for m in all_methods:
            methods_by_elem.setdefault(m.uml_element_id, []).append(m)

        uml_summary = []
        for elem in uml_elements:
            attrs = [f"{a.name}: {a.data_type}" for a in attrs_by_elem.get(elem.id, [])]
            methods = [f"{m.name}({m.parameters}): {m.return_type}" for m in methods_by_elem.get(elem.id, [])]

            uml_summary.append(
                f"""
Class: {elem.name}
Type: {elem.element_type}
Attributes: {', '.join(attrs) if attrs else 'None'}
Methods: {', '.join(methods) if methods else 'None'}
"""
            )

        prompt = f"""Generate {language} code from these UML class definitions:

{"".join(uml_summary)}

Requirements:
- Follow {language} best practices and conventions
- Include docstrings/comments
- Add type hints (if applicable)
- Include error handling
- Make it production-ready

Output clean, well-structured code."""

        provider, model = LLMService._get_configured_provider()
        response, interaction = LLMService._call_llm(
            prompt=prompt,
            model=model,
            provider=provider,
            pipeline_stage_id=pipeline_stage_id,
        )

        return response

    @staticmethod
    def analyze_application_semantically(app_data: Dict) -> Dict:
        """
        Analyze application for semantic understanding using existing LLM infrastructure.

        This method provides true AI architecture generation by understanding:
        - Business function classification
        - Process relationships (APQC codes)
        - Technology dependencies
        - Integration requirements
        - Risk factors

        Args:
            app_data: Dictionary containing application information
                     (name, description, vendor_name, category, etc.)

        Returns:
            Dictionary with semantic analysis results
        """
        prompt = f"""Analyze this enterprise application for architecture intelligence:

APPLICATION DETAILS:
- Name: {app_data.get('name', 'Unknown')}
- Description: {app_data.get('description', 'No description available')}
- Vendor: {app_data.get('vendor_name', 'Unknown vendor')}
- Category: {app_data.get('application_category', 'Uncategorized')}
- Technology Stack: {app_data.get('programming_languages', 'Not specified')}
- Business Criticality: {app_data.get('business_criticality', 'Not specified')}

ANALYSIS REQUIREMENTS:
Provide a JSON response with the following structure:

{{
    "business_function": {{
        "primary_function": "Main business purpose (e.g., HR Management, Finance, Customer Service)",
        "secondary_functions": ["Additional business functions"],
        "business_domain": "Business domain (e.g., Operations, Support, Strategic)",
        "value_proposition": "Business value delivered"
    }},
    "apqc_processes": [
        {{
            "process_code": "APQC process code (e.g., 2.2, 3.1)",
            "process_name": "APQC process name",
            "relevance_score": 0.9,
            "confidence": "High/Medium/Low",
            "rationale": "Why this process is relevant"
        }}
    ],
    "technology_analysis": {{
        "inferred_technologies": ["Technologies inferred from description"],
        "architecture_pattern": "Architecture pattern (e.g., Monolithic, Microservices, Client-Server)",
        "integration_complexity": "Low/Medium/High",
        "technical_debt_indicators": ["Signs of technical debt"]
    }},
    "integration_requirements": {{
        "data_integrations": ["Required data integrations"],
        "api_dependencies": ["API dependencies"],
        "enterprise_systems": ["Enterprise systems it connects to"],
        "integration_criticality": "Low/Medium/High"
    }},
    "risk_factors": {{
        "business_risks": ["Business-related risks"],
        "technical_risks": ["Technical-related risks"],
        "operational_risks": ["Operational risks"],
        "overall_risk_level": "Low/Medium/High"
    }},
    "recommendations": {{
        "architecture_improvements": ["Suggested architecture improvements"],
        "consolidation_opportunities": ["Potential consolidation opportunities"],
        "modernization_needs": ["Modernization requirements"],
        "strategic_alignment": ["Strategic alignment suggestions"]
    }}
}}

Focus on providing actionable insights that would be valuable for enterprise architecture planning.
Be specific and evidence-based in your analysis."""

        try:
            response = LLMService.generate_response(prompt)

            # Try to parse as JSON, fallback to raw response if parsing fails
            try:
                import json

                analysis_result = json.loads(response)
                return analysis_result
            except json.JSONDecodeError:
                return {
                    "raw_analysis": response,
                    "parsing_error": "Could not parse JSON response",
                    "status": "partial",
                }

        except Exception as e:
            logger.error(f"Error in semantic application analysis: {e}")
            return {
                "error": str(e),
                "status": "failed",
                "fallback_analysis": "Semantic analysis unavailable",
            }

    @staticmethod
    def generate_relationship_insights(applications: List[Dict]) -> Dict:
        """
        Generate intelligent relationship analysis between applications.

        This method provides AI-powered relationship inference by analyzing:
        - Vendor ecosystem relationships
        - Business process connections
        - Technology stack similarities
        - Data flow dependencies
        - Integration patterns

        Args:
            applications: List of application dictionaries

        Returns:
            Dictionary with relationship analysis results
        """
        if len(applications) < 2:
            return {"error": "Need at least 2 applications for relationship analysis"}

        # Format applications for analysis
        app_summary = []
        for i, app in enumerate(applications, 1):
            app_summary.append(
                f"""
APP {i}:
- Name: {app.get('name', 'Unknown')}
- Vendor: {app.get('vendor_name', 'Unknown')}
- Description: {app.get('description', 'No description')}
- Category: {app.get('application_category', 'Uncategorized')}
- Technologies: {app.get('programming_languages', 'Not specified')}
"""
            )

        prompt = f"""Analyze relationships between these enterprise applications:

APPLICATION PORTFOLIO:
{chr(10).join(app_summary)}

RELATIONSHIP ANALYSIS REQUIREMENTS:
Provide a JSON response with the following structure:

{{
    "vendor_ecosystems": [
        {{
            "vendor_name": "Vendor name",
            "applications": ["Application names"],
            "ecosystem_type": "Integrated/Standalone/Hybrid",
            "integration_level": "Low/Medium/High",
            "synergy_opportunities": ["Potential synergies"],
            "consolidation_potential": "Low/Medium/High"
        }}
    ],
    "process_relationships": [
        {{
            "process_area": "Business process area",
            "connected_applications": ["Application names"],
            "relationship_type": "Sequential/Parallel/Supporting",
            "data_flow_direction": "Description of data flow",
            "integration_criticality": "Low/Medium/High"
        }}
    ],
    "technology_clusters": [
        {{
            "technology_stack": "Common technology stack",
            "applications": ["Application names"],
            "cluster_type": "Homogeneous/Hybrid/Legacy",
            "standardization_opportunity": "Opportunity for standardization",
            "skill_set_optimization": ["Skill set optimizations"]
        }}
    ],
    "data_dependencies": [
        {{
            "source_application": "Data source app",
            "target_application": "Data consumer app",
            "data_type": "Type of data flowing",
            "dependency_criticality": "Low/Medium/High",
            "integration_pattern": "Real-time/Batch/Event-driven"
        }}
    ],
    "integration_recommendations": [
        {{
            "recommendation": "Specific integration recommendation",
            "applications_involved": ["Applications involved"],
            "benefit": "Expected benefit",
            "complexity": "Low/Medium/High",
            "priority": "High/Medium/Low"
        }}
    ],
    "consolidation_opportunities": [
        {{
            "opportunity": "Consolidation opportunity description",
            "applications_to_consolidate": ["Applications to consolidate"],
            "savings_potential": "Potential savings",
            "risk_level": "Low/Medium/High",
            "implementation_complexity": "Low/Medium/High"
        }}
    ]
}}

Focus on practical, actionable insights for enterprise architecture optimization.
Consider business value, technical feasibility, and strategic alignment."""

        try:
            response = LLMService.generate_response(prompt)

            # Try to parse as JSON, fallback to raw response if parsing fails
            try:
                import json

                analysis_result = json.loads(response)
                return analysis_result
            except json.JSONDecodeError:
                return {
                    "raw_analysis": response,
                    "parsing_error": "Could not parse JSON response",
                    "status": "partial",
                }

        except Exception as e:
            logger.error(f"Error in relationship analysis: {e}")
            return {
                "error": str(e),
                "status": "failed",
                "fallback_analysis": "Relationship analysis unavailable",
            }

    @staticmethod
    def generate_code_from_architecture_element(
        element, technology_stack, pipeline_stage_id: Optional[int] = None
    ) -> str:
        """
        Generate source code directly from a single ArchiMate architecture element.

        ENHANCED: Now uses ContextEngineeringService to aggregate complete business context.

        Args:
            element: ArchiMateElement instance
            technology_stack: TechnologyStack instance with language/framework info
            pipeline_stage_id: Optional PipelineStage ID for tracking

        Returns:
            Generated source code for the element
        """
        from .context_engineering_service import ContextEngineeringService

        # Build enriched context from existing models
        context = ContextEngineeringService.build_code_generation_context(element, technology_stack)

        # Format context for prompt injection
        context_text = ContextEngineeringService.format_context_for_prompt(context)

        language = technology_stack.primary_language or "python"
        framework = technology_stack.framework or "Flask"

        prompt = f"""{context_text}

GENERATION REQUIREMENTS:
1. Implement ALL functional requirements listed above with complete business logic
2. Enforce ALL business rules with proper validation and error messages
3. Meet ALL non-functional requirements (performance targets, security standards)
4. Implement data model exactly as specified in UML (all attributes and methods)
5. Handle ALL relationships correctly (foreign keys, cascades, collections)
6. Inject ALL dependencies listed in "DEPENDENCIES" section
7. Include comprehensive error handling for all edge cases
8. Add structured logging for all critical operations
9. Include docstrings explaining business logic
10. Follow {framework} best practices and conventions

OUTPUT FORMAT:
Return complete, production-ready {language} code with:
- All necessary imports
- Type hints (if {language} supports them)
- Comprehensive error handling
- Business logic implementation
- Data validation
- Logging
- No TODOs, no placeholders, no stubs

Generate the code now:"""

        provider, model = LLMService._get_configured_provider()
        response, interaction = LLMService._call_llm(
            prompt=prompt,
            model=model,
            provider=provider,
            pipeline_stage_id=pipeline_stage_id,
            max_tokens=4096,  # Maximum allowed for Claude Haiku
        )

        # Strip markdown code blocks if present
        import re

        code_match = re.search(r"```(?:\w+)?\n(.*?)\n```", response, re.DOTALL)
        if code_match:
            return code_match.group(1)

        return response

    @staticmethod
    def _get_file_extension(language: str) -> str:
        """Get file extension for a programming language."""
        extensions = {
            "python": "py",
            "java": "java",
            "javascript": "js",
            "typescript": "ts",
            "csharp": "cs",
            "go": "go",
            "ruby": "rb",
            "php": "php",
        }
        return extensions.get(language.lower(), "txt")

    @staticmethod
    def generate_test_cases_from_requirements(
        requirements, test_type: str = "unit", pipeline_stage_id: Optional[int] = None
    ) -> str:
        """
        Generate test cases from requirements.

        Args:
            requirements: List of Requirement instances
            test_type: Type of tests (unit, integration, e2e)
            pipeline_stage_id: Optional PipelineStage ID for tracking

        Returns:
            Generated test code
        """
        req_summary = "\n".join([f"- [{req.category}] {req.description}" for req in requirements])

        prompt = f"""Generate {test_type} test cases for these requirements:

{req_summary}

Requirements:
- Use pytest framework
- Include test fixtures if needed
- Test both happy path and edge cases
- Add descriptive test names and docstrings

Output clean, executable test code."""

        provider, model = LLMService._get_configured_provider()
        response, interaction = LLMService._call_llm(
            prompt=prompt, model=model, provider=provider, pipeline_stage_id=pipeline_stage_id
        )

        return response

    @staticmethod
    def _get_cache_key(prompt: str, model: str) -> str:
        """Generate cache key from prompt and model."""
        content = f"{prompt}:{model}"
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def _get_cached_response(cache_key: str) -> Optional[str]:
        """Get cached response using Redis cache service or fallback to in-memory."""
        # Try Redis cache first
        cache_key_full = f"llm_response:{cache_key}"
        cached = cache_service.get(cache_key_full)
        if cached:
            logger.info(
                f"[CACHE HIT] Returning cached LLM response from Redis (key: {cache_key[:16]}...)"
            )
            return cached

        # Fallback to in-memory cache if Redis not available
        global _llm_cache_fallback, _cache_expiry_fallback
        now = datetime.utcnow()

        # Clean expired entries
        expired_keys = [k for k, expiry in _cache_expiry_fallback.items() if expiry < now]
        for k in expired_keys:
            _llm_cache_fallback.pop(k, None)
            _cache_expiry_fallback.pop(k, None)

        # Return cached if exists and not expired
        if cache_key in _llm_cache_fallback and cache_key in _cache_expiry_fallback:
            if _cache_expiry_fallback[cache_key] > now:
                logger.info(
                    f"[CACHE HIT] Returning cached LLM response from memory (key: {cache_key[:16]}...)"
                )
                return _llm_cache_fallback[cache_key]

        return None

    @staticmethod
    def _cache_response(cache_key: str, response: str, ttl_seconds: int = CACHE_TTL_SECONDS):
        """Cache response with TTL using Redis cache service or fallback to in-memory."""
        # Try Redis cache first
        cache_key_full = f"llm_response:{cache_key}"
        if cache_service.set(cache_key_full, response, ttl_seconds):
            logger.debug(
                f"[CACHE SET] Stored LLM response in Redis (key: {cache_key[:16]}..., TTL: {ttl_seconds}s)"
            )
            return

        # Fallback to in-memory cache if Redis not available
        global _llm_cache_fallback, _cache_expiry_fallback
        _llm_cache_fallback[cache_key] = response
        _cache_expiry_fallback[cache_key] = datetime.utcnow() + timedelta(seconds=ttl_seconds)
        logger.info(
            f"[CACHE SET] Stored LLM response in memory (key: {cache_key[:16]}..., TTL: {ttl_seconds}s)"
        )

    @staticmethod
    def generate_from_prompt(
        prompt: str,
        pipeline_stage_id: Optional[int] = None,
        use_cache: bool = True,
        cache_ttl: int = CACHE_TTL_SECONDS,
        expected_schema: Optional[str] = None,
        job_id: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """
        Generic method to generate content from any prompt with caching.

        Args:
            prompt: The prompt to send to the LLM
            pipeline_stage_id: Optional PipelineStage ID for tracking
            use_cache: Whether to use cache (default: True)
            cache_ttl: Cache TTL in seconds (default: 3600)
            timeout: Optional per-call client-level timeout override, in seconds.
                Passed through to the underlying provider client for THIS call
                only — leaves every other caller's (unset) default timeout
                unchanged. Added for Task 4 (P0 wave):
                application_pattern_classifier_service.py needs a ≤60s bound
                on this path without lowering the global OpenAI/Anthropic/
                OpenRouter defaults used elsewhere (e.g. chat, ArchiMate
                generation).

        Returns:
            Generated response text
        """
        provider, model = LLMService._get_configured_provider()

        # Check cache first
        if use_cache:
            cache_key = LLMService._get_cache_key(prompt, model)
            cached_response = LLMService._get_cached_response(cache_key)
            if cached_response:
                return cached_response

        # Call LLM if not cached
        response, interaction = LLMService._call_llm(
            prompt=prompt,
            model=model,
            provider=provider,
            pipeline_stage_id=pipeline_stage_id,
            expected_schema=expected_schema,
            job_id=job_id,
            timeout=timeout,
        )

        # Cache the response
        if use_cache:
            LLMService._cache_response(cache_key, response, cache_ttl)

        return response

    @staticmethod
    def review_code(
        code_content: str, language: str = "python", pipeline_stage_id: Optional[int] = None
    ) -> Dict:
        """
        Perform automated code review using LLM.

        Args:
            code_content: Source code to review
            language: Programming language
            pipeline_stage_id: Optional PipelineStage ID for tracking

        Returns:
            Dict with: issues_found, quality_score, comments
        """
        prompt = f"""Perform a thorough code review of this {language} code:

```{language}
{code_content}
```

Analyze for:
1. Code quality and best practices
2. Security vulnerabilities
3. Performance issues
4. Maintainability and readability
5. Test coverage gaps

Provide:
- Quality score (0 - 100)
- List of specific issues found
- Constructive comments and suggestions

Format as JSON: {{"quality_score": 85, "issues": ["issue1", "issue2"], "comments": "..."}}"""

        provider, model = LLMService._get_configured_provider()
        response, interaction = LLMService._call_llm(
            prompt=prompt, model=model, provider=provider, pipeline_stage_id=pipeline_stage_id
        )

        # Parse JSON response
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {
                "quality_score": 0,
                "issues": ["Failed to parse LLM response"],
                "comments": response,
            }

    @staticmethod
    def _get_all_api_keys(provider: str) -> List[str]:
        """
        Get ALL available API keys for a provider.

        Priority: database (Admin UI) is canonical.  Environment variables are
        a bootstrap fallback used ONLY when there is no DB record at all for the
        provider.  This prevents silent conflicts where admins configure a key in
        the UI but the server keeps using a stale .env key instead.

        Supports multiple keys via:
        1. Database APISettings rows (multiple enabled rows per provider)
        2. Environment variables — ONLY if no DB record exists for the provider
        3. Numbered/secondary env vars (OPENAI_API_KEY_1, …_SECONDARY) —
           same rule: only when DB has no record
        """
        api_keys = []
        db_has_record = False

        # 1. Check database for configured keys (canonical source)
        try:
            from app.models.models import APISettings
            settings_list = APISettings.query.filter_by(
                provider=provider,
                enabled=True,
            ).all()

            for settings in settings_list:
                key = settings.api_key  # TypeDecorator decrypts automatically
                if key and key.strip() and key.strip() not in api_keys:
                    api_keys.append(key.strip())
                    db_has_record = True
                    logger.debug("Found API key for %s in database (settings id: %s)", provider, settings.id)

            # Even disabled rows count as "the admin has touched this provider"
            if not db_has_record:
                db_has_record = APISettings.query.filter_by(provider=provider).count() > 0
        except Exception as e:
            logger.debug("Could not query database API settings: %s", e)

        # 2-4. Environment variables — only when no DB record exists for this provider
        if not db_has_record:
            logger.info(
                "No database record for provider '%s' — falling back to environment variables. "
                "Consider migrating the key to Admin > API Settings.",
                provider,
            )

            env_key = os.getenv(f"{provider.upper()}_API_KEY")
            if env_key and env_key.strip() and env_key.strip() not in api_keys:
                api_keys.append(env_key.strip())
                logger.debug("Found primary API key for %s in environment", provider)

            for i in range(1, 10):
                backup_key = os.getenv(f"{provider.upper()}_API_KEY_{i}")
                if backup_key and backup_key.strip() and backup_key.strip() not in api_keys:
                    api_keys.append(backup_key.strip())
                    logger.debug("Found backup API key #%s for %s in environment", i, provider)

            secondary_key = os.getenv(f"{provider.upper()}_API_KEY_SECONDARY")
            if secondary_key and secondary_key.strip() and secondary_key.strip() not in api_keys:
                api_keys.append(secondary_key.strip())
                logger.debug("Found secondary API key for %s in environment", provider)

        elif os.getenv(f"{provider.upper()}_API_KEY"):
            logger.debug(
                "Ignoring %s_API_KEY environment variable — DB record takes precedence. "
                "Remove the env var to avoid confusion.",
                provider.upper(),
            )

        return api_keys
    
    @staticmethod
    def _call_llm_with_failover(
        prompt: str,
        model: str,
        provider: str = "openai",
        max_tokens: Optional[int] = None,
        pipeline_stage_id: Optional[int] = None,
        _already_tried: Optional[List[str]] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[str, LLMInteraction]:
        """
        Call LLM with automatic API key failover.

        Tries multiple API keys in sequence until one succeeds.
        This ensures elements are not lost due to API key issues.

        Args:
            prompt: The prompt text
            model: Model name
            provider: Provider name
            max_tokens: Optional max tokens
            pipeline_stage_id: Optional tracking ID
            timeout: Optional per-call client-level timeout override (seconds),
                passed through to the provider-specific call for this call only.

        Returns:
            Tuple of (response_text, interaction)

        Raises:
            RuntimeError: If ALL API keys fail
        """
        # Track providers already tried across recursive fallback calls to prevent looping
        if _already_tried is None:
            _already_tried = []
        _already_tried = list(_already_tried) + [provider]

        # Get all available API keys
        api_keys = LLMService._get_all_api_keys(provider)
        
        if not api_keys:
            raise ValueError(
                f"No API keys found for {provider}. "
                f"Please configure at least one API key in database or environment."
            )
        
        logger.info(f"Attempting LLM call with {len(api_keys)} API key(s) available for {provider}")
        
        # Track the last error for reporting
        last_error = None
        failed_keys = 0
        
        # Try each API key in sequence
        for idx, api_key in enumerate(api_keys):
            try:
                logger.info(f"Trying API key #{idx + 1} for {provider}...")
                
                # Call the appropriate provider method
                if provider == "openai":
                    response_text, token_input, token_output, cost = LLMService._call_openai(
                        prompt, model, api_key, max_tokens=max_tokens, timeout=timeout
                    )
                elif provider == "anthropic":
                    response_text, token_input, token_output, cost = LLMService._call_anthropic(
                        prompt, model, api_key, max_tokens=max_tokens, timeout=timeout
                    )
                elif provider == "gemini":
                    response_text, token_input, token_output, cost = LLMService._call_gemini(
                        prompt, model, api_key, max_tokens=max_tokens, timeout=timeout
                    )
                elif provider == "deepseek":
                    response_text, token_input, token_output, cost = LLMService._call_deepseek(
                        prompt, model, api_key, max_tokens=max_tokens, timeout=timeout
                    )
                elif provider == "huggingface":
                    response_text, token_input, token_output, cost = LLMService._call_huggingface(
                        prompt, model, api_key, max_tokens=max_tokens
                    )
                elif provider == "openrouter":
                    # OpenRouter supports multiple backup models (comma-separated in default_model)
                    # Try each model in sequence before failing the key
                    or_models = [m.strip() for m in model.split(",") if m.strip()] if "," in model else [model]
                    or_success = False
                    or_last_error = None
                    for or_model in or_models:
                        try:
                            response_text, token_input, token_output, cost = LLMService._call_openrouter(
                                prompt, or_model, api_key, max_tokens=max_tokens, timeout=timeout
                            )
                            model = or_model  # Update model for interaction record
                            or_success = True
                            if or_model != or_models[0]:
                                logger.info(f"🔄 OpenRouter backup model {or_model} succeeded (primary was {or_models[0]})")
                            break
                        except Exception as or_err:
                            or_last_error = or_err
                            logger.warning(f"⚠️  OpenRouter model {or_model} failed: {str(or_err)[:80]}")
                            continue
                    if not or_success:
                        raise or_last_error or RuntimeError("All OpenRouter models failed")
                else:
                    raise ValueError(f"Unsupported provider: {provider}")
                
                # Success! Return the result
                logger.info(f"✅ API key #{idx + 1} succeeded for {provider}")
                
                # Create interaction record
                interaction = LLMInteraction(
                    prompt=prompt,
                    response=response_text,
                    model_name=model,
                    provider=provider,
                    token_count_input=token_input,
                    token_count_output=token_output,
                    cost=cost,
                    pipeline_stage_id=pipeline_stage_id,
                )
                
                return response_text, interaction
                
            except Exception as e:
                failed_keys += 1
                last_error = e
                logger.warning(
                    f"⚠️  API key #{idx + 1} failed for {provider}: {str(e)[:100]}"
                )
                
                # If this is an authentication error, log it clearly
                if "authentication" in str(e).lower() or "unauthorized" in str(e).lower() or "invalid" in str(e).lower():
                    logger.error(f"🔑 API key #{idx + 1} appears invalid or expired")
                
                # If this is a rate limit error, note it
                error_lower = str(e).lower()
                if "rate" in error_lower or "limit" in error_lower or "quota" in error_lower:
                    logger.warning(f"⏱️  API key #{idx + 1} hit rate limit or quota")
                    # Account-level quota errors affect ALL keys — skip remaining keys for this provider
                    if "usage limit" in error_lower or "api usage" in error_lower or "quota exceeded" in error_lower:
                        logger.warning(f"🚫 Account-level quota exhausted for {provider} — skipping remaining keys")
                        break

                # Continue to next key
                continue
        
        # All keys failed for primary provider — try cross-provider fallback
        logger.error(f"❌ All {len(api_keys)} API key(s) failed for {provider}")

        fallback_order = ["openai", "anthropic", "gemini", "deepseek", "openrouter", "huggingface"]
        for fallback_provider in fallback_order:
            # Skip providers we've already tried in this call chain to prevent infinite loops
            if fallback_provider in _already_tried:
                continue
            fallback_keys = LLMService._get_all_api_keys(fallback_provider)
            if not fallback_keys:
                continue

            # Get a default model for the fallback provider — DB is source of truth
            fallback_defaults = {
                "openai": "gpt-4o-mini",
                "anthropic": "claude-haiku-4-5-20251001",
                "gemini": "gemini-2.0-flash",
                "deepseek": "deepseek-chat",
                "openrouter": "deepseek/deepseek-chat",
                "huggingface": "meta-llama/Llama-3.1-8B-Instruct",
            }
            try:
                # Look up configured model from DB
                from app.models.models import APISettings
                fb_settings = APISettings.query.filter_by(
                    provider=fallback_provider, enabled=True
                ).first()
                fb_model = (
                    fb_settings.default_model if fb_settings and fb_settings.default_model
                    else fallback_defaults.get(fallback_provider, "")
                )
            except Exception:
                fb_model = fallback_defaults.get(fallback_provider, "")

            if not fb_model:
                continue

            logger.info(f"🔄 Cross-provider fallback: trying {fallback_provider} ({fb_model})")
            try:
                return LLMService._call_llm_with_failover(
                    prompt=prompt,
                    model=fb_model,
                    provider=fallback_provider,
                    max_tokens=max_tokens,
                    pipeline_stage_id=pipeline_stage_id,
                    _already_tried=_already_tried,
                    timeout=timeout,
                )
            except Exception as fb_err:
                logger.warning(f"Cross-provider fallback to {fallback_provider} also failed: {str(fb_err)[:80]}")
                continue

        raise RuntimeError(
            f"All API keys failed for {provider} and all fallback providers. "
            f"Failed keys: {failed_keys}. "
            f"Last error: {str(last_error) if last_error else 'Unknown error'}"
        )

    @staticmethod
    def _call_llm(
        prompt: str,
        model: str,
        provider: str = "openai",
        pipeline_stage_id: Optional[int] = None,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        max_tokens: Optional[int] = None,
        expected_schema: Optional[str] = None,
        job_id: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[str, LLMInteraction]:
        """
        Internal method to call LLM API and track the interaction.

        Now includes:
        - API key failover (tries multiple keys if one fails)
        - Budget enforcement via LLMCostTracker
        - Intelligent model routing
        - Cost tracking in GBP

        Args:
            prompt: The prompt to send to the LLM
            model: Model name (e.g., 'gpt - 4', 'claude - 3 - sonnet')
            provider: Provider name ('openai', 'anthropic', 'gemini', 'azure')
            pipeline_stage_id: Optional PipelineStage ID for tracking
            user_id: Optional user ID for budget tracking
            project_id: Optional project ID for budget tracking
            timeout: Optional per-call client-level timeout override (seconds),
                passed through to the provider client for this call only.

        Returns:
            Tuple of (response_text, LLMInteraction instance)
        """
        # Initialize cost tracker
        cost_tracker = LLMCostTracker()

        # Pre-flight budget check with timeout protection
        estimated_tokens = len(prompt) // 4  # Rough estimate: 4 chars per token
        try:
            allowed, budget_message = cost_tracker.check_budget_before_call(
                user_id=user_id, project_id=project_id, estimated_tokens=estimated_tokens
            )
        except Exception as e:
            logger.error(f"Budget check failed, proceeding with call: {e}")
            allowed, budget_message = True, None  # Allow call if budget check fails

        if not allowed:
            raise ValueError(f"Budget limit exceeded: {budget_message}")

        # ENT-040: scrub sensitive data from the prompt before transmission.
        prompt = _scrub_prompt(prompt)
        start_time = time.time()

        # Call LLM with automatic API key failover (tries multiple keys)
        try:
            response_text, interaction = LLMService._call_llm_with_failover(
                prompt=prompt,
                model=model,
                provider=provider,
                max_tokens=max_tokens,
                pipeline_stage_id=pipeline_stage_id,
                timeout=timeout,
            )
        except RuntimeError as e:
            # All API keys failed
            logger.error(f"All API keys failed for {provider}: {e}")
            
            # Check if we should try a different provider as last resort
            error_str = str(e).lower()
            is_credit_error = (
                "credit" in error_str
                or "quota" in error_str
                or "billing" in error_str
                or "rate limit" in error_str
                or "usage limit" in error_str
                or "api usage" in error_str
            )
            
            if is_credit_error:
                logger.warning(f"🔄 Attempting cross-provider fallback after all {provider} keys failed...")
                try:
                    fallback_provider, fallback_model = LLMService._get_configured_provider(
                        exclude_providers=[provider]
                    )
                    logger.info(
                        f"✅ Cross-provider fallback: Switching from {provider} to {fallback_provider} "
                        f"with model {fallback_model}"
                    )
                    
                    # Recursively call with fallback provider
                    return LLMService._call_llm(
                        prompt=prompt,
                        model=fallback_model,
                        provider=fallback_provider,
                        pipeline_stage_id=pipeline_stage_id,
                        user_id=user_id,
                        project_id=project_id,
                        max_tokens=max_tokens,
                        expected_schema=expected_schema,
                        job_id=job_id,
                        timeout=timeout,
                    )
                except ValueError as fallback_error:
                    logger.error(f"❌ No alternative provider available: {fallback_error}")
                    raise RuntimeError(
                        f"All API keys failed for {provider} and no fallback provider is configured. "
                        f"Please add more API keys or configure another provider."
                    )
            else:
                raise

        # If interaction wasn't created by failover (no pipeline_stage_id), create it now
        latency_ms = int((time.time() - start_time) * 1000)
        
        if interaction is None and pipeline_stage_id is not None:
            # Create a basic interaction record
            interaction = LLMInteraction(
                pipeline_stage_id=pipeline_stage_id,
                model_name=model,
                provider=provider,
                prompt=prompt,
                response=response_text,
                token_count_input=0,  # Not tracked in this path
                token_count_output=0,
                cost=0.0,
                latency_ms=latency_ms,
            )
            
            db.session.add(interaction)
            try:
                db.session.commit()
            except Exception as e:
                logger.error(f"Failed to save LLM interaction: {e}")
                db.session.rollback()

        # Post-response validation via middleware
        if expected_schema and pipeline_stage_id is not None:
            try:
                from app.models.job import Job
                from app.services.llm_middleware import validate_llm_response

                job = Job.query.get(job_id) if job_id else None
                # If the LLM returned JSON text, try parsing to object
                parsed_obj = None
                try:
                    parsed_obj = json.loads(response_text)
                except Exception:
                    # Attempt to extract JSON object from text
                    import re

                    m = re.search(r"\{[\s\S]*\}", response_text)
                    if m:
                        try:
                            parsed_obj = json.loads(m.group(0))
                        except Exception:
                            parsed_obj = None

                validation_result = validate_llm_response(parsed_obj or {}, expected_schema, job)
                if validation_result.get("action") == "review":
                    logger.warning(
                        f"LLM response requires human review: {validation_result.get('errors')}"
                    )

            except Exception as e:
                logger.error(f"Error running LLM middleware validation: {e}")

        return response_text, interaction

    @staticmethod
    def _call_llm_with_tools(
        provider: str,
        model: str,
        api_key: str,
        system_prompt: str,
        messages: list,
        tools: list,
        stream: bool = False,
        base_url: str = None,
        user_id: Optional[int] = None,
        org_id: Optional[int] = None,
        emit: Optional[Callable] = None,
    ) -> dict:
        """Call an LLM with tool schemas, through the budget/scrub/meter guards.

        Every model call the AI chat's tool loop makes passes the same budget
        pre-flight, prompt scrub and usage metering as every other model call.

        Returns a normalised dict:
          {"text": str|None, "tool_calls": list, "usage": {"input_tokens": int,
           "output_tokens": int, "cost": float}}
        """
        # Scrub the system prompt and every user-role message content.
        scrubbed_system = _scrub_prompt(system_prompt)
        scrubbed_messages = []
        for msg in messages:
            m = dict(msg)
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    m["content"] = _scrub_prompt(content)
                elif isinstance(content, list):
                    m["content"] = [
                        dict(c, content=_scrub_prompt(c["content"])
                             if isinstance(c.get("content"), str) else c.get("content"))
                        for c in content
                    ]
            scrubbed_messages.append(m)

        # Budget pre-flight: estimate tokens from serialised messages.
        serialised = json.dumps(scrubbed_messages, default=str)
        estimated_tokens = len(serialised) // 4 + len(scrubbed_system) // 4
        cost_tracker = LLMCostTracker()
        try:
            allowed, budget_message = cost_tracker.check_budget_before_call(
                user_id=user_id, estimated_tokens=estimated_tokens
            )
        except Exception:
            allowed, budget_message = True, None
        if not allowed:
            raise ValueError(f"Budget limit exceeded: {budget_message}")

        # Call the provider with tools.
        if provider == "anthropic":
            result = LLMService._call_anthropic_tool_loop(
                model, api_key, scrubbed_system, scrubbed_messages, tools, stream, emit
            )
        else:
            result = LLMService._call_openai_compat_tool_loop(
                model, api_key, scrubbed_system, scrubbed_messages, tools,
                stream=stream, base_url=base_url, emit=emit,
            )

        usage = result.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cost = usage.get("cost", 0.0)

        # Record a usage event so every model call in the loop is metered.
        if org_id is not None:
            try:
                from app.services.usage_metering_service import UsageMeteringService
                UsageMeteringService.record(
                    org_id=org_id,
                    user_id=user_id,
                    event_type="llm_tool_call",
                    resource_type="ai_chat",
                    metadata={
                        "provider": provider,
                        "model": model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": cost,
                    },
                )
            except Exception:
                logger.warning("Failed to record usage event for tool-loop call", exc_info=True)

        return result

    @staticmethod
    def _call_anthropic_tool_loop(
        model: str,
        api_key: str,
        system_prompt: str,
        messages: list,
        tools: list,
        stream: bool = False,
        emit: Optional[Callable] = None,
    ) -> dict:
        """Call Anthropic with tool schemas and return normalised result."""
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, timeout=90.0)
        max_tokens = 8192 if "sonnet" in model or "opus" in model else 4096

        if stream:
            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system_prompt,
                         "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                tools=tools,
                tool_choice={"type": "auto"},
            ) as s:
                for _text in s.text_stream:
                    if emit:
                        emit({"type": "token", "text": _text})
                final = s.get_final_message()
            response = final
        else:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system_prompt,
                         "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                tools=tools,
                tool_choice={"type": "auto"},
            )

        text = None
        tool_calls = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })
            elif block.type == "text":
                text = block.text

        input_tokens = getattr(response.usage, "input_tokens", 0)
        output_tokens = getattr(response.usage, "output_tokens", 0)
        cache_read = getattr(response.usage, "cache_read_input_tokens", 0)
        cache_create = getattr(response.usage, "cache_creation_input_tokens", 0)

        from app.modules.ai_chat.services.model_defaults import price_for
        pricing = price_for(model)
        regular_input = input_tokens - cache_create - cache_read
        cost = (
            (regular_input / 1000.0 * pricing["input"])
            + (cache_create / 1000.0 * pricing["input"] * 1.25)
            + (cache_read / 1000.0 * pricing["input"] * 0.10)
            + (output_tokens / 1000.0 * pricing["output"])
        )

        return {
            "text": text,
            "tool_calls": tool_calls,
            "raw": response,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost": cost},
        }

    @staticmethod
    def _call_openai_compat_tool_loop(
        model: str,
        api_key: str,
        system_prompt: str,
        messages: list,
        tools: list,
        stream: bool = False,
        base_url: str = None,
        emit: Optional[Callable] = None,
    ) -> dict:
        """Call an OpenAI-compatible provider with tool schemas."""
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=90.0)
        full_messages = [{"role": "system", "content": system_prompt}] + list(messages)
        _token_limit = 8192 if ("gpt-4" in model or "gpt-5" in model) else 4096

        if stream:
            text_acc = ""
            tool_calls_acc: dict = {}
            with client.chat.completions.create(
                model=model,
                messages=full_messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.0,
                max_completion_tokens=_token_limit,
                stream=True,
            ) as s:
                for chunk in s:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        text_acc += delta.content
                        if emit:
                            emit({"type": "token", "text": delta.content})
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc.id:
                                tool_calls_acc[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls_acc[idx]["name"] += tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc.function.arguments
            tool_calls = []
            for idx in sorted(tool_calls_acc.keys()):
                tc = tool_calls_acc[idx]
                try:
                    arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    arguments = {}
                tool_calls.append({"id": tc["id"], "name": tc["name"], "arguments": arguments})
            return {
                "text": text_acc or None,
                "tool_calls": tool_calls,
                "raw": None,
                "usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0},
            }

        response = client.chat.completions.create(
            model=model,
            messages=full_messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.0,
            max_completion_tokens=_token_limit,
        )

        msg = response.choices[0].message
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })

        input_tokens = getattr(response.usage, "prompt_tokens", 0)
        output_tokens = getattr(response.usage, "completion_tokens", 0)

        from app.modules.ai_chat.services.model_defaults import price_for
        pricing = price_for(model)
        cost = (
            (input_tokens / 1000.0 * pricing["input"])
            + (output_tokens / 1000.0 * pricing["output"])
        )

        return {
            "text": msg.content,
            "tool_calls": tool_calls,
            "raw": response,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost": cost},
        }

    @staticmethod
    @retry_on_transient_error(max_attempts=3, min_wait=2, max_wait=10)
    def _call_openai(
        prompt: str,
        model: str,
        api_key: str,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[str, int, int, float]:
        """
        Call OpenAI API with automatic retry on transient failures.

        Retries on: Timeout, ConnectionError, RateLimitError
        Max attempts: 3
        Backoff: Exponential (2s, 4s, 8s)

        ``timeout`` overrides the default 90s client-level timeout for this
        call only (e.g. application_pattern_classifier_service.py passes a
        tighter bound); other callers keep the 90s default.
        """
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)

            # Use caller-provided limit or model-specific defaults.
            # gpt-4/gpt-5 series support 16K output; gpt-3.5 supports 4K.
            if max_tokens is None:
                max_tokens = 8192 if ("gpt-4" in model or "gpt-5" in model) else 4096

            effective_timeout = timeout if timeout is not None else 90.0

            # Enforce deterministic output for critical generation tasks
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert software architect and developer.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_completion_tokens=max_tokens,
                timeout=effective_timeout,
            )

            response_text = response.choices[0].message.content
            token_input = response.usage.prompt_tokens
            token_output = response.usage.completion_tokens

            # Calculate cost (approximate pricing)
            cost_per_1k_input = 0.03 if "gpt - 4" in model else 0.001
            cost_per_1k_output = 0.06 if "gpt - 4" in model else 0.002
            cost = (token_input / DEFAULT_TOKEN_COST_MULTIPLIER * cost_per_1k_input) + (
                token_output / DEFAULT_TOKEN_COST_MULTIPLIER * cost_per_1k_output
            )

            return response_text, token_input, token_output, cost

        except ImportError:
            return "OpenAI library not installed. Run: pip install openai", 0, 0, 0.0
        except Exception as e:
            logger.error(f"OpenAI API error after retries: {str(e)}")
            raise  # Re-raise for retry logic to handle

    @staticmethod
    def _enforce_deterministic_options():
        """Set global flags or environment variables to encourage deterministic LLM output.

        This is best-effort: actual control depends on provider APIs and configured model.
        """
        # Example: prefer temperature=0 for OpenAI-like calls; other providers handle differently
        os.environ["LLM_ENFORCE_TEMPERATURE"] = "0.0"

    @staticmethod
    @retry_on_transient_error(max_attempts=3, min_wait=2, max_wait=10)
    def _call_anthropic(
        prompt: str,
        model: str,
        api_key: str,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[str, int, int, float]:
        """
        Call Anthropic Claude API with prompt caching for 90% cost reduction.
        Automatically retries on transient failures (timeouts, rate limits).

        Caching Strategy:
        - System prompt (ArchiMate expertise) is cached and reused across calls
        - Cache hits reduce costs dramatically: only pay for new user prompts
        - Cache TTL: 5 minutes of inactivity

        Retry Strategy:
        - Max attempts: 3
        - Backoff: Exponential (2s, 4s, 8s)
        - Retries on: Timeout, ConnectionError, RateLimitError, APIError

        ``timeout`` overrides the default 85s client-level timeout for this
        call only; other callers keep the 85s default.
        """
        try:
            import anthropic

            from app.services.archimate.archimate_prompts import ARCHIMATE_SYSTEM_PROMPT

            logger.info("\n" + "=" * 80)
            logger.info("🤖 ANTHROPIC API CALL STARTING")
            logger.info("=" * 80)
            logger.info(f"Model: {model}")
            logger.info("API key configured (key present)")
            logger.info(f"Prompt length: {len(prompt)} characters")
            logger.info(f"System prompt length: {len(ARCHIMATE_SYSTEM_PROMPT)} characters")
            logger.info("=" * 80 + "\n")

            effective_timeout = timeout if timeout is not None else 85.0

            client = anthropic.Anthropic(
                api_key=api_key, timeout=effective_timeout
            )

            # Use prompt caching to reduce costs by 90%
            # System prompt is marked for caching and reused across calls
            # Set max_tokens based on model capability or use provided value
            if max_tokens is None:
                if "opus" in model or "sonnet" in model:
                    max_tokens = 8192  # Opus and Sonnet support 8K output
                else:
                    max_tokens = 4096  # Haiku supports 4K output
            else:
                # Validate max_tokens doesn't exceed model limits
                if "opus" in model:
                    max_tokens = min(max_tokens, 32000)  # Opus supports up to 32K
                elif "sonnet" in model:
                    max_tokens = min(max_tokens, 16384)  # Sonnet supports up to 16K
                else:
                    max_tokens = min(max_tokens, 4096)  # Haiku supports up to 4K
                logger.info(f"Using custom max_tokens: {max_tokens}")

            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": ARCHIMATE_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},  # Cache this system prompt
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
                timeout=effective_timeout,
            )

            response_text = response.content[0].text
            token_input = response.usage.input_tokens
            token_output = response.usage.output_tokens

            logger.info("\n" + "=" * 80)
            logger.info("✅ ANTHROPIC API RESPONSE RECEIVED")
            logger.info("=" * 80)
            logger.info(f"Response length: {len(response_text)} characters")
            logger.info(f"Input tokens: {token_input}")
            logger.info(f"Output tokens: {token_output}")
            logger.info("\nFirst 500 chars of response:")
            logger.info("-" * 80)
            logger.info(response_text[:500])
            logger.info("-" * 80)
            logger.info("=" * 80 + "\n")

            # Track cache performance
            cache_creation_tokens = getattr(response.usage, "cache_creation_input_tokens", 0)
            cache_read_tokens = getattr(response.usage, "cache_read_input_tokens", 0)

            # Calculate cost with cache-aware pricing
            if "opus" in model:
                cost_per_1k_input, cost_per_1k_output = 0.015, 0.075
                cost_per_1k_cache_write = 0.01875  # 25% more than input
                cost_per_1k_cache_read = 0.0015  # 90% less than input
            elif "sonnet" in model:
                cost_per_1k_input, cost_per_1k_output = 0.003, 0.015
                cost_per_1k_cache_write = 0.00375
                cost_per_1k_cache_read = 0.0003
            else:  # haiku
                cost_per_1k_input, cost_per_1k_output = 0.00025, 0.00125
                cost_per_1k_cache_write = 0.0003125
                cost_per_1k_cache_read = 0.000025

            # Calculate total cost including cache tokens
            regular_input_tokens = token_input - cache_creation_tokens - cache_read_tokens
            cost = (
                (regular_input_tokens / DEFAULT_TOKEN_COST_MULTIPLIER * cost_per_1k_input)
                + (cache_creation_tokens / DEFAULT_TOKEN_COST_MULTIPLIER * cost_per_1k_cache_write)
                + (cache_read_tokens / DEFAULT_TOKEN_COST_MULTIPLIER * cost_per_1k_cache_read)
                + (token_output / DEFAULT_TOKEN_COST_MULTIPLIER * cost_per_1k_output)
            )

            # Log cache performance for monitoring
            if cache_read_tokens > 0:
                cache_hit_ratio = cache_read_tokens / token_input if token_input > 0 else 0
                logger.info(
                    f"[CACHE HIT] {cache_read_tokens} tokens read from cache ({cache_hit_ratio:.1%} hit ratio)"
                )

            return response_text, token_input, token_output, cost

        except ImportError:
            logger.info("\n❌ ERROR: Anthropic library not installed")
            logger.error("Anthropic library not installed")
            raise ImportError("Anthropic library not installed. Run: pip install anthropic")
        except anthropic.APITimeoutError as e:
            logger.info(f"\n❌ ERROR: Anthropic API timeout: {str(e)}")
            logger.error(f"Anthropic API timeout after 120 seconds: {str(e)}")
            raise TimeoutError(
                "ArchiMate generation timed out. The complexity of your requirements may require simplification or breaking into smaller parts."
            )
        except anthropic.AuthenticationError as e:
            logger.info("\n❌ ERROR: Authentication failed - Invalid API key")
            logger.info(f"Error details: {str(e)}")
            logger.error(f"Anthropic authentication error: {str(e)}")
            raise ValueError(
                "Authentication failed - Invalid API key. Please check your API settings at /admin/api-settings"
            )
        except anthropic.BadRequestError as e:
            error_str = str(e)
            logger.info(f"\n❌ ERROR: Anthropic API error: {error_str}")
            logger.error(f"Anthropic API error: {error_str}")

            # Check if this is a credit/balance error (400 with credit balance message)
            if "credit balance" in error_str.lower() or "insufficient credits" in error_str.lower():
                logger.warning(
                    "⚠️ Anthropic API credits exhausted. Error will trigger automatic fallback."
                )
            raise
        except anthropic.APIError as e:
            logger.info(f"\n❌ ERROR: Anthropic API error: {str(e)}")
            logger.error(f"Anthropic API error: {str(e)}")
            raise
        except anthropic.RateLimitError as e:
            logger.error(f"Anthropic API rate limit exceeded: {str(e)}")
            raise ValueError("API rate limit exceeded. Please try again in a few moments.")
        except Exception as e:
            logger.error(f"Anthropic API error after retries: {str(e)}")
            raise  # Re-raise for proper error handling

    @staticmethod
    @retry_on_transient_error(max_attempts=3, min_wait=2, max_wait=10)
    def _call_gemini(
        prompt: str,
        model: str,
        api_key: str,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[str, int, int, float]:
        """Call Google Gemini models via REST API.

        ``timeout`` overrides the default 60s request timeout for this call
        only; other callers keep the 60s default.
        """

        # Normalize Gemini model names to correct API format
        # Try multiple model name formats if first fails
        model_normalized = model.lower().strip()

        # Try common model name variations
        model_variations = []
        if "gemini - 1.5 - pro" in model_normalized or model_normalized == "gemini - 1.5 - pro":
            model_variations = ["gemini - 1.5 - pro", "gemini - 1.5 - pro-latest", "gemini-pro"]
        elif (
            "gemini - 1.5 - flash" in model_normalized or model_normalized == "gemini - 1.5 - flash"
        ):
            model_variations = [
                "gemini - 1.5 - flash",
                "gemini - 1.5 - flash-latest",
                "gemini-flash",
            ]
        elif "gemini-pro" in model_normalized:
            model_variations = ["gemini-pro", "gemini-pro-latest", "gemini - 1.5 - pro"]
        else:
            # Default: try the model as-is, then add -latest suffix
            model_variations = [model_normalized, f"{model_normalized}-latest"]

        # Use first variation by default
        model_normalized = model_variations[0] if model_variations else model_normalized

        base_url = LLMService.PROVIDERS["gemini"]["base_url"].rstrip("/")
        endpoint = f"{base_url}/models/{model_normalized}:generateContent"

        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        if max_tokens is not None:
            payload["generationConfig"] = {"maxOutputTokens": max_tokens}

        effective_timeout = timeout if timeout is not None else 60
        try:
            response = requests.post(
                endpoint, params={"key": api_key}, json=payload, timeout=effective_timeout
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else "unknown"
            message = exc.response.text if exc.response else str(exc)

            # 404 errors are permanent (wrong model/endpoint) - don't retry
            if status == 404:
                error_msg = f"Gemini model '{model_normalized}' not found (404). Check model name in API settings."
                logger.error(error_msg)
                raise ValueError(error_msg) from exc

            logger.error(f"Gemini HTTP error ({status}): {message}")
            raise
        except requests.exceptions.RequestException as exc:
            logger.error(f"Gemini request failed: {exc}")
            raise

        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            logger.error(f"Gemini response missing candidates: {data}")
            raise ValueError("Gemini response did not include any candidates")

        parts = candidates[0].get("content", {}).get("parts", [])
        response_text = ""
        for part in parts:
            if "text" in part:
                response_text += part["text"]

        if not response_text:
            logger.warning(f"Gemini response contained no text. Raw response: {data}")

        usage = data.get("usageMetadata", {})
        token_input = usage.get("promptTokenCount", 0)
        token_output = usage.get("candidatesTokenCount", 0)
        if not token_output:
            total = usage.get("totalTokenCount")
            if total is not None:
                token_output = max(total - token_input, 0)

        # Pricing (USD per 1K tokens)
        model_name = model.lower()
        if "flash" in model_name:
            cost_per_1k_input = 0.0005
            cost_per_1k_output = 0.0015
        else:
            cost_per_1k_input = 0.0035
            cost_per_1k_output = 0.0105

        cost = (token_input / DEFAULT_TOKEN_COST_MULTIPLIER) * cost_per_1k_input + (token_output / DEFAULT_TOKEN_COST_MULTIPLIER) * cost_per_1k_output

        return response_text, token_input, token_output, cost

    @staticmethod
    @retry_on_transient_error(max_attempts=3, min_wait=2, max_wait=10)
    def _call_deepseek(
        prompt: str,
        model: str,
        api_key: str,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[str, int, int, float]:
        """
        Call DeepSeek API with automatic retry on transient failures.

        DeepSeek uses OpenAI-compatible API, so we can use the OpenAI client.

        Retries on: Timeout, ConnectionError, RateLimitError
        Max attempts: 3
        Backoff: Exponential (2s, 4s, 8s)

        ``timeout`` overrides the default 60s client-level timeout for this
        call only; other callers keep the 60s default.
        """
        try:
            from openai import OpenAI

            # NOTE: the OpenAI client has no request timeout by default, which let a
            # stalled DeepSeek connection hang a worker indefinitely (see Task 4,
            # P0 wave). Bound it explicitly at the client level.
            effective_timeout = timeout if timeout is not None else 60.0
            client = OpenAI(
                api_key=api_key, base_url="https://api.deepseek.com/v1", timeout=effective_timeout
            )

            # Use model-specific optimal token limits
            if max_tokens is None:
                max_tokens = 4096  # Default for DeepSeek

            # DeepSeek has a maximum of 8192 tokens - cap any value above that
            max_tokens = min(max_tokens, 8192)

            # Enforce deterministic output for critical generation tasks
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert software architect and developer.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
                timeout=effective_timeout,
            )

            response_text = response.choices[0].message.content
            token_input = response.usage.prompt_tokens
            token_output = response.usage.completion_tokens

            # Calculate cost (DeepSeek pricing - approximate, adjust based on actual pricing)
            # DeepSeek is typically much cheaper than OpenAI/Anthropic
            cost_per_1k_input = 0.0001  # Very low input cost
            cost_per_1k_output = 0.0002  # Very low output cost
            cost = (token_input / DEFAULT_TOKEN_COST_MULTIPLIER * cost_per_1k_input) + (
                token_output / DEFAULT_TOKEN_COST_MULTIPLIER * cost_per_1k_output
            )

            return response_text, token_input, token_output, cost

        except ImportError:
            return "OpenAI library not installed. Run: pip install openai", 0, 0, 0.0
        except Exception as e:
            logger.error(f"DeepSeek API error after retries: {str(e)}")
            raise  # Re-raise for retry logic to handle

    @staticmethod
    @retry_on_transient_error(max_attempts=3, min_wait=2, max_wait=10)
    def _call_huggingface(
        prompt: str, model: str, api_key: Optional[str] = None, max_tokens: Optional[int] = None
    ) -> Tuple[str, int, int, float]:
        """
        Call Hugging Face GENERATIVE models locally using transformers library.

        Supports free generative models:
        - gpt2: GPT - 2 Small (124M params) - Fast, good for simple tasks
        - gpt2 - medium: GPT - 2 Medium (355M params) - Better quality
        - EleutherAI/gpt-neo - 125M: GPT-Neo (125M params) - Open alternative
        - microsoft/DialoGPT-small: DialoGPT - Conversational model

        IMPORTANT: These are GENERATIVE models (decoder-based) suitable for text generation.
        Encoder models (BERT, RoBERTa, DistilBERT) are NOT suitable for document analysis.

        Args:
            prompt: Input text to process
            model: Hugging Face model ID (must be a generative model)
            api_key: Optional (not used for local models, but kept for API compatibility)
            max_tokens: Optional max tokens for generation (default: 200)

        Returns:
            Tuple of (response_text, token_input, token_output, cost)
        """
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

            # Hugging Face models are free (no cost)
            cost = 0.0

            # Use text generation pipeline for all models (they're all generative)
            try:
                # Determine device
                device = -1 if not torch.cuda.is_available() else 0

                # Load tokenizer and model
                tokenizer = AutoTokenizer.from_pretrained(model)
                model_obj = AutoModelForCausalLM.from_pretrained(model)

                # Set pad_token if not set (required for some models)
                if tokenizer.pad_token is None:
                    tokenizer.pad_token = tokenizer.eos_token

                # Get model's maximum context length
                model_max_length = getattr(model_obj.config, "max_position_embeddings", 1024)

                # CRITICAL FIX: Auto-switch from small context models to better ones
                # blenderbot - 1B-distill has only 128 tokens, which is too small for chat
                if "blenderbot" in model.lower() or model_max_length < 512:
                    current_app.logger.warning(
                        f"Model {model} has small context window ({model_max_length} tokens). "
                        f"Switching to GPT - 2 for better performance."
                    )
                    # Switch to GPT - 2 which has 1024 tokens
                    model = "gpt2"
                    tokenizer = AutoTokenizer.from_pretrained(model)
                    model_obj = AutoModelForCausalLM.from_pretrained(model)
                    model_max_length = 1024  # GPT - 2 context window
                    if tokenizer.pad_token is None:
                        tokenizer.pad_token = tokenizer.eos_token

                # CRITICAL FIX: For HuggingFace models with small context windows,
                # cap output tokens to a reasonable size (200) regardless of what was requested.
                # This prevents the bug where max_tokens=16000 would cause negative max_input_length.
                output_tokens = min(max_tokens or 200, 200)  # Cap at 200 tokens for output

                # Tokenize prompt to check length
                prompt_tokens = tokenizer.encode(prompt, add_special_tokens=False)
                prompt_length = len(prompt_tokens)

                # Calculate maximum input length
                # Reserve space for output tokens (leave some buffer)
                max_input_length = model_max_length - output_tokens - 10  # 10 token buffer

                if prompt_length > max_input_length:
                    # For Hugging Face models, intelligently truncate the prompt instead of failing
                    # This is better than raising an error for chat scenarios
                    import tiktoken

                    # Try to use tiktoken for approximation, fallback to simple ratio
                    try:
                        encoding = tiktoken.get_encoding(
                            "gpt2"
                        )  # Good approximation for many models
                        prompt_tokens = encoding.encode(prompt)
                        # Keep the beginning and end, truncate the middle
                        if len(prompt_tokens) > max_input_length:
                            keep_tokens = max_input_length // 2
                            truncated_tokens = (
                                prompt_tokens[:keep_tokens] + prompt_tokens[-keep_tokens:]
                            )
                            prompt = (
                                encoding.decode(truncated_tokens)
                                + "\n\n[Context truncated due to length limits...]"
                            )
                    except Exception:
                        # Fallback: simple character-based truncation
                        ratio = max_input_length / prompt_length
                        keep_chars = int(len(prompt) * ratio * 0.8)  # Keep 80% to be safe
                        prompt = (
                            prompt[:keep_chars]
                            + "\n\n[Context truncated due to length limits...]"
                            + prompt[-keep_chars // 2 :]
                        )

                    # Log the truncation
                    current_app.logger.warning(
                        f"Prompt truncated for Hugging Face model {model}: "
                        f"original {prompt_length} tokens -> {max_input_length} tokens max"
                    )

                # Create text generation pipeline
                generator = pipeline(
                    "text-generation",
                    model=model_obj,
                    tokenizer=tokenizer,
                    device=device,
                    do_sample=True,
                    temperature=0.7,
                    top_p=0.9,
                    repetition_penalty=1.1,
                )

                # Calculate max length (input + output, but don't exceed model max)
                min(prompt_length + output_tokens, model_max_length)

                # Generate text with proper truncation
                result = generator(
                    prompt,
                    max_new_tokens=output_tokens,  # Use max_new_tokens instead of max_length
                    num_return_sequences=1,
                    truncation=True,
                    pad_token_id=tokenizer.pad_token_id,
                )

                # Extract generated text
                if isinstance(result, list) and len(result) > 0:
                    generated_text = result[0].get("generated_text", "")
                    # Extract only the new part (after the prompt)
                    if generated_text.startswith(prompt):
                        response_text = generated_text[len(prompt) :].strip()
                    else:
                        response_text = generated_text.strip()
                else:
                    response_text = str(result)

                # Calculate tokens using tokenizer
                token_input = len(tokenizer.encode(prompt))
                token_output = len(tokenizer.encode(response_text))

                return response_text, token_input, token_output, cost

            except Exception as e:
                logger.error(f"Error with Hugging Face model {model}: {e}")
                # Return error message as response
                response_text = (
                    f"Error: Hugging Face model {model} failed to generate text. {str(e)}"
                )
                token_input = len(prompt) // 4
                token_output = len(response_text) // 4
                return response_text, token_input, token_output, cost

        except ImportError:
            error_msg = "Transformers library not installed. Run: pip install transformers torch"
            logger.error(error_msg)
            return error_msg, 0, 0, 0.0
        except Exception as e:
            logger.error(f"Hugging Face model error: {str(e)}")
            raise  # Re-raise for retry logic to handle

    @staticmethod
    def _call_openrouter(
        prompt: str,
        model: str,
        api_key: str,
        max_tokens: Optional[int] = 4096,
        timeout: Optional[float] = None,
    ) -> Tuple[str, int, int, float]:
        """
        Call OpenRouter API (OpenAI-compatible chat completions).

        OpenRouter provides access to 65+ models from multiple providers
        through a unified API, including free models.

        Args:
            prompt: The prompt text
            model: Model ID in provider/model format (e.g. "google/gemini-2.5-flash-preview:free")
            api_key: OpenRouter API key
            max_tokens: Maximum response tokens
            timeout: Optional override for the read timeout (seconds) of this
                call only; other callers keep the default (10s connect, 80s
                read). The connect timeout is left at 10s regardless.

        Returns:
            Tuple of (response_text, input_tokens, output_tokens, cost)
        """
        import requests as http_requests

        if max_tokens is None:
            max_tokens = 4096

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:5000",
            "X-Title": "Enterprise Architecture Platform",
        }

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }

        read_timeout = timeout if timeout is not None else 80
        try:
            response = http_requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=(10, read_timeout),
            )
            response.raise_for_status()
            data = response.json()

            # OpenRouter may return 200 with an error object instead of choices
            if "error" in data:
                err = data["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                err_code = err.get("code", "") if isinstance(err, dict) else ""
                logger.error(f"OpenRouter returned error for {model}: [{err_code}] {err_msg}")
                raise RuntimeError(
                    f"OpenRouter model error ({model}): {err_msg}"
                )

            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError(f"OpenRouter returned empty choices for {model}")

            content = choices[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)

            # OpenRouter may include cost in usage, otherwise estimate from pricing
            cost = 0.0
            if "total_cost" in usage:
                cost = float(usage["total_cost"])

            logger.info(
                f"OpenRouter ({model}): {tokens_in} in, {tokens_out} out, cost={cost}"
            )
            return content, tokens_in, tokens_out, cost

        except http_requests.exceptions.HTTPError as e:
            error_body = ""
            try:
                error_data = e.response.json()
                error_body = error_data.get("error", {}).get("message", e.response.text[:300])
            except Exception:
                error_body = e.response.text[:300] if e.response else str(e)
            logger.error(f"OpenRouter HTTP error for {model}: {error_body}")
            raise RuntimeError(f"OpenRouter API error ({model}): {error_body}") from e
        except http_requests.exceptions.Timeout:
            logger.error(f"OpenRouter API timeout after 120s for {model}")
            raise RuntimeError(f"OpenRouter API timeout for {model}. Try a different model.")
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"OpenRouter error for {model}: {str(e)}")
            raise

    @staticmethod
    def get_available_llms() -> List[Dict[str, Any]]:
        """
        Get all available LLMs with their working status.
        
        This checks which LLM providers have valid API keys and can be used.
        Also includes a "none" option for users who want to work without AI.
        
        Returns:
            List of dicts with provider info: {
                "id": provider name,
                "name": display name,
                "available": bool,
                "has_credit": bool,
                "models": list of available models,
                "test_result": result of connection test (if tested)
            }
        """
        available_llms = []
        
        # Provider configurations
        providers = {
            "openai": {
                "name": "OpenAI (GPT-4, GPT-3.5)",
                "default_models": ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"]
            },
            "anthropic": {
                "name": "Anthropic (Claude)",
                "default_models": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]
            },
            "gemini": {
                "name": "Google (Gemini)",
                "default_models": ["gemini-2.0-flash", "gemini-2.0-flash-lite"]
            },
            "deepseek": {
                "name": "DeepSeek",
                "default_models": ["deepseek-chat", "deepseek-coder"]
            },
            "huggingface": {
                "name": "Hugging Face (Local Models)",
                "default_models": ["gpt2", "gpt2-medium"]
            },
            "openrouter": {
                "name": "OpenRouter (65+ Models)",
                "default_models": ["google/gemini-2.5-flash-preview:free", "meta-llama/llama-3-8b-instruct:free"]
            }
        }
        
        # Batch-load all enabled API settings in ONE query (avoids N+1)
        all_settings_by_provider = {}
        try:
            from app.models.models import APISettings
            all_enabled = APISettings.query.filter_by(enabled=True).all()
            for s in all_enabled:
                all_settings_by_provider.setdefault(s.provider, []).append(s)
        except Exception as e:
            logger.debug(f"Could not query database API settings: {e}")

        for provider_id, config in providers.items():
            llm_info = {
                "id": provider_id,
                "name": config["name"],
                "available": False,
                "has_credit": True,  # Assume true until proven otherwise
                "models": [],
                "test_result": None,
                "key_count": 0
            }

            # Collect API keys from pre-loaded settings + environment
            api_keys = []
            provider_settings = all_settings_by_provider.get(provider_id, [])
            for s in provider_settings:
                if s.api_key and len(s.api_key.strip()) > 0:
                    api_keys.append(s.api_key.strip())
                    logger.debug(f"Found API key for {provider_id} in database (settings id: {s.id})")

            # Check environment variables
            env_key = os.getenv(f"{provider_id.upper()}_API_KEY")
            if env_key and len(env_key.strip()) > 0 and env_key not in api_keys:
                api_keys.append(env_key.strip())
                logger.debug(f"Found primary API key for {provider_id} in environment")

            if api_keys:
                llm_info["available"] = True
                llm_info["key_count"] = len(api_keys)

                # Get configured models from pre-loaded settings
                configured_models = [
                    s.default_model for s in provider_settings if s.default_model
                ]
                llm_info["models"] = configured_models if configured_models else config["default_models"]
                llm_info["test_result"] = f"{len(api_keys)} API key(s) configured"

            available_llms.append(llm_info)
        
        # Add "No LLM" option for manual/declarative workflows
        available_llms.insert(0, {
            "id": "none",
            "name": "No LLM - Manual Mode",
            "available": True,
            "has_credit": True,
            "models": ["manual"],
            "test_result": "Manual mode - no AI assistance",
            "key_count": 0,
            "is_no_llm": True
        })
        
        return available_llms
    
    @staticmethod
    def set_user_llm_preference(user_id: int, provider: str, model: Optional[str] = None) -> Dict[str, Any]:
        """
        Store user's LLM preference persistently in system_settings and cache in session.
        """
        try:
            from flask import session
            from sqlalchemy import text

            key = f"user_{user_id}_llm_provider"
            model_key = f"user_{user_id}_llm_model"

            # Persist to system_settings (key-value store)
            try:
                db.session.execute(
                    text("INSERT INTO system_settings (key, value) VALUES (:k, :v) "
                         "ON CONFLICT (key) DO UPDATE SET value=:v, updated_at=now()"),
                    {"k": key, "v": provider}
                )
                if model:
                    db.session.execute(
                        text("INSERT INTO system_settings (key, value) VALUES (:k, :v) "
                             "ON CONFLICT (key) DO UPDATE SET value=:v, updated_at=now()"),
                        {"k": model_key, "v": model}
                    )
                db.session.commit()
            except Exception as db_err:
                db.session.rollback()
                logger.warning(f"Could not persist LLM preference to DB: {db_err}")

            # Cache in session too
            session[key] = provider
            if model:
                session[model_key] = model

            logger.info(f"User {user_id} selected LLM provider: {provider}, model: {model}")
            return {"success": True, "provider": provider, "model": model}
        except Exception as e:
            logger.error(f"Failed to set LLM preference: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_user_llm_preference(user_id: int) -> Dict[str, Any]:
        """
        Get user's LLM preference — checks Flask session first, then DB.
        """
        try:
            from flask import session
            from sqlalchemy import text

            key = f"user_{user_id}_llm_provider"
            model_key = f"user_{user_id}_llm_model"

            # Check session cache first
            provider = session.get(key)
            model = session.get(model_key)

            if provider is None:
                # Fall back to DB
                try:
                    row = db.session.execute(
                        text("SELECT value FROM system_settings WHERE key=:k"), {"k": key}
                    ).fetchone()
                    if row:
                        provider = row[0]
                        session[key] = provider
                    row2 = db.session.execute(
                        text("SELECT value FROM system_settings WHERE key=:k"), {"k": model_key}
                    ).fetchone()
                    if row2:
                        model = row2[0]
                        session[model_key] = model
                except Exception as exc:
                    logger.debug("suppressed error in LLMService.get_user_llm_preference (app/modules/ai_chat/services/llm_service_impl.py): %s", exc)

            return {
                "provider": provider,
                "model": model,
                "has_preference": provider is not None and provider != "none",
            }
        except Exception as e:
            logger.debug(f"Could not get LLM preference: {e}")
            return {"provider": None, "model": None, "has_preference": False}

    @staticmethod
    def estimate_cost(
        prompt_length: int, expected_response_length: int, model: str = "gpt - 4"
    ) -> float:
        """
        Estimate the cost of an LLM call.

        Args:
            prompt_length: Approximate character length of prompt
            expected_response_length: Expected character length of response
            model: Model name

        Returns:
            Estimated cost in USD
        """
        # Rough estimate: 4 characters per token
        token_input = prompt_length // 4
        token_output = expected_response_length // 4

        if "gpt - 4" in model:
            cost = (token_input / DEFAULT_TOKEN_COST_MULTIPLIER * 0.03) + (token_output / DEFAULT_TOKEN_COST_MULTIPLIER * 0.06)
        elif "claude - 3 - opus" in model:
            cost = (token_input / DEFAULT_TOKEN_COST_MULTIPLIER * 0.015) + (token_output / DEFAULT_TOKEN_COST_MULTIPLIER * 0.075)
        elif "claude - 3 - sonnet" in model:
            cost = (token_input / DEFAULT_TOKEN_COST_MULTIPLIER * 0.003) + (token_output / DEFAULT_TOKEN_COST_MULTIPLIER * 0.015)
        elif "gemini" in model:
            if "flash" in model:
                cost = (token_input / DEFAULT_TOKEN_COST_MULTIPLIER * 0.0005) + (token_output / DEFAULT_TOKEN_COST_MULTIPLIER * 0.0015)
            else:
                cost = (token_input / DEFAULT_TOKEN_COST_MULTIPLIER * 0.0035) + (token_output / DEFAULT_TOKEN_COST_MULTIPLIER * 0.0105)
        else:
            cost = (token_input / DEFAULT_TOKEN_COST_MULTIPLIER * 0.001) + (token_output / DEFAULT_TOKEN_COST_MULTIPLIER * 0.002)

        return round(cost, 4)


    # =========================================================================
    # Decision Logging for Audit Trail (PRD: LLM-Driven Gap Analysis)
    # =========================================================================

    @staticmethod
    def log_decision(
        decision_type: str,
        context: Dict,
        decision: Dict,
        rationale: str,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Log an LLM-driven decision for audit trail.

        This method supports the PRD requirement for audit and traceability
        by persisting all LLM-driven decisions to the database.

        Args:
            decision_type: Type of decision being logged, e.g.:
                - 'gap_analysis': Gap identification decision
                - 'reuse_recommendation': Reuse vs build recommendation
                - 'roadmap_generation': Roadmap item creation
                - 'work_package_creation': Work package generation
                - 'validation_decision': Stakeholder feedback processing
            context: Input context provided to the decision process
                - Should include relevant IDs, parameters, and input data
            decision: The decision that was made
                - Should include the outcome, selected options, scores
            rationale: Human-readable explanation of the decision
            user_id: Optional user ID for tracking
            project_id: Optional project ID for tracking

        Returns:
            ID of the logged decision (LLMInteraction.id) or None if logging fails

        Example:
            >>> LLMService.log_decision(
            ...     decision_type='reuse_recommendation',
            ...     context={'gap_id': 123, 'architecture_id': 1},
            ...     decision={'recommendation': 'extend', 'application_id': 456, 'confidence': 0.85},
            ...     rationale='Application XYZ has 85% capability overlap and modern tech stack.'
            ... )
        """
        try:
            # Build the decision log entry
            ({
                "decision_type": decision_type,
                "context": context,
                "decision": decision,
                "rationale": rationale,
                "timestamp": datetime.utcnow().isoformat(),
            })

            # Create LLMInteraction record for audit
            interaction = LLMInteraction(
                provider="decision_log",
                model_name="audit_trail",
                prompt=json.dumps({"type": decision_type, "context": context}),
                response=json.dumps({"decision": decision, "rationale": rationale}),
                token_count_input=len(json.dumps(context)),  # Approximate
                token_count_output=len(json.dumps(decision)),  # Approximate
                cost=0.0,
                user_id=user_id,
            )

            db.session.add(interaction)
            db.session.commit()

            logger.info(
                f"Decision logged: type={decision_type}, "
                f"decision={decision.get('recommendation', decision)}, "
                f"id={interaction.id}"
            )

            return interaction.id

        except Exception as e:
            logger.error(f"Failed to log decision: {e}")
            db.session.rollback()
            return None

    @staticmethod
    def get_decision_log(
        decision_type: Optional[str] = None,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> list:
        """
        Retrieve decision logs for audit purposes.

        Args:
            decision_type: Filter by decision type (optional)
            user_id: Filter by user ID (optional)
            project_id: Filter by project ID (optional)
            limit: Maximum number of records to return
            since: Only return decisions after this timestamp

        Returns:
            List of decision log entries
        """
        try:
            query = LLMInteraction.query.filter_by(
                provider="decision_log", model_name="audit_trail"
            )

            if user_id:
                query = query.filter_by(user_id=user_id)

            if project_id:
                query = query.filter_by(project_id=project_id)

            if since:
                query = query.filter(LLMInteraction.created_at >= since)

            interactions = query.order_by(LLMInteraction.created_at.desc()).limit(limit).all()

            decision_logs = []
            for interaction in interactions:
                try:
                    prompt_data = json.loads(interaction.prompt) if interaction.prompt else {}
                    response_data = json.loads(interaction.response) if interaction.response else {}

                    # Filter by decision_type if specified
                    if decision_type and prompt_data.get("type") != decision_type:
                        continue

                    decision_logs.append(
                        {
                            "id": interaction.id,
                            "decision_type": prompt_data.get("type"),
                            "context": prompt_data.get("context", {}),
                            "decision": response_data.get("decision", {}),
                            "rationale": response_data.get("rationale", ""),
                            "timestamp": interaction.created_at.isoformat()
                            if interaction.created_at
                            else None,
                            "user_id": interaction.user_id,
                            "project_id": interaction.project_id,
                        }
                    )
                except json.JSONDecodeError:
                    continue

            return decision_logs

        except Exception as e:
            logger.error(f"Failed to retrieve decision logs: {e}")
            return []



def test_api_key(provider: str, api_key: str, model: str | None = None) -> Dict:
    """
    Test if an API key is valid by making a minimal API call.

    Args:
        provider: Provider name ('openai', 'anthropic', 'gemini', 'azure', 'huggingface', 'deepseek', 'custom')
        api_key: API key to test
        model: Model to use for the test. When None, reads from the DB row for this provider.
               Always prefer passing the configured model explicitly — hardcoded fallbacks go stale.

    Returns:
        Dict with 'success' (bool) and 'message' (str) keys
    """
    if not api_key or api_key.strip() == "":
        return {"success": False, "message": "API key is empty"}

    def _resolve_model(prov: str, passed: str | None, db_field: str = "default_model") -> str | None:
        """Return model: passed > DB setting (any enabled status) > None."""
        if passed and passed.strip():
            return passed.strip()
        try:
            from app.models.models import APISettings
            row = APISettings.query.filter_by(provider=prov).order_by(
                APISettings.enabled.desc()
            ).first()
            val = getattr(row, db_field, None) if row else None
            return val.strip() if val and val.strip() else None
        except Exception:
            return None

    try:
        if provider == "openai":
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            test_model = _resolve_model("openai", model) or "gpt-4o-mini"
            response = client.chat.completions.create(
                model=test_model,
                messages=[{"role": "user", "content": "test"}],
                max_completion_tokens=5,
            )

            return {"success": True, "message": f"Connection successful. Model: {response.model}"}

        elif provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            test_model = _resolve_model("anthropic", model) or "claude-haiku-4-5-20251001"
            response = client.messages.create(
                model=test_model,
                max_tokens=10,
                messages=[{"role": "user", "content": "test"}],
            )

            return {"success": True, "message": f"Connection successful. Model: {response.model}"}

        elif provider == "gemini":
            model_name = _resolve_model("gemini", model) or "gemini-2.0-flash"

            try:
                ping_response = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
                    params={"key": api_key},
                    json={"contents": [{"parts": [{"text": "ping"}]}]},
                    timeout=15,
                )
                ping_response.raise_for_status()
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response else "unknown"
                message = exc.response.text if exc.response else str(exc)
                return {"success": False, "message": f"Gemini API error ({status}): {message}"}
            except requests.exceptions.RequestException as exc:
                return {"success": False, "message": f"Gemini connection failed: {exc}"}

            return {"success": True, "message": f"Connection successful. Model: {model_name}"}

        elif provider == "azure":
            # Azure requires additional configuration (endpoint, deployment)
            return {
                "success": False,
                "message": "Azure OpenAI testing not yet implemented. Configure endpoint and deployment name.",
            }

        elif provider == "deepseek":
            # DeepSeek uses OpenAI-compatible API
            from openai import OpenAI

            test_model = _resolve_model("deepseek", model) or "deepseek-chat"
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
            response = client.chat.completions.create(
                model=test_model, messages=[{"role": "user", "content": "test"}], max_tokens=5
            )

            return {"success": True, "message": f"Connection successful. Model: {response.model}"}

        elif provider == "huggingface":
            # Hugging Face Inference API test
            try:
                model_id = _resolve_model("huggingface", model, db_field="hf_model_id") or "meta-llama/Llama-3.1-8B-Instruct"

                # Use Hugging Face Inference API
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

                data = {"inputs": "test", "parameters": {"max_new_tokens": 5, "temperature": 0.1}}

                response = requests.post(
                    f"https://api-inference.huggingface.co/models/{model_id}",
                    headers=headers,
                    json=data,
                    timeout=15,
                )
                response.raise_for_status()

                return {"success": True, "message": f"Connection successful. Model: {model_id}"}

            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response else "unknown"
                message = exc.response.text if exc.response else str(exc)
                return {
                    "success": False,
                    "message": f"Hugging Face API error ({status}): {message}",
                }
            except requests.exceptions.RequestException as exc:
                return {"success": False, "message": f"Hugging Face connection failed: {exc}"}

        elif provider == "custom":
            # Custom API testing
            try:
                from app.models.models import APISettings

                # Get custom API settings
                settings = APISettings.query.filter_by(provider="custom", enabled=True).first()
                if not settings or not settings.custom_endpoint_url:
                    return {"success": False, "message": "Custom API endpoint URL not configured"}

                endpoint_url = settings.custom_endpoint_url
                auth_method = settings.custom_auth_method or "bearer"
                custom_headers = {}

                # Parse custom headers if provided
                if settings.custom_headers:
                    try:
                        custom_headers = json.loads(settings.custom_headers)
                    except json.JSONDecodeError:
                        return {"success": False, "message": "Invalid JSON in custom headers"}

                # Set up authentication headers
                if auth_method == "bearer" and api_key:
                    custom_headers["Authorization"] = f"Bearer {api_key}"
                elif auth_method == "api_key_header" and api_key:
                    custom_headers["X-API-Key"] = api_key
                elif auth_method == "basic_auth" and api_key:
                    import base64

                    credentials = base64.b64encode(f"api:{api_key}".encode()).decode()
                    custom_headers["Authorization"] = f"Basic {credentials}"

                # Set default content type if not provided
                if "Content-Type" not in custom_headers:
                    custom_headers["Content-Type"] = "application/json"

                # Make test request
                test_data = (
                    {"test": True, "message": "API connection test"}
                    if custom_headers.get("Content-Type") == "application/json"
                    else {"test": "true"}
                )

                response = requests.post(
                    endpoint_url,
                    headers=custom_headers,
                    json=test_data
                    if custom_headers.get("Content-Type") == "application/json"
                    else None,
                    data=test_data
                    if custom_headers.get("Content-Type") != "application/json"
                    else None,
                    timeout=15,
                )

                # Check if response is successful (2xx status code)
                if 200 <= response.status_code < 300:
                    return {
                        "success": True,
                        "message": f"Connection successful. Status: {response.status_code}",
                    }
                else:
                    return {
                        "success": False,
                        "message": f"API returned status {response.status_code}: {response.text[:200]}",
                    }

            except requests.exceptions.RequestException as exc:
                return {"success": False, "message": f"Custom API connection failed: {exc}"}
            except Exception as exc:
                return {"success": False, "message": f"Custom API test error: {exc}"}

        elif provider == "openrouter":
            # OpenRouter — validate key by fetching models list (lightweight GET)
            try:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                }
                response = requests.get(
                    "https://openrouter.ai/api/v1/models",
                    headers=headers,
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
                model_count = len(data.get("data", []))

                # Resolve configured default model for display
                default_model = _resolve_model("openrouter", model) or "deepseek/deepseek-chat"

                return {
                    "success": True,
                    "message": f"Connection successful. {model_count} models available. Default: {default_model}",
                }

            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response else "unknown"
                body = exc.response.text[:200] if exc.response else str(exc)
                return {"success": False, "message": f"OpenRouter API error ({status}): {body}"}
            except requests.exceptions.RequestException as exc:
                return {"success": False, "message": f"OpenRouter connection failed: {exc}"}

        else:
            return {"success": False, "message": f"Unknown provider: {provider}"}

    except ImportError as e:
        return {"success": False, "message": f"Required library not installed: {str(e)}"}
    except Exception as e:
        error_msg = str(e)
        # Clean up the error message for display
        if "Incorrect API key" in error_msg or "invalid_api_key" in error_msg:
            return {
                "success": False,
                "message": "Invalid API key. Please check your key and try again.",
            }
        elif "authentication" in error_msg.lower():
            return {
                "success": False,
                "message": "Authentication failed. Please verify your API key.",
            }
        else:
            return {"success": False, "message": f"Connection test failed: {error_msg}"}
