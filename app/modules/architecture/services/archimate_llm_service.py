"""
ArchiMate LLM Service Extension

Extends the base LLM service with ArchiMate 3.2 specific generation and analysis capabilities.
Uses specialized prompts and validation for ArchiMate compliance.
"""

import json
import logging
import os
from typing import Dict, List, Optional, Tuple


from app import db

_FAST_INIT = os.getenv("APP_FAST_INIT", "0") == "1"


try:
    # Normal runtime: import from the full model export surface.
    from app.models import (  # dead-code-ok
        ArchiMateElement,
        ArchiMateRelationship,
        BusinessCapability,
        LLMInteraction,
        PipelineStage,
    )
except Exception:  # pragma: no cover
    # Fast-init / E2E: app.models intentionally exports only a small subset.
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship  # dead-code-ok

    BusinessCapability = None  # type: ignore[assignment]
    LLMInteraction = None  # type: ignore[assignment]
    PipelineStage = None  # type: ignore[assignment]


if _FAST_INIT:
    LLMService = None  # type: ignore[assignment]
else:
    try:
        from app.services.llm_service import LLMService
    except Exception:  # pragma: no cover
        LLMService = None  # type: ignore[assignment]
from app.services.llm_cache import get_cache

from app.services.archimate.archimate_prompts import (
    ARCHIMATE_SYSTEM_PROMPT,
    GENERATE_ARCHIMATE_FROM_REQUIREMENTS,
    build_archimate_prompt,
)
from app.services.archimate.archimate_validation_engine import ArchiMateValidationEngine
from app.models.archimate_element_types import StandardTypeTable

if _FAST_INIT:

    def log_event(*_args, **_kwargs):
        return None

    def get_applicable_override(*_args, **_kwargs):
        return None

else:
    from app.services.autogen_service import get_applicable_override, log_event

logger = logging.getLogger(__name__)


class ArchiMateLLMService:
    """
    Enhanced LLM service specialized for ArchiMate 3.2 architecture generation and analysis.

    This service provides:
    - ArchiMate-compliant architecture generation
    - Metamodel validation
    - Pattern detection and recommendation
    - Viewpoint generation
    - Impact analysis
    - Quality assessment
    """

    def __init__(self):
        """Initialize with ArchiMate validation engine."""
        if _FAST_INIT:
            raise RuntimeError("ArchiMateLLMService is disabled when APP_FAST_INIT=1")
        if LLMService is None:
            raise RuntimeError("LLMService import failed; ArchiMateLLMService unavailable")
        self.validator = ArchiMateValidationEngine()
        self.llm_service = LLMService()

    def generate_archimate_from_requirements(
        self,
        requirements: str,
        context: Optional[str] = None,
        model_name: Optional[str] = None,
        pipeline_stage_id: Optional[int] = None,
        validate: bool = True,
        target_layer: str = "complete",
    ) -> Tuple[Dict, Optional[LLMInteraction]]:
        """
        Generate complete ArchiMate 3.2 model from business requirements.

        Args:
            requirements: Business requirements text
            context: Additional context (existing systems, constraints, etc.)
            model_name: Optional name for the architecture model
            pipeline_stage_id: Optional pipeline stage for tracking
            validate: Whether to validate output against ArchiMate metamodel

        Returns:
            Tuple of (generated_model_dict, llm_interaction)
            Model dict contains: {
                'model_name': str,
                'model_description': str,
                'elements': List[Dict],
                'relationships': List[Dict],
                'rationale': str,
                'validation_results': Dict (if validate=True),
                'cached': bool (indicates if response was cached)
            }
        """
        cache = get_cache()
        context = context or ""
        model_name = model_name or "Generated Architecture"

        # Consult overrides: if an override disables generation for the requested layer/target type, log and abort
        try:
            ov = get_applicable_override(framework=None, domain=None, target_type="ArchiMateModel")
        except Exception:  # noqa: BLE001 — table may not exist in all deployments
            logger.debug("ArchiMateLLMService: autogen_override table unavailable, skipping override check")
            db.session.rollback()
            ov = None
        if ov and ov.enabled and ov.rule and "disable" in (ov.rule or "").lower():
            try:
                log_event(
                    "autogen_skip",
                    "ArchiMateLLMService",
                    target_type="ArchiMateModel",
                    status="skipped",
                    message="Override disabled ArchiMate generation",
                )
            except Exception:  # noqa: BLE001
                logger.debug("ArchiMateLLMService: skipping autogen_skip event log")
            return (
                {
                    "model_name": model_name,
                    "elements": [],
                    "relationships": [],
                    "error": "Auto-generation disabled by override",
                },
                None,
            )

        try:
            log_event(
                "autogen_start",
                "ArchiMateLLMService",
                target_type="ArchiMateModel",
                status="started",
                message=f"Starting generation for model {model_name}",
            )
        except Exception:  # noqa: BLE001
            logger.debug("ArchiMateLLMService: generation_event table unavailable, skipping event log")

        # Check cache first if request is cacheable
        if cache.is_cacheable(requirements, context):
            cached_response = cache.get(requirements, context, model_name)
            if cached_response:
                logger.info("✓ Using cached ArchiMate generation response")
                cached_response["cached"] = True
                return cached_response, None

        # Build prompt with layer-specific focus if requested
        prompt = build_archimate_prompt(
            GENERATE_ARCHIMATE_FROM_REQUIREMENTS,
            requirements=requirements,
            context=context,
            target_layer=target_layer,
        )

        # Get provider and model
        provider, model = LLMService._get_configured_provider()

        # Handle system prompt based on provider
        # For Anthropic: _call_anthropic automatically adds ARCHIMATE_SYSTEM_PROMPT
        # For OpenAI: need to prepend system prompt to user prompt
        if provider == "anthropic":
            full_prompt = prompt  # Don't prepend - _call_anthropic handles it
        else:
            full_prompt = f"{ARCHIMATE_SYSTEM_PROMPT}\n\n{prompt}"

        # Call LLM
        response_text, interaction = LLMService._call_llm(
            prompt=full_prompt, model=model, provider=provider, pipeline_stage_id=pipeline_stage_id
        )

        # Parse JSON response
        try:
            # Clean response (remove markdown code blocks if present)
            cleaned_response = response_text.strip()
            if cleaned_response.startswith("```json"):
                cleaned_response = cleaned_response[7:]
            if cleaned_response.startswith("```"):
                cleaned_response = cleaned_response[3:]
            if cleaned_response.endswith("```"):
                cleaned_response = cleaned_response[:-3]
            cleaned_response = cleaned_response.strip()

            # Extract JSON from response that may have text preamble
            if "{" in cleaned_response:
                # Find first { and last }
                start_idx = cleaned_response.find("{")
                end_idx = cleaned_response.rfind("}") + 1
                if start_idx != -1 and end_idx > start_idx:
                    cleaned_response = cleaned_response[start_idx:end_idx]

            # Try to parse - if it fails due to truncation, try to fix common issues
            try:
                model_data = json.loads(cleaned_response)
            except json.JSONDecodeError as parse_error:
                # Try to fix truncated JSON by closing arrays/objects
                logger.warning(f"Initial JSON parse failed, attempting recovery: {parse_error}")

                # Count unclosed braces and brackets
                open_braces = cleaned_response.count("{") - cleaned_response.count("}")
                open_brackets = cleaned_response.count("[") - cleaned_response.count("]")

                # Add closing characters
                fixed_response = cleaned_response
                if open_brackets > 0:
                    fixed_response += "]" * open_brackets
                if open_braces > 0:
                    fixed_response += "}" * open_braces

                # Try parsing again
                try:
                    model_data = json.loads(fixed_response)
                    logger.info("✓ Successfully recovered truncated JSON response")
                except json.JSONDecodeError:
                    # If still fails, raise original error
                    raise parse_error

            # Validate required fields
            if "elements" not in model_data:
                raise ValueError("Response missing 'elements' field")
            if "relationships" not in model_data:
                model_data["relationships"] = []

            # Validate against ArchiMate metamodel if requested
            if validate:
                validation_results = self._validate_generated_model(model_data)
                model_data["validation_results"] = validation_results

                if not validation_results["is_valid"]:
                    logger.warning(
                        f"Generated ArchiMate model has validation errors: "
                        f"{len(validation_results['errors'])} errors"
                    )

            # Cache successful response
            model_data["cached"] = False
            if cache.is_cacheable(requirements, context):
                cache.set(requirements, context, model_name, model_data)

            logger.info(
                f"✓ Generated ArchiMate model: {len(model_data.get('elements', []))} elements, "
                f"{len(model_data.get('relationships', []))} relationships"
            )
            try:
                log_event(
                    "autogen_success",
                    "ArchiMateLLMService",
                    target_type="ArchiMateModel",
                    status="success",
                    message=f"Generated {len(model_data.get('elements', []))} elements",
                )
            except Exception:  # noqa: BLE001
                logger.debug("ArchiMateLLMService: skipping autogen_success event log")

            return model_data, interaction

        except json.JSONDecodeError as e:
            logger.error(f"✗ Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Response was: {response_text[:500]}...")

            # Check if response contains API error
            if "authentication_error" in response_text:
                error_msg = (
                    "Authentication failed - Invalid API key. Please check your API settings."
                )
            elif "rate_limit" in response_text.lower():
                error_msg = "Rate limit exceeded. Please try again later."
            elif "error" in response_text.lower():
                error_msg = f"API Error: {response_text[:200]}"
            else:
                error_msg = f"JSON parse error: {str(e)}. Response: {response_text[:100]}"

            try:
                log_event(
                    "autogen_error",
                    "ArchiMateLLMService",
                    target_type="ArchiMateModel",
                    status="failure",
                    message=error_msg,
                )
            except Exception:  # noqa: BLE001
                logger.debug("ArchiMateLLMService: skipping autogen_error event log")
            return {
                "model_name": model_name or "Generated Architecture",
                "elements": [],
                "relationships": [],
                "error": error_msg,
            }, interaction
        except Exception as e:
            logger.error(f"✗ Error generating ArchiMate model: {e}")
            try:
                log_event(
                    "autogen_error",
                    "ArchiMateLLMService",
                    target_type="ArchiMateModel",
                    status="failure",
                    message=str(e),
                )
            except Exception:  # noqa: BLE001
                logger.debug("ArchiMateLLMService: skipping autogen_error event log")
            return {
                "model_name": model_name or "Generated Architecture",
                "elements": [],
                "relationships": [],
                "error": str(e),
            }, interaction

    def _validate_generated_model(self, model_data: Dict) -> Dict:
        """
        Validate generated model against ArchiMate 3.2 metamodel.

        ENHANCED: Uses comprehensive ArchiMateValidationEngine with 182 relationship combinations
        - Validates all element types against ArchiMate 3.2 specification
        - Validates relationships against complete relationship matrix
        - Auto-corrects layer assignments
        - Provides specific error messages and suggestions
        - Performance optimized for bulk validation

        Args:
            model_data: Generated model dictionary

        Returns:
            Validation results dictionary with detailed errors and warnings
        """
        elements = model_data.get("elements", [])
        relationships = model_data.get("relationships", [])

        # Use the comprehensive validation engine
        validation_result = self.validator.validate_model(elements, relationships)

        # Convert to legacy format for backward compatibility
        results = self.validator.get_validation_summary(validation_result)

        # Add validation statistics
        results["validation_details"] = {
            "elements_validated": len(elements),
            "relationships_validated": len(relationships),
            "critical_errors": len(validation_result.errors),
            "minor_warnings": len(validation_result.warnings),
            "relationship_matrix_size": self.validator.get_relationship_matrix_size(),
            "archimate_version": "3.2",
            "validation_engine": "ArchiMateValidationEngine",
        }

        # Log validation results
        if validation_result.is_valid:
            logger.info(
                f"✅ ArchiMate validation passed: {len(elements)} elements, {len(relationships)} relationships"
            )
        else:
            logger.warning(
                f"❌ ArchiMate validation failed: {len(validation_result.errors)} errors, {len(validation_result.warnings)} warnings"
            )
            for error in validation_result.errors[:5]:  # Log first 5 errors
                logger.warning(f"   - {error}")

        return results

    def create_element_from_dict(
        self, element_data: Dict, application_id: int = None, created_by: str = "ai_import"
    ) -> ArchiMateElement:
        """
        Create ArchiMate element from AI-generated dict.

        Args:
            element_data: {
                "name": "Order Management Service",
                "type": "ApplicationService",
                "layer": "Application",
                "description": "Handles order processing"
            }
            application_id: Source application (for linking)
            created_by: Creator identifier

        Returns:
            Created ArchiMateElement or None on error
        """
        try:
            # Validate required fields
            if not element_data.get("name"):
                raise ValueError("Element name is required")

            if not element_data.get("type"):
                raise ValueError("Element type is required")

            # Validate element type against ArchiMate 3.2 specification
            valid_types = [
                # Business Layer
                "BusinessActor",
                "BusinessRole",
                "BusinessCollaboration",
                "BusinessInterface",
                "BusinessProcess",
                "BusinessFunction",
                "BusinessInteraction",
                "BusinessEvent",
                "BusinessService",
                "BusinessObject",
                "Representation",
                "Meaning",
                "Value",
                "Product",
                "Contract",
                # Application Layer
                "ApplicationComponent",
                "ApplicationCollaboration",
                "ApplicationInterface",
                "ApplicationFunction",
                "ApplicationInteraction",
                "ApplicationProcess",
                "ApplicationEvent",
                "ApplicationService",
                "DataObject",
                # Technology Layer
                "Node",
                "Device",
                "SystemSoftware",
                "TechnologyCollaboration",
                "TechnologyInterface",
                "TechnologyFunction",
                "TechnologyProcess",
                "TechnologyInteraction",
                "TechnologyEvent",
                "TechnologyService",
                "Artifact",
                "Path",
                "CommunicationNetwork",
                # Physical Layer
                "Equipment",
                "Facility",
                "DistributionNetwork",
                "Material",
                # Motivation & Strategy
                "Stakeholder",
                "Driver",
                "Assessment",
                "Goal",
                "Outcome",
                "Principle",
                "Requirement",
                "Constraint",
                "CourseOfAction",
                "Capability",
                "Resource",
                "ValueStream",
                # Implementation & Migration
                "WorkPackage",
                "Deliverable",
                "ImplementationEvent",
                "Gap",
                "Plateau",
            ]

            element_type = element_data.get("type")
            if element_type not in valid_types:
                logger.warning(f"Unknown element type: {element_type}, proceeding anyway")

            # Auto-correct layer assignment based on element type
            expected_layer = self._get_layer_for_element_type(element_type)
            element_data["layer"] = expected_layer

            # Create element
            element = ArchiMateElement(
                name=element_data["name"],
                type=element_type,
                layer=element_data["layer"],
                description=element_data.get("description", ""),
                # Note: source_application_id field may not exist in core model
                # generation_method='ai_llm',  # Add if field exists
                # created_by=created_by,  # Add if field exists
                # created_at=datetime.utcnow()  # Add if field exists
            )

            db.session.add(element)
            db.session.flush()  # Get ID without committing

            logger.info(
                f"Created ArchiMate element: {element.name} ({element.type}) in {element.layer} layer"
            )
            return element

        except Exception as e:
            logger.error(f"Failed to create ArchiMate element from dict: {e}")
            db.session.rollback()
            return None

    def create_elements_batch(
        self,
        elements_data: List[Dict],
        relationships_data: List[Dict] = None,
        application_id: int = None,
    ) -> Dict:
        """
        Batch create ArchiMate elements and relationships with validation and rollback.

        Args:
            elements_data: List of element dictionaries
            relationships_data: List of relationship dictionaries
            application_id: Source application ID

        Returns:
            Dict with creation results and statistics
        """
        try:
            created_elements = {}
            created_relationships = []
            errors = []

            # Create elements
            for elem_data in elements_data:
                try:
                    elem = self.create_element_from_dict(elem_data, application_id)
                    if elem:
                        created_elements[elem_data["name"]] = elem
                    else:
                        errors.append(
                            f"Failed to create element: {elem_data.get('name', 'Unknown')}"
                        )
                except Exception as e:
                    errors.append(
                        f"Element creation error for {elem_data.get('name', 'Unknown')}: {str(e)}"
                    )

            # Create relationships if provided
            relationships_created = 0
            if relationships_data:
                for rel_data in relationships_data:
                    try:
                        source_name = rel_data.get("source")
                        target_name = rel_data.get("target")
                        rel_type = rel_data.get("type")

                        source = created_elements.get(source_name)
                        target = created_elements.get(target_name)

                        if not source or not target:
                            errors.append(
                                f"Relationship source/target not found: {source_name} -> {target_name}"
                            )
                            continue

                        # Validate relationship
                        if not self._is_valid_relationship(source.type, target.type, rel_type):
                            errors.append(
                                f"Invalid relationship: {source.type} -> {target.type} via {rel_type}"
                            )
                            continue

                        # Create relationship
                        relationship = ArchiMateRelationship(
                            type=rel_type,
                            source_id=source.id,
                            target_id=target.id
                            # Add other fields as needed
                        )
                        db.session.add(relationship)
                        created_relationships.append(relationship)
                        relationships_created += 1

                    except Exception as e:
                        errors.append(f"Relationship creation error: {str(e)}")

            # Commit transaction
            db.session.commit()

            return {
                "success": True,
                "elements_created": len(created_elements),
                "relationships_created": relationships_created,
                "errors": errors,
                "element_ids": {name: elem.id for name, elem in created_elements.items()},
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Batch creation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "elements_created": 0,
                "relationships_created": 0,
            }

    def _get_layer_for_element_type(self, element_type: str) -> str:
        """Determine the appropriate ArchiMate 3.2 layer for an element type."""
        layer_mapping = StandardTypeTable({
            # Motivation Layer
            "Stakeholder": "Motivation",
            "Driver": "Motivation",
            "Assessment": "Motivation",
            "Goal": "Motivation",
            "Outcome": "Motivation",
            "Principle": "Motivation",
            "Requirement": "Motivation",
            "Constraint": "Motivation",
            "Meaning": "Motivation",
            "Value": "Motivation",
            # Strategy Layer
            "Capability": "Strategy",
            "CourseOfAction": "Strategy",
            "ValueStream": "Strategy",
            "Resource": "Strategy",
            # Business Layer
            "BusinessActor": "Business",
            "BusinessRole": "Business",
            "BusinessCollaboration": "Business",
            "BusinessInterface": "Business",
            "BusinessProcess": "Business",
            "BusinessFunction": "Business",
            "BusinessInteraction": "Business",
            "BusinessEvent": "Business",
            "BusinessService": "Business",
            "BusinessObject": "Business",
            "Representation": "Business",
            "Product": "Business",
            "Contract": "Business",
            # Application Layer
            "ApplicationComponent": "Application",
            "ApplicationCollaboration": "Application",
            "ApplicationInterface": "Application",
            "ApplicationFunction": "Application",
            "ApplicationInteraction": "Application",
            "ApplicationProcess": "Application",
            "ApplicationEvent": "Application",
            "ApplicationService": "Application",
            "DataObject": "Application",
            # Technology Layer
            "Node": "Technology",
            "Device": "Technology",
            "SystemSoftware": "Technology",
            "TechnologyCollaboration": "Technology",
            "TechnologyInterface": "Technology",
            "Path": "Technology",
            "CommunicationNetwork": "Technology",
            "TechnologyFunction": "Technology",
            "TechnologyProcess": "Technology",
            "TechnologyInteraction": "Technology",
            "TechnologyEvent": "Technology",
            "TechnologyService": "Technology",
            "Artifact": "Technology",
            # Physical Layer
            "Equipment": "Physical",
            "Facility": "Physical",
            "DistributionNetwork": "Physical",
            "Material": "Physical",
            # Implementation & Migration
            "WorkPackage": "Implementation",
            "Deliverable": "Implementation",
            "ImplementationEvent": "Implementation",
            "Gap": "Implementation",
            "Plateau": "Implementation",
        })

        return layer_mapping.get(element_type, "Business")  # Default to Business layer

    def _is_valid_relationship(
        self, source_type: str, target_type: str, relationship_type: str
    ) -> bool:
        """
        Basic relationship validation using the validation engine.
        """
        validation_result = self.validator.validate_relationship(
            source_type, target_type, relationship_type
        )
        return validation_result.is_valid

    def _serialize_elements(self, elements) -> List[Dict]:
        """Serialize ArchiMateElement objects to dictionaries."""
        return [
            {
                "id": e.id,
                "name": e.name,
                "type": e.type,
                "layer": e.layer,
                "description": e.description or "",
            }
            for e in elements
        ]

    def _serialize_relationships(self, relationships) -> List[Dict]:
        """Serialize ArchiMateRelationship objects to dictionaries."""
        return [
            {
                "id": r.id,
                "source": r.source_element.name if r.source_element else "Unknown",
                "target": r.target_element.name if r.target_element else "Unknown",
                "type": r.type,
                "description": r.description or "",
            }
            for r in relationships
        ]

    def _clean_json_response(self, response: str) -> str:
        """Clean LLM response to extract valid JSON."""
        cleaned = response.strip()

        # Remove markdown code blocks
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

        return cleaned.strip()

    def _calculate_combined_score(self, rule_validation: Dict, ai_assessment: Dict) -> int:
        """Calculate combined quality score from rule and AI validation."""
        # Rule-based score (0 - 50 points)
        total_elements = rule_validation["summary"]["total_elements"]
        valid_elements = rule_validation["summary"]["valid_elements"]
        total_rels = rule_validation["summary"]["total_relationships"]
        valid_rels = rule_validation["summary"]["valid_relationships"]

        if total_elements == 0:
            rule_score = 0
        else:
            element_ratio = valid_elements / total_elements if total_elements > 0 else 0
            rel_ratio = valid_rels / total_rels if total_rels > 0 else 1
            rule_score = int(((element_ratio + rel_ratio) / 2) * 50)

        # AI score (0 - 50 points)
        ai_score = 0
        if "error" not in ai_assessment and "overall_score" in ai_assessment:
            ai_score = int(ai_assessment["overall_score"] * 0.5)

        return rule_score + ai_score
