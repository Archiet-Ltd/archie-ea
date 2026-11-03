"""
AI-Powered Implementation & Migration Layer Service for ArchiMate 3.2

This service provides comprehensive Implementation & Migration Layer modeling:
- Work package identification and planning
- Deliverable tracking and mapping
- Gap analysis (current state vs target state)
- Plateau (transition state) modeling
- Migration planning and sequencing
- Project dependency analysis

ArchiMate 3.2 Implementation & Migration Elements:
- WorkPackage: Series of actions to achieve goals (project, sprint, phase)
- Deliverable: Precisely-defined result of a work package (architecture document, software release)
- Gap: Statement of difference between two plateaus (current vs target)
- Plateau: Relatively stable state during limited time period (transition architecture state)
- ImplementationEvent: State change in implementation/migration domain
"""

import json
from typing import Dict, List, Optional

from app import db
from app.datetime_helpers import utcnow
from app.models import ArchiMateElement, ArchiMateRelationship
from app.services.llm_service import LLMService


class ImplementationMigrationService:
    """
    AI-powered service for ArchiMate 3.2 Implementation & Migration Layer modeling.

    Capabilities:
    - Identify work packages from project plans
    - Track deliverables and link to architecture elements
    - Perform gap analysis (current vs target state)
    - Model plateau (transition states)
    - Generate migration roadmap
    - Analyze project dependencies and critical paths
    """

    def __init__(self):
        self.llm_service = LLMService()

    # ========================================================================
    # Work Package Methods
    # ========================================================================

    def identify_work_packages(
        self, project_plan: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Identify work packages from project plan.

        WorkPackage: Series of actions achieving goals
        (Project, Program, Sprint, Phase, Task, Migration wave)

        Args:
            project_plan: Project plan description or WBS
            architecture_id: ID of the ArchitectureModel

        Returns:
            List of WorkPackage ArchiMateElements

        Example:
            >>> plan = '''
            ... Phase 1: Assessment & Planning (Q1 2024)
            ... - Current state architecture documentation
            ... - Gap analysis
            ... - Migration roadmap creation
            ...
            ... Phase 2: Infrastructure Modernization (Q2 - Q3 2024)
            ... - Cloud infrastructure setup
            ... - Database migration
            ... '''
            >>> packages = service.identify_work_packages(plan, 1)
        """
        prompt = self._build_work_package_prompt(project_plan)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            packages_data = json.loads(response)

            work_packages = []
            for pkg_info in packages_data.get("work_packages", []):
                package = self._create_implementation_element(
                    pkg_info, architecture_id, element_type="WorkPackage"
                )
                work_packages.append(package)

            # Create hierarchy relationships (parent-child work packages)
            for pkg_info in packages_data.get("work_packages", []):
                if "parent" in pkg_info and pkg_info["parent"]:
                    parent_pkg = next(
                        (wp for wp in work_packages if wp.name == pkg_info["parent"]), None
                    )
                    child_pkg = next(
                        (wp for wp in work_packages if wp.name == pkg_info["name"]), None
                    )

                    if parent_pkg and child_pkg:
                        # Parent aggregates child (composition)
                        relationship = ArchiMateRelationship(
                            type="composition",
                            source_id=parent_pkg.id,
                            target_id=child_pkg.id,
                            architecture_id=architecture_id,
                        )
                        db.session.add(relationship)

            db.session.commit()
            return work_packages

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Work package identification failed: {str(e)}")

    def analyze_work_package_dependencies(self, architecture_id: int) -> Dict:
        """
        Analyze dependencies between work packages.

        Returns:
            Dict with dependency analysis:
            {
                'dependencies': [...],
                'critical_path': [...],
                'parallel_tracks': [...],
                'duration_estimate': '...'
            }
        """
        work_packages = ArchiMateElement.query.filter_by(
            type="WorkPackage", architecture_id=architecture_id
        ).all()

        if not work_packages:
            return {"dependencies": [], "critical_path": [], "parallel_tracks": []}

        # Build context for LLM
        packages_context = []
        for wp in work_packages:
            props = json.loads(wp.properties) if wp.properties else {}
            packages_context.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "description": wp.description,
                    "duration": props.get("duration", "unknown"),
                    "dependencies": props.get("dependencies", []),
                }
            )

        prompt = self._build_dependency_analysis_prompt(packages_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            dependency_data = json.loads(response)

            # Create dependency relationships
            for dep in dependency_data.get("dependencies", []):
                source_wp = db.session.get(ArchiMateElement, dep["source_id"])
                target_wp = db.session.get(ArchiMateElement, dep["target_id"])

                if source_wp and target_wp:
                    # Triggering relationship (source triggers target)
                    relationship = ArchiMateRelationship(
                        type="triggering",
                        source_id=dep["source_id"],
                        target_id=dep["target_id"],
                        architecture_id=architecture_id,
                    )
                    db.session.add(relationship)

            db.session.commit()

            return dependency_data

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Work package dependency analysis failed: {str(e)}")

    # ========================================================================
    # Deliverable Methods
    # ========================================================================

    def identify_deliverables(
        self, work_package_id: int, deliverable_description: Optional[str] = None
    ) -> List[ArchiMateElement]:
        """
        Identify deliverables for a work package.

        Deliverable: Precisely-defined result of work package
        (Architecture document, Software release, Migration plan, Training materials)

        Args:
            work_package_id: ID of the WorkPackage
            deliverable_description: Optional deliverable description

        Returns:
            List of Deliverable ArchiMateElements
        """
        work_package = db.session.get(ArchiMateElement, work_package_id)
        if not work_package or work_package.type != "WorkPackage":
            raise ValueError(f"WorkPackage {work_package_id} not found")

        prompt = self._build_deliverable_prompt(work_package, deliverable_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            deliverables_data = json.loads(response)

            deliverables = []
            for deliv_info in deliverables_data.get("deliverables", []):
                deliverable = self._create_implementation_element(
                    deliv_info, work_package.architecture_id, element_type="Deliverable"
                )

                # WorkPackage realizes Deliverable
                relationship = ArchiMateRelationship(
                    type="realization",
                    source_id=work_package_id,
                    target_id=deliverable.id,
                    architecture_id=work_package.architecture_id,
                )
                db.session.add(relationship)

                deliverables.append(deliverable)

            db.session.commit()
            return deliverables

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Deliverable identification failed: {str(e)}")

    def map_deliverable_to_architecture(
        self, deliverable_id: int, architecture_element_id: int
    ) -> ArchiMateRelationship:
        """
        Map deliverable to architecture element it realizes/implements.

        Args:
            deliverable_id: ID of the Deliverable
            architecture_element_id: ID of architecture element (ApplicationComponent, etc.)

        Returns:
            ArchiMateRelationship (realization)
        """
        deliverable = db.session.get(ArchiMateElement, deliverable_id)
        arch_element = db.session.get(ArchiMateElement, architecture_element_id)

        if not deliverable or deliverable.type != "Deliverable":
            raise ValueError(f"Deliverable {deliverable_id} not found")
        if not arch_element:
            raise ValueError(f"Architecture element {architecture_element_id} not found")

        # Deliverable realizes architecture element
        relationship = ArchiMateRelationship(
            type="realization",
            source_id=deliverable_id,
            target_id=architecture_element_id,
            architecture_id=deliverable.architecture_id,
        )

        db.session.add(relationship)
        db.session.commit()

        return relationship

    # ========================================================================
    # Gap Analysis Methods
    # ========================================================================

    def perform_gap_analysis(
        self,
        current_plateau_id: int,
        target_plateau_id: int,
        business_context: Optional[str] = None,
    ) -> List[ArchiMateElement]:
        """
        Perform gap analysis between current and target plateaus.

        Gap: Statement of difference between two plateaus

        Args:
            current_plateau_id: ID of current Plateau
            target_plateau_id: ID of target Plateau
            business_context: Optional business context

        Returns:
            List of Gap ArchiMateElements
        """
        current_plateau = db.session.get(ArchiMateElement, current_plateau_id)
        target_plateau = db.session.get(ArchiMateElement, target_plateau_id)

        if not current_plateau or current_plateau.type != "Plateau":
            raise ValueError(f"Current Plateau {current_plateau_id} not found")
        if not target_plateau or target_plateau.type != "Plateau":
            raise ValueError(f"Target Plateau {target_plateau_id} not found")

        prompt = self._build_gap_analysis_prompt(current_plateau, target_plateau, business_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            gaps_data = json.loads(response)

            gaps = []
            for gap_info in gaps_data.get("gaps", []):
                gap = self._create_implementation_element(
                    gap_info, current_plateau.architecture_id, element_type="Gap"
                )

                # Gap associates current and target plateaus
                # Current -> Gap
                rel1 = ArchiMateRelationship(
                    type="association",
                    source_id=current_plateau_id,
                    target_id=gap.id,
                    architecture_id=current_plateau.architecture_id,
                )
                db.session.add(rel1)

                # Gap -> Target
                rel2 = ArchiMateRelationship(
                    type="association",
                    source_id=gap.id,
                    target_id=target_plateau_id,
                    architecture_id=current_plateau.architecture_id,
                )
                db.session.add(rel2)

                gaps.append(gap)

            db.session.commit()
            return gaps

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Gap analysis failed: {str(e)}")

    def prioritize_gaps(self, architecture_id: int, business_context: str) -> List[Dict]:
        """
        Prioritize identified gaps.

        Args:
            architecture_id: ID of the ArchitectureModel
            business_context: Business context for prioritization

        Returns:
            List of gaps with priority scores
        """
        gaps = ArchiMateElement.query.filter_by(type="Gap", architecture_id=architecture_id).all()

        if not gaps:
            return []

        # Build context for LLM
        gaps_context = []
        for gap in gaps:
            props = json.loads(gap.properties) if gap.properties else {}
            gaps_context.append(
                {
                    "id": gap.id,
                    "name": gap.name,
                    "description": gap.description,
                    "gap_type": props.get("gap_type", "unknown"),
                }
            )

        prompt = self._build_gap_prioritization_prompt(gaps_context, business_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            prioritization_data = json.loads(response)

            # Update gap properties with priority
            for gap_priority in prioritization_data.get("prioritized_gaps", []):
                gap = db.session.get(ArchiMateElement, gap_priority["gap_id"])
                if gap:
                    props = json.loads(gap.properties) if gap.properties else {}
                    props["priority_score"] = gap_priority["priority_score"]
                    props["priority_rationale"] = gap_priority["rationale"]
                    gap.properties = json.dumps(props)

            db.session.commit()

            return prioritization_data.get("prioritized_gaps", [])

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Gap prioritization failed: {str(e)}")

    # ========================================================================
    # Plateau (Transition State) Methods
    # ========================================================================

    def create_plateau(
        self,
        plateau_name: str,
        plateau_description: str,
        architecture_id: int,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> ArchiMateElement:
        """
        Create a plateau (transition architecture state).

        Plateau: Relatively stable state during limited time period

        Args:
            plateau_name: Plateau name
            plateau_description: Description of the state
            architecture_id: ID of the ArchitectureModel
            start_date: Optional start date (ISO format)
            end_date: Optional end date (ISO format)

        Returns:
            Plateau ArchiMateElement
        """
        properties = {
            "start_date": start_date or utcnow().isoformat(),
            "end_date": end_date,
            "created_at": utcnow().isoformat(),
        }

        plateau = ArchiMateElement(
            name=plateau_name,
            type="Plateau",
            layer="implementation_migration",
            description=plateau_description,
            properties=json.dumps(properties),
            architecture_id=architecture_id,
        )

        db.session.add(plateau)
        db.session.commit()

        return plateau

    def model_plateau_architecture(
        self, plateau_id: int, architecture_element_ids: List[int]
    ) -> List[ArchiMateRelationship]:
        """
        Link architecture elements active in a plateau.

        Args:
            plateau_id: ID of the Plateau
            architecture_element_ids: List of architecture element IDs in this plateau

        Returns:
            List of aggregation relationships
        """
        plateau = db.session.get(ArchiMateElement, plateau_id)
        if not plateau or plateau.type != "Plateau":
            raise ValueError(f"Plateau {plateau_id} not found")

        relationships = []

        for element_id in architecture_element_ids:
            element = db.session.get(ArchiMateElement, element_id)
            if not element:
                continue

            # Plateau aggregates architecture element (element exists in this plateau)
            relationship = ArchiMateRelationship(
                type="aggregation",
                source_id=plateau_id,
                target_id=element_id,
                architecture_id=plateau.architecture_id,
            )
            db.session.add(relationship)
            relationships.append(relationship)

        db.session.commit()
        return relationships

    # ========================================================================
    # Migration Planning Methods
    # ========================================================================

    def generate_migration_roadmap(
        self,
        current_plateau_id: int,
        target_plateau_id: int,
        business_context: str,
        constraints: Optional[str] = None,
    ) -> Dict:
        """
        Generate migration roadmap from current to target state.

        Args:
            current_plateau_id: ID of current Plateau
            target_plateau_id: ID of target Plateau
            business_context: Business context
            constraints: Optional migration constraints (budget, timeline, etc.)

        Returns:
            Dict with migration roadmap:
            {
                'transition_plateaus': [...],
                'work_packages': [...],
                'dependencies': [...],
                'timeline': '...',
                'risks': [...]
            }
        """
        current_plateau = db.session.get(ArchiMateElement, current_plateau_id)
        target_plateau = db.session.get(ArchiMateElement, target_plateau_id)

        if not current_plateau or current_plateau.type != "Plateau":
            raise ValueError(f"Current Plateau {current_plateau_id} not found")
        if not target_plateau or target_plateau.type != "Plateau":
            raise ValueError(f"Target Plateau {target_plateau_id} not found")

        prompt = self._build_migration_roadmap_prompt(
            current_plateau, target_plateau, business_context, constraints
        )

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            roadmap_data = json.loads(response)

            # Create transition plateaus
            transition_plateaus = []
            for plateau_info in roadmap_data.get("transition_plateaus", []):
                plateau = self.create_plateau(
                    plateau_name=plateau_info["name"],
                    plateau_description=plateau_info["description"],
                    architecture_id=current_plateau.architecture_id,
                    start_date=plateau_info.get("start_date"),
                    end_date=plateau_info.get("end_date"),
                )
                transition_plateaus.append(plateau)

            # Create work packages
            work_packages = []
            for pkg_info in roadmap_data.get("work_packages", []):
                package = self._create_implementation_element(
                    pkg_info, current_plateau.architecture_id, element_type="WorkPackage"
                )
                work_packages.append(package)

            db.session.commit()

            return {
                "transition_plateaus": [p.id for p in transition_plateaus],
                "work_packages": [wp.id for wp in work_packages],
                "dependencies": roadmap_data.get("dependencies", []),
                "timeline": roadmap_data.get("timeline", ""),
                "risks": roadmap_data.get("risks", []),
            }

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Migration roadmap generation failed: {str(e)}")

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _create_implementation_element(
        self, element_info: Dict, architecture_id: int, element_type: str
    ) -> ArchiMateElement:
        """Create Implementation & Migration Layer ArchiMateElement."""
        properties = element_info.get("properties", {})
        properties["created_at"] = utcnow().isoformat()

        element = ArchiMateElement(
            name=element_info["name"],
            type=element_type,
            layer="implementation_migration",
            description=element_info.get("description", ""),
            documentation=element_info.get("documentation", ""),
            properties=json.dumps(properties),
            architecture_id=architecture_id,
        )

        db.session.add(element)
        db.session.flush()
        return element

    # ========================================================================
    # Prompt Building Methods
    # ========================================================================

    def _build_work_package_prompt(self, project_plan: str) -> str:
        """Build work package identification prompt."""
        return f"""Identify WORK PACKAGES from this project plan.

Project Plan:
{project_plan}

A Work Package is a series of actions achieving goals:
- Projects: Major initiatives
- Programs: Collection of related projects
- Phases: Major project phases
- Sprints: Agile iterations
- Tasks: Individual work items
- Migration waves: Groups of applications/systems to migrate

For each work package:
- name: Work package name
- description: What will be done
- work_package_type: project | program | phase | sprint | task | migration_wave
- duration: Time estimate
- effort: Effort estimate (person-days/weeks)
- start_date: Planned start (if known)
- end_date: Planned end (if known)
- owner: Responsible party
- dependencies: Other work packages this depends on
- parent: Parent work package (if hierarchical)

Return JSON:
{{
  "work_packages": [
    {{
      "name": "Phase 1: Assessment & Planning",
      "description": "Assess current state, identify gaps, create migration plan",
      "work_package_type": "phase",
      "duration": "3 months",
      "effort": "120 person-days",
      "start_date": "2024 - 01 - 01",
      "end_date": "2024 - 03 - 31",
      "owner": "Architecture Team",
      "dependencies": [],
      "parent": null,
      "properties": {{
        "budget": "150,000",
        "risk_level": "medium"
      }}
    }},
    {{
      "name": "Current State Documentation",
      "description": "Document existing architecture, applications, infrastructure",
      "work_package_type": "task",
      "duration": "6 weeks",
      "effort": "30 person-days",
      "start_date": "2024 - 01 - 01",
      "end_date": "2024 - 02 - 15",
      "owner": "Enterprise Architect",
      "dependencies": [],
      "parent": "Phase 1: Assessment & Planning"
    }}
  ]
}}
"""

    def _build_dependency_analysis_prompt(self, packages_context: List[Dict]) -> str:
        """Build work package dependency analysis prompt."""
        return f"""Analyze DEPENDENCIES between work packages.

Work Packages:
{json.dumps(packages_context, indent=2)}

Identify:
1. **Dependencies**: Which packages must complete before others can start
2. **Critical Path**: Longest sequence of dependent activities
3. **Parallel Tracks**: Work packages that can run in parallel
4. **Duration Estimate**: Total timeline from start to finish

Return JSON:
{{
  "dependencies": [
    {{
      "source_id": 1,
      "target_id": 2,
      "dependency_type": "finish_to_start",
      "lag": "0 days"
    }}
  ],
  "critical_path": [1, 3, 5, 7],
  "parallel_tracks": [
    [2, 4],
    [6, 8]
  ],
  "duration_estimate": "18 months"
}}
"""

    def _build_deliverable_prompt(
        self, work_package: ArchiMateElement, deliverable_description: Optional[str]
    ) -> str:
        """Build deliverable identification prompt."""
        desc_section = (
            f"\n\nDeliverable Description:\n{deliverable_description}"
            if deliverable_description
            else ""
        )

        return f"""Identify DELIVERABLES for this work package.

Work Package: {work_package.name}
Description: {work_package.description}
{desc_section}

A Deliverable is a precisely-defined result:
- Architecture documents (current state, target state, gap analysis)
- Software releases (version 1.0, patch 1.2.3)
- Migration plans (data migration plan, cutover plan)
- Training materials (user guides, training videos)
- Infrastructure (new datacenter, cloud environment)

For each deliverable:
- name: Deliverable name
- description: What it contains
- deliverable_type: document | software_release | plan | training | infrastructure | report
- acceptance_criteria: How to verify completion
- due_date: When it's due

Return JSON:
{{
  "deliverables": [
    {{
      "name": "Current State Architecture Document",
      "description": "Comprehensive documentation of existing architecture",
      "deliverable_type": "document",
      "acceptance_criteria": "Reviewed and approved by architecture board",
      "due_date": "2024 - 02 - 15",
      "properties": {{
        "format": "PDF",
        "page_count": "50 - 75 pages",
        "sections": ["Business Architecture", "Application Architecture", "Technology Architecture"]
      }}
    }}
  ]
}}
"""

    def _build_gap_analysis_prompt(
        self,
        current_plateau: ArchiMateElement,
        target_plateau: ArchiMateElement,
        business_context: Optional[str],
    ) -> str:
        """Build gap analysis prompt."""
        context_section = f"\n\nBusiness Context:\n{business_context}" if business_context else ""

        return f"""Perform GAP ANALYSIS between current and target states.

Current State: {current_plateau.name}
{current_plateau.description}

Target State: {target_plateau.name}
{target_plateau.description}
{context_section}

Identify gaps in:
1. **Capabilities**: Missing business capabilities
2. **Applications**: Applications to retire/add/modify
3. **Technology**: Infrastructure/platform gaps
4. **Data**: Data quality, integration, migration gaps
5. **Processes**: Process improvements needed
6. **Skills**: Skills/training gaps

For each gap:
- name: Gap name
- description: Detailed gap description
- gap_type: capability | application | technology | data | process | skills
- severity: critical | high | medium | low
- effort_to_close: Effort estimate to close the gap

Return JSON:
{{
  "gaps": [
    {{
      "name": "Legacy CRM System",
      "description": "Current on-premises CRM lacks mobile access, modern UI, and integration capabilities needed for target state",
      "gap_type": "application",
      "severity": "high",
      "effort_to_close": "9 months, 500K",
      "properties": {{
        "current_state": "On-premises SugarCRM 6.5",
        "target_state": "Cloud-based Salesforce with mobile access",
        "business_impact": "Sales team productivity -20%, customer satisfaction issues"
      }}
    }}
  ]
}}
"""

    def _build_gap_prioritization_prompt(
        self, gaps_context: List[Dict], business_context: str
    ) -> str:
        """Build gap prioritization prompt."""
        return f"""Prioritize these GAPS based on business value and urgency.

Gaps:
{json.dumps(gaps_context, indent=2)}

Business Context:
{business_context}

Prioritize based on:
- Business value (revenue impact, cost savings, strategic alignment)
- Urgency (regulatory deadline, competitive pressure, risk mitigation)
- Effort (complexity, cost, time)
- Dependencies (prerequisite for other gaps)

For each gap, assign priority_score (1 - 100) where:
- 90 - 100: Critical, immediate action required
- 70 - 89: High priority, address in next phase
- 40 - 69: Medium priority, plan for future
- 1 - 39: Low priority, defer or eliminate

Return JSON:
{{
  "prioritized_gaps": [
    {{
      "gap_id": 1,
      "priority_score": 95,
      "rationale": "Regulatory compliance deadline in 6 months, 5M penalty risk"
    }}
  ]
}}
"""

    def _build_migration_roadmap_prompt(
        self,
        current_plateau: ArchiMateElement,
        target_plateau: ArchiMateElement,
        business_context: str,
        constraints: Optional[str],
    ) -> str:
        """Build migration roadmap generation prompt."""
        constraints_section = f"\n\nConstraints:\n{constraints}" if constraints else ""

        return f"""Generate MIGRATION ROADMAP from current to target state.

Current State: {current_plateau.name}
{current_plateau.description}

Target State: {target_plateau.name}
{target_plateau.description}

Business Context:
{business_context}
{constraints_section}

Create:
1. **Transition Plateaus**: Stable intermediate states (6 - 12 month increments)
2. **Work Packages**: Major initiatives to reach each plateau
3. **Dependencies**: Sequencing and prerequisites
4. **Timeline**: High-level schedule
5. **Risks**: Key migration risks

Return JSON:
{{
  "transition_plateaus": [
    {{
      "name": "Plateau 1: Foundation",
      "description": "Cloud infrastructure and core services established",
      "start_date": "2024 - 04 - 01",
      "end_date": "2024 - 09 - 30"
    }}
  ],
  "work_packages": [
    {{
      "name": "Cloud Infrastructure Setup",
      "description": "Provision AWS environment, networking, security baseline",
      "work_package_type": "project",
      "duration": "3 months",
      "properties": {{
        "plateau": "Plateau 1: Foundation"
      }}
    }}
  ],
  "dependencies": [
    {{
      "source": "Cloud Infrastructure Setup",
      "target": "Database Migration",
      "dependency_type": "finish_to_start"
    }}
  ],
  "timeline": "18 months total: Foundation (6mo), Transition (8mo), Optimization (4mo)",
  "risks": [
    {{
      "risk": "Data migration complexity",
      "probability": "high",
      "impact": "high",
      "mitigation": "Phased migration with extensive testing, parallel run period"
    }}
  ]
}}
"""
