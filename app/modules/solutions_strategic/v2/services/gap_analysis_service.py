"""
Architectural Gap Analysis Service

Identifies architectural gaps across multiple dimensions for Application Components.
Uses ArchiMate 3.2 relationships to detect missing elements, connections, and metadata.

Gap Types:
- Capability Gaps: Applications not supporting business capabilities
- Integration Gaps: Missing data flows and API connections
- Technology Gaps: Missing deployment/infrastructure documentation
- Process Gaps: Business processes without application support
- Data Gaps: Missing data objects and data management
- Compliance Gaps: Unmet regulatory requirements
- Metadata Gaps: Missing essential attributes
- Relationship Gaps: Missing ArchiMate relationships

Usage:
    analyzer = ArchitecturalGapAnalyzer()
    gaps = analyzer.analyze_application_gaps(application_id=123)
    portfolio_gaps = analyzer.analyze_portfolio_gaps()
"""

from datetime import date, datetime
from typing import Dict, List

from flask import current_app

from .decorators import transactional
from app.services.element_ownership_service import application_is_owned

from app import db
from app.models.application_layer import (
    ApplicationComponent,
    ApplicationInterface,
    DataObject,
)
from app.models.business_capabilities import BusinessCapability
from app.models.models import ArchiMateElement, ArchiMateRelationship, Requirement


class ArchitecturalGapAnalyzer:
    """
    Identifies architectural gaps for applications and portfolios.

    Provides comprehensive gap analysis across capability, integration,
    technology, process, data, compliance, and metadata dimensions.
    """

    @transactional
    def __init__(self):
        """Initialize the gap analyzer."""
        self.app = current_app._get_current_object() if current_app else None

    @transactional
    def analyze_application_gaps(self, application_id: int) -> Dict:
        """
        Complete gap analysis for a specific application.

        Args:
            application_id: ID of the ApplicationComponent

        Returns:
            Dictionary containing all gap categories with severity and recommendations
        """
        app_component = db.session.get(ApplicationComponent, application_id)

        if not app_component:
            return {"error": f"Application {application_id} not found"}

        # Analyze all gap dimensions
        capability_result = self._check_capability_coverage(app_component)
        gaps = {
            "application_id": application_id,
            "application_name": app_component.name,
            "timestamp": datetime.utcnow().isoformat(),
            "overall_score": 0,
            "high_priority_count": 0,
            "medium_priority_count": 0,
            "low_priority_count": 0,
            "capability_gaps": capability_result,
            "integration_gaps": self._check_integration_completeness(app_component),
            "technology_gaps": self._check_technology_stack(app_component),
            "process_gaps": self._check_process_support(app_component),
            "data_gaps": self._check_data_objects(app_component),
            "compliance_gaps": self._check_compliance_requirements(app_component),
            "metadata_gaps": self._check_essential_metadata(app_component),
            "relationship_gaps": self._check_archimate_relationships(app_component),
        }

        # Calculate overall metrics
        all_gaps = []
        # capability_gaps is now a dict with all_gaps, critical_gaps, refinement_gaps, gap_score
        all_gaps.extend(capability_result.get("all_gaps", []))
        for category in [
            "integration_gaps",
            "technology_gaps",
            "process_gaps",
            "data_gaps",
            "compliance_gaps",
            "metadata_gaps",
            "relationship_gaps",
        ]:
            all_gaps.extend(gaps[category])

        # Count by severity
        for gap in all_gaps:
            severity = gap.get("severity", "low")
            if severity == "critical" or severity == "high":
                gaps["high_priority_count"] += 1
            elif severity == "medium":
                gaps["medium_priority_count"] += 1
            else:
                gaps["low_priority_count"] += 1

        # Calculate overall score (0 - 100, where 100 is perfect)
        total_checks = 20  # Total possible checks
        issues_found = len(all_gaps)
        gaps["overall_score"] = max(0, int(100 * (1 - (issues_found / total_checks))))

        return gaps

    # Capability-level weighting multipliers for gap severity
    LEVEL_WEIGHT_MAP = {
        1: 3.0,  # L1 Strategic
        2: 2.0,  # L2 Tactical-Strategic
        3: 1.0,  # L3 Tactical
        4: 0.7,  # L4 Tactical-Operational
        5: 0.5,  # L5 Operational
    }
    CRITICAL_IMPORTANCE_MULTIPLIER = 1.5

    def _get_capability_weight(self, capability: "BusinessCapability") -> float:
        """
        Compute the weighting multiplier for a capability based on its level
        and strategic importance.

        L1 capabilities weigh 3x, L5 weigh 0.5x. Capabilities flagged as
        ``strategic_importance='critical'`` receive an additional 1.5x multiplier.
        """
        level = capability.level if capability.level else 3  # default to L3
        level_weight = self.LEVEL_WEIGHT_MAP.get(level, 1.0)

        if capability.strategic_importance == "critical":
            level_weight *= self.CRITICAL_IMPORTANCE_MULTIPLIER

        return level_weight

    @staticmethod
    def _base_severity_score(severity: str) -> float:
        """Map a severity label to a numeric base score."""
        return {
            "critical": 4.0,
            "high": 3.0,
            "medium": 2.0,
            "low": 1.0,
        }.get(severity, 1.0)

    def _check_capability_coverage(self, app: ApplicationComponent) -> Dict:
        """
        Check if application properly supports business capabilities.

        Returns a dictionary with:
        - ``all_gaps``: flat list of every gap (each with ``weighted_severity``)
        - ``critical_gaps``: gaps with weighted_severity >= 2.0
        - ``refinement_gaps``: gaps with weighted_severity < 2.0
        - ``gap_score``: weighted overall score (0.0–1.0) where 0 means no gaps
        """
        raw_gaps: List[Dict] = []

        # Check via application_capability_mapping table
        from app.models.application_capability import ApplicationCapabilityMapping

        # tenant-scoping-ok: app.id is from a TenantMixin-scoped ApplicationComponent load.
        capability_mappings = ApplicationCapabilityMapping.query.filter_by(
            application_component_id=app.id
        ).all()

        if not capability_mappings or len(capability_mappings) == 0:
            raw_gaps.append(
                {
                    "severity": "high",
                    "type": "capability_assignment",
                    "category": "capability",
                    "message": "Application not assigned to any business capabilities",
                    "impact": "Cannot assess business value or perform capability-based planning",
                    "recommendation": "Map this application to the capabilities it supports",
                    "fix_action": "assign_capabilities",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#capabilities",
                    "weighted_severity": self._base_severity_score("high"),
                }
            )
        else:
            # Check for single point of failure
            for mapping in capability_mappings:
                capability = mapping.business_capability
                if capability:
                    # Count how many apps support this capability
                    supporting_apps_count = ApplicationCapabilityMapping.query.filter_by(
                        business_capability_id=capability.id
                    ).count()  # model-safety-ok  # tenant-scoping-ok: FK id (app/capability) already org-scoped via TenantMixin load

                    if supporting_apps_count == 1:
                        base = self._base_severity_score("medium")
                        weight = self._get_capability_weight(capability)
                        weighted = round(base * weight, 4)

                        raw_gaps.append(
                            {
                                "severity": "medium",
                                "type": "single_point_of_failure",
                                "category": "capability",
                                "message": f'Capability "{capability.name}" has only one supporting application',
                                "impact": "Single point of failure - capability at risk if app fails",
                                "recommendation": "Consider redundancy or disaster recovery plan",
                                "fix_action": "add_redundancy",
                                "fix_url": f"/capabilities/{capability.id}",
                                "capability_level": capability.level,
                                "strategic_importance": capability.strategic_importance,
                                "weighted_severity": weighted,
                            }
                        )

        # Partition into critical vs refinement gaps
        critical_gaps = [g for g in raw_gaps if g.get("weighted_severity", 0) >= 2.0]
        refinement_gaps = [g for g in raw_gaps if g.get("weighted_severity", 0) < 2.0]

        # Compute weighted gap_score = sum(weighted) / max_possible
        # max_possible = number_of_gaps * highest_possible_weighted_severity
        # highest_possible = base_severity("critical") * L1_weight * critical_multiplier
        #                  = 4.0 * 3.0 * 1.5 = 18.0
        max_single = (
            self._base_severity_score("critical")
            * self.LEVEL_WEIGHT_MAP[1]
            * self.CRITICAL_IMPORTANCE_MULTIPLIER
        )
        total_weighted = sum(g.get("weighted_severity", 0) for g in raw_gaps)
        num_gaps = len(raw_gaps) if raw_gaps else 1
        gap_score = round(total_weighted / (num_gaps * max_single), 4) if raw_gaps else 0.0

        return {
            "all_gaps": raw_gaps,
            "critical_gaps": critical_gaps,
            "refinement_gaps": refinement_gaps,
            "gap_score": gap_score,
        }

    @transactional
    def _check_integration_completeness(self, app: ApplicationComponent) -> List[Dict]:
        """Check for missing integrations based on data dependencies."""
        gaps = []

        # Check if app has interfaces documented
        interfaces_count = (
            db.session.query(ApplicationInterface)
            .filter_by(application_component_id=app.id)
            .count()
        )

        if interfaces_count == 0 and app.exposes_api:
            gaps.append(
                {
                    "severity": "medium",
                    "type": "interfaces_not_documented",
                    "category": "integration",
                    "message": "Application exposes APIs but no interfaces documented",
                    "impact": "Cannot assess API dependencies or integration complexity",
                    "recommendation": "Document all exposed APIs and interfaces",
                    "fix_action": "add_interfaces",
                    "fix_url": f"/application_mgmt/applications/{app.id}/interfaces/create",
                }
            )

        # Check if dependencies are documented (via interfaces_count and dependencies_count)
        if app.dependencies_count and app.dependencies_count > 0:
            # Check if we have interface records for dependencies
            if interfaces_count == 0:
                gaps.append(
                    {
                        "severity": "high",
                        "type": "dependencies_not_documented",
                        "category": "integration",
                        "message": f"Application has {app.dependencies_count} dependencies but no integration details",
                        "impact": "Cannot perform impact analysis or trace data flows",
                        "recommendation": "Document integration points and data dependencies",
                        "fix_action": "document_integrations",
                        "fix_url": f"/application_mgmt/applications/{app.id}/edit#integrations",
                    }
                )

        # Check for data objects
        data_objects_count = (
            db.session.query(DataObject).filter_by(application_component_id=app.id).count()
        )

        if data_objects_count == 0:
            gaps.append(
                {
                    "severity": "medium",
                    "type": "data_objects_missing",
                    "category": "integration",
                    "message": "No data objects documented for this application",
                    "impact": "Cannot perform data flow analysis or GDPR compliance checks",
                    "recommendation": "Document what data this application manages",
                    "fix_action": "add_data_objects",
                    "fix_url": f"/application_mgmt/applications/{app.id}/data-objects/create",
                }
            )

        return gaps

    def _check_technology_stack(self, app: ApplicationComponent) -> List[Dict]:
        """Check if technology components and deployment are documented."""
        gaps = []

        # Check deployment model
        if not app.deployment_model:
            gaps.append(
                {
                    "severity": "high",
                    "type": "deployment_model_missing",
                    "category": "technology",
                    "message": "Deployment model not specified (On-Premise, Cloud, Hybrid, SaaS)",
                    "impact": "Cannot assess hosting costs, scalability, or cloud strategy alignment",
                    "recommendation": "Specify deployment model",
                    "fix_action": "set_deployment_model",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#deployment",
                }
            )

        # Check cloud provider if deployment is cloud-based
        if app.deployment_model in ["Cloud", "Hybrid"] and not app.cloud_provider:
            gaps.append(
                {
                    "severity": "medium",
                    "type": "cloud_provider_missing",
                    "category": "technology",
                    "message": "Cloud-based deployment but provider not specified",
                    "impact": "Cannot assess vendor lock-in or multi-cloud strategy",
                    "recommendation": "Specify cloud provider (AWS, Azure, GCP)",
                    "fix_action": "set_cloud_provider",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#deployment",
                }
            )

        # Check technology stack
        if not app.technology_stack:
            gaps.append(
                {
                    "severity": "medium",
                    "type": "technology_stack_missing",
                    "category": "technology",
                    "message": "Technology stack not documented",
                    "impact": "Cannot assess technical debt, skill requirements, or modernization needs",
                    "recommendation": "Document technology stack (languages, frameworks, databases)",
                    "fix_action": "document_tech_stack",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#technology",
                }
            )

        # Check for ArchiMate Technology Layer linkage
        if app.archimate_element_id:
            tech_relationships = self._get_technology_assignments(app.archimate_element_id)
            if not tech_relationships:
                gaps.append(
                    {
                        "severity": "low",
                        "type": "technology_layer_not_linked",
                        "category": "technology",
                        "message": "Not linked to Technology Layer components (servers, containers, etc.)",
                        "impact": "Cannot assess infrastructure dependencies or deployment architecture",
                        "recommendation": "Link to Technology Layer elements in ArchiMate model",
                        "fix_action": "link_technology_layer",
                        "fix_url": f"/application_mgmt/applications/{app.id}/archimate",
                    }
                )

        return gaps

    def _check_process_support(self, app: ApplicationComponent) -> List[Dict]:
        """Check if application supports documented business processes."""
        gaps = []

        # Check via supported_processes relationship
        processes = app.supported_processes if hasattr(app, "supported_processes") else []  # model-safety-ok

        if not processes or len(processes) == 0:
            gaps.append(
                {
                    "severity": "medium",
                    "type": "process_linkage_missing",
                    "category": "process",
                    "message": "Not linked to any business processes",
                    "impact": "Cannot assess business process impact if app fails or changes",
                    "recommendation": "Document which business processes use this application",
                    "fix_action": "link_processes",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#processes",
                }
            )

        return gaps

    def _check_data_objects(self, app: ApplicationComponent) -> List[Dict]:
        """Check for critical data objects and data management policies."""
        gaps = []

        # Check if app handles PII but no data retention policy
        if app.pii_data_processed and not app.data_retention_policy:
            gaps.append(
                {
                    "severity": "high",
                    "type": "data_retention_policy_missing",
                    "category": "data",
                    "message": "Processes PII data but no data retention policy documented",
                    "impact": "GDPR compliance risk - potential regulatory penalties",
                    "recommendation": "Define and implement data retention policy",
                    "fix_action": "add_retention_policy",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#data-governance",
                }
            )

        # Check data classification
        if not app.data_classification:
            gaps.append(
                {
                    "severity": "medium",
                    "type": "data_classification_missing",
                    "category": "data",
                    "message": "Data classification not specified",
                    "impact": "Cannot assess security controls or access requirements",
                    "recommendation": "Classify data (Public, Internal, Confidential, Restricted)",
                    "fix_action": "classify_data",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#data-governance",
                }
            )

        # Check if master data source is documented (use getattr for backward compat)
        if getattr(app, "master_data_source", None) and not app.primary_data_store:  # model-safety-ok: column may not exist (WFT-019)
            gaps.append(
                {
                    "severity": "low",
                    "type": "primary_data_store_missing",
                    "category": "data",
                    "message": "Marked as master data source but primary data store not specified",
                    "impact": "Cannot identify system of record for data governance",
                    "recommendation": "Specify primary data store",
                    "fix_action": "set_data_store",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#data-management",
                }
            )

        return gaps

    def _check_compliance_requirements(self, app: ApplicationComponent) -> List[Dict]:
        """Check if compliance requirements are met."""
        gaps = []

        # Check GDPR compliance if handling PII
        if app.pii_data_processed and not app.gdpr_compliant:
            gaps.append(
                {
                    "severity": "critical",
                    "type": "gdpr_compliance_missing",
                    "category": "compliance",
                    "message": "Processes PII but not marked as GDPR compliant",
                    "impact": "Legal and regulatory risk - potential fines up to 4% of revenue",
                    "recommendation": "Complete GDPR compliance assessment and implement required controls",
                    "fix_action": "assess_gdpr",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#compliance",
                }
            )

        # Check security audit date
        if app.last_security_audit_date:
            days_since_audit = (date.today() - app.last_security_audit_date).days
            if days_since_audit > 365:
                gaps.append(
                    {
                        "severity": "medium",
                        "type": "security_audit_overdue",
                        "category": "compliance",
                        "message": f"Last security audit was {days_since_audit} days ago (>1 year)",
                        "impact": "Security vulnerabilities may be undetected",
                        "recommendation": "Schedule security audit",
                        "fix_action": "schedule_audit",
                        "fix_url": f"/application_mgmt/applications/{app.id}/edit#security",
                    }
                )
        else:
            gaps.append(
                {
                    "severity": "medium",
                    "type": "security_audit_never",
                    "category": "compliance",
                    "message": "No security audit recorded",
                    "impact": "Security posture unknown",
                    "recommendation": "Conduct initial security audit",
                    "fix_action": "schedule_audit",
                    "fix_url": f"/application_mgmt/applications/{app.id}/edit#security",
                }
            )

        # Check for requirements linked to this application
        if app.archimate_element_id:
            requirements = Requirement.query.filter_by(
                archimate_element_id=app.archimate_element_id
            ).all()

            unsatisfied_reqs = [r for r in requirements if r.compliance_status != "verified"]

            if unsatisfied_reqs:
                gaps.append(
                    {
                        "severity": "high",
                        "type": "requirements_not_satisfied",
                        "category": "compliance",
                        "message": f"{len(unsatisfied_reqs)} requirement(s) not yet verified",
                        "impact": "Application may not meet stakeholder expectations",
                        "recommendation": "Review and verify outstanding requirements",
                        "fix_action": "review_requirements",
                        "fix_url": f"/application_mgmt/applications/{app.id}/requirements",
                    }
                )

        return gaps

    @transactional
    def _check_essential_metadata(self, app: ApplicationComponent) -> List[Dict]:
        """Check if essential metadata is present."""
        gaps = []

        metadata_checks = [
            (
                "business_owner",
                "Business owner not assigned",
                "Cannot determine business accountability",
            ),
            (
                "development_team",
                "Development team not specified",
                "Cannot route support requests or changes",
            ),
            (
                "business_criticality",
                "Business criticality not set",
                "Cannot prioritize support or disaster recovery",
            ),
            (
                "lifecycle_status",
                "Lifecycle status not set",
                "Cannot plan retirement or investment",
            ),
        ]

        for field, message, impact in metadata_checks:
            if not getattr(app, field, None):
                gaps.append(
                    {
                        "severity": "low",
                        "type": "metadata_missing",
                        "category": "metadata",
                        "message": message,
                        "impact": impact,
                        "recommendation": f'Add {field.replace("_", " ")} information',
                        "fix_action": "add_metadata",
                        "fix_url": f"/application_mgmt/applications/{app.id}/edit#metadata",
                    }
                )

        return gaps

    @transactional
    def _check_archimate_relationships(self, app: ApplicationComponent) -> List[Dict]:
        """Check ArchiMate model completeness."""
        gaps = []

        if not app.archimate_element_id:
            gaps.append(
                {
                    "severity": "high",
                    "type": "archimate_not_linked",
                    "category": "archimate",
                    "message": "Application not linked to ArchiMate model",
                    "impact": "Cannot perform ArchiMate-based analysis, views, or impact assessments",
                    "recommendation": "Create ArchiMate Application Component element",
                    "fix_action": "create_archimate_element",
                    "fix_url": f"/application_mgmt/applications/{app.id}/archimate/create",
                }
            )
            return gaps  # Can't check further without ArchiMate element

        # Check key ArchiMate relationships
        element = db.session.get(ArchiMateElement, app.archimate_element_id)

        if not element:
            return gaps

        # Check "Realizes" relationships (to Business Services)
        realizes_count = self._count_relationship_by_type(element.id, "realization")
        if realizes_count == 0:
            gaps.append(
                {
                    "severity": "medium",
                    "type": "realizes_relationship_missing",
                    "category": "archimate",
                    "message": "Application does not realize any Business Services",
                    "impact": "Cannot identify what business value this application provides",
                    "recommendation": "Document Business Services this application realizes",
                    "fix_action": "add_realizes_relationship",
                    "fix_url": f"/application_mgmt/applications/{app.id}/archimate/relationships",
                }
            )

        # Check "Accesses" relationships (to Data Objects)
        accesses_count = self._count_relationship_by_type(element.id, "access")
        if accesses_count == 0:
            gaps.append(
                {
                    "severity": "medium",
                    "type": "data_access_relationship_missing",
                    "category": "archimate",
                    "message": "No data access relationships documented",
                    "impact": "Data dependencies and GDPR scope unclear",
                    "recommendation": "Document what data this application accesses",
                    "fix_action": "add_access_relationship",
                    "fix_url": f"/application_mgmt/applications/{app.id}/archimate/relationships",
                }
            )

        # Check "Assignment" relationships (to Technology Components)
        assignment_count = self._count_relationship_by_type(element.id, "assignment")
        if assignment_count == 0:
            gaps.append(
                {
                    "severity": "low",
                    "type": "assignment_relationship_missing",
                    "category": "archimate",
                    "message": "Not assigned to any Technology Components",
                    "impact": "Deployment infrastructure unknown - limits disaster recovery planning",
                    "recommendation": "Link to servers, containers, or cloud services",
                    "fix_action": "add_assignment_relationship",
                    "fix_url": f"/application_mgmt/applications/{app.id}/archimate/relationships",
                }
            )

        return gaps

    # ========================================================================
    # Portfolio-Level Gap Analysis
    # ========================================================================

    def analyze_cobit_coverage_gaps(self) -> Dict:
        """
        Analyzes gaps between COBIT Processes and Application coverage.

        Returns:
            Dictionary containing coverage statistics and gap details.
        """
        from app.models.business_capabilities import BusinessCapability
        from app.models.capabilities import COBITProcess

        processes = COBITProcess.query.all()

        gaps = []
        covered = []

        for process in processes:
            # Get linked Enterprise Capabilities
            ent_caps = process.capabilities

            if not ent_caps:
                gaps.append(
                    {
                        "process_code": process.code,
                        "process_name": process.name,
                        "capability": "N/A",
                        "status": "GAP - No Capability Mapped",
                        "severity": "high",
                    }
                )
                continue

            for ent_cap in ent_caps:
                # Get linked Business Capability
                bus_cap = ent_cap.business_capability

                # Fallback: Try to find by ID if relationship is not loaded
                if not bus_cap and ent_cap.business_capability_id:
                    bus_cap = BusinessCapability.query.get(ent_cap.business_capability_id)

                # Fallback: Try to find by name
                if not bus_cap:
                    bus_cap = BusinessCapability.query.filter_by(name=ent_cap.name).first()  # model-safety-ok

                if bus_cap:
                    app_count = bus_cap.applications.count()  # model-safety-ok  # tenant-scoping-ok: FK id (app/capability) already org-scoped via TenantMixin load
                    if app_count == 0:
                        gaps.append(
                            {
                                "process_code": process.code,
                                "process_name": process.name,
                                "capability": bus_cap.name,
                                "status": "GAP - No Applications",
                                "severity": "critical",
                            }
                        )
                    else:
                        covered.append(
                            {
                                "process_code": process.code,
                                "process_name": process.name,
                                "capability": bus_cap.name,
                                "app_count": app_count,
                            }
                        )
                else:
                    gaps.append(
                        {
                            "process_code": process.code,
                            "process_name": process.name,
                            "capability": ent_cap.name,
                            "status": "GAP - Capability Not Unified",
                            "severity": "medium",
                        }
                    )

        return {
            "total_processes": len(processes),
            "covered_count": len(covered),
            "gap_count": len(gaps),
            "gaps": gaps,
            "covered": covered,
        }

    @transactional
    def analyze_portfolio_gaps(self) -> Dict:
        """
        Identify gaps across entire application portfolio.

        Returns:
            Dictionary containing portfolio-wide gaps
        """
        gaps = {
            "timestamp": datetime.utcnow().isoformat(),
            "unsupported_capabilities": [],
            "single_point_failures": [],
            "missing_integrations": [],
            "compliance_risks": [],
            "technology_debt": [],
            "orphaned_applications": [],
        }

        # Find capabilities with no applications
        all_capabilities = BusinessCapability.query.all()
        for cap in all_capabilities:
            from app.models.application_capability import ApplicationCapabilityMapping

            app_count = ApplicationCapabilityMapping.query.filter_by(
                business_capability_id=cap.id
            ).count()  # model-safety-ok  # tenant-scoping-ok: FK id (app/capability) already org-scoped via TenantMixin load

            if app_count == 0:
                gaps["unsupported_capabilities"].append(
                    {
                        "capability_id": cap.id,
                        "capability_name": cap.name,
                        "severity": "high",
                        "impact": f'Business capability "{cap.name}" has no supporting applications',
                        "recommendation": "Identify required applications or mark as manual process",
                    }
                )

        # Find single points of failure (critical capabilities with only one app)
        for cap in all_capabilities:
            from app.models.application_capability import ApplicationCapabilityMapping

            # tenant-scoping-ok: cap.id is from a TenantMixin-scoped BusinessCapability load.
            mappings = ApplicationCapabilityMapping.query.filter_by(
                business_capability_id=cap.id
            ).all()  # model-safety-ok

            if len(mappings) == 1:
                app_mapping = mappings[0]
                app = db.session.get(ApplicationComponent, app_mapping.application_component_id)

                if app and app.business_criticality in ["Critical", "High"]:
                    gaps["single_point_failures"].append(
                        {
                            "capability_id": cap.id,
                            "capability_name": cap.name,
                            "application_id": app.id,
                            "application_name": app.name,
                            "severity": "high",
                            "impact": f'Critical capability "{cap.name}" depends on single application "{app.name}"',
                            "recommendation": "Add redundancy or disaster recovery plan",
                        }
                    )

        # Find applications with GDPR/compliance risks
        compliance_risk_apps = ApplicationComponent.query.filter(
            ApplicationComponent.pii_data_processed == True,
            ApplicationComponent.gdpr_compliant == False,
        ).all()

        for app in compliance_risk_apps:
            gaps["compliance_risks"].append(
                {
                    "application_id": app.id,
                    "application_name": app.name,
                    "severity": "critical",
                    "impact": f'Application "{app.name}" processes PII but not GDPR compliant',
                    "recommendation": "Complete GDPR compliance assessment immediately",
                }
            )

        # Find orphaned applications (no capabilities, no processes, no business owner)
        orphaned_apps = ApplicationComponent.query.all()
        for app in orphaned_apps:
            from app.models.application_capability import ApplicationCapabilityMapping

            cap_count = ApplicationCapabilityMapping.query.filter_by(
                application_component_id=app.id
            ).count()  # model-safety-ok  # tenant-scoping-ok: FK id (app/capability) already org-scoped via TenantMixin load

            process_count = (
                len(app.supported_processes) if hasattr(app, "supported_processes") else 0  # model-safety-ok
            )

            if cap_count == 0 and process_count == 0 and not application_is_owned(app):
                gaps["orphaned_applications"].append(
                    {
                        "application_id": app.id,
                        "application_name": app.name,
                        "severity": "medium",
                        "impact": f'Application "{app.name}" has no business context',
                        "recommendation": "Link to capabilities/processes or mark for retirement",
                    }
                )

        return gaps

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _get_technology_assignments(self, archimate_element_id: int) -> List:
        """Get technology layer assignments via ArchiMate relationships."""
        relationships = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id == archimate_element_id,
            ArchiMateRelationship.type == "assignment",
        ).all()

        return relationships

    def _count_relationship_by_type(self, element_id: int, relationship_type: str) -> int:
        """Count ArchiMate relationships of a specific type."""
        count = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id == element_id,
            ArchiMateRelationship.type == relationship_type,
        ).count()

        return count

    def calculate_health_score(self, gaps: Dict) -> int:
        """
        Calculate overall health score (0 - 100) based on gaps.

        Args:
            gaps: Dictionary of gap analysis results

        Returns:
            Integer score from 0 - 100 (100 = perfect, no gaps)
        """
        total_checks = 20
        critical_weight = 3
        high_weight = 2
        medium_weight = 1
        low_weight = 0.5

        weighted_issues = (
            gaps.get("critical_count", 0) * critical_weight
            + gaps.get("high_priority_count", 0) * high_weight
            + gaps.get("medium_priority_count", 0) * medium_weight
            + gaps.get("low_priority_count", 0) * low_weight
        )

        max_possible_issues = total_checks * critical_weight
        score = max(0, int(100 * (1 - (weighted_issues / max_possible_issues))))

        return score
