"""
AI-Powered Motivation Layer Service for ArchiMate 3.2

This service provides comprehensive Motivation Layer support including:
- AI-powered requirement generation from business functions
- Automatic acceptance criteria generation
- ArchiMate 3.2 relationship validation and creation
- Hierarchical requirement decomposition
- Traceability matrix generation
- Stakeholder/Driver/Goal relationship management

The Motivation Layer in ArchiMate 3.2 includes:
- Stakeholder: Party with interest in outcome
- Driver: External/internal condition motivating org
- Assessment: Result of stakeholder analysis
- Goal: High-level statement of intent
- Outcome: End result
- Principle: Normative property of implementation
- Requirement: Statement of need (functional/non-functional)
- Constraint: Restriction on implementation
"""

from app.services.archimate_backbone import sync_archimate_element
import json
import logging
from typing import Dict, List, Optional

from app import db
from app.models import (
    AcceptanceCriteria,
    ArchiMateElement,
    ArchiMateRelationship,
    Requirement,
)
from app.models.business_capabilities import BusinessFunction
from app.models.compliance_models import (
    ComplianceRequirement,
    ProjectConstraint,
    QualityAttribute,
    RegulatoryFramework,
)
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class MotivationLayerService:
    """
    AI-Powered service for ArchiMate 3.2 Motivation Layer element generation
    and relationship validation.
    """

    def __init__(self):
        self.llm_service = LLMService()

    def generate_requirements_from_business_function(
        self,
        business_function_id: int,
        architecture_id: int,
        stakeholder_ids: Optional[List[int]] = None,
        context: Optional[str] = None,
        generate_acceptance_criteria: bool = True,
    ) -> List[Requirement]:
        """
        Generate AI-powered requirements from a business function with full
        ArchiMate 3.2 compliance.

        This method:
        1. Analyzes the business function using AI
        2. Generates functional and non-functional requirements
        3. Creates ArchiMate Requirement elements
        4. Establishes proper ArchiMate relationships
        5. Generates acceptance criteria for each requirement
        6. Links to stakeholders, drivers, and goals

        Args:
            business_function_id: ID of the BusinessFunction to decompose
            architecture_id: ID of the ArchitectureModel
            stakeholder_ids: Optional list of Stakeholder element IDs
            context: Optional additional context for generation
            generate_acceptance_criteria: If True, generates acceptance criteria

        Returns:
            List of created Requirement instances with full relationships

        Raises:
            ValueError: If business_function_id not found
        """
        # Get business function
        business_function = db.session.get(BusinessFunction, business_function_id)
        if not business_function:
            raise ValueError(f"BusinessFunction {business_function_id} not found")

        # Check for existing requirements
        existing = Requirement.query.filter_by(business_function_id=business_function_id).count()

        if existing > 0:
            logger.info(
                f"BusinessFunction {business_function_id} already has {existing} requirements"
            )
            return Requirement.query.filter_by(business_function_id=business_function_id).all()

        # Build AI prompt for requirement generation
        prompt = self._build_requirement_generation_prompt(
            business_function, context, stakeholder_ids
        )

        # Generate requirements using LLM
        response = self.llm_service.generate_from_prompt(prompt)

        # Parse JSON response
        requirements_data = self._parse_requirements_response(response)

        # Create requirements with full ArchiMate compliance
        created_requirements = []
        for req_data in requirements_data:
            requirement = self._create_requirement_with_relationships(
                req_data, business_function_id, architecture_id, stakeholder_ids
            )
            created_requirements.append(requirement)

            # Generate acceptance criteria if requested
            if generate_acceptance_criteria:
                self.generate_acceptance_criteria(requirement.id)

        db.session.commit()

        logger.info(
            f" Generated {len(created_requirements)} requirements for BusinessFunction '{business_function.name}'"
        )
        return created_requirements

    def _build_requirement_generation_prompt(
        self,
        business_function: BusinessFunction,
        context: Optional[str],
        stakeholder_ids: Optional[List[int]],
    ) -> str:
        """Build detailed prompt for AI requirement generation"""

        stakeholder_context = ""
        if stakeholder_ids:
            stakeholders = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(stakeholder_ids)
            ).all()
            stakeholder_names = [s.name for s in stakeholders]
            stakeholder_context = f"\n\nSTAKEHOLDERS:\n{', '.join(stakeholder_names)}"

        prompt = f"""You are an expert Enterprise Architect specializing in ArchiMate 3.2 Motivation Layer.

Your task is to generate comprehensive, well-structured requirements from a business function.

BUSINESS FUNCTION:
Name: {business_function.name}
Description: {business_function.description or 'No description provided'}
{stakeholder_context}

{f'ADDITIONAL CONTEXT:{chr(10)}{context}' if context else ''}

Generate a comprehensive set of requirements that:

1. **Functional Requirements**: What the system MUST do to support this business function
   - Use clear, testable language (MUST, SHALL keywords)
   - Include input/output specifications
   - Define business rules and validation
   - Specify data requirements

2. **Non-Functional Requirements**: Quality attributes and constraints
   - Performance (response times, throughput)
   - Security (authentication, authorization, encryption)
   - Scalability (concurrent users, data volume)
   - Availability (uptime, disaster recovery)
   - Usability (accessibility, user experience)
   - Compliance (standards, regulations like ISO, GDPR, etc.)

3. **For each requirement provide**:
   - title: Clear, concise requirement title
   - description: Detailed requirement description using RFC 2119 keywords (MUST, SHALL, SHOULD, MAY)
   - category: "Functional" or "Non-Functional"
   - type: Subcategory (e.g., "performance", "security", "usability", "data", "integration")
   - priority: "high", "medium", or "low" based on criticality
   - rationale: WHY this requirement exists (business justification)
   - verification_method: How to verify ("inspection", "analysis", "test", "demonstration")

Return ONLY a JSON array with this structure:
[
  {{
    "title": "System SHALL validate input data",
    "description": "The system MUST validate all user input against defined business rules before processing...",
    "category": "Functional",
    "type": "data_validation",
    "priority": "high",
    "rationale": "Ensures data integrity and prevents invalid data from corrupting business processes",
    "verification_method": "test"
  }},
  ...
]

Generate 5 - 10 well-structured requirements covering both functional and non-functional aspects.
Focus on QUALITY over quantity - each requirement should be specific, measurable, and testable.
"""

        return prompt

    def _parse_requirements_response(self, response: str) -> List[Dict]:
        """Parse JSON response from LLM"""
        try:
            # Extract JSON from response
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                json_text = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                json_text = response[json_start:json_end].strip()
            else:
                json_text = response.strip()

            requirements_data = json.loads(json_text)

            if not isinstance(requirements_data, list):
                raise ValueError("Expected JSON array of requirements")

            return requirements_data

        except json.JSONDecodeError as e:
            logger.error(f" Failed to parse requirements JSON: {e}")
            logger.info(f"Response: {response[:500]}")
            return []

    def _create_requirement_with_relationships(
        self,
        req_data: Dict,
        business_function_id: int,
        architecture_id: int,
        stakeholder_ids: Optional[List[int]],
    ) -> Requirement:
        """
        Create Requirement record with ArchiMate element and relationships
        following ArchiMate 3.2 specification
        """
        # 1. Create ArchiMate Requirement element first
        archimate_element = ArchiMateElement(
            architecture_id=architecture_id,
            name=req_data["title"],
            type="Requirement",
            layer="motivation",
            description=req_data["description"],
            properties={
                "category": req_data.get("category", "Functional"),
                "type": req_data.get("type", "general"),
                "verification_method": req_data.get("verification_method", "test"),
            },
        )
        db.session.add(archimate_element)
        db.session.flush()

        # 2. Create Requirement record linked to ArchiMate element (Basecoat pattern)
        requirement = Requirement(
            title=req_data["title"],
            description=req_data["description"],
            category=req_data.get("category", "Functional"),
            type=req_data.get("type", "functional").lower().replace("-", "_"),
            priority=req_data.get("priority", "medium"),
            rationale=req_data.get("rationale", ""),
            verification_method=req_data.get("verification_method", "test"),
            compliance_status="draft",
            architecture_id=architecture_id,
            archimate_element_id=archimate_element.id,
            business_function_id=business_function_id,
            stakeholder_id=stakeholder_ids[0] if stakeholder_ids else None,
        )
        db.session.add(requirement)
        sync_archimate_element(requirement)
        db.session.flush()

        # 3. Create ArchiMate 3.2 relationships
        self._create_archimate_relationships(
            requirement, archimate_element, business_function_id, architecture_id, stakeholder_ids
        )

        return requirement

    def _create_archimate_relationships(
        self,
        requirement: Requirement,
        archimate_element: ArchiMateElement,
        business_function_id: int,
        architecture_id: int,
        stakeholder_ids: Optional[List[int]],
    ):
        """
        Create ArchiMate 3.2 compliant relationships for a requirement

        Per ArchiMate 3.2 spec:
        - Stakeholder --influence--> Requirement
        - Requirement --realization--> Goal
        - BusinessFunction --realization--> Requirement
        """

        # Get business function's ArchiMate element
        business_func = db.session.get(BusinessFunction, business_function_id)
        if business_func and business_func.archimate_element_id:
            # BusinessFunction realizes Requirement
            rel = ArchiMateRelationship(
                architecture_id=architecture_id,
                source_id=business_func.archimate_element_id,
                target_id=archimate_element.id,
                type="realization",
                description="Business function realizes requirement",
            )
            db.session.add(rel)

        # Stakeholder influences Requirement
        if stakeholder_ids:
            for stakeholder_id in stakeholder_ids:
                rel = ArchiMateRelationship(
                    architecture_id=architecture_id,
                    source_id=stakeholder_id,
                    target_id=archimate_element.id,
                    type="influence",
                )
                db.session.add(rel)

    def enrich_requirement(
        self, requirement_id: int, context: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Use AI to enrich a requirement with intelligent analysis.

        This method analyzes the requirement description and:
        - Determines the appropriate type (functional, performance, security, etc.)
        - Assigns intelligent priority based on RFC 2119 keywords (MUST, SHOULD, MAY)
        - Determines category (Functional vs Non-Functional)
        - Suggests verification method
        - Generates rationale explaining why the requirement exists

        Args:
            requirement_id: ID of the Requirement to enrich
            context: Optional context dict with additional metadata

        Returns:
            Dictionary with enriched fields or None if enrichment fails
            {
                'type': str,  # functional, performance, security, etc.
                'priority': str,  # high, medium, low
                'category': str,  # Functional, Non-Functional, Constraint
                'verification_method': str,  # test, inspection, analysis, demonstration
                'rationale': str  # Why this requirement exists
            }
        """
        requirement = db.session.get(Requirement, requirement_id)
        if not requirement:
            return None

        # Build AI prompt for requirement enrichment
        prompt = f"""Analyze this requirement and provide intelligent classification:

REQUIREMENT:
Title: {requirement.title}
Description: {requirement.description}

TASK:
Analyze the requirement and determine:
1. Type (functional, performance, security, usability, reliability, compliance, data, integration)
2. Priority (high if MUST/SHALL, medium if SHOULD, low if COULD/MAY)
3. Category (Functional, Non-Functional, or Constraint)
4. Verification Method (test, inspection, analysis, demonstration)
5. Rationale (1 - 2 sentences explaining WHY this requirement exists and what business need it addresses)

RULES:
- Look for RFC 2119 keywords: MUST, SHALL, REQUIRED = high priority
- SHOULD, RECOMMENDED = medium priority
- MAY, OPTIONAL, COULD = low priority
- Security/performance/scalability requirements are Non-Functional
- Business process/feature requirements are Functional
- Hard limits/restrictions are Constraints

Respond ONLY with valid JSON:
{{
    "type": "security",
    "priority": "high",
    "category": "Non-Functional",
    "verification_method": "test",
    "rationale": "This requirement exists to ensure data protection compliance with GDPR and maintain customer trust."
}}"""

        try:
            response = self.llm_service.generate_from_prompt(prompt)

            # Parse JSON response
            # Strip markdown code blocks if present
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()

            enriched_data = json.loads(response)

            return enriched_data

        except Exception as e:
            logger.warning(f" Failed to enrich requirement {requirement_id}: {e}")
            return None

    def generate_acceptance_criteria(
        self, requirement_id: int, count: Optional[int] = None
    ) -> List[AcceptanceCriteria]:
        """
        Generate AI-powered SMART acceptance criteria for a requirement.

        SMART criteria are:
        - Specific: Clear and unambiguous
        - Measurable: Quantifiable or verifiable
        - Achievable: Realistic and attainable
        - Relevant: Aligned with requirement
        - Testable: Can be verified through test

        Args:
            requirement_id: ID of the Requirement
            count: Optional number of criteria to generate (default: 3 - 5)

        Returns:
            List of created AcceptanceCriteria instances
        """
        requirement = db.session.get(Requirement, requirement_id)
        if not requirement:
            raise ValueError(f"Requirement {requirement_id} not found")

        # Check for existing criteria
        existing = AcceptanceCriteria.query.filter_by(requirement_id=requirement_id).count()

        if existing > 0:
            logger.info(f" Requirement {requirement_id} already has {existing} acceptance criteria")
            return AcceptanceCriteria.query.filter_by(requirement_id=requirement_id).all()

        # Build prompt for acceptance criteria generation
        prompt = f"""You are a Quality Assurance expert generating SMART acceptance criteria.

REQUIREMENT:
Title: {requirement.title}
Description: {requirement.description}
Category: {requirement.category}
Type: {requirement.type}
Priority: {requirement.priority}
Verification Method: {requirement.verification_method}

Generate {count or '3 - 5'} SMART acceptance criteria that:
1. Are Specific, Measurable, Achievable, Relevant, and Testable
2. Can be verified using the verification method: {requirement.verification_method}
3. Cover different aspects of the requirement (happy path, edge cases, performance, security, etc.)
4. Use clear pass/fail language

Return ONLY a JSON array:
[
  {{
    "description": "System validates email format and returns error for invalid emails within 100ms",
    "order": 1
  }},
  ...
]
"""

        response = self.llm_service.generate_from_prompt(prompt)

        # Parse response
        try:
            # Extract JSON from response
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                json_text = response[json_start:json_end].strip()
            elif "[" in response and "]" in response:
                # Find the JSON array in the response
                json_start = response.find("[")
                json_end = response.rfind("]") + 1
                json_text = response[json_start:json_end].strip()
            else:
                json_text = response.strip()

            criteria_data = json.loads(json_text)

            created_criteria = []
            for crit in criteria_data:
                ac = AcceptanceCriteria(
                    requirement_id=requirement_id,
                    description=crit["description"],
                    order=crit.get("order", len(created_criteria) + 1),
                    status="pending",
                )
                db.session.add(ac)
                created_criteria.append(ac)

            db.session.commit()

            logger.info(
                f" Generated {len(created_criteria)} acceptance criteria for requirement '{requirement.title}'"
            )
            return created_criteria

        except json.JSONDecodeError as e:
            logger.error(f" Failed to parse acceptance criteria JSON: {e}")
            return []

    def validate_archimate_compliance(self, requirement_id: int) -> Dict:
        """
        Validate that a requirement meets ArchiMate 3.2 Motivation Layer
        relationship standards.

        Checks for:
        - Realization relationship from BusinessFunction
        - Influence relationship from Stakeholder
        - Realization relationship to Goal (if applicable)
        - Association to Driver (if applicable)

        Args:
            requirement_id: ID of the Requirement to validate

        Returns:
            Dictionary with validation results:
            {
                'is_compliant': bool,
                'violations': [list of violations],
                'recommendations': [list of recommendations],
                'completeness_score': int (0 - 100)
            }
        """
        requirement = db.session.get(Requirement, requirement_id)
        if not requirement:
            raise ValueError(f"Requirement {requirement_id} not found")

        violations = []
        recommendations = []
        score = 0
        max_score = 100

        # Check 1: Has ArchiMate element (20 points)
        if not requirement.archimate_element_id:
            violations.append(
                {
                    "severity": "error",
                    "message": "Requirement has no ArchiMate element",
                    "fix": "Create ArchiMate Requirement element",
                }
            )
        else:
            score += 20

        # Check 2: Linked to BusinessFunction (30 points)
        if not requirement.business_function_id:
            violations.append(
                {
                    "severity": "error",
                    "message": "Requirement not linked to BusinessFunction",
                    "fix": "Link requirement to the business function it supports",
                }
            )
        else:
            score += 30

            # Check for realization relationship
            if requirement.archimate_element_id:
                business_func = db.session.get(BusinessFunction, requirement.business_function_id)
                if business_func and business_func.archimate_element_id:
                    rel_exists = ArchiMateRelationship.query.filter_by(
                        source_id=business_func.archimate_element_id,
                        target_id=requirement.archimate_element_id,
                        type="realization",
                    ).first()

                    if not rel_exists:
                        recommendations.append(
                            {
                                "severity": "warning",
                                "message": "Missing realization relationship from BusinessFunction",
                                "fix": "Add realization relationship from BusinessFunction to Requirement element",
                            }
                        )

        # Check 3: Has Stakeholder influence (20 points)
        if not requirement.stakeholder_id:
            recommendations.append(
                {
                    "severity": "warning",
                    "message": "No stakeholder linked to requirement",
                    "fix": "Link requirement to the stakeholder who defined it",
                }
            )
        else:
            score += 20

            # Check for influence relationship
            if requirement.archimate_element_id:
                rel_exists = ArchiMateRelationship.query.filter_by(
                    source_id=requirement.stakeholder_id,
                    target_id=requirement.archimate_element_id,
                    type="influence",
                ).first()

                if not rel_exists:
                    recommendations.append(
                        {
                            "severity": "warning",
                            "message": "Missing influence relationship from Stakeholder",
                            "fix": "Add influence relationship from Stakeholder to Requirement",
                        }
                    )

        # Check 4: Has acceptance criteria (15 points)
        criteria_count = len(requirement.acceptance_criteria)
        if criteria_count == 0:
            recommendations.append(
                {
                    "severity": "warning",
                    "message": "No acceptance criteria defined",
                    "fix": "Generate acceptance criteria for testability",
                }
            )
        else:
            score += 15

        # Check 5: Has rationale (15 points)
        if not requirement.rationale:
            recommendations.append(
                {
                    "severity": "info",
                    "message": "No rationale provided",
                    "fix": "Add rationale explaining why this requirement exists",
                }
            )
        else:
            score += 15

        is_compliant = len([v for v in violations if v["severity"] == "error"]) == 0

        return {
            "is_compliant": is_compliant,
            "violations": violations,
            "recommendations": recommendations,
            "completeness_score": score,
            "max_score": max_score,
        }

    def generate_traceability_matrix(self, architecture_id: int) -> Dict:
        """
        Generate comprehensive traceability matrix for all requirements
        in an architecture model.

        Shows relationships:
        - Stakeholder -> Requirement
        - Driver -> Requirement
        - Requirement -> Goal
        - BusinessFunction -> Requirement
        - Requirement -> AcceptanceCriteria
        - Requirement -> TestCase

        Args:
            architecture_id: ID of the ArchitectureModel

        Returns:
            Dictionary containing traceability data
        """
        requirements = Requirement.query.filter_by(architecture_id=architecture_id).all()

        matrix = {
            "architecture_id": architecture_id,
            "total_requirements": len(requirements),
            "requirements": [],
        }

        for req in requirements:
            req_trace = {
                "id": req.id,
                "title": req.title,
                "category": req.category,
                "priority": req.priority,
                "stakeholders": [],
                "drivers": [],
                "goals": [],
                "business_functions": [],
                "acceptance_criteria": [],
                "test_cases": [],
            }

            # Get stakeholder
            if req.stakeholder:
                req_trace["stakeholders"].append(
                    {"id": req.stakeholder.id, "name": req.stakeholder.name}
                )

            # Get driver
            if req.driver:
                req_trace["drivers"].append({"id": req.driver.id, "name": req.driver.name})

            # Get goal
            if req.goal:
                req_trace["goals"].append({"id": req.goal.id, "name": req.goal.name})

            # Get business function
            if req.business_function:
                req_trace["business_functions"].append(
                    {"id": req.business_function.id, "name": req.business_function.name}
                )

            # Get acceptance criteria
            for ac in req.acceptance_criteria:
                req_trace["acceptance_criteria"].append(
                    {"id": ac.id, "description": ac.description, "status": ac.status}
                )

            matrix["requirements"].append(req_trace)

        return matrix

    def auto_link_requirements_to_motivation_elements(
        self, architecture_id: int, use_ai: bool = True
    ) -> Dict:
        """
        Automatically link all requirements in an architecture to appropriate
        Motivation Layer elements (Stakeholders, Drivers, Goals).

        This method:
        1. Gets all requirements that lack relationships
        2. Uses AI to analyze requirement context and suggest linkages
        3. Links to existing Stakeholders, Drivers, Goals in the architecture
        4. Creates proper ArchiMate relationships
        5. Establishes hierarchical parent-child requirement relationships

        Args:
            architecture_id: ID of the ArchitectureModel
            use_ai: If True, use AI to suggest linkages. If False, use heuristics.

        Returns:
            Dictionary with linking results:
            {
                'requirements_processed': int,
                'stakeholder_links_created': int,
                'driver_links_created': int,
                'goal_links_created': int,
                'parent_links_created': int,
                'archimate_relationships_created': int,
                'details': [...]
            }
        """
        # Get all requirements in architecture
        requirements = Requirement.query.filter_by(architecture_id=architecture_id).all()

        # Get all available Motivation Layer elements
        stakeholders = ArchiMateElement.query.filter_by(
            architecture_id=architecture_id, type="Stakeholder"
        ).all()

        drivers = ArchiMateElement.query.filter_by(
            architecture_id=architecture_id, type="Driver"
        ).all()

        goals = ArchiMateElement.query.filter_by(architecture_id=architecture_id, type="Goal").all()

        # Statistics
        stats = {
            "requirements_processed": 0,
            "stakeholder_links_created": 0,
            "driver_links_created": 0,
            "goal_links_created": 0,
            "parent_links_created": 0,
            "archimate_relationships_created": 0,
            "details": [],
        }

        # Process each requirement
        for req in requirements:
            req_result = {
                "requirement_id": req.id,
                "requirement_title": req.title,
                "links_created": [],
            }

            if use_ai:
                # Use AI to suggest appropriate linkages
                linkages = self._ai_suggest_requirement_linkages(
                    requirement=req,
                    stakeholders=stakeholders,
                    drivers=drivers,
                    goals=goals,
                    all_requirements=requirements,
                )
            else:
                # Use heuristic-based linkages
                linkages = self._heuristic_suggest_linkages(
                    requirement=req, stakeholders=stakeholders, drivers=drivers, goals=goals
                )

            # Apply stakeholder linkages
            if linkages.get("stakeholder_id") and not req.stakeholder_id:
                req.stakeholder_id = linkages["stakeholder_id"]
                stats["stakeholder_links_created"] += 1
                req_result["links_created"].append(
                    f"Linked to stakeholder: {linkages.get('stakeholder_name')}"
                )

                # Create ArchiMate influence relationship
                if req.archimate_element_id:
                    rel = ArchiMateRelationship(
                        architecture_id=architecture_id,
                        source_id=linkages["stakeholder_id"],
                        target_id=req.archimate_element_id,
                        type="influence",
                    )
                    db.session.add(rel)
                    stats["archimate_relationships_created"] += 1

            # Apply driver linkages
            if linkages.get("driver_id") and not req.driver_id:
                req.driver_id = linkages["driver_id"]
                stats["driver_links_created"] += 1
                req_result["links_created"].append(
                    f"Linked to driver: {linkages.get('driver_name')}"
                )

                # Create ArchiMate association relationship
                if req.archimate_element_id:
                    rel = ArchiMateRelationship(
                        architecture_id=architecture_id,
                        source_id=linkages["driver_id"],
                        target_id=req.archimate_element_id,
                        type="association",
                    )
                    db.session.add(rel)
                    stats["archimate_relationships_created"] += 1

            # Apply goal linkages
            if linkages.get("goal_id") and not req.goal_id:
                req.goal_id = linkages["goal_id"]
                stats["goal_links_created"] += 1
                req_result["links_created"].append(f"Linked to goal: {linkages.get('goal_name')}")

                # Create ArchiMate realization relationship
                if req.archimate_element_id:
                    rel = ArchiMateRelationship(
                        architecture_id=architecture_id,
                        source_id=req.archimate_element_id,
                        target_id=linkages["goal_id"],
                        type="realization",
                    )
                    db.session.add(rel)
                    stats["archimate_relationships_created"] += 1

            # Apply parent requirement linkages (hierarchical decomposition)
            if linkages.get("parent_requirement_id") and not req.parent_requirement_id:
                req.parent_requirement_id = linkages["parent_requirement_id"]
                stats["parent_links_created"] += 1
                req_result["links_created"].append(
                    f"Linked to parent requirement: {linkages.get('parent_requirement_title')}"
                )

            if req_result["links_created"]:
                stats["requirements_processed"] += 1
                stats["details"].append(req_result)

        # Commit all changes
        db.session.commit()

        return stats

    def _ai_suggest_requirement_linkages(
        self,
        requirement: Requirement,
        stakeholders: list,
        drivers: list,
        goals: list,
        all_requirements: list,
    ) -> Dict:
        """
        Use AI to suggest appropriate linkages for a requirement.

        Returns:
            {
                'stakeholder_id': int or None,
                'stakeholder_name': str or None,
                'driver_id': int or None,
                'driver_name': str or None,
                'goal_id': int or None,
                'goal_name': str or None,
                'parent_requirement_id': int or None,
                'parent_requirement_title': str or None
            }
        """
        # Build context for AI
        stakeholder_context = (
            "\n".join([f"- {s.id}: {s.name}" for s in stakeholders])
            if stakeholders
            else "None available"
        )
        driver_context = (
            "\n".join([f"- {d.id}: {d.name}" for d in drivers]) if drivers else "None available"
        )
        goal_context = (
            "\n".join([f"- {g.id}: {g.name}" for g in goals]) if goals else "None available"
        )

        # Only include requirements without this one
        parent_candidates = [
            r for r in all_requirements if r.id != requirement.id and not r.parent_requirement_id
        ]
        parent_context = (
            "\n".join([f"- {r.id}: {r.title}" for r in parent_candidates[:10]])
            if parent_candidates
            else "None available"
        )

        prompt = f"""Analyze this requirement and suggest appropriate ArchiMate Motivation Layer linkages.

REQUIREMENT:
ID: {requirement.id}
Title: {requirement.title}
Description: {requirement.description}
Category: {requirement.category}

AVAILABLE STAKEHOLDERS:
{stakeholder_context}

AVAILABLE DRIVERS:
{driver_context}

AVAILABLE GOALS:
{goal_context}

AVAILABLE PARENT REQUIREMENTS (for hierarchical decomposition):
{parent_context}

TASK:
Determine which stakeholder, driver, goal, and parent requirement (if any) this requirement should link to based on semantic similarity and ArchiMate 3.2 best practices.

RULES:
- Stakeholder: Who would define or care about this requirement?
- Driver: What external/internal force motivates this requirement?
- Goal: What high-level objective does this requirement help achieve?
- Parent Requirement: Is this a decomposition/refinement of a broader requirement?
- Return null for any linkage that doesn't have a clear match

Respond ONLY with valid JSON:
{{
    "stakeholder_id": 5,
    "stakeholder_name": "Compliance Officer",
    "driver_id": 3,
    "driver_name": "GDPR Compliance",
    "goal_id": 7,
    "goal_name": "Ensure Data Privacy",
    "parent_requirement_id": null,
    "parent_requirement_title": null,
    "rationale": "This requirement is driven by GDPR compliance, defined by compliance stakeholders, and contributes to data privacy goals."
}}"""

        try:
            response = self.llm_service.generate_from_prompt(prompt)

            # Parse JSON response
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()

            linkages = json.loads(response)
            return linkages

        except Exception as e:
            logger.warning(f" AI linkage suggestion failed for requirement {requirement.id}: {e}")
            return {}

    def _heuristic_suggest_linkages(
        self, requirement: Requirement, stakeholders: list, drivers: list, goals: list
    ) -> Dict:
        """
        Use simple heuristics to suggest linkages when AI is not available.

        Uses keyword matching and similarity scoring.
        """
        linkages = {}

        req_text = f"{requirement.title} {requirement.description}".lower()

        # Match stakeholder by keyword overlap
        best_stakeholder = None
        best_stakeholder_score = 0
        for s in stakeholders:
            score = sum(1 for word in s.name.lower().split() if word in req_text)
            if score > best_stakeholder_score:
                best_stakeholder_score = score
                best_stakeholder = s

        if best_stakeholder and best_stakeholder_score > 0:
            linkages["stakeholder_id"] = best_stakeholder.id
            linkages["stakeholder_name"] = best_stakeholder.name

        # Match driver by keyword overlap
        best_driver = None
        best_driver_score = 0
        for d in drivers:
            score = sum(1 for word in d.name.lower().split() if word in req_text)
            if score > best_driver_score:
                best_driver_score = score
                best_driver = d

        if best_driver and best_driver_score > 0:
            linkages["driver_id"] = best_driver.id
            linkages["driver_name"] = best_driver.name

        # Match goal by keyword overlap
        best_goal = None
        best_goal_score = 0
        for g in goals:
            score = sum(1 for word in g.name.lower().split() if word in req_text)
            if score > best_goal_score:
                best_goal_score = score
                best_goal = g

        if best_goal and best_goal_score > 0:
            linkages["goal_id"] = best_goal.id
            linkages["goal_name"] = best_goal.name

        return linkages

    def generate_compliance_requirements_from_document(
        self,
        document_content: str,
        architecture_id: int,
        region: str = "US",
        industry: str = "general",
        archimate_element_id: Optional[int] = None,
    ) -> Dict:
        """
        Generate comprehensive compliance requirements from uploaded document.

        This method enhances the standard requirement generation with:
        - Regulatory Requirements (GDPR, HIPAA, SOX, PCI-DSS, ISO, OSHA, EPA, FDA)
        - Compliance Requirements (internal policies, audit requirements)
        - Constraint Requirements (budget, timeline, technical debt)
        - Quality Attributes (NFRs with measurable thresholds like 99.9% uptime, <200ms)

        Args:
            document_content: The uploaded document text
            architecture_id: ID of the ArchitectureModel
            region: Geographic region (US, EU, Global)
            industry: Industry type (pharmaceutical, automotive, food, general)
            archimate_element_id: Optional element to link requirements to

        Returns:
            Dictionary with created compliance requirements:
            {
                'regulatory_requirements': [...],
                'quality_attributes': [...],
                'project_constraints': [...],
                'compliance_requirements': [...],
                'summary': {...}
            }
        """
        # Get applicable regulatory frameworks
        frameworks = RegulatoryFramework.query.filter_by(
            status="active", applies_to_region=region
        ).all()

        if industry != "general":
            frameworks = [
                f
                for f in frameworks
                if f.industry_specific == industry or f.industry_specific == "general"
            ]

        # Build comprehensive compliance-aware prompt
        prompt = self._build_compliance_requirements_prompt(
            document_content, frameworks, region, industry
        )

        # Generate requirements using LLM
        response = self.llm_service.generate_from_prompt(prompt)

        # Parse response
        requirements_data = self._parse_compliance_response(response)

        # Create compliance requirements in database
        result = {
            "regulatory_requirements": [],
            "quality_attributes": [],
            "project_constraints": [],
            "compliance_requirements": [],
            "summary": {
                "total_created": 0,
                "frameworks_addressed": len(frameworks),
                "region": region,
                "industry": industry,
            },
        }

        # Create Regulatory/Compliance Requirements
        for req_data in requirements_data.get("regulatory_requirements", []):
            compliance_req = ComplianceRequirement(
                archimate_element_id=archimate_element_id,
                title=req_data["title"],
                description=req_data["description"],
                requirement_type="regulatory",
                priority=req_data.get("priority", "medium"),
                risk_if_not_met=req_data.get("risk_level", "medium"),
                penalty_description=req_data.get("penalty", ""),
                acceptance_criteria=req_data.get("acceptance_criteria", ""),
                threshold_value=req_data.get("threshold", ""),
                applies_to_region=region,
                status="active",
                implementation_status="not_started",
            )
            db.session.add(compliance_req)
            result["regulatory_requirements"].append(compliance_req)

        # Create Quality Attributes (NFRs)
        for qa_data in requirements_data.get("quality_attributes", []):
            quality_attr = QualityAttribute(
                archimate_element_id=archimate_element_id,
                name=qa_data["name"],
                category=qa_data.get("category", "performance"),
                subcategory=qa_data.get("subcategory", ""),
                description=qa_data.get("description", ""),
                metric_name=qa_data.get("metric_name", ""),
                metric_unit=qa_data.get("metric_unit", ""),
                target_value=qa_data["target_value"],
                minimum_acceptable=qa_data.get("minimum_acceptable", ""),
                priority=qa_data.get("priority", "medium"),
                source="AI-generated from document",
                rationale=qa_data.get("rationale", ""),
                measurement_method=qa_data.get("measurement_method", ""),
                status="active",
            )
            db.session.add(quality_attr)
            result["quality_attributes"].append(quality_attr)

        # Create Project Constraints
        for constraint_data in requirements_data.get("project_constraints", []):
            constraint = ProjectConstraint(
                archimate_element_id=archimate_element_id,
                name=constraint_data["name"],
                constraint_type=constraint_data.get("constraint_type", "technical"),
                description=constraint_data["description"],
                limit_type=constraint_data.get("limit_type", "maximum"),
                limit_value=constraint_data.get("limit_value", ""),
                priority=constraint_data.get("priority", "high"),
                is_hard_constraint=constraint_data.get("is_hard", True),
                flexibility_notes=constraint_data.get("flexibility_notes", ""),
                status="active",
                is_violated=False,
            )
            db.session.add(constraint)
            result["project_constraints"].append(constraint)

        db.session.commit()

        # Calculate summary
        result["summary"]["total_created"] = (
            len(result["regulatory_requirements"])
            + len(result["quality_attributes"])
            + len(result["project_constraints"])
        )

        return result

    def _build_compliance_requirements_prompt(
        self,
        document_content: str,
        frameworks: List[RegulatoryFramework],
        region: str,
        industry: str,
    ) -> str:
        """Build comprehensive compliance-aware prompt"""

        frameworks_context = "\n\n**APPLICABLE REGULATORY FRAMEWORKS:**\n"
        for fw in frameworks:
            frameworks_context += f"- {fw.code}: {fw.name} ({fw.category})\n"
            frameworks_context += (
                f"  Enforcement: {fw.enforcement_level}, Penalty Risk: {fw.penalty_risk}\n"
            )

        prompt = f"""You are an expert Enterprise Architect specializing in compliance-driven architecture for manufacturing.

**REGION:** {region}
**INDUSTRY:** {industry}

{frameworks_context}

**DOCUMENT CONTENT:**
{document_content[:3000]}  # Limit to first 3000 chars

**TASK:**
Analyze the document and generate comprehensive compliance requirements covering:

1. **Regulatory Requirements** - Requirements mandated by applicable frameworks
   - Must address critical controls from applicable frameworks
   - Include penalty/risk if not met
   - Specify measurable acceptance criteria
   - Link to specific regulatory control IDs where applicable

2. **Quality Attributes (NFRs)** - Non-functional requirements with measurable thresholds
   - Performance: Response time, throughput, latency (e.g., "<200ms", "1000 req/sec")
   - Reliability: Uptime, MTBF, MTTR, disaster recovery (e.g., "99.9% uptime")
   - Security: Authentication, encryption, access control (e.g., "AES - 256 encryption")
   - Scalability: Concurrent users, data volume (e.g., "10,000 concurrent users")
   - Usability: Accessibility, user experience (e.g., "WCAG 2.1 AA compliance")
   - Each MUST have specific numeric thresholds

3. **Project Constraints** - Budget, timeline, resource, technical debt constraints
   - Budget constraints (e.g., "Total project cost <$500K")
   - Timeline constraints (e.g., "Go-live by Q2 2025")
   - Resource constraints (e.g., "2 Java developers available")
   - Technical constraints (e.g., "Must use existing Oracle database")
   - Specify if hard (cannot be violated) or soft (negotiable) constraint

**OUTPUT FORMAT (JSON):**
{{
  "regulatory_requirements": [
    {{
      "title": "GDPR Article 32 - Security of Processing",
      "description": "System MUST implement appropriate technical and organizational measures to ensure a level of security appropriate to the risk...",
      "priority": "critical",
      "risk_level": "critical",
      "penalty": "Fines up to €20M or 4% of annual global turnover",
      "acceptance_criteria": "Encryption at rest and in transit, access controls implemented, audit logs maintained",
      "threshold": "100% data encrypted"
    }}
  ],
  "quality_attributes": [
    {{
      "name": "API Response Time",
      "category": "performance",
      "subcategory": "response_time",
      "description": "All API endpoints must respond within acceptable time limits",
      "metric_name": "p95 Response Time",
      "metric_unit": "ms",
      "target_value": "<200",
      "minimum_acceptable": "<500",
      "priority": "high",
      "rationale": "Ensures good user experience and meets SLA commitments",
      "measurement_method": "APM tool (Datadog, New Relic)"
    }}
  ],
  "project_constraints": [
    {{
      "name": "Total Project Budget",
      "constraint_type": "budget",
      "description": "Total project cost including development, infrastructure, and licenses",
      "limit_type": "maximum",
      "limit_value": "$500,000 USD",
      "priority": "critical",
      "is_hard": true,
      "flexibility_notes": "No flexibility - hard cap from finance"
    }}
  ]
}}

Generate comprehensive requirements based on the document content and applicable frameworks.
Focus on QUALITY and SPECIFICITY - each requirement must be measurable and testable.
"""

        return prompt

    def _parse_compliance_response(self, response: str) -> Dict:
        """Parse compliance requirements JSON response"""
        try:
            # Extract JSON from response
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                json_text = response[json_start:json_end].strip()
            elif "{" in response and "}" in response:
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                json_text = response[json_start:json_end].strip()
            else:
                json_text = response.strip()

            requirements_data = json.loads(json_text)

            # Ensure all required keys exist
            if "regulatory_requirements" not in requirements_data:
                requirements_data["regulatory_requirements"] = []
            if "quality_attributes" not in requirements_data:
                requirements_data["quality_attributes"] = []
            if "project_constraints" not in requirements_data:
                requirements_data["project_constraints"] = []

            return requirements_data

        except json.JSONDecodeError as e:  # fabricated-ok: unparseable response yields empty lists, not fabricated requirements
            logger.error(f" Failed to parse compliance requirements JSON: {e}")
            logger.info(f"Response: {response[:500]}")
            return {
                "regulatory_requirements": [],
                "quality_attributes": [],
                "project_constraints": [],
            }

    # ------------------------------------------------------------------ #
    # Driver, Assessment and Goal-trace write paths (strategy traceability)
    # ------------------------------------------------------------------ #

    @staticmethod
    def create_driver(data: Dict, organization_id: int):
        """Create a Driver record with source, date and owner.

        Args:
            data: Dict with name, description, driver_type, source,
                  identified_date, and optional fields.
            organization_id: The tenant organisation id.

        Returns:
            The created Driver instance.
        """
        from app.models.motivation import Driver

        driver = Driver(
            name=data["name"],
            description=data.get("description"),
            driver_type=data.get("driver_type"),
            source=data.get("source"),
            identified_date=data.get("identified_date"),
            organization_id=organization_id,
        )
        db.session.add(driver)
        db.session.flush()
        return driver

    @staticmethod
    def create_assessment(driver_id: int, data: Dict, organization_id: int):
        """Create an Assessment row against a Driver.

        Args:
            driver_id: The Driver this assessment is conducted against.
            data: Dict with name, description, assessment_type, result_score,
                  assessor, date_assessed.
            organization_id: The tenant organisation id.

        Returns:
            The created Assessment instance.
        """
        from app.models.motivation import Assessment, Driver

        driver = Driver.query.filter_by(id=driver_id, organization_id=organization_id).first()
        if driver is None:
            raise ValueError(f"Driver {driver_id} not found in organisation {organization_id}")

        assessment = Assessment(
            name=data["name"],
            description=data.get("description"),
            assessment_type=data.get("assessment_type"),
            result_score=data.get("result_score"),
            assessor=data.get("assessor"),
            date_assessed=data.get("date_assessed"),
            driver_id=driver_id,
            organization_id=organization_id,
        )
        db.session.add(assessment)
        db.session.flush()
        return assessment

    @staticmethod
    def get_goal_trace(goal_id: int, organization_id: int) -> Dict:
        """Walk the traceability chain from a Goal.

        Goal → Capability (ArchiMate Realization) →
        Initiative (initiative_goals / strategic_initiative_goals) →
        Work Package / Application.

        Each hop cites its relationship row.

        Args:
            goal_id: The Goal id.
            organization_id: The tenant organisation id.

        Returns:
            Dict with goal, capabilities, initiatives, work_packages,
            applications, and a trace chain.
        """
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.models.motivation import Goal
        from app.models.strategic import StrategicInitiative
        from app.models.unified_capability import UnifiedCapability
        from app.models.unified_work_package import UnifiedWorkPackage
        from app.models.implementation_migration import WorkPackage

        goal = Goal.query.filter_by(id=goal_id, organization_id=organization_id).first()
        if goal is None:
            return {"goal": None, "reason": "not_found"}

        goal_dict = {
            "id": goal.id,
            "name": goal.name,
            "description": goal.description,
            "goal_type": goal.goal_type,
            "status": goal.status,
            "archimate_element_id": goal.archimate_element_id,
        }

        # Hop 1: Goal → Capability through ArchiMate Realization relationships.
        # A Goal's archimate_element is the source of a Realization whose
        # target is a Capability's archimate_element.
        capabilities = []
        capability_hops = []
        if goal.archimate_element_id:
            realizations = ArchiMateRelationship.query.filter_by(
                source_id=goal.archimate_element_id,
                type="realization",
            ).all()
            for rel in realizations:
                target_elem = ArchiMateElement.query.filter_by(
                    id=rel.target_id
                ).first()
                if target_elem is None:
                    continue
                caps = UnifiedCapability.query.filter_by(
                    archimate_element_id=target_elem.id
                ).all()
                for cap in caps:
                    capabilities.append({
                        "id": cap.id,
                        "name": cap.name,
                        "code": getattr(cap, "code", None),
                        "current_maturity_level": getattr(cap, "current_maturity_level", None),
                    })
                    capability_hops.append({
                        "relationship_type": "realization",
                        "relationship_id": rel.id,
                        "source": f"Goal:{goal.id}",
                        "target": f"UnifiedCapability:{cap.id}",
                    })

        # Hop 2: Goal → Initiative through junction tables.
        enterprise_initiatives = []
        strategic_initiatives = []
        initiative_hops = []

        # EnterpriseInitiative via initiative_goals
        for ei in goal.initiatives:
            enterprise_initiatives.append({
                "id": ei.id,
                "name": ei.name,
                "status": getattr(ei, "status", None),
            })
            initiative_hops.append({
                "relationship_type": "initiative_goals",
                "source": f"Goal:{goal.id}",
                "target": f"EnterpriseInitiative:{ei.id}",
            })

        # StrategicInitiative via strategic_initiative_goals
        for si in StrategicInitiative.query.filter(
            StrategicInitiative.goals.any(id=goal.id)
        ).all():
            strategic_initiatives.append({
                "id": si.id,
                "name": si.name,
                "status": si.status,
                "record_kind": si.record_kind,
            })
            initiative_hops.append({
                "relationship_type": "strategic_initiative_goals",
                "source": f"Goal:{goal.id}",
                "target": f"StrategicInitiative:{si.id}",
            })

        # Hop 3: Initiative → Work Package / Application.
        work_packages = []
        application_components = []
        wp_hops = []

        all_initiative_ids = [ei["id"] for ei in enterprise_initiatives]

        # WorkPackage rows linked to EnterpriseInitiative
        for wp in WorkPackage.query.filter(
            WorkPackage.enterprise_initiative_id.in_(all_initiative_ids),
            WorkPackage.organization_id == organization_id,
        ).all():
            work_packages.append({
                "id": wp.id,
                "name": wp.name,
                "status": getattr(wp, "status", None),
            })
            wp_hops.append({
                "relationship_type": "work_package_enterprise_initiative",
                "source": f"EnterpriseInitiative:{wp.enterprise_initiative_id}",
                "target": f"WorkPackage:{wp.id}",
            })

        # UnifiedWorkPackage rows linked to EnterpriseInitiative
        for uwp in UnifiedWorkPackage.query.filter(
            UnifiedWorkPackage.enterprise_initiative_id.in_(all_initiative_ids),
        ).all():
            work_packages.append({
                "id": uwp.id,
                "name": uwp.name,
                "status": getattr(uwp, "status", None),
            })
            wp_hops.append({
                "relationship_type": "unified_work_package_enterprise_initiative",
                "source": f"EnterpriseInitiative:{uwp.enterprise_initiative_id}",
                "target": f"UnifiedWorkPackage:{uwp.id}",
            })

        # Application components linked to initiatives
        for ei in goal.initiatives:
            if hasattr(ei, "application_components") and ei.application_components:
                for ac in ei.application_components:
                    application_components.append({
                        "id": ac.id,
                        "name": getattr(ac, "name", None) or getattr(ac, "application_name", str(ac.id)),
                    })
                    wp_hops.append({
                        "relationship_type": "initiative_application",
                        "source": f"EnterpriseInitiative:{ei.id}",
                        "target": f"ApplicationComponent:{ac.id}",
                    })

        has_trace = bool(
            goal.archimate_element_id
            or goal.initiatives
            or StrategicInitiative.query.filter(
                StrategicInitiative.goals.any(id=goal.id)
            ).first()
        )

        return {
            "goal": goal_dict,
            "trace_status": "recorded" if has_trace else "not_recorded",
            "capabilities": capabilities,
            "capability_hops": capability_hops,
            "enterprise_initiatives": enterprise_initiatives,
            "strategic_initiatives": strategic_initiatives,
            "initiative_hops": initiative_hops,
            "work_packages": work_packages,
            "application_components": application_components,
            "work_package_hops": wp_hops,
        }

    @staticmethod
    def suggest_goals_for_driver(driver_id: int, organization_id: int) -> Dict:
        """Suggest candidate goals for a driver by text similarity.

        Never auto-links; returns a list of candidate goals with similarity
        scores for the strategy officer to confirm.

        Args:
            driver_id: The Driver id.
            organization_id: The tenant organisation id.

        Returns:
            Dict with driver info and a list of candidate goals.
        """
        from app.models.motivation import Driver, Goal

        driver = Driver.query.filter_by(id=driver_id, organization_id=organization_id).first()
        if driver is None:
            return {"driver": None, "reason": "not_found"}

        # Collect driver text for matching
        driver_text = " ".join(
            filter(None, [driver.name, driver.description or ""])
        ).lower()

        # Get all goals in the tenant
        goals = Goal.query.filter_by(organization_id=organization_id).all()

        candidates = []
        for goal in goals:
            goal_text = " ".join(
                filter(None, [goal.name, goal.description or ""])
            ).lower()

            # Simple word-overlap similarity
            driver_words = set(driver_text.split())
            goal_words = set(goal_text.split())
            if not driver_words or not goal_words:
                score = 0.0
            else:
                intersection = driver_words & goal_words
                score = len(intersection) / max(len(driver_words), 1)

            if score > 0:
                candidates.append({
                    "goal_id": goal.id,
                    "goal_name": goal.name,
                    "goal_type": goal.goal_type,
                    "similarity_score": round(score, 3),
                })

        # Sort by similarity descending
        candidates.sort(key=lambda c: c["similarity_score"], reverse=True)

        return {
            "driver": {
                "id": driver.id,
                "name": driver.name,
                "driver_type": driver.driver_type,
            },
            "candidates": candidates,
        }

    @staticmethod
    def link_driver_to_goal(driver_id: int, goal_id: int, organization_id: int) -> Dict:
        """Link a Driver to a Goal.

        Sets the Goal's driver_id FK. The strategy officer confirms the link;
        this is never automatic.

        Args:
            driver_id: The Driver id.
            goal_id: The Goal id.
            organization_id: The tenant organisation id.

        Returns:
            Dict with the linked driver and goal info.
        """
        from app.models.motivation import Driver, Goal

        driver = Driver.query.filter_by(id=driver_id, organization_id=organization_id).first()
        if driver is None:
            raise ValueError(f"Driver {driver_id} not found in organisation {organization_id}")

        goal = Goal.query.filter_by(id=goal_id, organization_id=organization_id).first()
        if goal is None:
            raise ValueError(f"Goal {goal_id} not found in organisation {organization_id}")

        if goal.driver_id is not None:
            raise ValueError(
                f"Goal {goal_id} is already linked to driver {goal.driver_id}. "
                "Unlink it first before linking to a different driver."
            )

        goal.driver_id = driver_id
        db.session.flush()

        return {
            "driver": {"id": driver.id, "name": driver.name},
            "goal": {"id": goal.id, "name": goal.name},
            "linked": True,
        }
