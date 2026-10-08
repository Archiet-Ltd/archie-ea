"""Provider Integration Layer for the ArchiMate Inference Engine.

Defines the LayerProvider protocol, adapter classes that wrap existing services,
and the PROVIDER_REGISTRY mapping element types to providers.
All adapters use lazy instantiation to avoid creating LLM clients at import time.

Pass 3 (semantic refinement) uses LLM via APISettings DB keys to generate
architect-quality names and descriptions for inferred elements.
"""
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared LLM refinement helper
# ---------------------------------------------------------------------------

def _get_llm_client():
    """Get an OpenAI client using API key from the database.

    Returns (client, model_name) or (None, None) if unavailable.
    """
    try:
        from app.models.models import APISettings
        setting = APISettings.query.filter_by(provider='openai', enabled=True).first()
        if not setting or not setting.api_key:
            # Fallback to anthropic
            setting = APISettings.query.filter_by(provider='anthropic', enabled=True).first()
            if setting and setting.api_key:
                import anthropic
                client = anthropic.Anthropic(api_key=setting.api_key)
                model = setting.default_model or 'claude-sonnet-4-20250514'
                return client, model, 'anthropic'
            return None, None, None
        import openai
        client = openai.OpenAI(api_key=setting.api_key)
        model = setting.default_model or 'gpt-4o'
        # Handle comma-separated model lists
        if ',' in model:
            model = model.split(',')[0].strip()
        return client, model, 'openai'
    except Exception as e:
        logger.warning("Failed to initialize LLM client: %s", e)
        return None, None, None


def _llm_refine_element(element_type: str, current_name: str,
                        context_description: str = "",
                        layer_hint: str = "") -> Optional[dict]:
    """Call the LLM to produce an architect-quality name and description.

    Returns {"name": str, "description": str} or None on failure.
    """
    client, model, provider_type = _get_llm_client()
    if not client:
        logger.debug("No LLM client available for refinement")
        return None

    prompt = f"""You are an enterprise architect naming ArchiMate 3.2 elements.

Given this auto-generated element, produce a proper architecture name and one-sentence description.

Element type: {element_type}
Current name: {current_name}
Layer: {layer_hint or 'unknown'}
Context: {context_description or 'inferred from solution goal'}

Rules:
- Name must be 3-8 words, professional, no prefixes like "Goal:" or "Capability for"
- Name should reflect what this element IS in enterprise architecture, not how it was generated
- Description is one sentence explaining the element's purpose
- Use standard TOGAF/ArchiMate terminology
- For Stakeholder: use role titles (e.g. "Chief Information Officer", "Enterprise Architecture Board")
- For Driver: use business concerns (e.g. "Operational Cost Reduction", "Regulatory Compliance")
- For Goal: use strategic objectives (e.g. "Consolidate CRM Platforms Across EMEA")
- For Capability: use business capability names (e.g. "Customer Relationship Management", "Order Fulfillment")
- For BusinessProcess: use process names (e.g. "Lead-to-Opportunity Conversion", "Contract Renewal")
- For ApplicationComponent: use system names (e.g. "Salesforce CRM", "SAP ERP Central Component")
- For TechnologyService: use technology service names (e.g. "Cloud Hosting Service", "API Gateway")

Return ONLY valid JSON:
{{"name": "...", "description": "..."}}"""

    try:
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        LLMService._guard_provider_call(
            provider_type,
            model,
            prompt=prompt,
        )
        if provider_type == 'anthropic':
            response = client.messages.create(
                model=model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
        else:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.3,
            )
            text = response.choices[0].message.content.strip()

        # Parse JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        import json
        result = json.loads(text)
        name = result.get("name", "").strip()
        desc = result.get("description", "").strip()
        if name and len(name) <= 100:
            return {"name": name, "description": desc}
        return None
    except Exception as e:
        logger.warning("LLM refinement failed for %s '%s': %s", element_type, current_name, e)
        return None


@dataclass
class GeneratedElement:
    """Normalized output from any provider."""
    name: str
    description: Optional[str] = None
    subtype: Optional[str] = None
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "description": self.description,
            "subtype": self.subtype,
        }
        if self.metadata:
            result.update(self.metadata)
        return result


@runtime_checkable
class LayerProvider(Protocol):
    """Contract that all providers must satisfy."""

    def generate_element(self, element_type: str, context: dict) -> Optional[GeneratedElement]:
        """Create a new element of the given type."""

    def refine_element(self, element: Any, context: dict) -> Any:
        """Refine element name/description via LLM."""

    def validate_element(self, element: Any) -> float:
        """Return confidence score 0.0-1.0."""


class MotivationProviderAdapter:
    """Adapts MotivationLayerService to LayerProvider protocol."""

    def __init__(self):
        self._service = None

    @property
    def service(self):
        if self._service is None:
            try:
                from app.modules.architecture.services.motivation_layer_service import MotivationLayerService
                self._service = MotivationLayerService()
            except ImportError:
                logger.warning("MotivationLayerService not available")
        return self._service

    def generate_element(self, element_type: str, context: dict) -> Optional[GeneratedElement]:
        from_node = context.get("from")
        from_name = getattr(from_node, "name", "") if from_node else ""
        if not from_name:
            return None
        name = f"{element_type} for {from_name}"
        return GeneratedElement(
            name=name,
            description=f"LLM-generated {element_type} inferred from {from_name}",
        )

    def refine_element(self, element: Any, context: dict) -> Any:
        etype = getattr(element, "element_type", "") or ""
        name = getattr(element, "name", "") or ""
        desc = getattr(element, "description", "") or ""
        refined = _llm_refine_element(etype, name, desc, "Motivation")
        if refined:
            element.name = refined["name"]
            if refined.get("description"):
                element.description = refined["description"]
        return element

    def validate_element(self, element: Any) -> float:
        return 0.5


class BusinessProviderAdapter:
    """Adapts BusinessLayerService to LayerProvider protocol."""

    def __init__(self):
        self._service = None

    @property
    def service(self):
        if self._service is None:
            try:
                from app.modules.architecture.services.business_layer_service import BusinessLayerService
                self._service = BusinessLayerService()
            except ImportError:
                logger.warning("BusinessLayerService not available")
        return self._service

    def generate_element(self, element_type: str, context: dict) -> Optional[GeneratedElement]:
        from_node = context.get("from")
        from_name = getattr(from_node, "name", "") if from_node else ""
        if not from_name:
            return None
        name = f"{element_type} for {from_name}"
        return GeneratedElement(
            name=name,
            description=f"LLM-generated {element_type} inferred from {from_name}",
        )

    def refine_element(self, element: Any, context: dict) -> Any:
        etype = getattr(element, "element_type", "") or ""
        name = getattr(element, "name", "") or ""
        desc = getattr(element, "description", "") or ""
        refined = _llm_refine_element(etype, name, desc, "Business")
        if refined:
            element.name = refined["name"]
            if refined.get("description"):
                element.description = refined["description"]
        return element

    def validate_element(self, element: Any) -> float:
        return 0.5


class ApplicationProviderAdapter:
    """Adapts ApplicationLayerService to LayerProvider protocol."""

    def __init__(self):
        self._service = None

    @property
    def service(self):
        if self._service is None:
            try:
                from app.modules.architecture.services.application_layer_service import ApplicationLayerService
                self._service = ApplicationLayerService()
            except ImportError:
                logger.warning("ApplicationLayerService not available")
        return self._service

    def generate_element(self, element_type: str, context: dict) -> Optional[GeneratedElement]:
        from_node = context.get("from")
        from_name = getattr(from_node, "name", "") if from_node else ""
        if not from_name:
            return None
        return GeneratedElement(
            name=f"{element_type} for {from_name}",
            description=f"LLM-generated {element_type} inferred from {from_name}",
        )

    def refine_element(self, element: Any, context: dict) -> Any:
        etype = getattr(element, "element_type", "") or ""
        name = getattr(element, "name", "") or ""
        desc = getattr(element, "description", "") or ""
        refined = _llm_refine_element(etype, name, desc, "Application")
        if refined:
            element.name = refined["name"]
            if refined.get("description"):
                element.description = refined["description"]
        return element

    def validate_element(self, element: Any) -> float:
        return 0.5


class TechnologyProviderAdapter:
    """Adapts TechnologyLayerService to LayerProvider protocol."""

    def __init__(self):
        self._service = None

    @property
    def service(self):
        if self._service is None:
            try:
                from app.modules.architecture.services.technology_layer_service import TechnologyLayerService
                self._service = TechnologyLayerService()
            except ImportError:
                logger.warning("TechnologyLayerService not available")
        return self._service

    def generate_element(self, element_type: str, context: dict) -> Optional[GeneratedElement]:
        from_node = context.get("from")
        from_name = getattr(from_node, "name", "") if from_node else ""
        if not from_name:
            return None
        return GeneratedElement(
            name=f"{element_type} for {from_name}",
            description=f"LLM-generated {element_type} inferred from {from_name}",
        )

    def refine_element(self, element: Any, context: dict) -> Any:
        etype = getattr(element, "element_type", "") or ""
        name = getattr(element, "name", "") or ""
        desc = getattr(element, "description", "") or ""
        refined = _llm_refine_element(etype, name, desc, "Technology")
        if refined:
            element.name = refined["name"]
            if refined.get("description"):
                element.description = refined["description"]
        return element

    def validate_element(self, element: Any) -> float:
        return 0.5


class GapProviderAdapter:
    """Adapts GapDiscoveryService to LayerProvider protocol."""

    def __init__(self):
        self._service = None

    @property
    def service(self):
        if self._service is None:
            try:
                from app.services.gap_discovery_service import GapDiscoveryService
                self._service = GapDiscoveryService()
            except ImportError:
                logger.warning("GapDiscoveryService not available")
        return self._service

    def generate_element(self, element_type: str, context: dict) -> Optional[GeneratedElement]:
        from_node = context.get("from")
        from_name = getattr(from_node, "name", "") if from_node else ""
        if not from_name:
            return None
        return GeneratedElement(
            name=f"{element_type} for {from_name}",
            description=f"Implementation gap identified from {from_name}",
        )

    def refine_element(self, element: Any, context: dict) -> Any:
        return element

    def validate_element(self, element: Any) -> float:
        return 0.5


class MigrationProviderAdapter:
    """Adapts GapResolutionService to LayerProvider protocol."""

    def __init__(self):
        self._service = None

    @property
    def service(self):
        if self._service is None:
            try:
                from app.modules.architecture.services.gap_resolution_service import GapResolutionService
                self._service = GapResolutionService()
            except ImportError:
                logger.warning("GapResolutionService not available")
        return self._service

    def generate_element(self, element_type: str, context: dict) -> Optional[GeneratedElement]:
        from_node = context.get("from")
        from_name = getattr(from_node, "name", "") if from_node else ""
        if not from_name:
            return None
        return GeneratedElement(
            name=f"{element_type} for {from_name}",
            description=f"Migration element for {from_name}",
        )

    def refine_element(self, element: Any, context: dict) -> Any:
        return element

    def validate_element(self, element: Any) -> float:
        return 0.5


# ---- Provider Registry ----
# All entries use adapter classes with lazy instantiation.
_motivation = MotivationProviderAdapter()
_business = BusinessProviderAdapter()
_application = ApplicationProviderAdapter()
_technology = TechnologyProviderAdapter()
_gap = GapProviderAdapter()
_migration = MigrationProviderAdapter()

PROVIDER_REGISTRY: dict[str, LayerProvider] = {
    # Motivation
    "Stakeholder": _motivation, "Driver": _motivation, "Assessment": _motivation,
    "Goal": _motivation, "Outcome": _motivation, "Principle": _motivation,
    "Requirement": _motivation, "Constraint": _motivation,
    # Strategy
    "CourseOfAction": _motivation, "Resource": _business, "ValueStreamStage": _business,
    # Business
    "Capability": _business, "BusinessProcess": _business,
    "BusinessFunction": _business, "BusinessService": _business,
    "BusinessEvent": _business, "BusinessRole": _business, "BusinessObject": _business,
    # Application
    "ApplicationService": _application, "ApplicationComponent": _application,
    "ApplicationFunction": _application, "DataObject": _application,
    # Technology
    "TechnologyService": _technology, "TechnologyFunction": _technology,
    "TechnologyComponent": _technology, "Node": _technology,
    "Artifact": _technology, "CommunicationNetwork": _technology,
    # Implementation & Migration
    "Gap": _gap, "WorkPackage": _migration, "Deliverable": _migration, "Plateau": _migration,
}
