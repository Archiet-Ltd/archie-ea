"""
Enhanced ArchiMate Extraction Service - Enterprise Grade

Features:
- Multi-pass AI extraction (broad → deep)
- Quality validation gates
- Implicit stakeholder detection
- Hierarchical goal decomposition
- Course of Action inference
- Post-extraction enhancement
"""

import json
import logging
from typing import Dict, List, Optional  # dead-code-ok

from app.services.archimate.archimate_prompts import GENERATE_ARCHIMATE_FROM_REQUIREMENTS
from app.services.core.cache_service import cache_service
from app.services.core.error_handler import (  # dead-code-ok
    ArchiMateValidationError,
    ErrorSeverity,
    handle_service_errors,
    log_performance,
)
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class EnhancedArchiMateExtractor:
    """Enterprise-grade ArchiMate extraction with quality validation"""

    def __init__(self):
        self.llm_service = LLMService()
        self.cache = cache_service

    def _call_llm_with_provider(
        self, prompt: str, provider: Optional[str] = None, max_tokens: int = 4000
    ) -> str:
        """Helper method to call LLM with best available provider (respects user preference)."""
        provider_name, model = LLMService._get_configured_provider()

        response, _ = LLMService._call_llm(
            prompt=prompt,
            model=model,
            provider=provider_name,
            user_id=None,
            project_id=None,
            max_tokens=max_tokens,
        )
        return response

    @handle_service_errors(severity=ErrorSeverity.HIGH)
    @log_performance(threshold_ms=5000)
    def extract_comprehensive_architecture(
        self,
        document_text: str,
        document_type: str = "requirements",
        context: str = "",
        provider: Optional[str] = None,
    ) -> Dict:
        """
        Multi-pass extraction with quality validation

        CACHED: Results cached for 6 hours to reduce LLM costs

        Args:
            document_text: Source document content
            document_type: Type of document (requirements, regulation, technical_spec)
            context: Additional context for extraction
            provider: LLM provider to use (e.g., 'openrouter', 'anthropic', 'openai'). If None, uses default.

        Returns:
            Enhanced architecture data with quality metrics
        """

        # Validate inputs
        if not document_text or not document_text.strip():
            raise ArchiMateValidationError(
                "Document text cannot be empty",
                severity=ErrorSeverity.MEDIUM,
                context={"document_type": document_type},
            )

        if len(document_text) < 100:
            logger.warning(
                f"Document text is very short ({len(document_text)} chars) - quality may be poor"
            )

        # Check cache first (6 hour TTL for document extraction)
        cache_key = self.cache._generate_cache_key(
            "archimate_extraction",
            document_text[:1000],  # First 1000 chars as key
            document_type,
            context,
        )

        cached_result = self.cache.get(cache_key)
        if cached_result:
            logger.info("Using cached ArchiMate extraction")
            logger.info("\n[CACHE] Using cached extraction result")
            return cached_result

        logger.info("\n[PHASE 1] Initial Comprehensive Extraction...")

        # Pass 1: Comprehensive extraction using enhanced prompt
        initial_extraction = self._extract_with_enhanced_prompt(
            document_text, document_type, context, provider
        )

        # Validate quality
        quality_report = self._validate_extraction_quality(initial_extraction)

        logger.info(
            f"\n[QUALITY CHECK] Initial extraction quality: {quality_report['overall_score']}/100"
        )

        # If quality is insufficient, perform targeted enhancements
        if quality_report["overall_score"] < 70:
            logger.info("[PHASE 2] Quality insufficient - performing targeted enhancements...")

            enhanced_extraction = self._enhance_extraction(
                document_text, initial_extraction, quality_report, provider
            )
        else:
            enhanced_extraction = initial_extraction

        # Phase 3: Post-processing enhancements
        logger.info("\n[PHASE 3] Post-processing enhancements...")

        final_extraction = self._post_process_extraction(enhanced_extraction)

        # Final quality check
        final_quality = self._validate_extraction_quality(final_extraction)

        logger.info(f"\n[FINAL QUALITY] Score: {final_quality['overall_score']}/100")
        logger.info(f"  - Total elements: {final_quality['metrics']['total_elements']}")
        logger.info(f"  - Motivation Layer: {final_quality['metrics']['motivation_count']}")
        logger.info(f"  - Goals: {final_quality['metrics']['goal_count']}")
        logger.info(f"  - Stakeholders: {final_quality['metrics']['stakeholder_count']}")
        logger.info(f"  - Course of Action: {final_quality['metrics']['course_of_action_count']}")

        # Add quality metadata
        final_extraction["quality_metrics"] = final_quality

        # Cache the result (6 hour TTL)
        self.cache.set(cache_key, final_extraction, ttl=21600)
        logger.info("Cached ArchiMate extraction result")

        return final_extraction

    def _extract_with_enhanced_prompt(
        self, document_text: str, document_type: str, context: str, provider: Optional[str] = None
    ) -> Dict:
        """Extract using the enhanced comprehensive prompt"""

        full_context = f"Document type: {document_type}\n{context}"

        prompt = GENERATE_ARCHIMATE_FROM_REQUIREMENTS.format(
            requirements=document_text, context=full_context
        )

        # Use specified provider or default
        # Get best available provider (respects user preference + intelligent selection)
        provider_name, model = LLMService._get_configured_provider()

        max_tokens_limit = LLMService.get_max_tokens_limit(provider_name, model, 16000)
        logger.info(
            f"Calling LLM with provider={provider_name}, model={model}, max_tokens={max_tokens_limit}"
        )
        logger.info(
            f"Prompt length: {len(prompt)} chars, document text length: {len(document_text)} chars"
        )

        try:
            response = self._call_llm_with_provider(prompt, provider, max_tokens=max_tokens_limit)
            logger.info(f"LLM response received, length: {len(response)} chars")
            logger.info(f"Response preview (first 500 chars): {response[:500]}")
        except Exception as e:
            error_str = str(e).lower()
            # Check if it's a Hugging Face error
            is_hf_error = (
                "hugging face" in error_str
                or "gpt2" in error_str
                or "context window" in error_str
                or "too long" in error_str
                or provider_name == "huggingface"
            )

            if is_hf_error:
                logger.warning(f"⚠️ Hugging Face error detected: {e}")
                logger.warning("🔄 Attempting automatic fallback to paid provider...")

                # Try to find a paid provider
                from app.models.models import APISettings

                fallback_provider = None
                fallback_model = None

                for paid_provider in ["anthropic", "openai", "gemini", "azure"]:
                    paid_settings = APISettings.query.filter_by(
                        provider=paid_provider, enabled=True
                    ).first()
                    if paid_settings and paid_settings.api_key and paid_settings.default_model:
                        fallback_provider = paid_provider
                        fallback_model = paid_settings.default_model
                        logger.info(
                            f"✅ Found paid provider: {fallback_provider} with model: {fallback_model}"
                        )
                        break

                if fallback_provider:
                    # Retry with paid provider
                    logger.info(f"🔄 Retrying with {fallback_provider}...")
                    provider_name = fallback_provider
                    model = fallback_model
                    max_tokens_limit = LLMService.get_max_tokens_limit(provider_name, model, 16000)
                    response = self._call_llm_with_provider(
                        prompt, fallback_provider, max_tokens=max_tokens_limit
                    )
                    logger.info(f"✅ Paid provider response received, length: {len(response)} chars")
                else:
                    logger.error("❌ No paid provider available for fallback")
                    raise ValueError(
                        f"Hugging Face failed and no paid provider available. "
                        f"Error: {e}. Please configure a paid provider (Claude/GPT - 4) in API Settings."
                    )
            else:
                # Re-raise non-Hugging Face errors
                raise

        # Parse JSON
        try:
            # Try to extract JSON from markdown code blocks
            json_text = None
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                if json_end > json_start:
                    json_text = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                if json_end > json_start:
                    json_text = response[json_start:json_end].strip()
            else:
                json_text = response.strip()

            if not json_text:
                raise ValueError("No JSON content found in response")

            logger.info(f"Extracted JSON text length: {len(json_text)} chars")
            logger.info(f"JSON preview: {json_text[:300]}")

            parsed = json.loads(json_text)

            # Validate parsed structure
            if not isinstance(parsed, dict):
                raise ValueError(f"Parsed JSON is not a dictionary, got: {type(parsed)}")

            elements = parsed.get("elements", [])
            relationships = parsed.get("relationships", [])

            logger.info(
                f"Successfully parsed: {len(elements)} elements, {len(relationships)} relationships"
            )

            if len(elements) == 0:
                logger.warning(
                    "⚠️ LLM returned 0 elements - this may indicate an issue with the prompt or LLM response"
                )
                logger.warning(f"Full response: {response[:2000]}")

            return parsed

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"❌ JSON parsing failed: {e}")
            logger.error(f"Response length: {len(response)}")
            logger.error(f"Response preview: {response[:1000]}")

            # Try to extract any JSON-like content even if parsing failed
            import re

            json_match = re.search(r'\{[\s\S]*"elements"[\s\S]*\}', response)
            if json_match:
                try:
                    logger.info("Attempting to parse extracted JSON pattern...")
                    parsed = json.loads(json_match.group(0))
                    logger.info(
                        f"Successfully parsed extracted JSON: {len(parsed.get('elements', []))} elements"
                    )
                    return parsed
                except Exception as e:
                    logger.debug("Failed to parse extracted JSON: %s", e)

            return {
                "elements": [],
                "relationships": [],
                "metadata": {
                    "error": f"JSON parsing failed: {str(e)}",
                    "error_type": "parsing_error",
                    "raw_response": response[:2000],
                    "response_length": len(response),
                },
            }

    def _validate_extraction_quality(self, extraction: Dict) -> Dict:
        """
        Validate extraction quality against enterprise standards

        Returns quality report with score and gaps
        """

        elements = extraction.get("elements", [])
        relationships = extraction.get("relationships", [])

        # Count by layer
        layer_counts = {}
        type_counts = {}

        for elem in elements:
            layer = elem.get("layer", "unknown")
            elem_type = elem.get("type", "unknown")

            layer_counts[layer] = layer_counts.get(layer, 0) + 1

            if layer == "motivation":
                type_counts[f"motivation_{elem_type}"] = (
                    type_counts.get(f"motivation_{elem_type}", 0) + 1
                )
            elif layer == "strategy":
                type_counts[f"strategy_{elem_type}"] = (
                    type_counts.get(f"strategy_{elem_type}", 0) + 1
                )

        # Calculate quality metrics
        metrics = {
            "total_elements": len(elements),
            "total_relationships": len(relationships),
            "motivation_count": layer_counts.get("motivation", 0),
            "strategy_count": layer_counts.get("strategy", 0),
            "stakeholder_count": type_counts.get("motivation_Stakeholder", 0),
            "driver_count": type_counts.get("motivation_Driver", 0),
            "goal_count": type_counts.get("motivation_Goal", 0),
            "requirement_count": type_counts.get("motivation_Requirement", 0),
            "principle_count": type_counts.get("motivation_Principle", 0),
            "constraint_count": type_counts.get("motivation_Constraint", 0),
            "capability_count": type_counts.get("strategy_Capability", 0),
            "course_of_action_count": type_counts.get("strategy_CourseOfAction", 0),
        }

        # Quality checks (0 - 100 scale)
        checks = {
            "total_elements_check": min(100, (metrics["total_elements"] / 50.0) * 100),
            "motivation_layer_check": min(100, (metrics["motivation_count"] / 15.0) * 100),
            "goal_quantity_check": min(100, (metrics["goal_count"] / 7.0) * 100),
            "stakeholder_quantity_check": min(100, (metrics["stakeholder_count"] / 5.0) * 100),
            "course_of_action_check": min(100, (metrics["course_of_action_count"] / 3.0) * 100),
            "driver_check": min(100, (metrics["driver_count"] / 5.0) * 100),
        }

        overall_score = sum(checks.values()) / len(checks)

        # Identify gaps
        gaps = []

        if metrics["total_elements"] < 50:
            gaps.append(f"Need {50 - metrics['total_elements']} more elements (target: 50+)")

        if metrics["motivation_count"] < 15:
            gaps.append(f"Need {15 - metrics['motivation_count']} more Motivation Layer elements")

        if metrics["goal_count"] < 7:
            gaps.append(f"Need {7 - metrics['goal_count']} more Goals")

        if metrics["stakeholder_count"] < 5:
            gaps.append(f"Need {5 - metrics['stakeholder_count']} more Stakeholders")

        if metrics["course_of_action_count"] == 0:
            gaps.append("CRITICAL: No Course of Action elements - need 3 - 5")

        return {"overall_score": overall_score, "metrics": metrics, "checks": checks, "gaps": gaps}

    def _enhance_extraction(
        self,
        document_text: str,
        initial_extraction: Dict,
        quality_report: Dict,
        provider: Optional[str] = None,
    ) -> Dict:
        """
        Perform targeted extraction to fill gaps
        """

        metrics = quality_report["metrics"]

        elements = initial_extraction.get("elements", [])
        relationships = initial_extraction.get("relationships", [])

        # Enhancement 1: Extract more stakeholders if needed
        if metrics["stakeholder_count"] < 5:
            logger.info("[ENHANCE] Extracting implicit stakeholders...")
            additional_stakeholders = self._extract_implicit_stakeholders(
                document_text, elements, provider
            )
            elements.extend(additional_stakeholders)
            logger.info(f"  Added {len(additional_stakeholders)} implicit stakeholders")

        # Enhancement 2: Extract hierarchical goals if needed
        if metrics["goal_count"] < 7:
            logger.info("[ENHANCE] Extracting hierarchical goals...")
            additional_goals = self._extract_hierarchical_goals(document_text, elements, provider)
            elements.extend(additional_goals)
            logger.info(f"  Added {len(additional_goals)} hierarchical goals")

        # Enhancement 3: Extract Course of Action if missing
        if metrics["course_of_action_count"] == 0:
            logger.info("[ENHANCE] Extracting Course of Action elements...")
            course_of_actions = self._extract_course_of_action(document_text, elements, provider)
            elements.extend(course_of_actions)
            logger.info(f"  Added {len(course_of_actions)} Course of Action elements")

        # Enhancement 4: Extract Principles and Constraints
        if metrics["principle_count"] < 3:
            logger.info("[ENHANCE] Extracting Principles...")
            principles = self._extract_principles(document_text, elements, provider)
            elements.extend(principles)
            logger.info(f"  Added {len(principles)} Principles")

        return {
            "model_name": initial_extraction.get("model_name", "Enhanced Architecture"),
            "model_description": initial_extraction.get("model_description", ""),
            "elements": elements,
            "relationships": relationships,
            "rationale": initial_extraction.get("rationale", ""),
        }

    def _extract_implicit_stakeholders(
        self, document_text: str, existing_elements: List[Dict], provider: Optional[str] = None
    ) -> List[Dict]:
        """Extract stakeholders not explicitly mentioned but implied by context"""

        existing_stakeholders = [
            e["name"] for e in existing_elements if e.get("type") == "Stakeholder"
        ]

        prompt = f"""Analyze this document and identify IMPLICIT stakeholders not explicitly mentioned.

Document:
{document_text[:3000]}

Already identified stakeholders:
{', '.join(existing_stakeholders)}

Extract 5 - 10 ADDITIONAL implicit stakeholders who would be affected by or interested in these requirements.

For regulatory/compliance documents, consider:
- Regulatory bodies and certification agencies
- Customs and border authorities
- Consumer advocacy groups
- Industry associations
- Supply chain partners
- Auditors and compliance officers
- Government ministries
- International standards organizations

Return ONLY valid JSON array:
[
  {{
    "name": "Stakeholder Name",
    "type": "Stakeholder",
    "layer": "motivation",
    "description": "Why this stakeholder is involved (2 - 3 sentences)"
  }}
]
"""

        response = self._call_llm_with_provider(prompt, provider, max_tokens=4000)

        try:
            if "[" in response and "]" in response:
                json_start = response.find("[")
                json_end = response.rfind("]") + 1
                json_text = response[json_start:json_end].strip()
                parsed = json.loads(json_text)
                return parsed if isinstance(parsed, list) else []
            else:
                return []
        except Exception as e:
            logger.error("Failed to parse implicit stakeholders response: %s", e, exc_info=True)
            return []

    def _extract_hierarchical_goals(
        self, document_text: str, existing_elements: List[Dict], provider: Optional[str] = None
    ) -> List[Dict]:
        """Extract hierarchical goal decomposition (strategic → tactical → operational)"""

        existing_goals = [e["name"] for e in existing_elements if e.get("type") == "Goal"]

        prompt = f"""Analyze this document and extract a HIERARCHICAL goal structure.

Document:
{document_text[:3000]}

Already identified goals:
{', '.join(existing_goals)}

Create a 3 - level goal hierarchy with 10 - 15 total goals:
1. **Strategic goals** (2 - 3): High-level business outcomes
2. **Tactical goals** (4 - 6): Operational improvements to achieve strategic goals
3. **Operational goals** (4 - 6): Specific measurable targets

Return ONLY valid JSON array:
[
  {{
    "name": "Goal Name",
    "type": "Goal",
    "layer": "motivation",
    "description": "What this goal achieves",
    "properties": {{
      "goal_level": "strategic|tactical|operational",
      "parent_goal": "Name of parent goal (if applicable)"
    }}
  }}
]
"""

        response = self._call_llm_with_provider(prompt, provider, max_tokens=4000)

        try:
            if "[" in response and "]" in response:
                json_start = response.find("[")
                json_end = response.rfind("]") + 1
                json_text = response[json_start:json_end].strip()
                parsed = json.loads(json_text)
                return parsed if isinstance(parsed, list) else []
            else:
                return []
        except Exception as e:
            logger.error("Failed to parse hierarchical goals response: %s", e, exc_info=True)
            return []

    def _extract_course_of_action(
        self, document_text: str, existing_elements: List[Dict], provider: Optional[str] = None
    ) -> List[Dict]:
        """Extract Course of Action elements (strategic initiatives)"""

        prompt = f"""Analyze this document and identify strategic COURSES OF ACTION - specific initiatives or programs to achieve the goals.

Document:
{document_text[:3000]}

A Course of Action is:
- A strategic initiative or program
- A specific implementation approach
- A transformation roadmap

Examples:
- "Implement Blockchain-Based Product Registry"
- "Establish Industry-Wide Data Standards Committee"
- "Deploy Automated Compliance Verification System"
- "Create Digital Product Passport Mobile App"

Extract 3 - 7 Course of Action elements.

Return ONLY valid JSON array:
[
  {{
    "name": "Course of Action Name",
    "type": "CourseOfAction",
    "layer": "strategy",
    "description": "What this initiative will accomplish (2 - 3 sentences)"
  }}
]
"""

        response = self._call_llm_with_provider(prompt, provider, max_tokens=4000)

        try:
            if "[" in response and "]" in response:
                json_start = response.find("[")
                json_end = response.rfind("]") + 1
                json_text = response[json_start:json_end].strip()
                parsed = json.loads(json_text)
                return parsed if isinstance(parsed, list) else []
            else:
                return []
        except Exception as e:
            logger.error("Failed to parse course of action response: %s", e, exc_info=True)
            return []

    def _extract_principles(
        self, document_text: str, existing_elements: List[Dict], provider: Optional[str] = None
    ) -> List[Dict]:
        """Extract architectural and business principles"""

        prompt = f"""Analyze this document and identify PRINCIPLES - normative properties that guide design and implementation.

Document:
{document_text[:3000]}

A Principle is:
- An architectural principle (e.g., "Data Privacy by Design", "API-First Architecture")
- A business principle (e.g., "Transparency in Supply Chains", "Lifecycle Accountability")
- A guiding constraint on how solutions are designed

Extract 3 - 7 Principles.

Return ONLY valid JSON array:
[
  {{
    "name": "Principle Name",
    "type": "Principle",
    "layer": "motivation",
    "description": "What this principle means and why it matters"
  }}
]
"""

        response = self._call_llm_with_provider(prompt, provider, max_tokens=4000)

        try:
            if "[" in response and "]" in response:
                json_start = response.find("[")
                json_end = response.rfind("]") + 1
                json_text = response[json_start:json_end].strip()
                parsed = json.loads(json_text)
                return parsed if isinstance(parsed, list) else []
            else:
                return []
        except Exception as e:
            logger.error("Failed to parse principles response: %s", e, exc_info=True)
            return []

    def _post_process_extraction(self, extraction: Dict) -> Dict:
        """
        Post-process extraction to infer missing relationships
        """

        elements = extraction.get("elements", [])
        relationships = extraction.get("relationships", [])

        # Create element lookup
        element_by_name = {e["name"]: e for e in elements}

        # Infer hierarchical goal relationships
        goals = [e for e in elements if e.get("type") == "Goal"]

        for goal in goals:
            parent_goal_name = goal.get("properties", {}).get("parent_goal")
            if parent_goal_name and parent_goal_name in element_by_name:
                # Add realization relationship: child goal realizes parent goal
                rel = {
                    "source_name": goal["name"],
                    "target_name": parent_goal_name,
                    "type": "Realization",
                    "description": "Sub-goal realizes parent goal",
                }
                if rel not in relationships:
                    relationships.append(rel)

        extraction["relationships"] = relationships

        return extraction
