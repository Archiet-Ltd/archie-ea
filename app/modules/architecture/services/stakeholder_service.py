"""
AI-Powered Stakeholder Analysis Service for ArchiMate 3.2 Motivation Layer

This service provides comprehensive Stakeholder identification and analysis:
- AI-powered stakeholder identification from business context
- Power/Interest matrix analysis
- Stakeholder concern extraction
- Influence mapping
- Engagement strategy recommendations
- Stakeholder-to-requirement traceability

ArchiMate 3.2 Compliance:
- Stakeholder is a Motivation Layer element
- Stakeholder has interest in Outcomes
- Stakeholder has concerns (Assessments)
- Stakeholder influences Goals
- Requirements address Stakeholder concerns
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from app import db
from app.models import ArchiMateElement, Requirement
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class StakeholderService:
    """
    AI-powered service for Stakeholder identification and analysis.

    Capabilities:
    - Identify stakeholders from business context
    - Analyze stakeholder power and interest
    - Extract stakeholder concerns
    - Map stakeholder influence
    - Recommend engagement strategies
    - Trace stakeholder-to-requirement links
    """

    def __init__(self):
        self.llm_service = LLMService()

    # ========================================================================
    # Stakeholder Identification Methods
    # ========================================================================

    def identify_stakeholders(
        self, business_context: str, architecture_id: int, capability_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """
        Identify stakeholders from business context using AI.

        Stakeholder Types:
        - Executive Sponsor
        - Business Owner
        - End User
        - IT Operations
        - Compliance Officer
        - External Customer
        - Vendor/Supplier
        - Regulator

        Args:
            business_context: Business context describing initiative
            architecture_id: ID of the ArchitectureModel
            capability_id: Optional BusinessCapability ID for capability-specific stakeholders

        Returns:
            List of Stakeholder ArchiMateElements

        Example:
            >>> context = '''
            ... Building customer portal for EU customers.
            ... Must comply with GDPR.
            ... CFO requires ROI within 18 months.
            ... 50K existing customers will use portal.
            ... '''
            >>> stakeholders = service.identify_stakeholders(context, arch_id=1)
            >>> # Returns: CFO, Customers, Compliance Officer, etc.
        """
        prompt = self._build_stakeholder_identification_prompt(business_context, capability_id)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            stakeholders_data = json.loads(response)

            stakeholders = []
            for stakeholder_info in stakeholders_data.get("stakeholders", []):
                stakeholder = self._create_stakeholder(stakeholder_info, architecture_id)
                stakeholders.append(stakeholder)

            db.session.commit()
            return stakeholders

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Stakeholder identification failed: {str(e)}")

    # ========================================================================
    # Power/Interest Analysis Methods
    # ========================================================================

    def analyze_stakeholder_influence(
        self, stakeholder_id: int, business_context: Optional[str] = None
    ) -> Dict:
        """
        Analyze stakeholder power and interest using Power/Interest Matrix.

        Power/Interest Matrix:
        - High Power, High Interest → Manage Closely (key players)
        - High Power, Low Interest → Keep Satisfied (important, but not engaged)
        - Low Power, High Interest → Keep Informed (supportive, but less influential)
        - Low Power, Low Interest → Monitor (minimal effort)

        Args:
            stakeholder_id: ID of the Stakeholder ArchiMateElement
            business_context: Optional context for analysis

        Returns:
            Dict with influence analysis:
            {
                'stakeholder_id': 5,
                'stakeholder_name': 'CFO',
                'power_level': 'high',  # high, medium, low
                'interest_level': 'high',  # high, medium, low
                'influence_score': 85,  # 0 - 100
                'engagement_strategy': 'manage_closely',
                'engagement_actions': [...],
                'communication_frequency': 'weekly'
            }
        """
        stakeholder = db.session.get(ArchiMateElement, stakeholder_id)
        if not stakeholder or stakeholder.type != "Stakeholder":
            raise ValueError(f"Stakeholder {stakeholder_id} not found")

        prompt = self._build_influence_analysis_prompt(stakeholder, business_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            influence_data = json.loads(response)

            # Update stakeholder properties with analysis
            props = json.loads(stakeholder.properties) if stakeholder.properties else {}
            props["influence_analysis"] = influence_data
            props["analyzed_at"] = datetime.utcnow().isoformat()

            stakeholder.properties = json.dumps(props)
            stakeholder.priority = influence_data.get("power_level", "medium")

            db.session.commit()

            return influence_data

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Influence analysis failed: {str(e)}")

    # ========================================================================
    # Concern Extraction Methods
    # ========================================================================

    def extract_stakeholder_concerns(
        self,
        stakeholder_id: int,
        interview_notes: Optional[str] = None,
        business_context: Optional[str] = None,
    ) -> List[Dict]:
        """
        Extract stakeholder concerns using AI.

        Concerns are specific issues/interests stakeholders have.

        Args:
            stakeholder_id: ID of the Stakeholder ArchiMateElement
            interview_notes: Optional stakeholder interview transcripts
            business_context: Optional business context

        Returns:
            List of concerns:
            [
                {
                    'concern': 'ROI must be positive within 18 months',
                    'category': 'financial',
                    'priority': 'critical',
                    'addressable': True,
                    'how_to_address': 'Include ROI projection in business case'
                }
            ]
        """
        stakeholder = db.session.get(ArchiMateElement, stakeholder_id)
        if not stakeholder or stakeholder.type != "Stakeholder":
            raise ValueError(f"Stakeholder {stakeholder_id} not found")

        prompt = self._build_concern_extraction_prompt(
            stakeholder, interview_notes, business_context
        )

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            concerns_data = json.loads(response)

            # Store concerns in stakeholder properties
            props = json.loads(stakeholder.properties) if stakeholder.properties else {}
            props["concerns"] = concerns_data.get("concerns", [])
            props["concerns_extracted_at"] = datetime.utcnow().isoformat()

            stakeholder.properties = json.dumps(props)
            db.session.commit()

            return concerns_data.get("concerns", [])

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Concern extraction failed: {str(e)}")

    # ========================================================================
    # Requirement Mapping Methods
    # ========================================================================

    def map_stakeholders_to_requirements(
        self, stakeholder_id: int, architecture_id: Optional[int] = None
    ) -> List[Requirement]:
        """
        Map stakeholder to requirements that address their concerns.

        Args:
            stakeholder_id: ID of the Stakeholder ArchiMateElement
            architecture_id: Optional architecture ID (uses stakeholder's if not provided)

        Returns:
            List of Requirements addressing stakeholder concerns
        """
        stakeholder = db.session.get(ArchiMateElement, stakeholder_id)
        if not stakeholder or stakeholder.type != "Stakeholder":
            raise ValueError(f"Stakeholder {stakeholder_id} not found")

        arch_id = architecture_id or stakeholder.architecture_id

        # Get stakeholder concerns
        props = json.loads(stakeholder.properties) if stakeholder.properties else {}
        concerns = props.get("concerns", [])

        if not concerns:
            # Extract concerns first
            concerns = self.extract_stakeholder_concerns(stakeholder_id)

        # Get all requirements
        requirements = Requirement.query.filter_by(architecture_id=arch_id).all()

        if not requirements:
            return []

        # Use AI to map concerns to requirements
        prompt = self._build_requirement_mapping_prompt(stakeholder, concerns, requirements)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            mapping_data = json.loads(response)

            mapped_requirements = []
            for mapping in mapping_data.get("mappings", []):
                req_id = mapping["requirement_id"]
                req = db.session.get(Requirement, req_id)
                if req:
                    # Link stakeholder to requirement
                    req.stakeholder_id = stakeholder_id
                    mapped_requirements.append(req)

            db.session.commit()
            return mapped_requirements

        except Exception:
            # As in DriverService: the rollback means nothing was mapped, so []
            # is accurate — but the failure must be recorded, or an LLM outage
            # reads exactly like "no requirement matched this stakeholder".
            logger.exception(
                "map_stakeholders_to_requirements(stakeholder=%s) failed; nothing mapped",
                stakeholder_id,
            )
            db.session.rollback()
            return []

    # ========================================================================
    # Engagement Strategy Methods
    # ========================================================================

    def recommend_engagement_strategy(self, stakeholder_id: int) -> Dict:
        """
        Recommend stakeholder engagement strategy based on power/interest.

        Args:
            stakeholder_id: ID of the Stakeholder ArchiMateElement

        Returns:
            Dict with engagement recommendations:
            {
                'strategy': 'manage_closely',
                'communication_frequency': 'weekly',
                'communication_channels': ['executive briefings', 'steering committee'],
                'engagement_actions': [...],
                'escalation_path': '...'
            }
        """
        # First analyze influence if not done yet
        influence_data = self.analyze_stakeholder_influence(stakeholder_id)

        strategy = influence_data.get("engagement_strategy")

        if strategy == "manage_closely":
            return {
                "strategy": "manage_closely",
                "communication_frequency": "weekly",
                "communication_channels": [
                    "One-on-one executive briefings",
                    "Steering committee meetings",
                    "Monthly progress reports",
                ],
                "engagement_actions": [
                    "Involve in key decisions",
                    "Regular status updates",
                    "Address concerns proactively",
                    "Seek input on major milestones",
                ],
                "escalation_path": "Direct escalation to project sponsor",
            }
        elif strategy == "keep_satisfied":
            return {
                "strategy": "keep_satisfied",
                "communication_frequency": "bi-weekly",
                "communication_channels": ["Email updates", "Quarterly reviews"],
                "engagement_actions": [
                    "Keep informed of progress",
                    "Address concerns when raised",
                    "Seek approval at key gates",
                ],
                "escalation_path": "Through steering committee",
            }
        elif strategy == "keep_informed":
            return {
                "strategy": "keep_informed",
                "communication_frequency": "monthly",
                "communication_channels": ["Project newsletter", "Town halls", "Intranet updates"],
                "engagement_actions": [
                    "Regular communications",
                    "Respond to questions",
                    "Gather feedback",
                ],
                "escalation_path": "Through project manager",
            }
        else:  # monitor
            return {
                "strategy": "monitor",
                "communication_frequency": "quarterly",
                "communication_channels": ["General project updates", "As-needed communications"],
                "engagement_actions": [
                    "Minimal active engagement",
                    "Monitor for changes in interest/power",
                ],
                "escalation_path": "Standard project channels",
            }

    # ========================================================================
    # Stakeholder Map (SolutionStakeholder) Methods
    #
    # The two methods above (identify_stakeholders, recommend_engagement_strategy)
    # operate on ArchiMateElement rows scoped to an ArchitectureModel. The
    # Stakeholder Map page (app/templates/stakeholders/map.html) is built on a
    # different table — SolutionStakeholder, scoped to a Solution via
    # SolutionStakeholderMapping — so those methods don't apply there directly.
    # These two reuse the same prompts/LLM call pattern for that page instead of
    # duplicating the AI-calling logic in the route layer.
    # ========================================================================

    def identify_stakeholders_from_context(self, business_context: str) -> List[Dict]:
        """
        AI-suggest stakeholders from free-text business context for the
        Stakeholder Map page. Returns raw suggestion dicts and does NOT persist —
        the map page lets the user review suggestions and add the ones they want
        via the existing POST /api/stakeholders/ endpoint, which creates
        SolutionStakeholder rows.

        Args:
            business_context: Free text describing the initiative

        Returns:
            List of suggestion dicts: {name, description, type, role, department, interest}

        Raises:
            ValueError: business_context is blank
            Exception: LLM call failed or returned unparseable JSON
        """
        if not business_context or not business_context.strip():
            raise ValueError("business_context is required")

        prompt = self._build_stakeholder_identification_prompt(business_context, None)
        response = self.llm_service.generate_from_prompt(prompt)
        data = json.loads(response)
        return data.get("stakeholders", [])

    def recommend_engagement_strategy_for_solution_stakeholder(self, stakeholder) -> Dict:
        """
        AI-generated engagement strategy for a SolutionStakeholder.

        Unlike recommend_engagement_strategy() above — which returns one of four
        canned, quadrant-keyed responses — this asks the LLM to tailor
        communication channels/actions/escalation path to this stakeholder's
        actual description, attitude and recorded concerns, using the quadrant
        already known deterministically from their Power/Interest grid position
        (SolutionStakeholder.quadrant, set by the user dragging the node).

        Args:
            stakeholder: A SolutionStakeholder instance

        Returns:
            Dict: {strategy, communication_frequency, communication_channels,
                   engagement_actions, escalation_path}

        Raises:
            Exception: LLM call failed or returned unparseable JSON
        """
        prompt = self._build_solution_stakeholder_engagement_prompt(stakeholder)
        response = self.llm_service.generate_from_prompt(prompt)
        return json.loads(response)

    def _build_solution_stakeholder_engagement_prompt(self, stakeholder) -> str:
        """Build engagement-strategy prompt for a SolutionStakeholder."""
        concerns = stakeholder.concerns or []
        concerns_str = "\n".join(f"- {c}" for c in concerns) if concerns else "(none recorded)"
        stype = stakeholder.stakeholder_type.value if stakeholder.stakeholder_type else "individual"
        attitude = stakeholder.attitude.value if stakeholder.attitude else "neutral"

        return f"""You are a stakeholder engagement expert.

Recommend an engagement strategy for this stakeholder based on their Power/Interest
position, attitude and known concerns.

Stakeholder: {stakeholder.name}
Type: {stype}
Description: {stakeholder.description or "(none provided)"}
Power/Interest Quadrant: {stakeholder.quadrant} (Influence {stakeholder.influence_level}/5, Interest {stakeholder.interest_level}/5)
Current Attitude: {attitude}
Known Concerns:
{concerns_str}

Power/Interest Matrix:
- manage_closely: High power, high interest (key players)
- keep_satisfied: High power, low interest (keep happy but don't over-communicate)
- keep_informed: Low power, high interest (supportive allies)
- monitor: Low power, low interest (minimal effort)

Tailor the recommendation to this stakeholder specifically — reference their
concerns and attitude where relevant, don't just restate the generic quadrant advice.

Return JSON:
{{
  "strategy": "{stakeholder.quadrant}",
  "communication_frequency": "weekly",
  "communication_channels": ["...", "..."],
  "engagement_actions": ["...", "..."],
  "escalation_path": "..."
}}
"""

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _create_stakeholder(self, stakeholder_info: Dict, architecture_id: int) -> ArchiMateElement:
        """Create Stakeholder ArchiMateElement."""
        properties = {
            "stakeholder_type": stakeholder_info.get("type"),
            "role": stakeholder_info.get("role"),
            "department": stakeholder_info.get("department"),
            "identified_at": datetime.utcnow().isoformat(),
        }

        stakeholder = ArchiMateElement(
            name=stakeholder_info["name"],
            type="Stakeholder",
            layer="motivation",
            description=stakeholder_info.get("description", ""),
            stakeholder_interest=stakeholder_info.get("interest", ""),
            properties=json.dumps(properties),
            architecture_id=architecture_id,
        )

        db.session.add(stakeholder)
        return stakeholder

    # ========================================================================
    # Prompt Building Methods
    # ========================================================================

    def _build_stakeholder_identification_prompt(
        self, business_context: str, capability_id: Optional[int]
    ) -> str:
        """Build stakeholder identification prompt."""
        capability_section = ""
        if capability_id:
            from app.models import BusinessCapability

            capability = db.session.get(BusinessCapability, capability_id)
            if capability:
                capability_section = (
                    f"\n\nCapability: {capability.name}\nDescription: {capability.description}"
                )

        return f"""You are a stakeholder analysis expert.

Identify all STAKEHOLDERS who have interest in or influence over this initiative.

Business Context:
{business_context}
{capability_section}

Stakeholder Types to Consider:
- **Executive Sponsor**: C-level exec accountable for success
- **Business Owner**: Business unit leader owning the capability
- **End User**: People who will use the solution daily
- **IT Operations**: Team responsible for running/maintaining systems
- **Compliance Officer**: Ensures regulatory compliance
- **Security Officer**: Ensures security requirements met
- **External Customer**: Customers impacted by changes
- **Vendor/Supplier**: Third parties providing services
- **Regulator**: Government/regulatory bodies

For each stakeholder:
- name: Role/title (e.g., "CFO", "EU Customers", "Compliance Officer")
- description: Brief description of who they are
- type: From types above
- role: Specific job title if known
- department: Department/organization
- interest: What they care about in this initiative

Return JSON:
{{
  "stakeholders": [
    {{
      "name": "CFO",
      "description": "Chief Financial Officer, accountable for financial performance",
      "type": "Executive Sponsor",
      "role": "Chief Financial Officer",
      "department": "Finance",
      "interest": "ROI must be positive within 18 months, reduce operational costs by 20%"
    }},
    {{
      "name": "EU Customers",
      "description": "50,000 existing customers in European Union",
      "type": "External Customer",
      "role": "Customer",
      "department": "External",
      "interest": "Easy-to-use portal, data privacy protection, fast performance"
    }},
    {{
      "name": "Data Protection Officer",
      "description": "Ensures GDPR compliance across organization",
      "type": "Compliance Officer",
      "role": "DPO",
      "department": "Legal/Compliance",
      "interest": "Full GDPR compliance, data residency in EU, consent management"
    }}
  ]
}}

Identify 5 - 10 key stakeholders from the context.
"""

    def _build_influence_analysis_prompt(
        self, stakeholder: ArchiMateElement, business_context: Optional[str]
    ) -> str:
        """Build influence analysis prompt."""
        context_section = f"\n\nBusiness Context:\n{business_context}" if business_context else ""

        return f"""Analyze stakeholder power and interest using Power/Interest Matrix.

Stakeholder:
Name: {stakeholder.name}
Description: {stakeholder.description}
Interest: {stakeholder.stakeholder_interest}
{context_section}

Assess:

1. **Power Level** (ability to influence project):
   - high: Can make/break project (exec sponsor, budget holder)
   - medium: Significant influence on decisions
   - low: Limited decision-making authority

2. **Interest Level** (how much they care):
   - high: Deeply invested in outcome, actively engaged
   - medium: Interested but not primary focus
   - low: Peripheral interest only

3. **Engagement Strategy** (based on Power/Interest Matrix):
   - manage_closely: High power, high interest (key players)
   - keep_satisfied: High power, low interest (keep happy but not over-communicate)
   - keep_informed: Low power, high interest (supportive allies)
   - monitor: Low power, low interest (minimal effort)

4. **Influence Score**: 0 - 100 (combination of power and interest)

Return JSON:
{{
  "stakeholder_id": {stakeholder.id},
  "stakeholder_name": "{stakeholder.name}",
  "power_level": "high",
  "power_reasoning": "Controls project budget and can cancel initiative",
  "interest_level": "high",
  "interest_reasoning": "Personally committed to achieving 20% cost reduction target",
  "influence_score": 90,
  "engagement_strategy": "manage_closely",
  "engagement_actions": [
    "Weekly one-on-one status briefings",
    "Involve in milestone decisions",
    "Address concerns immediately",
    "Demonstrate ROI progress monthly"
  ],
  "communication_frequency": "weekly"
}}
"""

    def _build_concern_extraction_prompt(
        self,
        stakeholder: ArchiMateElement,
        interview_notes: Optional[str],
        business_context: Optional[str],
    ) -> str:
        """Build concern extraction prompt."""
        notes_section = f"\n\nInterview Notes:\n{interview_notes}" if interview_notes else ""
        context_section = f"\n\nBusiness Context:\n{business_context}" if business_context else ""

        return f"""Extract stakeholder concerns (specific issues/interests they care about).

Stakeholder:
Name: {stakeholder.name}
Description: {stakeholder.description}
Interest: {stakeholder.stakeholder_interest}
{notes_section}
{context_section}

Identify specific concerns:

Categories:
- financial: ROI, cost, budget
- schedule: Timeline, deadlines
- quality: Performance, reliability, usability
- risk: Security, compliance, operational risk
- strategic: Alignment with strategy, competitive advantage
- operational: Day-to-day operations, maintenance

For each concern:
- concern: Specific issue/interest
- category: From categories above
- priority: critical | high | medium | low
- addressable: Can this be addressed in project? (true/false)
- how_to_address: How to address this concern

Return JSON:
{{
  "concerns": [
    {{
      "concern": "ROI must be positive within 18 months",
      "category": "financial",
      "priority": "critical",
      "addressable": true,
      "how_to_address": "Include detailed ROI projection in business case. Track cost savings monthly. Demonstrate quick wins in first 6 months."
    }},
    {{
      "concern": "Cannot afford any GDPR violations",
      "category": "risk",
      "priority": "critical",
      "addressable": true,
      "how_to_address": "Implement GDPR compliance requirements from day 1. Legal review at each stage. Third-party compliance audit before launch."
    }}
  ]
}}

Extract 3 - 8 key concerns.
"""

    def _build_requirement_mapping_prompt(
        self, stakeholder: ArchiMateElement, concerns: List[Dict], requirements: List[Requirement]
    ) -> str:
        """Build requirement mapping prompt."""
        concerns_str = "\n".join(
            [f"- {c.get('concern')} (Priority: {c.get('priority')})" for c in concerns]
        )

        requirements_str = "\n".join(
            [
                f"ID: {r.id}, Title: {r.title}, Description: {r.description[:100]}"
                for r in requirements[:50]
            ]
        )

        return f"""Map stakeholder concerns to requirements that address them.

Stakeholder: {stakeholder.name}

Stakeholder Concerns:
{concerns_str}

Available Requirements:
{requirements_str}

For each mapping:
- requirement_id: ID of requirement
- concern_addressed: Which concern does it address
- how_it_addresses: How requirement addresses the concern

Return JSON:
{{
  "mappings": [
    {{
      "requirement_id": 7,
      "concern_addressed": "ROI must be positive within 18 months",
      "how_it_addresses": "This requirement includes cost tracking and ROI dashboard to monitor financial performance"
    }}
  ]
}}

Only map requirements that genuinely address stakeholder concerns.
"""



# ============================================================================
# Transformation programme stakeholder map (no model call)
# ============================================================================


def programme_for_map(programme_id):
    """The transformation programme with this id in the caller's organisation,
    or ``None``. StrategicInitiative is tenant-scoped, so another
    organisation's programme is simply not found."""
    from app.models.strategic import StrategicInitiative

    if not programme_id:
        return None
    return StrategicInitiative.query.filter(
        StrategicInitiative.id == programme_id,
        StrategicInitiative.record_kind == "transformation_programme",
    ).first()


def programmes_for_map():
    """The organisation's live transformation programmes, by name."""
    from app.models.strategic import StrategicInitiative

    return (
        StrategicInitiative.query.filter(
            StrategicInitiative.record_kind == "transformation_programme",
            StrategicInitiative.archived_at.is_(None),
        )
        .order_by(StrategicInitiative.name)
        .all()
    )


def programme_owner_suggestions(programme_id) -> Dict:
    """People the programme's records name as owners of what it affects.

    What a programme affects is recorded on its transformation options, as
    the business capabilities each option changes; a capability records its
    business owner and IT owner by name. Each suggestion says which
    capability and which ownership it comes from. Names already on the
    programme's map are left out. Nothing is inferred: a programme with no
    options, or whose affected capabilities record no owner, gets no
    suggestions, and ``affected_count`` says how many affected capabilities
    were looked at.
    """
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_stakeholder import SolutionStakeholder, SolutionStakeholderMapping
    from app.models.transformation_decision import TransformationOption
    from app.models.transformation_programme import ProgrammeWorkstream

    options = (
        TransformationOption.query.join(
            ProgrammeWorkstream, ProgrammeWorkstream.id == TransformationOption.workstream_id
        )
        .filter(ProgrammeWorkstream.programme_id == programme_id)
        .all()
    )
    capability_ids = sorted({
        int(cid) for option in options for cid in (option.affected_capability_ids or [])
        if str(cid).isdigit()
    })
    capabilities = (
        BusinessCapability.query.filter(BusinessCapability.id.in_(capability_ids))
        .order_by(BusinessCapability.name)
        .all()
        if capability_ids else []
    )
    on_map = {
        (name or "").strip().lower()
        for (name,) in db.session.query(SolutionStakeholder.name)
        .join(SolutionStakeholderMapping, SolutionStakeholderMapping.stakeholder_id == SolutionStakeholder.id)
        .filter(SolutionStakeholderMapping.programme_id == programme_id)
        .all()
    }
    by_name: Dict[str, Dict] = {}
    for capability in capabilities:
        for label, owner in (("Business owner", capability.business_owner),
                             ("IT owner", capability.it_owner)):
            name = (owner or "").strip()
            if not name or name.lower() in on_map:
                continue
            entry = by_name.setdefault(name.lower(), {"name": name, "basis": []})
            entry["basis"].append("%s of %s" % (label, capability.name))
    return {
        "affected_count": len(capabilities),
        "suggestions": sorted(by_name.values(), key=lambda s: s["name"].lower()),
    }
