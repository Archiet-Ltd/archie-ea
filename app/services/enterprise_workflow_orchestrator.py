"""
Enterprise Workflow Orchestrator Service

Orchestrates enterprise architecture workflows by integrating governance,
validation, and automated roadmap generation using EXISTING roadmap infrastructure.

This service REUSES the existing RoadmapWorkPackage model and APIs (99.99% reuse).
"""

from datetime import datetime, timedelta
from typing import Dict, List, Tuple


from app import db
from app.models.application_layer import Application
from app.models.roadmap_models import RoadmapWorkPackage
from app.services import work_package_service
from app.models.unified_capability import UnifiedCapability
from app.models.vendor.vendor_organization import VendorOrganization
from app.services.advanced_tco_engine import AdvancedTCOEngine
from app.services.apqc_pcf_service import APQCPCFService
from app.services.archimate_metamodel_validator import ArchiMateMetamodelValidator


class EnterpriseWorkflowOrchestrator:
    """
    Orchestrates enterprise architecture workflows with automated roadmap generation.

    Key Features:
    - Governance validation using ArchiMate 3.2 and APQC PCF
    - Automatic roadmap work package generation (REUSES existing roadmap infrastructure)
    - Maturity assessment with improvement tracking
    - Vendor evaluation with onboarding roadmaps
    - Impact analysis with migration roadmaps
    """

    def __init__(self):
        self.archimate_validator = ArchiMateMetamodelValidator()
        self.apqc_service = APQCPCFService()
        self.tco_engine = AdvancedTCOEngine()

    # ========================================================================
    # 1. APPLICATION CREATION WITH GOVERNANCE
    # ========================================================================

    def create_application_with_governance(
        self,
        name: str,
        description: str,
        application_type: str,
        business_criticality: str,
        deployment_status: str,
        capability_ids: List[int],
        apqc_process_ids: List[int],
        create_roadmap: bool = True,
        **kwargs,
    ) -> Tuple[Application, Dict]:
        """
        Create application with full governance validation and auto-generate roadmap.

        Args:
            name: Application name
            description: Application description
            application_type: Type of application
            business_criticality: Criticality level
            deployment_status: Current deployment status
            capability_ids: List of capability IDs to link
            apqc_process_ids: List of APQC process IDs to link
            create_roadmap: Whether to auto-create roadmap work package
            **kwargs: Additional application attributes

        Returns:
            Tuple of (Application, governance_report)
        """
        governance_report = {
            "timestamp": datetime.utcnow().isoformat(),
            "validations": [],
            "warnings": [],
            "errors": [],
            "governance_score": 0,
            "roadmap_created": False,
        }

        # Validate ArchiMate relationships
        archimate_validation = self._validate_archimate_application(
            name, application_type, capability_ids
        )
        governance_report["validations"].append(archimate_validation)

        # Validate APQC process mappings
        apqc_validation = self._validate_apqc_mappings(apqc_process_ids)
        governance_report["validations"].append(apqc_validation)

        # Calculate governance score
        governance_score = self._calculate_governance_score(
            archimate_validation, apqc_validation, business_criticality
        )
        governance_report["governance_score"] = governance_score

        # Create application
        application = Application(
            name=name,
            description=description,
            application_type=application_type,
            business_criticality=business_criticality,
            deployment_status=deployment_status,
            **kwargs,
        )

        db.session.add(application)
        db.session.flush()  # Get application.id

        # Link capabilities
        for cap_id in capability_ids:
            capability = UnifiedCapability.query.get(cap_id)
            if capability:
                application.capabilities.append(capability)

        # Auto-generate roadmap work package (REUSE existing infrastructure)
        if create_roadmap and deployment_status in ["planned", "development"]:
            work_package = self._create_roadmap_work_package_for_application(
                application, governance_score
            )
            governance_report["roadmap_created"] = True
            governance_report["work_package_id"] = work_package.id

        db.session.commit()

        return application, governance_report

    def _validate_archimate_application(
        self, name: str, app_type: str, capability_ids: List[int]
    ) -> Dict:
        """Validate application using ArchiMate metamodel."""
        validation = {"validator": "ArchiMate 3.2", "passed": True, "issues": []}

        # Check if application type is valid ArchiMate element
        valid_types = [
            "web_application",
            "mobile_application",
            "desktop_application",
            "saas",
            "api_service",
            "database",
        ]
        if app_type not in valid_types:
            validation["issues"].append(
                f"Application type '{app_type}' not in ArchiMate standard types"
            )
            validation["passed"] = False

        # Validate capability relationships
        if not capability_ids:
            validation["issues"].append(
                "No capability relationships defined (ArchiMate requires Realization relationship)"
            )
            validation["passed"] = False

        return validation

    def _validate_apqc_mappings(self, apqc_process_ids: List[int]) -> Dict:
        """Validate APQC PCF process mappings."""
        validation = {"validator": "APQC PCF", "passed": True, "issues": []}

        if not apqc_process_ids:
            validation["issues"].append("No APQC process mappings defined")
            validation["passed"] = False

        return validation

    def _calculate_governance_score(
        self, archimate_val: Dict, apqc_val: Dict, criticality: str
    ) -> float:
        """Calculate governance readiness score (0 - 100)."""
        score = 50.0  # Base score

        if archimate_val["passed"]:
            score += 25.0

        if apqc_val["passed"]:
            score += 25.0

        # Adjust for criticality
        if criticality in ["mission_critical", "high"]:
            if not archimate_val["passed"] or not apqc_val["passed"]:
                score -= 20.0

        return max(0.0, min(100.0, score))

    def _create_roadmap_work_package_for_application(
        self, application: Application, governance_score: float
    ) -> RoadmapWorkPackage:
        """
        Create roadmap work package using EXISTING roadmap infrastructure.

        CRITICAL: This reuses RoadmapWorkPackage model (99.99% reuse requirement).
        """
        work_package = RoadmapWorkPackage(
            name=f"Implement {application.name}",
            description=application.description
            or f"Implementation work package for {application.name}",
            status="planned",
            business_capability=application.business_domain or "Application Implementation",
            start_date=application.implementation_date or datetime.utcnow(),
            end_date=application.go_live_date or (datetime.utcnow() + timedelta(days=90)),
            estimated_cost=getattr(application, "total_cost_of_ownership", None),
            priority=self._map_criticality_to_priority(application.business_criticality),
            complexity_score=self._calculate_complexity_score(application),
            auto_generated=True,  # Mark as auto-generated by orchestrator
            source_type="application",
            source_id=application.id,
            confidence_score=governance_score / 100.0,  # Convert to 0 - 1 scale
            generation_method="enterprise_orchestrator",
            source_data={
                "application_name": application.name,
                "application_type": application.application_type,
                "business_criticality": application.business_criticality,
                "governance_score": governance_score,
            },
        )

        # Add default deliverables
        deliverables = [
            dict(
                name="Requirements Specification",
                description=f"Detailed requirements for {application.name}",
                deliverable_type="documentation",
                delivery_status="pending",
                target_date=work_package.start_date + timedelta(days=14),
            ),
            dict(
                name="Technical Design",
                description=f"Architecture and technical design for {application.name}",
                deliverable_type="technical",
                delivery_status="pending",
                target_date=work_package.start_date + timedelta(days=30),
            ),
            dict(
                name="Application Deployment",
                description=f"Production deployment of {application.name}",
                deliverable_type="milestone",
                delivery_status="pending",
                target_date=work_package.end_date,
            ),
        ]

        db.session.add(work_package)
        # The flush lets the bridge copy the row into the one work package store;
        # the deliverables belong to that copy (the one deliverable store).
        db.session.flush()
        work_package_service.create_deliverables_for_roadmap_copy(work_package, deliverables)
        return work_package

    # ========================================================================
    # 2. CAPABILITY MATURITY ASSESSMENT
    # ========================================================================

    def assess_capability_maturity(
        self, capability_id: int, assessment_data: Dict, create_improvement_roadmap: bool = True
    ) -> Dict:
        """
        Assess capability maturity and auto-generate improvement roadmap.

        Args:
            capability_id: ID of capability to assess
            assessment_data: Maturity assessment data
            create_improvement_roadmap: Whether to auto-create improvement work packages

        Returns:
            Maturity report with improvement priorities
        """
        capability = UnifiedCapability.query.get(capability_id)
        if not capability:
            raise ValueError(f"Capability {capability_id} not found")

        maturity_report = {
            "capability_id": capability_id,
            "capability_name": capability.name,
            "timestamp": datetime.utcnow().isoformat(),
            "dimensions": [],
            "overall_maturity": 0.0,
            "improvement_priorities": [],
            "roadmap_work_packages": [],
        }

        # Assess maturity dimensions
        dimensions = ["people", "process", "technology", "data", "governance"]
        total_score = 0.0

        for dimension in dimensions:
            score = assessment_data.get(f"{dimension}_score", 0)
            target = assessment_data.get(f"{dimension}_target", 5)
            gap = target - score

            maturity_report["dimensions"].append(
                {
                    "dimension": dimension,
                    "current_score": score,
                    "target_score": target,
                    "gap": gap,
                    "priority": "high" if gap >= 2 else "medium" if gap >= 1 else "low",
                }
            )

            total_score += score

            # Auto-generate improvement work package if gap exists
            if create_improvement_roadmap and gap > 0:
                work_package = self._create_improvement_work_package(
                    capability, dimension, score, target
                )
                maturity_report["roadmap_work_packages"].append(work_package.id)

        maturity_report["overall_maturity"] = total_score / len(dimensions)

        db.session.commit()

        return maturity_report

    def _create_improvement_work_package(
        self,
        capability: UnifiedCapability,
        dimension: str,
        current_score: float,
        target_score: float,
    ) -> RoadmapWorkPackage:
        """Create improvement work package using existing roadmap infrastructure."""
        gap = target_score - current_score

        work_package = RoadmapWorkPackage(
            name=f"Improve {capability.name} - {dimension.title()}",
            description=f"Enhance {dimension} capability from level {current_score} to {target_score}",
            status="planned",
            business_capability=capability.name,
            priority="high" if gap >= 2 else "medium",
            complexity_score=int(gap * 20),  # Gap of 2 = complexity 40
            duration_days=int(gap * 30),  # 30 days per maturity level
            auto_generated=True,
            source_type="capability_maturity",
            source_id=capability.id,
            confidence_score=0.80,
            generation_method="maturity_assessment",
            source_data={
                "capability_name": capability.name,
                "dimension": dimension,
                "current_score": current_score,
                "target_score": target_score,
                "gap": gap,
            },
        )

        # Link to capability using existing many-to-many relationship
        work_package.capabilities.append(capability)

        db.session.add(work_package)
        return work_package

    # ========================================================================
    # 3. VENDOR EVALUATION AND ONBOARDING
    # ========================================================================

    def evaluate_vendor_for_capability(
        self,
        vendor_id: int,
        capability_ids: List[int],
        evaluation_criteria: Dict,
        create_onboarding_roadmap: bool = True,
    ) -> Dict:
        """
        Evaluate vendor against capabilities and auto-generate onboarding roadmap.

        Args:
            vendor_id: Vendor organization ID
            capability_ids: Capabilities vendor products support
            evaluation_criteria: Scoring criteria
            create_onboarding_roadmap: Whether to auto-create onboarding work package

        Returns:
            Evaluation report with recommendation
        """
        vendor = VendorOrganization.query.get(vendor_id)
        if not vendor:
            raise ValueError(f"Vendor {vendor_id} not found")

        evaluation_report = {
            "vendor_id": vendor_id,
            "vendor_name": vendor.name,
            "timestamp": datetime.utcnow().isoformat(),
            "scores": {},
            "total_score": 0,
            "recommendation": "not_recommended",
            "roadmap_work_package_id": None,
        }

        # Evaluate vendor
        criteria_weights = {
            "technical_capability": 0.30,
            "financial_stability": 0.20,
            "support_quality": 0.20,
            "strategic_fit": 0.15,
            "cost_effectiveness": 0.15,
        }

        total_score = 0.0
        for criterion, weight in criteria_weights.items():
            score = evaluation_criteria.get(criterion, 0)
            weighted_score = score * weight
            evaluation_report["scores"][criterion] = {
                "score": score,
                "weight": weight,
                "weighted_score": weighted_score,
            }
            total_score += weighted_score

        evaluation_report["total_score"] = total_score

        # Determine recommendation
        if total_score >= 70:
            evaluation_report["recommendation"] = "highly_recommended"
        elif total_score >= 50:
            evaluation_report["recommendation"] = "recommended"
        else:
            evaluation_report["recommendation"] = "not_recommended"

        # Auto-generate onboarding roadmap if recommended
        if create_onboarding_roadmap and total_score >= 50:
            work_package = self._create_vendor_onboarding_work_package(
                vendor, capability_ids, total_score
            )
            evaluation_report["roadmap_work_package_id"] = work_package.id

        db.session.commit()

        return evaluation_report

    def _create_vendor_onboarding_work_package(
        self, vendor: VendorOrganization, capability_ids: List[int], evaluation_score: float
    ) -> RoadmapWorkPackage:
        """Create vendor onboarding work package using existing roadmap infrastructure."""
        work_package = RoadmapWorkPackage(
            name=f"{vendor.name} Selection & Onboarding",
            description=f"Evaluate, select, contract, and deploy {vendor.name} products",
            status="planned",
            business_capability="Vendor Management",
            priority="high" if evaluation_score >= 70 else "medium",
            complexity_score=60,
            duration_days=120,
            auto_generated=True,
            source_type="vendor_evaluation",
            source_id=vendor.id,
            confidence_score=evaluation_score / 100.0,
            generation_method="vendor_orchestrator",
            source_data={
                "vendor_name": vendor.name,
                "evaluation_score": evaluation_score,
                "capability_count": len(capability_ids),
            },
        )

        # Add standard vendor onboarding deliverables
        deliverables = [
            dict(
                name="Vendor Contract Signed",
                description="Legal and commercial terms finalized",
                deliverable_type="milestone",
                delivery_status="pending",
                target_date=datetime.utcnow() + timedelta(days=30),
            ),
            dict(
                name="Technical Integration Complete",
                description="API integration and data migration",
                deliverable_type="technical",
                delivery_status="pending",
                target_date=datetime.utcnow() + timedelta(days=90),
            ),
            dict(
                name="User Training Completed",
                description="End-user training and documentation",
                deliverable_type="business",
                delivery_status="pending",
                target_date=datetime.utcnow() + timedelta(days=120),
            ),
        ]

        db.session.add(work_package)
        # The flush lets the bridge copy the row into the one work package store;
        # the deliverables belong to that copy (the one deliverable store).
        db.session.flush()
        work_package_service.create_deliverables_for_roadmap_copy(work_package, deliverables)
        return work_package

    # ========================================================================
    # 4. IMPACT ANALYSIS AND MIGRATION PLANNING
    # ========================================================================

    def analyze_end_to_end_impact(
        self,
        element_id: int,
        element_type: str,
        change_type: str,
        create_migration_roadmap: bool = True,
    ) -> Dict:
        """
        Analyze impact of changes and auto-generate migration roadmap.

        Args:
            element_id: ID of element being changed
            element_type: Type (application, capability, etc.)
            change_type: retirement, replacement, upgrade
            create_migration_roadmap: Whether to auto-create migration work packages

        Returns:
            Impact analysis report with migration roadmap
        """
        impact_report = {
            "element_id": element_id,
            "element_type": element_type,
            "change_type": change_type,
            "timestamp": datetime.utcnow().isoformat(),
            "impacted_elements": [],
            "overall_severity": "MEDIUM",
            "migration_work_packages": [],
        }

        # Analyze impacts based on element type
        if element_type == "application":
            application = Application.query.get(element_id)
            if application:
                # Count dependent elements
                capability_count = len(application.capabilities)
                process_count = len(getattr(application, "apqc_processes", []))

                impact_report["impacted_elements"] = [
                    {"type": "capability", "count": capability_count},
                    {"type": "process", "count": process_count},
                ]

                total_impacts = capability_count + process_count
                if total_impacts >= 10:
                    impact_report["overall_severity"] = "CRITICAL"
                elif total_impacts >= 5:
                    impact_report["overall_severity"] = "HIGH"

        # Auto-generate migration roadmap for critical changes
        if create_migration_roadmap and impact_report["overall_severity"] in ["CRITICAL", "HIGH"]:
            work_packages = self._create_migration_roadmap(
                element_id, element_type, change_type, impact_report["overall_severity"]
            )
            impact_report["migration_work_packages"] = [wp.id for wp in work_packages]

        db.session.commit()

        return impact_report

    def _create_migration_roadmap(
        self, element_id: int, element_type: str, change_type: str, severity: str
    ) -> List[RoadmapWorkPackage]:
        """Create phased migration roadmap using existing roadmap infrastructure."""
        phases = [
            {
                "name": f"Phase 1: {change_type.title()} Assessment & Planning",
                "duration_days": 30,
                "priority": "critical" if severity == "CRITICAL" else "high",
                "description": "Detailed assessment and migration planning",
            },
            {
                "name": f"Phase 2: Pilot {change_type.title()}",
                "duration_days": 60,
                "priority": "high",
                "description": "Pilot migration with limited scope",
            },
            {
                "name": f"Phase 3: Full {change_type.title()}",
                "duration_days": 90,
                "priority": "high",
                "description": "Complete migration rollout",
            },
            {
                "name": "Phase 4: Validation & Decommissioning",
                "duration_days": 30,
                "priority": "medium",
                "description": "Validation and decommissioning of old system",
            },
        ]

        work_packages = []
        previous_wp = None
        current_date = datetime.utcnow()

        for i, phase_data in enumerate(phases):
            wp = RoadmapWorkPackage(
                name=phase_data["name"],
                description=phase_data["description"],
                status="planned",
                business_capability="Migration & Transformation",
                start_date=current_date,
                end_date=current_date + timedelta(days=phase_data["duration_days"]),
                duration_days=phase_data["duration_days"],
                priority=phase_data["priority"],
                complexity_score=80 - (i * 15),  # Decrease complexity over phases
                auto_generated=True,
                source_type=f"{element_type}_migration",
                source_id=element_id,
                confidence_score=0.75,
                generation_method="impact_analysis",
                source_data={
                    "element_type": element_type,
                    "change_type": change_type,
                    "severity": severity,
                    "phase": i + 1,
                },
            )

            # Link dependencies using existing relationship
            if previous_wp:
                wp.dependencies.append(previous_wp)

            db.session.add(wp)
            work_packages.append(wp)
            previous_wp = wp

            # Update start date for next phase
            current_date = wp.end_date + timedelta(days=1)

        return work_packages

    # ========================================================================
    # HELPER METHODS
    # ========================================================================

    def _map_criticality_to_priority(self, criticality: str) -> str:
        """Map business criticality to roadmap priority."""
        mapping = {"mission_critical": "critical", "high": "high", "medium": "medium", "low": "low"}
        return mapping.get(criticality, "medium")

    def _calculate_complexity_score(self, application: Application) -> int:
        """Calculate complexity score (0 - 100) for application."""
        score = 30  # Base score

        # Add complexity based on relationships
        capability_count = len(application.capabilities)
        score += min(capability_count * 5, 30)  # Max 30 points

        # Add complexity based on criticality
        if application.business_criticality == "mission_critical":
            score += 20
        elif application.business_criticality == "high":
            score += 10

        return min(score, 100)
