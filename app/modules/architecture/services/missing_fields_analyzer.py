"""
Missing Fields Analyzer Service

Analyzes extracted elements to identify missing fields and uses LLM
to generate/pre-populate missing application details.
"""

import logging
from typing import Any, Dict, List, Optional

from app.services.llm_service import LLMService
from app.models.archimate_element_types import is_application_kind

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


class MissingFieldsAnalyzer:
    """
    Analyzes and generates missing fields for application elements.

    Features:
    - Identifies missing required/optional fields
    - Uses LLM to generate missing details
    - Compares uploaded data with existing DB records
    - Provides gap analysis
    """

    # Required fields for ApplicationComponent
    REQUIRED_FIELDS = ["name"]

    # Important fields (should be populated)
    IMPORTANT_FIELDS = [
        "description",
        "component_type",
        "deployment_status",
        "business_domain",
        "technology_stack",
        "business_owner",
    ]

    # Optional but valuable fields
    OPTIONAL_FIELDS = [
        "application_category",
        "architecture_style",
        "version",
        "product_manager",
        "technical_lead",
        "development_team",
        "programming_languages",
        "frameworks",
        "primary_database",
        "business_criticality",
        "user_count",
    ]

    def analyze_missing_fields(
        self, elements: List[Dict], element_type: str = "ApplicationComponent"
    ) -> List[Dict]:
        """
        Analyze elements and identify missing fields.

        Returns elements with 'missing_fields' and 'completeness_score' added.
        """
        analyzed = []

        for element in elements:
            element_name = element.get("name", "")
            if not element_name:
                continue

            # Get field mapping for this element type
            field_mapping = self._get_field_mapping(element_type)

            # Analyze missing fields
            missing_required = []
            missing_important = []
            missing_optional = []
            present_fields = []

            # Check required fields
            for field in self.REQUIRED_FIELDS:
                mapped_field = field_mapping.get(field, field)
                if not element.get(mapped_field) and not element.get("properties", {}).get(
                    f"custom_{field}"
                ):
                    missing_required.append(field)
                else:
                    present_fields.append(field)

            # Check important fields
            for field in self.IMPORTANT_FIELDS:
                mapped_field = field_mapping.get(field, field)
                if not element.get(mapped_field) and not element.get("properties", {}).get(
                    f"custom_{field}"
                ):
                    missing_important.append(field)
                else:
                    present_fields.append(field)

            # Check optional fields
            for field in self.OPTIONAL_FIELDS:
                mapped_field = field_mapping.get(field, field)
                if not element.get(mapped_field) and not element.get("properties", {}).get(
                    f"custom_{field}"
                ):
                    missing_optional.append(field)
                else:
                    present_fields.append(field)

            # Calculate completeness score
            total_fields = (
                len(self.REQUIRED_FIELDS) + len(self.IMPORTANT_FIELDS) + len(self.OPTIONAL_FIELDS)
            )
            present_count = len(present_fields)
            completeness_score = (present_count / total_fields) * 100 if total_fields > 0 else 0

            # Add analysis to element
            element["missing_fields"] = {
                "required": missing_required,
                "important": missing_important,
                "optional": missing_optional,
                "all_missing": missing_required + missing_important + missing_optional,
            }
            element["completeness_score"] = round(completeness_score, 1)
            element["present_fields"] = present_fields

            analyzed.append(element)

        return analyzed

    def generate_missing_fields(
        self,
        element: Dict,
        element_type: str = "ApplicationComponent",
        use_llm: bool = True,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate missing fields using LLM or heuristics.

        Returns dictionary of generated field values.
        """
        generated = {}
        missing = element.get("missing_fields", {}).get("all_missing", [])

        if not missing:
            return generated

        element_name = element.get("name", "")
        existing_desc = element.get("description", "")
        existing_type = element.get("type", element_type)

        # Use LLM to generate missing important fields
        if use_llm and missing and provider:
            try:
                llm_generated = self._generate_with_llm(
                    element_name, existing_desc, existing_type, missing, provider=provider
                )
                generated.update(llm_generated)
            except Exception as e:
                logger.warning(f"LLM generation failed: {e}, using heuristics")
                generated.update(self._generate_with_heuristics(element_name, missing))
        else:
            generated.update(self._generate_with_heuristics(element_name, missing))

        return generated

    def _generate_with_llm(
        self,
        name: str,
        description: str,
        element_type: str,
        missing_fields: List[str],
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Use LLM to generate missing fields."""
        prompt = f"""You are an Enterprise Architecture expert. Generate missing application details based on the provided information.

APPLICATION INFORMATION:
- Name: {name}
- Type: {element_type}
- Existing Description: {description or 'None provided'}

MISSING FIELDS TO GENERATE:
{', '.join(missing_fields)}

TASK:
Generate realistic, professional values for the missing fields based on the application name and any available context.
Use industry best practices and common patterns.

For each field, provide:
- A realistic value appropriate for an enterprise application
- Values should be consistent with the application name and type
- Use standard terminology and formats

Return ONLY valid JSON in this format:
{{
  "description": "Detailed description if missing",
  "component_type": "Web Application|Mobile App|API Service|etc",
  "deployment_status": "Production|Development|Staging|Retired",
  "business_domain": "Domain name",
  "technology_stack": "Technologies used",
  "business_owner": "Owner name or department",
  "application_category": "Category",
  "architecture_style": "Architecture pattern",
  "programming_languages": "Languages used",
  "frameworks": "Frameworks used",
  "primary_database": "Database type",
  "business_criticality": "High|Medium|Low"
}}

Only include fields that were listed as missing. Use null for fields you cannot reasonably infer.
"""

        try:
            # Get best available provider (respects user preference + intelligent selection)
            provider_name, model = LLMService._get_configured_provider()

            response_text, _ = LLMService._call_llm(
                prompt=prompt,
                model=model,
                provider=provider_name,
                user_id=None,
                project_id=None,
                max_tokens=2000,
            )

            # Parse JSON response
            import json

            # Try to extract JSON from response
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            generated = json.loads(response_text)

            # Filter to only include requested missing fields
            return {k: v for k, v in generated.items() if k in missing_fields and v}

        except Exception as e:
            logger.error(f"Error generating fields with LLM: {e}")
            return {}

    def _generate_with_heuristics(self, name: str, missing_fields: List[str]) -> Dict[str, Any]:
        """Generate missing fields using heuristics."""
        generated = {}

        # Heuristic-based generation
        name_lower = name.lower()

        if "description" in missing_fields:
            generated["description"] = f"Application component: {name}"

        if "component_type" in missing_fields:
            if any(word in name_lower for word in ["api", "service", "gateway"]):
                generated["component_type"] = "API Service"
            elif any(word in name_lower for word in ["web", "portal", "site"]):
                generated["component_type"] = "Web Application"
            elif any(word in name_lower for word in ["mobile", "app"]):
                generated["component_type"] = "Mobile Application"
            else:
                generated["component_type"] = "Application Component"

        if "deployment_status" in missing_fields:
            generated["deployment_status"] = "Development"  # Safe default

        if "business_criticality" in missing_fields:
            if any(word in name_lower for word in ["critical", "core", "primary"]):
                generated["business_criticality"] = "High"
            else:
                generated["business_criticality"] = "Medium"

        return generated

    def _get_field_mapping(self, element_type: str) -> Dict[str, str]:
        """Get field name mapping for element type."""
        if is_application_kind(element_type):
            return {
                "name": "name",
                "description": "description",
                "component_type": "component_type",
                "deployment_status": "deployment_status",
                "business_domain": "business_domain",
                "technology_stack": "technology_stack",
                "business_owner": "business_owner",
            }
        return {}

    def compare_with_existing(
        self, element: Dict, existing_element: Any  # ApplicationComponent model instance
    ) -> Dict[str, Any]:
        """
        Compare uploaded element with existing database element.

        Returns comparison showing differences and gaps.
        """
        comparison = {
            "is_duplicate": True,
            "differences": {},
            "missing_in_uploaded": [],
            "missing_in_existing": [],
            "suggested_action": "review",
        }

        # Compare key fields
        fields_to_compare = [
            "description",
            "component_type",
            "deployment_status",
            "business_domain",
            "technology_stack",
            "business_owner",
            "application_category",
            "architecture_style",
            "version",
        ]

        for field in fields_to_compare:
            uploaded_val = element.get(field) or element.get("properties", {}).get(
                f"custom_{field}"
            )
            existing_val = getattr(existing_element, field, None)

            if uploaded_val and existing_val:
                if str(uploaded_val).strip().lower() != str(existing_val).strip().lower():
                    comparison["differences"][field] = {
                        "uploaded": uploaded_val,
                        "existing": existing_val,
                    }
            elif uploaded_val and not existing_val:
                comparison["missing_in_existing"].append(field)
            elif not uploaded_val and existing_val:
                comparison["missing_in_uploaded"].append(field)

        # Determine suggested action
        if not comparison["differences"] and not comparison["missing_in_uploaded"]:
            comparison["suggested_action"] = "skip"  # Identical or existing has more data
        elif comparison["missing_in_existing"]:
            comparison["suggested_action"] = "update_existing"  # Uploaded has new data
        elif comparison["differences"]:
            comparison["suggested_action"] = "merge"  # Conflicts need resolution

        return comparison
