"""
-> app.modules.ai_chat.services.ai_assistant_service

Context-Aware AI Helper Service

Provides intelligent, context-aware assistance throughout the application:
- Inline field suggestions based on historical data
- Smart form completion
- Real-time validation with AI insights
- Context loading from current user activity
- Workflow-specific helpers
"""

import logging
import re
from datetime import datetime  # dead-code-ok
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func  # dead-code-ok

from app import db
from app.extensions.cache import org_scoped_cache_key
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_capability import UnifiedCapability
from app.models.vendor.vendor_organization import VendorOrganization
from app.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class ContextType:
    """Types of context that can be loaded."""

    APPLICATION_FORM = "application_form"
    CAPABILITY_MAPPING = "capability_mapping"
    VENDOR_SELECTION = "vendor_selection"
    ARCHIMATE_ELEMENT = "archimate_element"
    APQC_MAPPING = "apqc_mapping"
    GENERAL = "general"


class ContextAwareAIHelper:
    """
    Provides context-aware AI assistance throughout the application.

    Features:
    - Analyzes current user context (page, form, data)
    - Provides intelligent field suggestions
    - Validates inputs with AI reasoning
    - Offers workflow-specific guidance
    - Learns from historical patterns
    """

    def __init__(self, user_id: Optional[int] = None):
        """
        Initialize the context-aware AI helper.

        Args:
            user_id: Optional user ID for personalization
        """
        self.user_id = user_id
        self.logger = logging.getLogger(__name__)
        self.llm_service = LLMService()

        # Context cache (in-memory for now)
        self._context_cache = {}
        self._suggestions_cache = {}

    def load_context(self, context_type: str, **kwargs) -> Dict[str, Any]:
        """
        Load relevant context based on the current workflow.

        Args:
            context_type: Type of context to load (from ContextType)
            **kwargs: Additional context parameters

        Returns:
            Dictionary of contextual information
        """
        # Keyed by organisation; with no organisation in context nothing is cached.
        cache_key = org_scoped_cache_key(f"{context_type}:{str(kwargs)}")

        # Check cache first
        if cache_key and cache_key in self._context_cache:
            cached = self._context_cache[cache_key]
            if (datetime.utcnow() - cached["timestamp"]).seconds < 300:  # 5 min TTL
                return cached["data"]

        context = {}

        try:
            if context_type == ContextType.APPLICATION_FORM:
                context = self._load_application_context(**kwargs)
            elif context_type == ContextType.CAPABILITY_MAPPING:
                context = self._load_capability_context(**kwargs)
            elif context_type == ContextType.VENDOR_SELECTION:
                context = self._load_vendor_context(**kwargs)
            elif context_type == ContextType.ARCHIMATE_ELEMENT:
                context = self._load_archimate_context(**kwargs)
            elif context_type == ContextType.APQC_MAPPING:
                context = self._load_apqc_context(**kwargs)
            else:
                context = self._load_general_context(**kwargs)

            # Cache the context
            if cache_key:
                self._context_cache[cache_key] = {"data": context, "timestamp": datetime.utcnow()}

        except Exception as e:
            self.logger.error(f"Error loading context: {e}", exc_info=True)
            context = {"error": str(e)}

        return context

    def suggest_field_value(
        self, field_name: str, context: Dict[str, Any], partial_value: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Suggest a value for a form field based on context.

        Args:
            field_name: Name of the field to suggest for
            context: Current context information
            partial_value: Optional partial input from user

        Returns:
            Dictionary with suggestions and confidence scores
        """
        try:
            # Check suggestions cache
            cache_key = org_scoped_cache_key(f"{field_name}:{partial_value or ''}")
            if cache_key and cache_key in self._suggestions_cache:
                cached = self._suggestions_cache[cache_key]
                if (datetime.utcnow() - cached["timestamp"]).seconds < 60:
                    return cached["data"]

            suggestions = []

            # Get historical patterns
            historical_values = self._get_historical_values(field_name, context)

            # Add historical suggestions
            for value, frequency in historical_values[:5]:
                suggestions.append(
                    {
                        "value": value,
                        "confidence": min(0.9, frequency / 100),
                        "source": "historical",
                        "reason": f"Used {frequency} times previously",
                    }
                )

            # Add AI-generated suggestions if context allows
            if context.get("allow_ai_suggestions", True):
                ai_suggestions = self._generate_ai_suggestions(field_name, context, partial_value)
                suggestions.extend(ai_suggestions)

            # Sort by confidence
            suggestions.sort(key=lambda x: x["confidence"], reverse=True)

            result = {
                "field": field_name,
                "suggestions": suggestions[:5],  # Top 5
                "has_ai_suggestions": any(s["source"] == "ai" for s in suggestions),
                "timestamp": datetime.utcnow().isoformat(),
            }

            # Cache the result
            if cache_key:
                self._suggestions_cache[cache_key] = {"data": result, "timestamp": datetime.utcnow()}

            return result

        except Exception as e:
            self.logger.error(f"Error generating suggestions: {e}", exc_info=True)
            return {"field": field_name, "suggestions": [], "error": str(e)}

    def validate_with_ai(
        self, field_name: str, value: Any, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate a field value using AI reasoning.

        Args:
            field_name: Name of the field
            value: Value to validate
            context: Current context

        Returns:
            Validation result with AI reasoning
        """
        try:
            # Basic validation first
            basic_validation = self._basic_validation(field_name, value, context)
            if not basic_validation["valid"]:
                return basic_validation

            # AI-enhanced validation
            if context.get("enable_ai_validation", True):
                ai_validation = self._ai_validate(field_name, value, context)
                return ai_validation

            return basic_validation

        except Exception as e:
            self.logger.error(f"Error in AI validation: {e}", exc_info=True)
            return {"valid": True, "warnings": [f"Validation check failed: {str(e)}"]}  # Fail open

    def get_inline_help(self, field_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get contextual inline help for a field.

        Args:
            field_name: Name of the field
            context: Current context

        Returns:
            Inline help information
        """
        help_text = self._get_field_help(field_name, context)
        examples = self._get_field_examples(field_name, context)
        best_practices = self._get_best_practices(field_name, context)

        return {
            "field": field_name,
            "help_text": help_text,
            "examples": examples,
            "best_practices": best_practices,
            "related_fields": self._get_related_fields(field_name, context),
        }

    def _load_application_context(self, **kwargs) -> Dict[str, Any]:
        """Load context for application form."""
        context = {
            "context_type": ContextType.APPLICATION_FORM,
            "total_applications": ApplicationComponent.query.count() + 1,
        }

        # Get recent applications for pattern analysis
        recent_apps = (
            ApplicationComponent.query.order_by(ApplicationComponent.created_at.desc())
            .limit(10)
            .all()
        )

        context["recent_applications"] = [
            {
                "name": app.name,
                "status": app.status,
                "criticality": getattr(app, "criticality", None),
            }
            for app in recent_apps
        ]

        # Get common patterns
        context["common_statuses"] = self._get_common_values(ApplicationComponent, "status")
        context["common_criticality_levels"] = self._get_common_values(
            ApplicationComponent, "criticality"
        )

        return context

    def _load_capability_context(self, **kwargs) -> Dict[str, Any]:
        """Load context for capability mapping."""
        context = {
            "context_type": ContextType.CAPABILITY_MAPPING,
            "total_capabilities": UnifiedCapability.query.count(),
        }

        # Get capability statistics
        context["domains"] = (
            db.session.query(UnifiedCapability.domain, func.count(UnifiedCapability.id))
            .group_by(UnifiedCapability.domain)
            .all()
        )

        context["levels"] = (
            db.session.query(UnifiedCapability.level, func.count(UnifiedCapability.id))
            .group_by(UnifiedCapability.level)
            .all()
        )

        return context

    def _load_vendor_context(self, **kwargs) -> Dict[str, Any]:
        """Load context for vendor selection."""
        context = {
            "context_type": ContextType.VENDOR_SELECTION,
            "total_vendors": VendorOrganization.query.count(),
        }

        # Get vendor statistics
        context["vendor_types"] = (
            db.session.query(VendorOrganization.vendor_type, func.count(VendorOrganization.id))
            .group_by(VendorOrganization.vendor_type)
            .all()
        )

        context["strategic_tiers"] = (
            db.session.query(VendorOrganization.strategic_tier, func.count(VendorOrganization.id))
            .group_by(VendorOrganization.strategic_tier)
            .all()
        )

        return context

    def _load_archimate_context(self, **kwargs) -> Dict[str, Any]:
        """Load context for ArchiMate element creation."""
        return {
            "context_type": ContextType.ARCHIMATE_ELEMENT,
            "archimate_version": "3.2",
            "common_layers": ["business", "application", "technology"],
            "common_element_types": [
                "ApplicationComponent",
                "BusinessProcess",
                "TechnologyService",
            ],
        }

    def _load_apqc_context(self, **kwargs) -> Dict[str, Any]:
        """Load context for APQC process mapping."""
        # CM-01: this used to hardcode total_processes=1000 as an "Approximate"
        # fabricated value. Query the real count instead — None (not a fake
        # number) when the model can't be loaded, per the never-invent-data rule.
        total_processes = None
        try:
            from app.models.apqc_process import APQCProcess

            total_processes = APQCProcess.query.count()
        except Exception:
            pass
        return {
            "context_type": ContextType.APQC_MAPPING,
            "apqc_version": "7.3.0",
            "total_processes": total_processes,
        }

    def _load_general_context(self, **kwargs) -> Dict[str, Any]:
        """Load general application context."""
        return {
            "context_type": ContextType.GENERAL,
            "user_id": self.user_id,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _get_historical_values(
        self, field_name: str, context: Dict[str, Any]
    ) -> List[Tuple[str, int]]:
        """Get historical values for a field with frequency counts."""
        try:
            context_type = context.get("context_type")

            if context_type == ContextType.APPLICATION_FORM:
                if field_name == "status":
                    return self._get_common_values(ApplicationComponent, "status")
                elif field_name == "criticality":
                    return self._get_common_values(ApplicationComponent, "criticality")

            elif context_type == ContextType.CAPABILITY_MAPPING:
                if field_name == "domain":
                    return self._get_common_values(UnifiedCapability, "domain")
                elif field_name == "level":
                    return self._get_common_values(UnifiedCapability, "level")

            return []

        except Exception as e:
            self.logger.error(f"Error getting historical values: {e}")
            return []

    def _get_common_values(self, model, field: str, limit: int = 10) -> List[Tuple[str, int]]:
        """Get most common values for a field."""
        try:
            field_attr = getattr(model, field, None)
            if field_attr is None:
                return []

            results = (
                db.session.query(field_attr, func.count(field_attr))
                .group_by(field_attr)
                .order_by(func.count(field_attr).desc())
                .limit(limit)
                .all()
            )

            return [(str(value), count) for value, count in results if value is not None]

        except Exception as e:
            self.logger.error(f"Error getting common values: {e}")
            return []

    def _generate_ai_suggestions(
        self, field_name: str, context: Dict[str, Any], partial_value: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Generate AI-powered suggestions using LLM."""
        try:
            import json as _json
            context_type = context.get("context_type", "general")
            partial_hint = f" The user has typed '{partial_value}'." if partial_value else ""
            prompt = (
                f"You are helping fill in a '{field_name}' field in an enterprise architecture tool "
                f"(context: {context_type}).{partial_hint}\n"
                f"Suggest up to 3 sensible values for this field. "
                f"Respond ONLY as a JSON array of objects with keys: value, reason. "
                f"Example: [{{\"value\":\"Active\",\"reason\":\"Most common status\"}}]"
            )
            raw = LLMService.generate_from_prompt(prompt, use_cache=True)
            match = re.search(r'\[.*?\]', raw, re.DOTALL)
            if not match:
                return []
            items = _json.loads(match.group())
            results = []
            for item in items[:3]:
                if isinstance(item, dict) and "value" in item:
                    results.append({
                        "value": str(item["value"])[:200],
                        "confidence": 0.6,
                        "source": "ai",
                        "reason": str(item.get("reason", "AI suggestion"))[:200],
                    })
            return results
        except Exception as exc:
            self.logger.warning(f"_generate_ai_suggestions failed: {exc}")
            return []

    def _basic_validation(
        self, field_name: str, value: Any, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Perform basic validation."""
        valid = True
        errors = []
        warnings = []

        # Required field check
        if context.get("required_fields", []).count(field_name) > 0:
            if not value or (isinstance(value, str) and not value.strip()):
                valid = False
                errors.append(f"{field_name} is required")

        # Type-specific validation
        if isinstance(value, str):
            # Check length
            max_length = context.get("max_lengths", {}).get(field_name, 255)
            if len(value) > max_length:
                valid = False
                errors.append(f"{field_name} exceeds maximum length of {max_length}")

            # Check for SQL injection patterns
            if self._contains_sql_injection(value):
                valid = False
                errors.append(f"{field_name} contains potentially unsafe content")

        return {"valid": valid, "errors": errors, "warnings": warnings}

    def _ai_validate(self, field_name: str, value: Any, context: Dict[str, Any]) -> Dict[str, Any]:
        """AI-enhanced semantic validation using LLM."""
        try:
            import json as _json
            context_type = context.get("context_type", "general")
            prompt = (
                f"You are validating a '{field_name}' field value in an enterprise architecture tool "
                f"(context: {context_type}).\n"
                f"Value to validate: '{value}'\n"
                f"Respond ONLY as a JSON object with keys: valid (bool), issues (list of strings), "
                f"suggestions (list of strings).\n"
                f"Example: {{\"valid\":true,\"issues\":[],\"suggestions\":[\"Consider title case\"]}}"
            )
            raw = LLMService.generate_from_prompt(prompt, use_cache=False)
            match = re.search(r'\{.*?\}', raw, re.DOTALL)
            if not match:
                return {"valid": True, "ai_checked": True, "suggestions": []}
            parsed = _json.loads(match.group())
            return {
                "valid": bool(parsed.get("valid", True)),
                "ai_checked": True,
                "suggestions": [str(s)[:200] for s in parsed.get("suggestions", [])[:5]],
                "issues": [str(i)[:200] for i in parsed.get("issues", [])[:5]],
            }
        except Exception as exc:
            self.logger.warning(f"_ai_validate failed: {exc}")
            return {"valid": True, "ai_checked": True, "suggestions": []}

    def _contains_sql_injection(self, value: str) -> bool:
        """Check for SQL injection patterns."""
        dangerous_patterns = [
            r"('\s*(OR|AND)\s*'?\w+)",
            r"(--|\#|\/\*)",
            r"(DROP\s+TABLE|DELETE\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET)",
            r"(UNION\s+SELECT)",
            r"(xp_cmdshell|exec\s*\()",
        ]

        value_upper = value.upper()
        for pattern in dangerous_patterns:
            if re.search(pattern, value_upper, re.IGNORECASE):
                return True

        return False

    def _get_field_help(self, field_name: str, context: Dict[str, Any]) -> str:
        """Get contextual help text for a field."""
        # This would be loaded from a help text database or config
        help_texts = {
            "name": "Enter a descriptive name that clearly identifies this item",
            "status": "Select the current lifecycle status",
            "criticality": "Indicate the business criticality level",
            "description": "Provide a detailed description of the purpose and functionality",
        }

        return help_texts.get(field_name, "Enter a value for this field")

    def _get_field_examples(self, field_name: str, context: Dict[str, Any]) -> List[str]:
        """Get example values for a field."""
        # Would be context-specific in production
        examples_map = {
            "name": ["Customer Portal", "Order Management System", "HR Platform"],
            "status": ["active", "planned", "deprecated", "sunset"],
            "criticality": ["critical", "high", "medium", "low"],
        }

        return examples_map.get(field_name, [])

    def _get_best_practices(self, field_name: str, context: Dict[str, Any]) -> List[str]:
        """Get best practices for a field."""
        practices_map = {
            "name": [
                "Use clear, business-friendly names",
                "Avoid acronyms unless widely known",
                "Keep names under 50 characters",
            ],
            "description": [
                "Include purpose, scope, and key features",
                "Mention primary users and stakeholders",
                "Note any dependencies or integrations",
            ],
        }

        return practices_map.get(field_name, [])

    def _get_related_fields(self, field_name: str, context: Dict[str, Any]) -> List[str]:
        """Get fields related to the current field."""
        # Field dependencies and relationships
        related_map = {
            "name": ["description", "status"],
            "status": ["criticality", "lifecycle_stage"],
            "criticality": ["business_owner", "technical_owner"],
        }

        return related_map.get(field_name, [])


# Singleton instance
_ai_helper_instance = None


def get_ai_helper(user_id: Optional[int] = None) -> ContextAwareAIHelper:
    """Get or create the AI helper instance."""
    global _ai_helper_instance

    if _ai_helper_instance is None or (
        _ai_helper_instance.user_id != user_id and user_id is not None
    ):
        _ai_helper_instance = ContextAwareAIHelper(user_id=user_id)

    return _ai_helper_instance
