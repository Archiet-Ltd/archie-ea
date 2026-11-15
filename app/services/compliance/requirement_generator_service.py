"""
Requirement Generator Service

AI-powered service for generating hierarchical compliance requirements
from regulatory framework controls using LLM analysis.

Generates:
- Parent requirements (high-level compliance objectives)
- Child requirements (detailed implementation requirements)
- ArchiMate Requirement elements for each requirement
- Traceability links to controls, frameworks, and capabilities
"""
import logging
from typing import Dict, List, Optional


from app import db
from app.models.compliance_models import (
    ComplianceControl,
    ComplianceRequirement,
    RegulatoryFramework,
)
from app.models.models import ArchiMateElement
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class RequirementGeneratorService:
    """Service for LLM-based requirement generation from compliance controls"""

    @staticmethod
    def generate_requirements_from_control(control_id: int, created_by_id: int) -> Dict:
        """
        Generate hierarchical requirements from a compliance control using LLM.

        Args:
            control_id: ID of the ComplianceControl to generate requirements from
            created_by_id: User ID who initiated the generation

        Returns:
            Dict with generation results:
            {
                'status': 'success' | 'error',
                'message': str,
                'requirements_generated': int,
                'requirement_ids': List[int],
                'parent_requirements': List[Dict],
                'error': str (if status=='error')
            }
        """
        try:
            # Get the control
            control = db.session.get(ComplianceControl, control_id)
            if not control:
                return {"status": "error", "error": f"Control with ID {control_id} not found"}

            # Get the framework
            framework = control.framework
            if not framework:
                return {"status": "error", "error": f"Framework not found for control {control_id}"}

            logger.info(
                f"Generating requirements for control {control.control_id} "
                f"in framework {framework.code}"
            )

            # Build LLM prompt
            prompt = RequirementGeneratorService._build_generation_prompt(
                framework=framework, control=control
            )

            # Call LLM service
            llm_service = LLMService()

            try:
                llm_response = llm_service.generate_from_prompt(
                    prompt=prompt,
                    max_tokens=4000,
                    temperature=0.3,  # Lower temperature for more deterministic output
                )
            except Exception as llm_error:
                logger.error(f"LLM API error: {str(llm_error)}")
                return {
                    "status": "error",
                    "error": f"LLM API error: {str(llm_error)}. Please check AI settings at /admin/api-settings",
                }

            # Parse LLM response
            requirements_data = RequirementGeneratorService._parse_llm_response(
                llm_response=llm_response, framework_id=framework.id, control_id=control.id
            )

            if not requirements_data:
                return {
                    "status": "error",
                    "error": "Failed to parse LLM response. No valid requirements generated.",
                }

            # Create requirements in database with hierarchy
            created_requirements = RequirementGeneratorService._create_requirements_with_hierarchy(
                requirements_data=requirements_data,
                framework_id=framework.id,
                control_id=control.id,
                created_by_id=created_by_id,
            )

            logger.info(
                f"Successfully generated {len(created_requirements)} requirements "
                f"for control {control.control_id}"
            )

            return {
                "status": "success",
                "message": f"Generated {len(created_requirements)} requirements",
                "requirements_generated": len(created_requirements),
                "requirement_ids": [req.id for req in created_requirements],
                "parent_requirements": [
                    req.to_dict(include_children=True)
                    for req in created_requirements
                    if req.hierarchy_level == 0
                ],
            }

        except Exception as e:
            logger.error(f"Error generating requirements: {str(e)}", exc_info=True)
            return {"status": "error", "error": f"Unexpected error: {str(e)}"}

    @staticmethod
    def _build_generation_prompt(framework: RegulatoryFramework, control: ComplianceControl) -> str:
        """
        Build LLM prompt for requirement generation.

        Returns structured prompt that guides LLM to generate hierarchical requirements.
        """
        prompt = f"""You are an Enterprise Architect specializing in compliance requirement analysis.

REGULATORY CONTEXT:
- Framework: {framework.code} - {framework.name}
- Category: {framework.category}
- Jurisdiction: {framework.jurisdiction}
- Enforcement Level: {framework.enforcement_level}
- Penalty Risk: {framework.penalty_risk}

COMPLIANCE CONTROL TO ANALYZE:
- Control ID: {control.control_id}
- Title: {control.title}
- Description: {control.description}
- Control Type: {control.control_type}
- Priority: {control.priority}
"""

        if control.requirements_text:
            prompt += f"\nFull Control Text:\n{control.requirements_text}\n"

        if control.implementation_guidance:
            prompt += f"\nImplementation Guidance:\n{control.implementation_guidance}\n"

        if control.evidence_required:
            prompt += f"\nEvidence Required:\n{control.evidence_required}\n"

        prompt += """
YOUR TASK:
Analyze this compliance control and generate a hierarchical set of requirements.

REQUIREMENTS STRUCTURE:
1. Generate 1 - 3 PARENT requirements (high-level compliance objectives)
2. For each parent, generate 2 - 5 CHILD requirements (detailed implementation requirements)
3. Each requirement must be specific, measurable, and actionable

OUTPUT FORMAT (JSON):
Return ONLY valid JSON in this exact structure:

{
  "requirements": [
    {
      "title": "Parent requirement title (max 100 chars)",
      "description": "Detailed description of what must be achieved",
      "requirement_type": "regulatory",
      "priority": "critical|high|medium|low",
      "risk_if_not_met": "critical|high|medium|low",
      "acceptance_criteria": "Specific criteria to verify compliance (bullet points)",
      "measurement_method": "How to measure/verify this requirement",
      "threshold_value": "Quantifiable threshold if applicable (e.g., '99.9%', '100% coverage')",
      "hierarchy_level": 0,
      "children": [
        {
          "title": "Child requirement title",
          "description": "Detailed description",
          "requirement_type": "regulatory",
          "priority": "high|medium|low",
          "risk_if_not_met": "high|medium|low",
          "acceptance_criteria": "Specific criteria",
          "measurement_method": "How to measure",
          "threshold_value": "Quantifiable threshold",
          "hierarchy_level": 1
        }
      ]
    }
  ]
}

IMPORTANT RULES:
- All text fields must be clear, professional, and actionable
- Priority: critical (only for mandatory controls), high, medium, or low
- Risk: Use framework penalty_risk as baseline, adjust per requirement
- Acceptance criteria: 3 - 5 bullet points starting with "- "
- Threshold values: Use quantifiable metrics when possible (%, time, count)
- Generate realistic, implementable requirements
- Parent requirements = strategic objectives
- Child requirements = tactical implementation details

Generate the JSON now:"""

        return prompt

    @staticmethod
    def _parse_llm_response(
        llm_response: str, framework_id: int, control_id: int
    ) -> Optional[List[Dict]]:
        """
        Parse LLM JSON response into structured requirement data.

        Returns:
            List of requirement dictionaries with hierarchy, or None if parsing fails
        """
        try:
            # Extract JSON from response (LLM might wrap in markdown code blocks)
            import json
            import re

            # Try to find JSON block in response
            json_match = re.search(r"```json\s*(.*?)\s*```", llm_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON object
                json_match = re.search(r"\{.*\}", llm_response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    logger.error("No JSON found in LLM response")
                    return None

            parsed = json.loads(json_str)

            if "requirements" not in parsed:
                logger.error("No 'requirements' key in parsed JSON")
                return None

            requirements_list = parsed["requirements"]

            if not isinstance(requirements_list, list) or len(requirements_list) == 0:
                logger.error("'requirements' is not a list or is empty")
                return None

            logger.info(
                f"Successfully parsed {len(requirements_list)} parent requirements from LLM"
            )
            return requirements_list

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {str(e)}")
            logger.error(f"LLM response was: {llm_response[:500]}")
            return None
        except Exception as e:
            logger.error(f"Error parsing LLM response: {str(e)}")
            return None

    @staticmethod
    def _create_requirements_with_hierarchy(
        requirements_data: List[Dict], framework_id: int, control_id: int, created_by_id: int
    ) -> List[ComplianceRequirement]:
        """
        Create ComplianceRequirement records with ArchiMate elements and hierarchy.

        Returns:
            List of created ComplianceRequirement objects (both parents and children)
        """
        created_requirements = []

        for parent_data in requirements_data:
            try:
                # Create ArchiMate element for parent requirement
                parent_archimate = ArchiMateElement(
                    element_type="Requirement",  # ArchiMate Motivation layer
                    name=parent_data["title"][:256],
                    description=parent_data.get("description", ""),
                    layer="Motivation",
                    created_by_id=created_by_id,
                )
                db.session.add(parent_archimate)
                db.session.flush()  # Get ID

                # Create parent requirement
                parent_req = ComplianceRequirement(
                    title=parent_data["title"][:500],
                    description=parent_data["description"],
                    requirement_type=parent_data.get("requirement_type", "regulatory"),
                    framework_id=framework_id,
                    control_id=control_id,
                    archimate_element_id=parent_archimate.id,
                    priority=parent_data.get("priority", "medium"),
                    risk_if_not_met=parent_data.get("risk_if_not_met", "medium"),
                    acceptance_criteria=parent_data.get("acceptance_criteria", ""),
                    measurement_method=parent_data.get("measurement_method", ""),
                    threshold_value=parent_data.get("threshold_value", ""),
                    hierarchy_level=0,
                    parent_requirement_id=None,
                    status="active",
                    implementation_status="not_started",
                    created_by_id=created_by_id,
                )
                db.session.add(parent_req)
                db.session.flush()  # Get ID for children to reference

                created_requirements.append(parent_req)
                logger.info(f"Created parent requirement: {parent_req.title}")

                # Create child requirements
                children_data = parent_data.get("children", [])
                for child_data in children_data:
                    # Create ArchiMate element for child requirement
                    child_archimate = ArchiMateElement(
                        element_type="Requirement",
                        name=child_data["title"][:256],
                        description=child_data.get("description", ""),
                        layer="Motivation",
                        created_by_id=created_by_id,
                    )
                    db.session.add(child_archimate)
                    db.session.flush()

                    # Create child requirement
                    child_req = ComplianceRequirement(
                        title=child_data["title"][:500],
                        description=child_data["description"],
                        requirement_type=child_data.get("requirement_type", "regulatory"),
                        framework_id=framework_id,
                        control_id=control_id,
                        archimate_element_id=child_archimate.id,
                        priority=child_data.get("priority", "medium"),
                        risk_if_not_met=child_data.get("risk_if_not_met", "medium"),
                        acceptance_criteria=child_data.get("acceptance_criteria", ""),
                        measurement_method=child_data.get("measurement_method", ""),
                        threshold_value=child_data.get("threshold_value", ""),
                        hierarchy_level=1,
                        parent_requirement_id=parent_req.id,
                        status="active",
                        implementation_status="not_started",
                        created_by_id=created_by_id,
                    )
                    db.session.add(child_req)
                    created_requirements.append(child_req)
                    logger.info(f"Created child requirement: {child_req.title}")

            except Exception as e:
                logger.error(f"Error creating requirement from data: {str(e)}")
                continue

        # Commit all requirements at once
        db.session.commit()
        logger.info(f"Successfully committed {len(created_requirements)} requirements to database")

        return created_requirements

    @staticmethod
    def get_requirements_for_control(control_id: int) -> List[ComplianceRequirement]:
        """
        Get all requirements (hierarchical) for a specific control.

        Returns only root-level requirements; use .children to access hierarchy.
        """
        return (
            ComplianceRequirement.query.filter_by(
                control_id=control_id, hierarchy_level=0  # Only root requirements
            )
            .order_by(ComplianceRequirement.created_at.desc())
            .all()
        )

    @staticmethod
    def get_requirement_tree(control_id: int) -> List[Dict]:
        """
        Get full requirement tree for a control in nested dictionary format.

        Returns:
            List of parent requirement dictionaries with nested children
        """
        root_requirements = RequirementGeneratorService.get_requirements_for_control(control_id)
        return [req.to_dict(include_children=True) for req in root_requirements]
