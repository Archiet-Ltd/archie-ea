"""

AI-Powered Application-Capability Mapper

Automatically analyzes applications and suggests BusinessCapability mappings
based on semantic analysis of application names, descriptions, and context.

Usage:
    service = ApplicationCapabilityMapperService()

    # Analyze single application
    suggestions = service.suggest_capabilities_for_application(app_id)

    # Bulk map all unmapped applications
    result = service.bulk_map_applications(confidence_threshold=0.7)
"""
import json
import logging
from typing import Dict, List, Optional

from app import db
from app.datetime_helpers import utcnow
from app.models.application_component_fast import ApplicationComponent
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability
from app.services.llm_service import LLMService

# from app.services.decorators import transactional  # Temporarily disabled

logger = logging.getLogger(__name__)


class ApplicationCapabilityMapperService:
    """AI service for automatic application-to-capability mapping."""

    @staticmethod
    # @transactional  # Temporarily disabled
    def suggest_capabilities_for_application(application_id: int, top_n: int = 5) -> List[Dict]:
        """
        Suggest BusinessCapabilities for a given application using AI analysis.

        Args:
            application_id: ID of the ApplicationComponent
            top_n: Number of top suggestions to return

        Returns:
            List of capability suggestions with confidence scores:
            [
                {
                    'capability_id': int,
                    'capability_name': str,
                    'confidence_score': float (0.0 - 1.0),
                    'support_level': str ('primary', 'secondary', 'partial'),
                    'reasoning': str,
                    'suggested_maturity': int (1 - 5)
                }
            ]
        """
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            raise ValueError(f"Application {application_id} not found")

        # Get all active capabilities
        capabilities = UnifiedCapability.active().filter_by(status="defined").all()
        if not capabilities:
            logger.warning("No active capabilities found in system")
            return []

        # Build capability context for LLM
        capability_context = "\n".join(
            [
                f"- ID {cap.id}: {cap.name} | Domain: {cap.domain.code if cap.domain else 'N/A'} | Desc: {cap.description[:100] if cap.description else 'No description'}..."
                for cap in capabilities[:50]  # Limit to avoid token overflow
            ]
        )

        # Build application context
        app_context = {
            "name": app.name,
            "type": app.component_type or "Unknown",
            "description": app.description or "No description",
            "domain": app.business_domain or "Not specified",
            "owner": app.business_owner or "Not specified",
            "platform": getattr(app, "deployment_environment", "Unknown"),  # model-safety-ok: polymorphic (field on fast-init variant only)
            "lifecycle": getattr(app, "lifecycle_status", "Unknown"),  # model-safety-ok: polymorphic (field on full model only, fast-init uses lifecycle_stage)
        }

        # Build LLM prompt
        prompt = f"""Analyze this application and suggest which business capabilities it supports:

APPLICATION:
- Name: {app_context['name']}
- Type: {app_context['type']}
- Description: {app_context['description']}
- Business Domain: {app_context['domain']}
- Owner: {app_context['owner']}
- Platform: {app_context['platform']}
- Lifecycle: {app_context['lifecycle']}

AVAILABLE BUSINESS CAPABILITIES:
{capability_context}

TASK:
Identify the TOP {top_n} business capabilities this application supports or enables.
For each suggestion:
1. Determine confidence_score (0.0 - 1.0): How confident are you this is correct?
2. Determine support_level:
   - 'primary': App is the main system for this capability
   - 'secondary': App provides backup/supplemental support
   - 'partial': App covers some aspects of the capability
3. Provide clear reasoning
4. Suggest maturity_level (1 - 5): How mature is the app's support for this capability?

RESPOND WITH VALID JSON ONLY (no markdown):
{{
    "suggestions": [
        {{
            "capability_id": 123,
            "capability_name": "Customer Relationship Management",
            "confidence_score": 0.95,
            "support_level": "primary",
            "reasoning": "Application name and description indicate it's a CRM system...",
            "suggested_maturity": 4
        }}
    ]
}}"""

        try:
            # Get best available provider (respects user preference + intelligent selection)
            provider, model = LLMService._get_configured_provider()
            response, _interaction = LLMService._call_llm_with_failover(
                prompt=prompt,
                model=model,
                provider=provider,
            )

            # Parse JSON response
            result = json.loads(response.strip())
            suggestions = result.get("suggestions", [])

            # Sort by confidence score
            suggestions.sort(key=lambda x: x.get("confidence_score", 0), reverse=True)

            logger.info(
                f"Generated {len(suggestions)} capability suggestions for application {application_id}"
            )
            return suggestions[:top_n]

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {e}")
            logger.debug(f"Raw response: {response}")
            return []
        except Exception as e:
            logger.error(f"Error suggesting capabilities: {e}")
            return []

    @staticmethod
    # @transactional  # Temporarily disabled
    def create_mapping_from_suggestion(
        application_id: int, suggestion: Dict, created_by: Optional[str] = None
    ) -> UnifiedApplicationCapabilityMapping:
        """
        Create a UnifiedApplicationCapabilityMapping from an AI suggestion.

        Args:
            application_id: ID of the ApplicationComponent
            suggestion: Suggestion dict from suggest_capabilities_for_application()
            created_by: Username or identifier of who created the mapping

        Returns:
            Created UnifiedApplicationCapabilityMapping instance
        """
        # Validate application exists
        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            raise ValueError(f"Application {application_id} not found")

        # Validate capability exists
        capability_id = suggestion.get("capability_id")
        capability = db.session.get(UnifiedCapability, capability_id)
        if not capability:
            raise ValueError(f"Capability {capability_id} not found")

        # Check if mapping already exists - FIXED P0-1: Ensure correct column mapping
        # unified_capability_id gets capability_id, application_component_id gets application_id
        existing = UnifiedApplicationCapabilityMapping.query.filter_by(
            unified_capability_id=capability_id,
            application_component_id=application_id
        ).first()

        if existing:
            logger.info(
                f"Mapping already exists: App {application_id} → Capability {capability_id}"
            )
            return existing

        # Create new mapping
        mapping = UnifiedApplicationCapabilityMapping(
            unified_capability_id=capability_id,
            application_component_id=application_id,
            support_level=suggestion.get("support_level", "partial"),
            coverage_percentage=75,  # Default coverage
            support_quality=suggestion.get("suggested_maturity", 3),
            relationship_type="enables",
            relationship_strength=4,
            gap_status="partially_covered"
            if suggestion.get("support_level") == "partial"
            else "fully_covered",
            assessment_notes=json.dumps(
                {
                    "ai_generated": True,
                    "confidence_score": suggestion.get("confidence_score"),
                    "reasoning": suggestion.get("reasoning"),
                    "created_by": created_by or "AI Auto-Mapper",
                    "created_at": utcnow().isoformat(),
                }
            ),
        )

        db.session.add(mapping)
        db.session.commit()

        logger.info(
            f"Created mapping: App {application_id} → Capability {capability_id} ({mapping.support_level})"
        )
        return mapping

    @staticmethod
    def bulk_map_applications(
        confidence_threshold: float = 0.7,
        max_applications: Optional[int] = None,
        auto_create: bool = False,
        created_by: Optional[str] = None,
    ) -> Dict:
        """
        Bulk analyze and map unmapped applications to capabilities.

        Args:
            confidence_threshold: Only suggest mappings above this confidence (0.0 - 1.0)
            max_applications: Limit number of applications to process (None = all)
            auto_create: If True, automatically create mappings above threshold
            created_by: Username for auto-created mappings

        Returns:
            {
                'total_analyzed': int,
                'applications_with_suggestions': int,
                'mappings_created': int,
                'suggestions': [
                    {
                        'application_id': int,
                        'application_name': str,
                        'suggested_capabilities': [...]
                    }
                ]
            }
        """
        # Find unmapped applications - FIXED P0-2: Use correct column name
        mapped_app_ids = {
            m.application_component_id for m in db.session.query(
                UnifiedApplicationCapabilityMapping.application_component_id
            ).distinct().all()
        }
        unmapped_apps = (
            ApplicationComponent.query.filter(
                ~ApplicationComponent.id.in_(mapped_app_ids) if mapped_app_ids else True
            )
            .limit(max_applications)
            .all()
        )

        logger.info(f"Starting bulk mapping for {len(unmapped_apps)} unmapped applications")

        total_analyzed = 0
        apps_with_suggestions = 0
        mappings_created = 0
        all_suggestions = []

        for app in unmapped_apps:
            try:
                suggestions = (
                    ApplicationCapabilityMapperService.suggest_capabilities_for_application(
                        application_id=app.id, top_n=3  # Top 3 suggestions per app
                    )
                )

                # Filter by confidence threshold
                high_confidence = [
                    s for s in suggestions if s.get("confidence_score", 0) >= confidence_threshold
                ]

                if high_confidence:
                    apps_with_suggestions += 1

                    app_result = {
                        "application_id": app.id,
                        "application_name": app.name,
                        "suggested_capabilities": high_confidence,
                    }

                    # Auto-create mappings if enabled
                    if auto_create:
                        for suggestion in high_confidence:
                            try:
                                ApplicationCapabilityMapperService.create_mapping_from_suggestion(
                                    application_id=app.id,
                                    suggestion=suggestion,
                                    created_by=created_by,
                                )
                                mappings_created += 1
                            except Exception as e:
                                logger.error(f"Failed to create mapping: {e}")

                    all_suggestions.append(app_result)

                total_analyzed += 1

                # Log progress every 10 apps
                if total_analyzed % 10 == 0:
                    logger.info(
                        f"Progress: {total_analyzed}/{len(unmapped_apps)} applications analyzed"
                    )

            except Exception as e:
                logger.error(f"Error analyzing application {app.id} ({app.name}): {e}")
                continue

        result = {
            "total_analyzed": total_analyzed,
            "applications_with_suggestions": apps_with_suggestions,
            "mappings_created": mappings_created,
            "suggestions": all_suggestions,
        }

        logger.info(
            f"Bulk mapping complete: {mappings_created} mappings created from {total_analyzed} applications"
        )
        return result

    @staticmethod
    def generate_solution_requirements_from_apqc(
        apqc_mappings: List, application_id: int
    ) -> List[Dict]:
        """
        Generate solution requirements from APQC process mappings.

        Converts APQC PCF processes mapped to an application into actionable
        solution requirements with priorities, effort estimates, and dependencies.

        Args:
            apqc_mappings: List of APQC process mapping objects or dicts
            application_id: ID of the application these requirements are for

        Returns:
            List of requirement dictionaries with:
                - title: Requirement title
                - description: Detailed description
                - priority: high/medium/low
                - category: functional/technical/integration/data
                - estimated_effort: Effort in hours
                - dependencies: List of dependent requirement IDs
                - apqc_process_id: Source APQC process ID
                - apqc_process_name: Source APQC process name
        """
        from app.models.application_layer import ApplicationComponent

        try:
            requirements = []

            # Get application details for context
            app = ApplicationComponent.query.get(application_id)
            if not app:
                logger.error(f"Application {application_id} not found")
                return []

            for mapping in apqc_mappings:
                # Handle both object and dict formats
                if hasattr(mapping, "process_name"):  # model-safety-ok: polymorphic (handles both ORM objects and dicts)
                    process_name = mapping.process_name
                    process_description = getattr(mapping, "process_description", "")  # model-safety-ok: polymorphic (handles both ORM objects and dicts)
                    process_id = getattr(mapping, "id", None) or getattr(  # model-safety-ok: polymorphic (handles both ORM objects and dicts)
                        mapping, "apqc_process_id", None  # model-safety-ok: polymorphic
                    )
                    pcf_id = getattr(mapping, "pcf_id", "")  # model-safety-ok: polymorphic (handles both ORM objects and dicts)
                    level = getattr(mapping, "level", 3)  # model-safety-ok: polymorphic (handles both ORM objects and dicts)
                else:
                    process_name = mapping.get(
                        "process_name", mapping.get("name", "Unknown Process")
                    )
                    process_description = mapping.get(
                        "process_description", mapping.get("description", "")
                    )
                    process_id = mapping.get("id", mapping.get("apqc_process_id"))
                    pcf_id = mapping.get("pcf_id", "")
                    level = mapping.get("level", 3)

                # Calculate priority based on APQC process level (higher level = higher priority)
                if level <= 2:
                    priority = "high"
                    base_effort = 160  # 4 weeks
                elif level == 3:
                    priority = "medium"
                    base_effort = 80  # 2 weeks
                else:
                    priority = "low"
                    base_effort = 40  # 1 week

                # Determine category based on process name keywords
                process_lower = process_name.lower()
                if any(kw in process_lower for kw in ["data", "information", "record", "document"]):
                    category = "data"
                elif any(
                    kw in process_lower for kw in ["integrate", "interface", "connect", "api"]
                ):
                    category = "integration"
                elif any(
                    kw in process_lower
                    for kw in ["system", "technical", "infrastructure", "platform"]
                ):
                    category = "technical"
                else:
                    category = "functional"

                # Create requirement
                requirement = {
                    "title": f"Implement {process_name}",
                    "description": process_description
                    or f"Implement support for APQC process: {process_name} ({pcf_id})",
                    "priority": priority,
                    "category": category,
                    "estimated_effort": base_effort,
                    "dependencies": [],  # Will be populated based on APQC hierarchy
                    "apqc_process_id": str(process_id) if process_id else None,
                    "apqc_process_name": process_name,
                    "pcf_id": pcf_id,
                    "application_id": application_id,
                    "application_name": app.name,
                    "status": "draft",
                    "created_from": "apqc_mapping",
                }

                requirements.append(requirement)

            # Sort by priority (high -> medium -> low)
            priority_order = {"high": 0, "medium": 1, "low": 2}
            requirements.sort(key=lambda x: priority_order.get(x["priority"], 3))

            return requirements

        except Exception as e:
            logger.error(f"Error generating requirements from APQC: {str(e)}")
            return []
