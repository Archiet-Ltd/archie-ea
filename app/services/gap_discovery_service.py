"""
Intelligent Gap Discovery Service for Enterprise Portfolio

This service automatically discovers gaps in the enterprise portfolio by analyzing
applications, business capabilities, and architecture elements against business requirements
and best practices.

Features:
- Automatic gap detection across all ArchiMate layers
- Business capability coverage analysis
- Application lifecycle gap identification
- Technology stack gap analysis
- Process and workflow gap detection
- Risk-based gap prioritization
- Automated gap reporting and recommendations

Complies with:
- ArchiMate 3.2 Specification
- Enterprise Architecture best practices
- TOGAF ADM guidelines
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


from app.models.business_capabilities import BusinessCapability

from .. import db
from . import gap_register_service
from ..models.application_portfolio import ApplicationComponent
from ..models.models import ArchiMateElement

logger = logging.getLogger(__name__)


class GapDiscoveryService:
    """
    Intelligent gap discovery service for enterprise portfolio analysis.
    """

    def __init__(self):
        self.gap_types = {
            "capability": "Business Capability Gap",
            "application": "Application Gap",
            "technology": "Technology Gap",
            "process": "Process Gap",
            "data": "Data Gap",
            "integration": "Integration Gap",
            "security": "Security Gap",
            "compliance": "Compliance Gap",
        }

        self.gap_severity_levels = {
            "critical": {"score": 4, "color": "red", "impact": "Business critical"},
            "high": {"score": 3, "color": "orange", "impact": "Significant business impact"},
            "medium": {"score": 2, "color": "yellow", "impact": "Moderate business impact"},
            "low": {"score": 1, "color": "blue", "impact": "Minor business impact"},
        }

    def discover_all_gaps(self, architecture_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Discover all types of gaps in the enterprise portfolio.

        Args:
            architecture_id: Optional architecture model ID to scope analysis

        Returns:
            Dictionary containing discovered gaps and analysis summary
        """
        logger.info("Starting comprehensive gap discovery analysis")

        gaps = []

        # Discover different types of gaps
        gaps.extend(self.discover_capability_gaps(architecture_id))
        gaps.extend(self.discover_application_gaps(architecture_id))
        gaps.extend(self.discover_technology_gaps(architecture_id))
        gaps.extend(self.discover_process_gaps(architecture_id))
        gaps.extend(self.discover_data_gaps(architecture_id))
        gaps.extend(self.discover_integration_gaps(architecture_id))
        gaps.extend(self.discover_security_gaps(architecture_id))
        gaps.extend(self.discover_compliance_gaps(architecture_id))

        # Analyze and prioritize gaps
        prioritized_gaps = self.prioritize_gaps(gaps)

        # Generate summary statistics
        summary = self.generate_gap_summary(prioritized_gaps)

        logger.info(f"Gap discovery completed: {len(prioritized_gaps)} gaps identified")

        return {
            "gaps": prioritized_gaps,
            "summary": summary,
            "discovery_timestamp": datetime.utcnow().isoformat(),
            "architecture_id": architecture_id,
        }

    def discover_capability_gaps(
        self, architecture_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Discover business capability gaps by analyzing capability coverage.
        """
        gaps = []

        try:
            # Get all business capabilities
            capabilities = BusinessCapability.query.all()

            # Analyze capability coverage
            for capability in capabilities:
                gap_analysis = self._analyze_capability_coverage(capability)

                if gap_analysis["has_gaps"]:
                    gaps.append(
                        {
                            "id": f"capability_gap_{capability.id}",
                            "name": f"Capability Gap: {capability.name}",
                            "gap_type": "capability",
                            "element_id": capability.id,
                            "element_name": capability.name,
                            "gap_description": gap_analysis["description"],
                            "baseline_state": gap_analysis["baseline"],
                            "target_state": gap_analysis["target"],
                            "impact_level": gap_analysis["severity"],
                            "business_risk": gap_analysis["risk"],
                            "urgency": gap_analysis["urgency"],
                            "affected_elements": gap_analysis["affected_elements"],
                            "proposed_solution": gap_analysis["solution"],
                            "success_criteria": gap_analysis["criteria"],
                            "properties": {
                                "capability_level": capability.level,
                                "capability_category": capability.category,
                                "coverage_percentage": gap_analysis["coverage_percentage"],
                            },
                        }
                    )

        except Exception as e:
            logger.error(f"Error discovering capability gaps: {e}")

        return gaps

    def discover_application_gaps(
        self, architecture_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Discover application gaps by analyzing application lifecycle and coverage.
        """
        gaps = []

        try:
            # Get all applications
            applications = ApplicationComponent.query.all()

            for app in applications:
                # Analyze application lifecycle
                lifecycle_gaps = self._analyze_application_lifecycle(app)
                gaps.extend(lifecycle_gaps)

                # Analyze application coverage
                coverage_gaps = self._analyze_application_coverage(app)
                gaps.extend(coverage_gaps)

        except Exception as e:
            logger.error(f"Error discovering application gaps: {e}")

        return gaps

    def discover_technology_gaps(
        self, architecture_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Discover technology gaps by analyzing technology stack and infrastructure.
        """
        gaps = []

        try:
            # Get technology layer elements
            tech_elements = ArchiMateElement.query.filter_by(layer="technology").all()

            # Analyze technology stack
            tech_stack_gaps = self._analyze_technology_stack(tech_elements)
            gaps.extend(tech_stack_gaps)

            # Analyze infrastructure coverage
            infra_gaps = self._analyze_infrastructure_coverage(tech_elements)
            gaps.extend(infra_gaps)

        except Exception as e:
            logger.error(f"Error discovering technology gaps: {e}")

        return gaps

    def discover_process_gaps(self, architecture_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Discover process gaps by analyzing business processes and workflows.
        """
        gaps = []

        try:
            # Get business process elements
            process_elements = (
                ArchiMateElement.query.filter_by(layer="business")
                .filter(ArchiMateElement.type.in_(["BusinessProcess", "BusinessFunction"]))
                .all()
            )

            for process in process_elements:
                process_gaps = self._analyze_process_coverage(process)
                gaps.extend(process_gaps)

        except Exception as e:
            logger.error(f"Error discovering process gaps: {e}")

        return gaps

    def discover_data_gaps(self, architecture_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Discover data gaps by analyzing data objects and information flows.
        """
        gaps = []

        try:
            # Get data objects
            data_objects = ArchiMateElement.query.filter_by(type="DataObject").all()

            # Analyze data coverage and quality
            data_gaps = self._analyze_data_coverage(data_objects)
            gaps.extend(data_gaps)

        except Exception as e:
            logger.error(f"Error discovering data gaps: {e}")

        return gaps

    def discover_integration_gaps(
        self, architecture_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Discover integration gaps by analyzing application interfaces and connections.
        """
        gaps = []

        try:
            # Get application interfaces
            interfaces = ArchiMateElement.query.filter_by(type="ApplicationInterface").all()

            # Analyze integration coverage
            integration_gaps = self._analyze_integration_coverage(interfaces)
            gaps.extend(integration_gaps)

        except Exception as e:
            logger.error(f"Error discovering integration gaps: {e}")

        return gaps

    def discover_security_gaps(self, architecture_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Discover security gaps by analyzing security controls and compliance.
        """
        gaps = []

        try:
            # Analyze security controls across all layers
            security_gaps = self._analyze_security_coverage()
            gaps.extend(security_gaps)

        except Exception as e:
            logger.error(f"Error discovering security gaps: {e}")

        return gaps

    def discover_compliance_gaps(
        self, architecture_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Discover compliance gaps by analyzing regulatory requirements.
        """
        gaps = []

        try:
            # Analyze compliance requirements
            compliance_gaps = self._analyze_compliance_coverage()
            gaps.extend(compliance_gaps)

        except Exception as e:
            logger.error(f"Error discovering compliance gaps: {e}")

        return gaps

    def _analyze_capability_coverage(self, capability: BusinessCapability) -> Dict[str, Any]:
        """
        Analyze coverage of a business capability.
        """
        # Check if capability has supporting applications. ApplicationComponent
        # has no 'business_capabilities' relationship; it maps via
        # capability_mappings (ApplicationCapabilityMapping.business_capability_id).
        supporting_apps = ApplicationComponent.query.filter(
            ApplicationComponent.capability_mappings.any(
                business_capability_id=capability.id
            )
        ).count()

        # Check if capability has defined processes
        supporting_processes = (
            ArchiMateElement.query.filter_by(layer="business")
            .filter(ArchiMateElement.properties.like(f"%{capability.name}%"))
            .count()
        )

        coverage_percentage = 0
        if supporting_apps > 0 or supporting_processes > 0:
            coverage_percentage = min(100, (supporting_apps * 40) + (supporting_processes * 20))

        has_gaps = coverage_percentage < 80  # Less than 80% coverage indicates gap

        if has_gaps:
            return {
                "has_gaps": True,
                "description": f"Business capability '{capability.name}' has insufficient support ({coverage_percentage}% coverage)",
                "baseline": f"Current coverage: {supporting_apps} applications, {supporting_processes} processes",
                "target": "Target: Minimum 80% coverage with adequate application and process support",
                "severity": "high" if coverage_percentage < 50 else "medium",
                "risk": "high" if capability.level == "strategic" else "medium",
                "urgency": "high" if coverage_percentage < 50 else "medium",
                "affected_elements": [f"Capability: {capability.name}"],
                "solution": f"Develop or acquire applications to support {capability.name} capability",
                "criteria": "Achieve 80%+ coverage with measurable business outcomes",
                "coverage_percentage": coverage_percentage,
            }

        return {"has_gaps": False, "coverage_percentage": coverage_percentage}

    def _analyze_application_lifecycle(self, app: ApplicationComponent) -> List[Dict[str, Any]]:
        """
        Analyze application lifecycle and identify retirement/replacement gaps.
        """
        gaps = []

        # Check for legacy applications
        if hasattr(app, "technology_stack") and app.technology_stack:
            tech_stack = app.technology_stack.lower()
            legacy_indicators = ["mainframe", "cobol", "vb6", "classic asp", "powerbuilder"]

            if any(indicator in tech_stack for indicator in legacy_indicators):
                gaps.append(
                    {
                        "id": f"legacy_app_gap_{app.id}",
                        "name": f"Legacy Application: {app.name}",
                        "gap_type": "application",
                        "element_id": app.id,
                        "element_name": app.name,
                        "gap_description": f"Application '{app.name}' uses legacy technology stack",
                        "baseline_state": f"Current: {app.technology_stack}",
                        "target_state": "Modern technology stack with cloud-native capabilities",
                        "impact_level": "high",
                        "business_risk": "high",
                        "urgency": "medium",
                        "affected_elements": [f"Application: {app.name}"],
                        "proposed_solution": "Plan migration to modern platform or replacement",
                        "success_criteria": "Application migrated or retired within 24 months",
                        "properties": {
                            "technology_stack": app.technology_stack,
                            "gap_category": "legacy_technology",
                        },
                    }
                )

        # Check for applications near end-of-life
        if hasattr(app, "support_end_date") and app.support_end_date:
            support_end = app.support_end_date
            if support_end < datetime.now() + timedelta(days=365):  # Within 1 year
                urgency = (
                    "critical" if support_end < datetime.now() + timedelta(days=90) else "high"
                )

                gaps.append(
                    {
                        "id": f"eol_app_gap_{app.id}",
                        "name": f"End-of-Life Application: {app.name}",
                        "gap_type": "application",
                        "element_id": app.id,
                        "element_name": app.name,
                        "gap_description": f"Application '{app.name}' support ends on {support_end.strftime('%Y-%m-%d')}",
                        "baseline_state": f"Support ending: {support_end.strftime('%Y-%m-%d')}",
                        "target_state": "Application migrated, replaced, or retired before EOL",
                        "impact_level": "critical",
                        "business_risk": "critical",
                        "urgency": urgency,
                        "affected_elements": [f"Application: {app.name}"],
                        "proposed_solution": "Immediate planning for migration or replacement",
                        "success_criteria": "Migration plan in place and execution started",
                        "properties": {
                            "support_end_date": support_end.isoformat(),
                            "gap_category": "end_of_life",
                        },
                    }
                )

        return gaps

    def _analyze_application_coverage(self, app: ApplicationComponent) -> List[Dict[str, Any]]:
        """
        Analyze application coverage and identify missing capabilities.
        """
        gaps = []

        # Check for applications without clear business purpose
        if not app.description or len(app.description.strip()) < 20:
            gaps.append(
                {
                    "id": f"purpose_gap_{app.id}",
                    "name": f"Undefined Purpose: {app.name}",
                    "gap_type": "application",
                    "element_id": app.id,
                    "element_name": app.name,
                    "gap_description": f"Application '{app.name}' lacks clear business purpose description",
                    "baseline_state": "No or insufficient business purpose documented",
                    "target_state": "Clear business value and purpose documented",
                    "impact_level": "medium",
                    "business_risk": "medium",
                    "urgency": "low",
                    "affected_elements": [f"Application: {app.name}"],
                    "proposed_solution": "Define and document business purpose and value proposition",
                    "success_criteria": "Business purpose documented with measurable outcomes",
                    "properties": {"gap_category": "governance"},
                }
            )

        return gaps

    def _analyze_technology_stack(
        self, tech_elements: List[ArchiMateElement]
    ) -> List[Dict[str, Any]]:
        """
        Analyze technology stack for gaps and modernization opportunities.
        """
        gaps = []

        # Check for missing cloud capabilities
        cloud_elements = [elem for elem in tech_elements if "cloud" in elem.name.lower()]
        if len(cloud_elements) < len(tech_elements) * 0.3:  # Less than 30% cloud adoption
            gaps.append(
                {
                    "id": "cloud_adoption_gap",
                    "name": "Cloud Adoption Gap",
                    "gap_type": "technology",
                    "element_id": None,
                    "element_name": "Technology Stack",
                    "gap_description": "Insufficient cloud adoption in technology portfolio",
                    "baseline_state": f"Current cloud elements: {len(cloud_elements)}/{len(tech_elements)}",
                    "target_state": "Target: 70%+ cloud-native technology stack",
                    "impact_level": "high",
                    "business_risk": "medium",
                    "urgency": "medium",
                    "affected_elements": [f"Technology elements: {len(tech_elements)}"],
                    "proposed_solution": "Develop cloud migration strategy and roadmap",
                    "success_criteria": "70%+ of technology elements cloud-native within 3 years",
                    "properties": {
                        "current_cloud_percentage": (len(cloud_elements) / len(tech_elements))
                        * 100,
                        "gap_category": "modernization",
                    },
                }
            )

        return gaps

    def _analyze_infrastructure_coverage(
        self, tech_elements: List[ArchiMateElement]
    ) -> List[Dict[str, Any]]:
        """
        Analyze infrastructure coverage and gaps.
        """
        gaps = []

        # Check for missing disaster recovery capabilities
        dr_elements = [
            elem
            for elem in tech_elements
            if "disaster" in elem.name.lower() or "recovery" in elem.name.lower()
        ]
        if len(dr_elements) == 0:
            gaps.append(
                {
                    "id": "disaster_recovery_gap",
                    "name": "Disaster Recovery Gap",
                    "gap_type": "technology",
                    "element_id": None,
                    "element_name": "Infrastructure",
                    "gap_description": "No disaster recovery capabilities identified",
                    "baseline_state": "No DR infrastructure documented",
                    "target_state": "Comprehensive disaster recovery and business continuity",
                    "impact_level": "critical",
                    "business_risk": "critical",
                    "urgency": "high",
                    "affected_elements": ["Infrastructure components"],
                    "proposed_solution": "Implement disaster recovery and business continuity planning",
                    "success_criteria": "DR capabilities implemented and tested quarterly",
                    "properties": {"gap_category": "resilience"},
                }
            )

        return gaps

    def _analyze_process_coverage(self, process: ArchiMateElement) -> List[Dict[str, Any]]:
        """
        Analyze business process coverage and gaps.
        """
        gaps = []

        # properties is a TEXT column holding JSON; normalise to a dict so .get works.
        props = process.properties
        if isinstance(props, str):
            try:
                props = json.loads(props) if props.strip() else {}
            except (ValueError, TypeError):
                props = {}
        props = props or {}

        # Check for processes without clear owners
        if not props.get("owner"):
            gaps.append(
                {
                    "id": f"process_owner_gap_{process.id}",
                    "name": f"Process Owner Gap: {process.name}",
                    "gap_type": "process",
                    "element_id": process.id,
                    "element_name": process.name,
                    "gap_description": f"Process '{process.name}' lacks defined owner",
                    "baseline_state": "No process owner documented",
                    "target_state": "Clear process ownership and accountability",
                    "impact_level": "medium",
                    "business_risk": "medium",
                    "urgency": "medium",
                    "affected_elements": [f"Process: {process.name}"],
                    "proposed_solution": "Assign process owner and document responsibilities",
                    "success_criteria": "Process owner assigned and documented",
                    "properties": {"gap_category": "governance"},
                }
            )

        return gaps

    def _analyze_data_coverage(self, data_objects: List[ArchiMateElement]) -> List[Dict[str, Any]]:
        """
        Analyze data coverage and quality gaps.
        """
        gaps = []

        # Check for missing data governance
        governance_elements = [
            elem
            for elem in data_objects
            if "governance" in elem.name.lower() or "quality" in elem.name.lower()
        ]
        if len(governance_elements) == 0:
            gaps.append(
                {
                    "id": "data_governance_gap",
                    "name": "Data Governance Gap",
                    "gap_type": "data",
                    "element_id": None,
                    "element_name": "Data Objects",
                    "gap_description": "No data governance framework identified",
                    "baseline_state": "No data governance capabilities documented",
                    "target_state": "Comprehensive data governance and quality management",
                    "impact_level": "high",
                    "business_risk": "high",
                    "urgency": "high",
                    "affected_elements": ["Data objects and information assets"],
                    "proposed_solution": "Implement data governance framework and quality controls",
                    "success_criteria": "Data governance policies and procedures implemented",
                    "properties": {"gap_category": "governance"},
                }
            )

        return gaps

    def _analyze_integration_coverage(
        self, interfaces: List[ArchiMateElement]
    ) -> List[Dict[str, Any]]:
        """
        Analyze integration coverage and gaps.
        """
        gaps = []

        # Check for API management capabilities
        api_elements = [elem for elem in interfaces if "api" in elem.name.lower()]
        if len(api_elements) < len(interfaces) * 0.5:  # Less than 50% API-based
            gaps.append(
                {
                    "id": "api_management_gap",
                    "name": "API Management Gap",
                    "gap_type": "integration",
                    "element_id": None,
                    "element_name": "Application Interfaces",
                    "gap_description": "Insufficient API-based integration capabilities",
                    "baseline_state": f"Current API interfaces: {len(api_elements)}/{len(interfaces)}",
                    "target_state": "70%+ of integrations using standardized APIs",
                    "impact_level": "medium",
                    "business_risk": "medium",
                    "urgency": "medium",
                    "affected_elements": ["Application interfaces"],
                    "proposed_solution": "Implement API management platform and standards",
                    "success_criteria": "70%+ of integrations API-based with proper governance",
                    "properties": {
                        "current_api_percentage": (len(api_elements) / len(interfaces)) * 100,
                        "gap_category": "modernization",
                    },
                }
            )

        return gaps

    def _analyze_security_coverage(self) -> List[Dict[str, Any]]:
        """
        Analyze security coverage and gaps.
        """
        gaps = []

        # Check for security architecture elements
        security_elements = ArchiMateElement.query.filter(
            ArchiMateElement.name.like("%security%")
            | ArchiMateElement.name.like("%authentication%")
            | ArchiMateElement.name.like("%authorization%")
        ).all()

        if len(security_elements) == 0:
            gaps.append(
                {
                    "id": "security_architecture_gap",
                    "name": "Security Architecture Gap",
                    "gap_type": "security",
                    "element_id": None,
                    "element_name": "Security Architecture",
                    "gap_description": "No security architecture elements documented",
                    "baseline_state": "No security architecture documented",
                    "target_state": "Comprehensive security architecture and controls",
                    "impact_level": "critical",
                    "business_risk": "critical",
                    "urgency": "critical",
                    "affected_elements": ["Enterprise architecture components"],
                    "proposed_solution": "Develop and implement security architecture framework",
                    "success_criteria": "Security architecture documented and implemented",
                    "properties": {"gap_category": "security"},
                }
            )

        return gaps

    def _analyze_compliance_coverage(self) -> List[Dict[str, Any]]:
        """
        Analyze compliance coverage and gaps.
        """
        gaps = []

        # Check for compliance frameworks
        compliance_elements = ArchiMateElement.query.filter(
            ArchiMateElement.name.like("%compliance%")
            | ArchiMateElement.name.like("%regulation%")
            | ArchiMateElement.name.like("%audit%")
        ).all()

        if len(compliance_elements) == 0:
            gaps.append(
                {
                    "id": "compliance_framework_gap",
                    "name": "Compliance Framework Gap",
                    "gap_type": "compliance",
                    "element_id": None,
                    "element_name": "Compliance Framework",
                    "gap_description": "No compliance framework elements documented",
                    "baseline_state": "No compliance framework documented",
                    "target_state": "Comprehensive compliance and audit framework",
                    "impact_level": "high",
                    "business_risk": "high",
                    "urgency": "high",
                    "affected_elements": ["Enterprise governance components"],
                    "proposed_solution": "Implement compliance framework and audit procedures",
                    "success_criteria": "Compliance framework documented and operational",
                    "properties": {"gap_category": "compliance"},
                }
            )

        return gaps

    def prioritize_gaps(self, gaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Prioritize gaps based on impact, urgency, and business risk.
        """

        def calculate_priority_score(gap):
            impact_score = self.gap_severity_levels.get(
                gap.get("impact_level", "low"), {"score": 1}
            )["score"]
            risk_score = self.gap_severity_levels.get(
                gap.get("business_risk", "low"), {"score": 1}
            )["score"]
            urgency_score = self.gap_severity_levels.get(gap.get("urgency", "low"), {"score": 1})[
                "score"
            ]

            return impact_score + risk_score + urgency_score

        # Calculate priority scores
        for gap in gaps:
            gap["priority_score"] = calculate_priority_score(gap)
            gap["priority"] = self._get_priority_from_score(gap["priority_score"])

        # Sort by priority score (descending)
        return sorted(gaps, key=lambda x: x["priority_score"], reverse=True)

    def _get_priority_from_score(self, score: int) -> str:
        """Convert priority score to priority level."""
        if score >= 10:
            return "critical"
        elif score >= 7:
            return "high"
        elif score >= 4:
            return "medium"
        else:
            return "low"

    def generate_gap_summary(self, gaps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate summary statistics for discovered gaps.
        """
        summary = {
            "total_gaps": len(gaps),
            "by_type": {},
            "by_severity": {},
            "by_priority": {},
            "by_urgency": {},
            "top_critical_gaps": [],
            "recommendations": [],
        }

        # Count by type
        for gap in gaps:
            gap_type = gap.get("gap_type", "unknown")
            summary["by_type"][gap_type] = summary["by_type"].get(gap_type, 0) + 1

        # Count by severity
        for gap in gaps:
            severity = gap.get("impact_level", "low")
            summary["by_severity"][severity] = summary["by_severity"].get(severity, 0) + 1

        # Count by priority
        for gap in gaps:
            priority = gap.get("priority", "low")
            summary["by_priority"][priority] = summary["by_priority"].get(priority, 0) + 1

        # Count by urgency
        for gap in gaps:
            urgency = gap.get("urgency", "low")
            summary["by_urgency"][urgency] = summary["by_urgency"].get(urgency, 0) + 1

        # Top critical gaps
        summary["top_critical_gaps"] = [gap for gap in gaps if gap.get("priority") == "critical"][
            :10
        ]

        # Generate recommendations
        summary["recommendations"] = self._generate_recommendations(gaps)

        return summary

    def _generate_recommendations(self, gaps: List[Dict[str, Any]]) -> List[str]:
        """
        Generate recommendations based on gap analysis.
        """
        recommendations = []

        # Analyze gap patterns
        critical_gaps = [gap for gap in gaps if gap.get("priority") == "critical"]
        high_gaps = [gap for gap in gaps if gap.get("priority") == "high"]

        if critical_gaps:
            recommendations.append(
                f"Immediate action required for {len(critical_gaps)} critical gaps"
            )

        if high_gaps:
            recommendations.append(
                f"Develop detailed action plans for {len(high_gaps)} high-priority gaps"
            )

        # Type-specific recommendations
        gap_types = {}
        for gap in gaps:
            gap_type = gap.get("gap_type", "unknown")
            gap_types[gap_type] = gap_types.get(gap_type, 0) + 1

        if gap_types.get("security", 0) > 0:
            recommendations.append("Implement comprehensive security architecture framework")

        if gap_types.get("compliance", 0) > 0:
            recommendations.append("Establish compliance and audit framework")

        if gap_types.get("technology", 0) > 0:
            recommendations.append("Develop technology modernization roadmap")

        if gap_types.get("capability", 0) > 0:
            recommendations.append("Strengthen business capability mapping and coverage")

        return recommendations

    def save_discovered_gaps(
        self,
        gaps_data: Dict[str, Any],
        architecture_id: Optional[int],
        organization_id: int,
    ) -> Dict[str, int]:
        """
        Save discovered gaps to the one gap register (gap_register_service.create_gap),
        refusing a gap already saved for this organisation and architecture.

        The previous version of this method constructed
        app.models.implementation_migration.Gap (imported here under the
        confusing local alias ImplementationGap) with column names that
        belong to a different, unrelated model also named ImplementationGap
        (app.models.implementation_planning.ImplementationGap) -- ORM columns
        Gap does not have. Every save raised a TypeError, was swallowed by a
        bare except, and nothing was ever saved.

        Args:
            gaps_data: Dictionary containing gaps and analysis (from discover_all_gaps)
            architecture_id: Architecture model ID the gaps were discovered against
            organization_id: The caller's organisation; required, never inferred

        Returns:
            {"saved": n, "duplicates": n, "failed": n} -- honest counts, never
            a fabricated 0 when something was actually found.
        """
        if not organization_id:
            raise ValueError("save_discovered_gaps requires organization_id")

        saved = duplicates = failed = 0

        for gap_data in gaps_data.get("gaps", []):
            description = gap_data.get("gap_description", "")
            for label, key in (
                ("Proposed solution", "proposed_solution"),
                ("Success criteria", "success_criteria"),
            ):
                value = gap_data.get(key)
                if value:
                    description = f"{description}\n\n{label}: {value}" if description else f"{label}: {value}"

            try:
                gap, created = gap_register_service.create_gap(
                    organization_id,
                    gap_data.get("name", ""),
                    description=description,
                    gap_type=(gap_data.get("gap_type") or "")[:30] or None,
                    priority=gap_data.get("priority", "medium"),
                    severity=gap_data.get("impact_level", "medium"),
                    architecture_id=architecture_id,
                    current_state_ref=(gap_data.get("baseline_state") or "")[:255] or None,
                    target_state_ref=(gap_data.get("target_state") or "")[:255] or None,
                    auto_generated=True,
                    generation_source="gap_discovery",
                )
            except ValueError as exc:
                logger.warning("gap discovery: skipped an unsaveable gap: %s", exc)
                failed += 1
                continue

            if created:
                saved += 1
            else:
                duplicates += 1

        db.session.commit()
        logger.info(
            "gap discovery: saved %d, %d already in the register, %d could not be saved",
            saved, duplicates, failed,
        )
        return {"saved": saved, "duplicates": duplicates, "failed": failed}
