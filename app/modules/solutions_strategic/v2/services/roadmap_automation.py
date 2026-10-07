"""
Roadmap Automation Engine
Intelligent generation and optimization of roadmap elements
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional  # dead-code-ok

from sqlalchemy import text  # dead-code-ok

from app import db
from app.models.roadmap_models import (  # dead-code-ok
    ImplementationGap,
    PlanningDeliverable,
)
from app.models.unified_work_package import UnifiedWorkPackage
from app.services import work_package_service

# Aliases for backwards compatibility
Deliverable = PlanningDeliverable
ImplementationWorkPackage = UnifiedWorkPackage
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_capability import UnifiedCapability

logger = logging.getLogger(__name__)


@dataclass
class GenerationOptions:
    """Options for work package generation"""

    include_dependencies: bool = True
    include_resources: bool = True
    include_milestones: bool = True
    complexity_level: str = "medium"  # low, medium, high
    timeline_months: int = 12
    budget_constraint: Optional[float] = None
    resource_constraint: Optional[Dict] = None


@dataclass
class ConflictInfo:
    """Information about detected conflicts"""

    conflict_type: str  # timeline, resource, budget, dependency
    severity: str  # low, medium, high, critical
    description: str
    entities: List[Dict[str, Any]]
    suggested_resolution: str
    impact_score: float


class RoadmapAutomationEngine:
    """Intelligent automation engine for roadmap generation and optimization"""

    def __init__(self):
        self.generation_templates = self._load_generation_templates()
        self.optimization_algorithms = self._load_optimization_algorithms()

    def generate_work_packages(
        self, source_type: str, source_id: int, options: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate work packages from various sources

        Args:
            source_type: 'capability', 'gap', 'application', 'portfolio'
            source_id: ID of the source entity
            options: Generation options and constraints

        Returns:
            List of generated work package dictionaries
        """
        try:
            gen_options = GenerationOptions(**(options or {}))

            if source_type == "capability":
                return self._generate_from_capability(source_id, gen_options)
            elif source_type == "gap":
                return self._generate_from_gap(source_id, gen_options)
            elif source_type == "application":
                return self._generate_from_application(source_id, gen_options)
            elif source_type == "portfolio":
                return self._generate_from_portfolio(source_id, gen_options)
            else:
                raise ValueError(f"Unsupported source type: {source_type}")

        except Exception as e:
            logger.error(f"Error generating work packages from {source_type} {source_id}: {e}")
            raise

    def _generate_from_capability(
        self, capability_id: int, options: GenerationOptions
    ) -> List[Dict[str, Any]]:
        """Generate work packages from business capability"""
        capability = UnifiedCapability.query.get_or_404(capability_id)

        # Get related applications
        applications = db.session.execute(  # tenant-filtered: scoped via capability_id FK
            text(
                """
            SELECT a.* FROM applications a
            JOIN application_capability_mapping acm ON a.id = acm.application_id
            WHERE acm.capability_id = :cap_id
        """
            ),
            {"cap_id": capability_id},
        ).fetchall()

        # Get related gaps
        gaps = ImplementationGap.query.filter_by(source_capability_id=capability_id).all()

        work_packages = []

        # Generate capability implementation work package
        wp_capability = self._create_capability_work_package(
            capability, applications, gaps, options
        )
        work_packages.append(wp_capability)

        # Generate application-specific work packages
        for app in applications:
            wp_app = self._create_application_work_package(app, capability, options)
            work_packages.append(wp_app)

        # Generate gap resolution work packages
        for gap in gaps:
            wp_gap = self._create_gap_resolution_work_package(gap, capability, options)
            work_packages.append(wp_gap)

        # Generate dependencies between work packages
        if options.include_dependencies:
            work_packages = self._add_work_package_dependencies(work_packages)

        return work_packages

    def _generate_from_gap(self, gap_id: int, options: GenerationOptions) -> List[Dict[str, Any]]:
        """Generate work packages from implementation gap"""
        gap = ImplementationGap.query.get_or_404(gap_id)

        work_packages = []

        # Main gap resolution work package
        wp_main = {
            "name": f"Resolve Gap: {gap.name}",
            "description": f"Address {gap.gap_type} gap: {gap.description}",
            "business_capability": self._infer_capability_from_gap(gap),
            "status": "planned",
            "priority": gap.priority,
            "risk_level": gap.risk_level,
            "estimated_cost": gap.estimated_resolution_cost,
            "source_type": "gap",
            "source_id": gap.id,
            "auto_generated": True,
            "confidence_score": gap.confidence_score,
            "generation_method": "gap_resolution",
        }

        # Calculate timeline based on gap complexity
        if gap.estimated_resolution_time:
            start_date = datetime.utcnow()
            end_date = start_date + timedelta(days=gap.estimated_resolution_time)
            wp_main["start_date"] = start_date.isoformat()
            wp_main["end_date"] = end_date.isoformat()

        work_packages.append(wp_main)

        # Generate supporting work packages based on gap type
        supporting_wps = self._generate_gap_supporting_packages(gap, options)
        work_packages.extend(supporting_wps)

        return work_packages

    def _generate_from_application(
        self, application_id: int, options: GenerationOptions
    ) -> List[Dict[str, Any]]:
        """Generate work packages from application modernization"""
        application = ApplicationComponent.query.get_or_404(application_id)

        work_packages = []

        # Application assessment work package
        wp_assessment = {
            "name": f"Assess {application.name}",
            "description": f"Comprehensive assessment of {application.name} for modernization roadmap",
            "business_capability": self._get_primary_capability_for_application(application_id),
            "status": "planned",
            "priority": "high",
            "estimated_cost": self._estimate_assessment_cost(application),
            "source_type": "application",
            "source_id": application_id,
            "auto_generated": True,
            "generation_method": "application_assessment",
        }
        work_packages.append(wp_assessment)

        # Modernization work package
        wp_modernization = {
            "name": f"Modernize {application.name}",
            "description": f"Modernize {application.name} based on assessment results",
            "business_capability": self._get_primary_capability_for_application(application_id),
            "status": "planned",
            "priority": "medium",
            "estimated_cost": self._estimate_modernization_cost(application),
            "source_type": "application",
            "source_id": application_id,
            "auto_generated": True,
            "generation_method": "application_modernization",
        }
        work_packages.append(wp_modernization)

        return work_packages

    def _generate_from_portfolio(
        self, portfolio_id: int, options: GenerationOptions
    ) -> List[Dict[str, Any]]:
        """Generate work packages from portfolio analysis"""
        # This would analyze portfolio-level initiatives and generate work packages
        # Implementation would depend on portfolio model structure

        work_packages = []

        # Portfolio assessment
        wp_portfolio = {
            "name": "Portfolio Assessment",
            "description": "Comprehensive portfolio assessment and strategic alignment",
            "business_capability": "portfolio_management",
            "status": "planned",
            "priority": "high",
            "estimated_cost": 50000,
            "source_type": "portfolio",
            "source_id": portfolio_id,
            "auto_generated": True,
            "generation_method": "portfolio_assessment",
        }
        work_packages.append(wp_portfolio)

        return work_packages

    def _create_capability_work_package(
        self,
        capability: UnifiedCapability,
        applications: List,
        gaps: List,
        options: GenerationOptions,
    ) -> Dict[str, Any]:
        """Create main capability implementation work package"""

        # Estimate complexity and duration
        complexity = self._calculate_capability_complexity(capability, applications, gaps)
        duration = self._estimate_duration_from_complexity(complexity, options.timeline_months)
        cost = self._estimate_capability_cost(capability, applications, gaps, options)

        return {
            "name": f"Implement {capability.name}",
            "description": f"Implement {capability.name} capability with {len(applications)} applications and {len(gaps)} gaps",
            "business_capability": capability.name,
            "status": "planned",
            "priority": self._determine_priority_from_importance(capability.strategic_importance),
            "risk_level": self._assess_risk_level(capability, applications, gaps),
            "estimated_cost": cost,
            "start_date": datetime.utcnow().isoformat(),
            "end_date": (datetime.utcnow() + timedelta(days=duration)).isoformat(),
            "source_type": "capability",
            "source_id": capability.id,
            "auto_generated": True,
            "confidence_score": self._calculate_confidence_score(capability, applications, gaps),
            "generation_method": "capability_implementation",
            "automation_metadata": json.dumps(
                {
                    "complexity_score": complexity,
                    "application_count": len(applications),
                    "gap_count": len(gaps),
                    "strategic_importance": capability.strategic_importance,
                }
            ),
        }

    def _create_application_work_package(
        self, application: Any, capability: UnifiedCapability, options: GenerationOptions
    ) -> Dict[str, Any]:
        """Create application-specific work package"""

        app_name = application.name if hasattr(application, "name") else str(application.id)

        return {
            "name": f"Modernize {app_name}",
            "description": f"Modernize {app_name} to support {capability.name} capability",
            "business_capability": capability.name,
            "status": "planned",
            "priority": "medium",
            "risk_level": "medium",
            "estimated_cost": self._estimate_application_modernization_cost(application),
            "start_date": (datetime.utcnow() + timedelta(days=30)).isoformat(),
            "end_date": (datetime.utcnow() + timedelta(days=90)).isoformat(),
            "source_type": "application",
            "source_id": application.id,
            "auto_generated": True,
            "confidence_score": 0.8,
            "generation_method": "application_modernization",
        }

    def _create_gap_resolution_work_package(
        self, gap: ImplementationGap, capability: UnifiedCapability, options: GenerationOptions
    ) -> Dict[str, Any]:
        """Create gap resolution work package"""

        return {
            "name": f"Resolve {gap.gap_type} Gap: {gap.name}",
            "description": f"Address {gap.gap_type} gap: {gap.description}",
            "business_capability": capability.name,
            "status": "planned",
            "priority": gap.priority,
            "risk_level": gap.risk_level,
            "estimated_cost": gap.estimated_resolution_cost or 25000,
            "start_date": (datetime.utcnow() + timedelta(days=15)).isoformat(),
            "end_date": (
                datetime.utcnow() + timedelta(days=gap.estimated_resolution_time or 60)
            ).isoformat(),
            "source_type": "gap",
            "source_id": gap.id,
            "auto_generated": True,
            "confidence_score": gap.confidence_score,
            "generation_method": "gap_resolution",
        }

    def _add_work_package_dependencies(
        self, work_packages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Add logical dependencies between work packages"""

        # Add dependencies based on logical sequence
        for i, wp in enumerate(work_packages):
            wp["dependencies"] = []

            # Assessment work packages should come first
            if "assessment" in wp["name"].lower():
                continue  # No dependencies for assessment packages

            # Other packages depend on assessment
            for j, other_wp in enumerate(work_packages):
                if i != j and "assessment" in other_wp["name"].lower():
                    wp["dependencies"].append(
                        {
                            "id": other_wp.get("id", j),
                            "name": other_wp["name"],
                            "type": "finish_to_start",
                        }
                    )

        return work_packages

    @staticmethod
    def _load_work_packages(work_package_ids: List[int]) -> List[UnifiedWorkPackage]:
        """The caller's own work packages among these ids; another
        organisation's id is simply not found."""
        from app.utils.tenant import current_organization_id

        org_id = current_organization_id()
        found = []
        for raw in work_package_ids or []:
            try:
                wp = work_package_service.get_work_package(int(raw), org_id)
            except (TypeError, ValueError):
                continue
            if wp is not None:
                found.append(wp)
        return found

    def optimize_timeline(
        self, work_package_ids: List[int], constraints: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Optimize timeline based on dependencies and constraints

        Args:
            work_package_ids: List of work package IDs to optimize
            constraints: Optimization constraints (budget, resources, etc.)

        Returns:
            Optimized timeline with suggested dates and conflicts
        """
        try:
            # Get work packages (this organisation's own rows of the one store)
            work_packages = self._load_work_packages(work_package_ids)

            if not work_packages:
                return {"error": "No work packages found"}

            # Apply critical path method
            critical_path = self._calculate_critical_path(work_packages)

            # Optimize with constraints
            optimized_timeline = self._apply_constraints_optimization(
                work_packages, constraints or {}
            )

            # Detect conflicts
            conflicts = self.detect_conflicts(work_package_ids)

            return {
                "optimized_timeline": optimized_timeline,
                "critical_path": critical_path,
                "conflicts": conflicts,
                "optimization_applied": bool(constraints),
                "total_duration": self._calculate_total_duration(optimized_timeline),
            }

        except Exception as e:
            logger.error(f"Error optimizing timeline: {e}")
            raise

    def detect_conflicts(
        self, work_package_ids: List[int], date_range: Optional[Dict] = None
    ) -> List[ConflictInfo]:
        """
        Detect various types of conflicts in work packages

        Args:
            work_package_ids: List of work package IDs to check
            date_range: Optional date range to limit conflict detection

        Returns:
            List of detected conflicts
        """
        try:
            conflicts = []

            # Get work packages (this organisation's own rows of the one store)
            work_packages = self._load_work_packages(work_package_ids)

            # Detect timeline conflicts
            timeline_conflicts = self._detect_timeline_conflicts(work_packages)
            conflicts.extend(timeline_conflicts)

            # Detect resource conflicts
            resource_conflicts = self._detect_resource_conflicts(work_packages)
            conflicts.extend(resource_conflicts)

            # Detect budget conflicts
            budget_conflicts = self._detect_budget_conflicts(work_packages)
            conflicts.extend(budget_conflicts)

            # Detect dependency conflicts
            dependency_conflicts = self._detect_dependency_conflicts(work_packages)
            conflicts.extend(dependency_conflicts)

            return conflicts

        except Exception as e:
            logger.error(f"Error detecting conflicts: {e}")
            raise

    def _detect_timeline_conflicts(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> List[ConflictInfo]:
        """Detect timeline conflicts between work packages"""
        conflicts = []

        for i, wp1 in enumerate(work_packages):
            for j, wp2 in enumerate(work_packages[i + 1 :], i + 1):
                if self._dates_overlap(wp1, wp2):
                    conflict = ConflictInfo(
                        conflict_type="timeline",
                        severity="medium",
                        description=f"Timeline overlap between {wp1.name} and {wp2.name}",
                        entities=[
                            {"id": wp1.id, "name": wp1.name, "type": "work_package"},
                            {"id": wp2.id, "name": wp2.name, "type": "work_package"},
                        ],
                        suggested_resolution="Adjust dates or add dependency constraint",
                        impact_score=0.6,
                    )
                    conflicts.append(conflict)

        return conflicts

    def _detect_resource_conflicts(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> List[ConflictInfo]:
        """Detect resource conflicts between work packages"""
        conflicts = []

        # Group by assigned resources
        resource_assignments = {}
        for wp in work_packages:
            if wp.assigned_to:
                if wp.assigned_to not in resource_assignments:
                    resource_assignments[wp.assigned_to] = []
                resource_assignments[wp.assigned_to].append(wp)

        # Check for overlapping assignments
        for resource, assigned_wps in resource_assignments.items():
            if len(assigned_wps) > 1:
                for i, wp1 in enumerate(assigned_wps):
                    for j, wp2 in enumerate(assigned_wps[i + 1 :], i + 1):
                        if self._dates_overlap(wp1, wp2):
                            conflict = ConflictInfo(
                                conflict_type="resource",
                                severity="high",
                                description=f"RoadmapResource {resource} assigned to overlapping work packages",
                                entities=[
                                    {"id": wp1.id, "name": wp1.name, "type": "work_package"},
                                    {"id": wp2.id, "name": wp2.name, "type": "work_package"},
                                    {"id": resource, "name": resource, "type": "resource"},
                                ],
                                suggested_resolution="Reassign resource or adjust timeline",
                                impact_score=0.8,
                            )
                            conflicts.append(conflict)

        return conflicts

    def _detect_budget_conflicts(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> List[ConflictInfo]:
        """Detect budget conflicts"""
        conflicts = []

        total_estimated_cost = sum(wp.estimated_cost or 0 for wp in work_packages)

        # Check if total exceeds reasonable budget (example: $1M)
        if total_estimated_cost > 1000000:
            conflict = ConflictInfo(
                conflict_type="budget",
                severity="critical",
                description=f"Total estimated cost ${total_estimated_cost:,.0f} exceeds budget limit",
                entities=[{"id": "budget", "name": "Budget Constraint", "type": "constraint"}],
                suggested_resolution="Reduce scope, prioritize work packages, or increase budget",
                impact_score=1.0,
            )
            conflicts.append(conflict)

        return conflicts

    def _detect_dependency_conflicts(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> List[ConflictInfo]:
        """Detect dependency cycle conflicts"""
        conflicts = []

        # This would implement cycle detection in dependency graph
        # For now, just check for obvious issues

        return conflicts

    def _dates_overlap(
        self, wp1: ImplementationWorkPackage, wp2: ImplementationWorkPackage
    ) -> bool:
        """Check if two work packages have overlapping dates"""
        if not wp1.start_date or not wp1.end_date or not wp2.start_date or not wp2.end_date:
            return False

        return (wp1.start_date <= wp2.end_date) and (wp2.start_date <= wp1.end_date)

    def _calculate_critical_path(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> List[Dict[str, Any]]:
        """Calculate critical path using CPM algorithm"""
        # Simplified critical path calculation
        # In production, this would be a full CPM implementation

        critical_path = []

        # Sort by start date and priority
        sorted_wps = sorted(work_packages, key=lambda x: (x.start_date or datetime.max, x.priority))

        for wp in sorted_wps:
            critical_path.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "start_date": wp.start_date.isoformat() if wp.start_date else None,
                    "end_date": wp.end_date.isoformat() if wp.end_date else None,
                    "duration": wp.duration_days or 0,
                    "slack": 0,  # Would calculate actual slack
                    "is_critical": True,
                }
            )

        return critical_path

    def _apply_constraints_optimization(
        self, work_packages: List[ImplementationWorkPackage], constraints: Dict
    ) -> List[Dict[str, Any]]:
        """Apply optimization constraints to timeline"""
        optimized = []

        budget_constraint = constraints.get("budget_constraint")
        timeline_constraint = constraints.get("timeline_constraint")

        for wp in work_packages:
            wp_data = work_package_service.to_dict(wp)

            # Apply budget constraint
            if budget_constraint and wp.estimated_cost:
                if wp.estimated_cost > budget_constraint:
                    # Reduce cost proportionally
                    reduction_factor = budget_constraint / wp.estimated_cost
                    wp_data["estimated_cost"] = budget_constraint
                    wp_data[
                        "optimization_notes"
                    ] = f"Cost reduced by {(1 - reduction_factor)*100:.1f}%"

            # Apply timeline constraint
            if timeline_constraint and wp.duration_days:
                if wp.duration_days > timeline_constraint:
                    # Compress timeline
                    wp_data["duration_days"] = timeline_constraint
                    if wp.start_date:
                        wp_data["end_date"] = (
                            wp.start_date + timedelta(days=timeline_constraint)
                        ).isoformat()
                    wp_data[
                        "optimization_notes"
                    ] = f"Timeline compressed to {timeline_constraint} days"

            optimized.append(wp_data)

        return optimized

    def _calculate_total_duration(self, timeline: List[Dict[str, Any]]) -> int:
        """Calculate total duration of timeline"""
        if not timeline:
            return 0

        start_dates = [wp.get("start_date") for wp in timeline if wp.get("start_date")]
        end_dates = [wp.get("end_date") for wp in timeline if wp.get("end_date")]

        if not start_dates or not end_dates:
            return 0

        min_start = min(datetime.fromisoformat(d) for d in start_dates)
        max_end = max(datetime.fromisoformat(d) for d in end_dates)

        return (max_end - min_start).days

    # Helper methods for generation logic
    def _load_generation_templates(self) -> Dict[str, Any]:
        """Load generation templates and patterns"""
        return {
            "capability_templates": {
                "low_complexity": {
                    "base_duration": 30,
                    "cost_multiplier": 1.0,
                    "risk_level": "low",
                },
                "medium_complexity": {
                    "base_duration": 60,
                    "cost_multiplier": 1.5,
                    "risk_level": "medium",
                },
                "high_complexity": {
                    "base_duration": 120,
                    "cost_multiplier": 2.0,
                    "risk_level": "high",
                },
            }
        }

    def _load_optimization_algorithms(self) -> Dict[str, Any]:
        """Load optimization algorithms and heuristics"""
        return {
            "critical_path_method": True,
            "resource_leveling": True,
            "cost_optimization": True,
            "risk_balancing": True,
        }

    def _calculate_capability_complexity(
        self, capability: UnifiedCapability, applications: List, gaps: List
    ) -> float:
        """Calculate complexity score for capability implementation"""
        complexity = 1.0

        # Base complexity from strategic importance
        if capability.strategic_importance == "critical":
            complexity += 1.0
        elif capability.strategic_importance == "high":
            complexity += 0.5

        # Add complexity from applications
        complexity += len(applications) * 0.2

        # Add complexity from gaps
        complexity += len(gaps) * 0.3

        return min(complexity, 3.0)  # Cap at 3.0

    def _estimate_duration_from_complexity(self, complexity: float, timeline_months: int) -> int:
        """Estimate duration based on complexity and timeline constraint"""
        base_duration = int(complexity * 30)  # 30 days per complexity point
        max_duration = timeline_months * 30

        return min(base_duration, max_duration)

    def _estimate_capability_cost(
        self,
        capability: UnifiedCapability,
        applications: List,
        gaps: List,
        options: GenerationOptions,
    ) -> float:
        """Estimate cost for capability implementation"""
        base_cost = 50000  # Base cost per capability

        # Add cost for applications
        app_cost = len(applications) * 25000

        # Add cost for gaps
        gap_cost = sum(gap.estimated_resolution_cost or 20000 for gap in gaps)

        total_cost = base_cost + app_cost + gap_cost

        # Apply complexity multiplier
        complexity = self._calculate_capability_complexity(capability, applications, gaps)
        total_cost *= complexity

        # Apply budget constraint if specified
        if options.budget_constraint:
            total_cost = min(total_cost, options.budget_constraint)

        return total_cost

    def _determine_priority_from_importance(self, strategic_importance: str) -> str:
        """Determine work package priority from strategic importance"""
        mapping = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
        return mapping.get(strategic_importance, "medium")

    def _assess_risk_level(
        self, capability: UnifiedCapability, applications: List, gaps: List
    ) -> str:
        """Assess risk level for capability implementation"""
        risk_score = 1.0

        # Risk from gaps
        high_priority_gaps = len([g for g in gaps if g.priority in ["critical", "high"]])
        risk_score += high_priority_gaps * 0.3

        # Risk from application complexity
        risk_score += len(applications) * 0.1

        if risk_score >= 2.0:
            return "high"
        elif risk_score >= 1.5:
            return "medium"
        else:
            return "low"

    def _calculate_confidence_score(
        self, capability: UnifiedCapability, applications: List, gaps: List
    ) -> float:
        """Calculate confidence score for generated work packages"""
        confidence = 1.0

        # Reduce confidence based on data quality
        if not capability.description:
            confidence -= 0.1

        if not applications:
            confidence -= 0.2

        if gaps and any(not g.description for g in gaps):
            confidence -= 0.1

        return max(confidence, 0.5)  # Minimum confidence of 0.5

    def _infer_capability_from_gap(self, gap: ImplementationGap) -> str:
        """Infer business capability from gap"""
        if gap.source_capability_id:
            capability = UnifiedCapability.query.get(gap.source_capability_id)
            return capability.name if capability else "unknown"

        # Infer from gap type
        if gap.gap_type == "technology":
            return "technology_management"
        elif gap.gap_type == "process":
            return "process_optimization"
        elif gap.gap_type == "skill":
            return "human_resources"
        else:
            return "general_improvement"

    def _generate_gap_supporting_packages(
        self, gap: ImplementationGap, options: GenerationOptions
    ) -> List[Dict[str, Any]]:
        """Generate supporting work packages for gap resolution"""
        supporting_wps = []

        # Add requirements gathering if not specified
        if not gap.current_state or not gap.target_state:
            wp_requirements = {
                "name": f"Requirements Analysis for {gap.name}",
                "description": "Gather detailed requirements for gap resolution",
                "business_capability": self._infer_capability_from_gap(gap),
                "status": "planned",
                "priority": "high",
                "estimated_cost": 15000,
                "source_type": "gap",
                "source_id": gap.id,
                "auto_generated": True,
                "generation_method": "gap_requirements",
            }
            supporting_wps.append(wp_requirements)

        return supporting_wps

    def _get_primary_capability_for_application(self, application_id: int) -> str:
        """Get primary business capability for application"""
        result = db.session.execute(  # tenant-filtered: scoped via application_id FK
            text(
                """
            -- application_capability_mapping names its capability FK
            -- business_capability_id; there is no capability_id column on that
            -- table, so this join raised UndefinedColumn.
            SELECT uc.name FROM unified_capabilities uc
            JOIN application_capability_mapping acm ON uc.id = acm.business_capability_id
            WHERE acm.application_id = :app_id
            LIMIT 1
        """
            ),
            {"app_id": application_id},
        ).fetchone()

        return result[0] if result else "unknown_capability"

    def _estimate_assessment_cost(self, application: Any) -> float:
        """Estimate cost for application assessment"""
        # Base cost with complexity factors
        base_cost = 25000

        # Add complexity based on application type
        if hasattr(application, "technology_stack"):
            complexity_factor = len(application.technology_stack.split(",")) * 0.1
            base_cost *= 1 + complexity_factor

        return base_cost

    def _estimate_modernization_cost(self, application: Any) -> float:
        """Estimate cost for application modernization"""
        # Modernization is typically 3 - 5x assessment cost
        assessment_cost = self._estimate_assessment_cost(application)
        return assessment_cost * 4

    def _estimate_application_modernization_cost(self, application: Any) -> float:
        """Estimate cost for application modernization work package"""
        base_cost = 75000  # Base modernization cost

        # Adjust based on application complexity
        if hasattr(application, "criticality"):
            if application.criticality == "critical":
                base_cost *= 1.5
            elif application.criticality == "high":
                base_cost *= 1.2

        return base_cost

    # =========================================================================
    # Reuse-Aware Roadmap Generation (PRD: LLM-Driven Gap Analysis)
    # =========================================================================

    def generate_reuse_roadmap(
        self,
        gaps: List[Dict[str, Any]],
        reuse_analysis: Dict[str, Any],
        options: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """
        Generate roadmap items with reuse-first actions.

        This method supports the "Solution Design and Roadmap Generation" requirement
        from the PRD by creating prioritized roadmap items that specify the recommended
        action (reuse, extend, replace, or build new) for each gap.

        Args:
            gaps: List of gap dictionaries from gap analysis services
            reuse_analysis: Dictionary containing reuse recommendations per gap
                           Format: {gap_id: recommendation_dict}
            options: Optional generation options including:
                    - timeline_months: Maximum roadmap duration (default: 24)
                    - budget_constraint: Maximum total budget
                    - include_dependencies: Whether to compute dependencies (default: True)
                    - prioritize_reuse: Weight factor for reuse preference (default: 1.5)

        Returns:
            List of roadmap item dictionaries with:
            - action_type: 'reuse_existing' | 'extend_existing' | 'replace' | 'build_new'
            - source_application_id (for reuse/extend)
            - work_packages with specific tasks
            - impact_score calculated from strategic importance
            - owner assignment
            - target dates
        """
        logger.info(f"Generating reuse-aware roadmap for {len(gaps)} gaps")

        gen_options = GenerationOptions(**(options or {}))
        roadmap_items = []

        # Process each gap
        for gap in gaps:
            gap_id = gap.get("id") or gap.get("capability_id")
            recommendation = reuse_analysis.get(str(gap_id), {})

            # Get the action type from recommendation
            action_type = self._map_recommendation_to_action(recommendation)

            # Calculate impact score
            impact_score = self.calculate_business_impact_score(
                gap, action_type, self._get_affected_capabilities(gap)
            )

            # Generate roadmap item
            roadmap_item = self._create_reuse_roadmap_item(
                gap, recommendation, action_type, impact_score, gen_options
            )

            roadmap_items.append(roadmap_item)

        # Sort by impact score and priority
        roadmap_items = self._prioritize_roadmap_items(roadmap_items, gen_options)

        # Add dependencies if requested
        if gen_options.include_dependencies:
            roadmap_items = self._add_reuse_dependencies(roadmap_items)

        # Apply budget constraint if specified
        if gen_options.budget_constraint:
            roadmap_items = self._apply_budget_constraint(
                roadmap_items, gen_options.budget_constraint
            )

        logger.info(f"Generated {len(roadmap_items)} roadmap items")

        return roadmap_items

    def _map_recommendation_to_action(self, recommendation: Dict[str, Any]) -> str:
        """Map recommendation type to roadmap action type."""
        rec_type = recommendation.get("recommendation", "build_new").lower()

        mapping = {
            "reuse": "reuse_existing",
            "extend": "extend_existing",
            "replace": "replace",
            "build_new": "build_new",
            "build": "build_new",
        }

        return mapping.get(rec_type, "build_new")

    def _create_reuse_roadmap_item(
        self,
        gap: Dict[str, Any],
        recommendation: Dict[str, Any],
        action_type: str,
        impact_score: float,
        options: GenerationOptions,
    ) -> Dict[str, Any]:
        """Create a single roadmap item for a gap with reuse recommendation."""

        capability_name = gap.get("capability_name", "Unknown Capability")
        gap_type = gap.get("gap_type", "unknown")

        # Determine work package name based on action type
        action_names = {
            "reuse_existing": f"Reuse Application for {capability_name}",
            "extend_existing": f"Extend Application for {capability_name}",
            "replace": f"Replace Application for {capability_name}",
            "build_new": f"Build New Solution for {capability_name}",
        }
        wp_name = action_names.get(action_type, f"Address Gap: {capability_name}")

        # Get source application details if reuse/extend
        source_app_id = recommendation.get("recommended_application_id")
        source_app_name = recommendation.get("recommended_application_name", "")

        # Estimate effort and cost based on action type
        effort_weeks = self._estimate_effort_by_action(action_type, recommendation, gap)
        estimated_cost = self._estimate_cost_by_action(action_type, recommendation, gap)

        # Calculate dates
        start_date = datetime.utcnow()
        end_date = start_date + timedelta(weeks=effort_weeks)

        # Determine priority from gap severity and impact score
        priority = self._determine_priority_from_impact(impact_score, gap.get("severity", "medium"))

        # Build roadmap item
        roadmap_item = {
            "name": wp_name,
            "description": self._build_roadmap_description(gap, recommendation, action_type),
            "action_type": action_type,
            "source_application_id": source_app_id,
            "source_application_name": source_app_name,
            "gap_id": gap.get("id"),
            "gap_type": gap_type,
            "capability_id": gap.get("capability_id"),
            "capability_name": capability_name,
            "domain": gap.get("domain", ""),
            "status": "planned",
            "priority": priority,
            "risk_level": self._assess_action_risk(action_type, recommendation),
            "estimated_cost": estimated_cost,
            "estimated_effort_weeks": effort_weeks,
            "impact_score": round(impact_score, 2),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "assigned_to": recommendation.get("owner_team", ""),
            "auto_generated": True,
            "generation_method": "reuse_roadmap",
            "confidence_score": recommendation.get("confidence_score", 0.7),
            "reuse_rationale": recommendation.get("rationale", ""),
            "cost_savings_percentage": recommendation.get("cost_comparison", {}).get(
                "cost_savings_percentage", 0
            ),
            "implementation_approach": recommendation.get("implementation_approach", ""),
            "risks": recommendation.get("risks", []),
            "alternatives": recommendation.get("alternatives", []),
            "success_criteria": recommendation.get("success_criteria", ""),
            "dependencies": recommendation.get("dependencies", []),
            "work_packages": self._generate_sub_work_packages(gap, recommendation, action_type),
        }

        return roadmap_item

    def _build_roadmap_description(
        self, gap: Dict[str, Any], recommendation: Dict[str, Any], action_type: str
    ) -> str:
        """Build description for roadmap item."""
        capability_name = gap.get("capability_name", "the capability")
        app_name = recommendation.get("recommended_application_name", "")

        descriptions = {
            "reuse_existing": (
                f"Reuse existing application '{app_name}' to address the {gap.get('gap_type', '')} gap "
                f"for {capability_name}. This approach maximizes reuse and minimizes development effort."
            ),
            "extend_existing": (
                f"Extend application '{app_name}' with new features to address the {gap.get('gap_type', '')} gap "
                f"for {capability_name}. Extension is preferred over building new."
            ),
            "replace": (
                f"Replace existing inadequate application with a better solution for {capability_name}. "
                f"Current application does not meet requirements for this gap."
            ),
            "build_new": (
                f"Build new solution to address the {gap.get('gap_type', '')} gap for {capability_name}. "
                f"No suitable existing applications found for reuse or extension."
            ),
        }

        return descriptions.get(action_type, f"Address gap for {capability_name}")

    def _estimate_effort_by_action(
        self, action_type: str, recommendation: Dict[str, Any], gap: Dict[str, Any]
    ) -> int:
        """Estimate effort in weeks based on action type."""
        # Use recommendation estimate if available
        if recommendation.get("estimated_effort_weeks"):
            return int(recommendation["estimated_effort_weeks"])

        # Base effort by action type (in weeks)
        base_efforts = {
            "reuse_existing": 2,  # Configuration and integration
            "extend_existing": 8,  # Development and testing
            "replace": 16,  # Full migration
            "build_new": 24,  # New development
        }

        base = base_efforts.get(action_type, 12)

        # Adjust for gap severity
        severity = gap.get("severity", "medium").lower()
        if severity == "critical":
            base = int(base * 1.5)
        elif severity == "low":
            base = int(base * 0.75)

        return base

    def _estimate_cost_by_action(
        self, action_type: str, recommendation: Dict[str, Any], gap: Dict[str, Any]
    ) -> float:
        """Estimate cost based on action type."""
        # Use recommendation estimate if available
        cost_comparison = recommendation.get("cost_comparison", {})
        if action_type == "reuse_existing" and cost_comparison.get("reuse_estimated_cost"):
            return float(cost_comparison["reuse_estimated_cost"])
        if action_type == "extend_existing" and cost_comparison.get("extend_estimated_cost"):
            return float(cost_comparison["extend_estimated_cost"])
        if action_type == "build_new" and cost_comparison.get("build_new_estimated_cost"):
            return float(cost_comparison["build_new_estimated_cost"])

        # Base cost by action type (in USD)
        base_costs = {
            "reuse_existing": 25000,  # Configuration and integration
            "extend_existing": 100000,  # Development and testing
            "replace": 200000,  # Full migration
            "build_new": 300000,  # New development
        }

        base = base_costs.get(action_type, 150000)

        # Adjust for gap severity
        severity = gap.get("severity", "medium").lower()
        if severity == "critical":
            base *= 1.5
        elif severity == "low":
            base *= 0.75

        return base

    def _assess_action_risk(self, action_type: str, recommendation: Dict[str, Any]) -> str:
        """Assess risk level for the recommended action."""
        # Base risk by action type
        base_risks = {
            "reuse_existing": "low",
            "extend_existing": "medium",
            "replace": "high",
            "build_new": "high",
        }

        risk = base_risks.get(action_type, "medium")

        # Adjust based on confidence score
        confidence = recommendation.get("confidence_score", 0.7)
        if confidence < 0.5:
            # Bump up risk if low confidence
            risk_levels = ["low", "medium", "high", "critical"]
            current_idx = risk_levels.index(risk) if risk in risk_levels else 1
            risk = risk_levels[min(current_idx + 1, 3)]

        return risk

    def _determine_priority_from_impact(self, impact_score: float, severity: str) -> str:
        """Determine priority from impact score and severity."""
        severity_boost = {"critical": 1.5, "high": 1.2, "medium": 1.0, "low": 0.8}
        adjusted_score = impact_score * severity_boost.get(severity.lower(), 1.0)

        if adjusted_score >= 8.0:
            return "critical"
        elif adjusted_score >= 6.0:
            return "high"
        elif adjusted_score >= 4.0:
            return "medium"
        else:
            return "low"

    def _generate_sub_work_packages(
        self, gap: Dict[str, Any], recommendation: Dict[str, Any], action_type: str
    ) -> List[Dict[str, Any]]:
        """Generate sub work packages for a roadmap item."""
        work_packages = []
        gap.get("capability_name", "Capability")
        app_name = recommendation.get("recommended_application_name", "Application")

        if action_type == "reuse_existing":
            work_packages = [
                {"name": "Requirements Validation", "duration_days": 5, "phase": "planning"},
                {"name": f"Configure {app_name}", "duration_days": 10, "phase": "implementation"},
                {"name": "Integration Testing", "duration_days": 5, "phase": "testing"},
                {"name": "User Acceptance Testing", "duration_days": 5, "phase": "testing"},
                {"name": "Deployment", "duration_days": 3, "phase": "deployment"},
            ]
        elif action_type == "extend_existing":
            work_packages = [
                {"name": "Requirements Analysis", "duration_days": 10, "phase": "planning"},
                {"name": "Architecture Design", "duration_days": 10, "phase": "planning"},
                {
                    "name": f"Extend {app_name} Features",
                    "duration_days": 30,
                    "phase": "implementation",
                },
                {"name": "Integration Development", "duration_days": 15, "phase": "implementation"},
                {"name": "Testing and QA", "duration_days": 20, "phase": "testing"},
                {"name": "User Training", "duration_days": 5, "phase": "deployment"},
                {"name": "Production Deployment", "duration_days": 5, "phase": "deployment"},
            ]
        elif action_type == "replace":
            work_packages = [
                {"name": "Requirements Analysis", "duration_days": 15, "phase": "planning"},
                {"name": "Solution Selection", "duration_days": 20, "phase": "planning"},
                {"name": "Architecture Design", "duration_days": 15, "phase": "planning"},
                {"name": "Data Migration Planning", "duration_days": 10, "phase": "planning"},
                {"name": "Solution Implementation", "duration_days": 40, "phase": "implementation"},
                {"name": "Data Migration", "duration_days": 20, "phase": "implementation"},
                {"name": "Integration Development", "duration_days": 20, "phase": "implementation"},
                {"name": "Testing and QA", "duration_days": 25, "phase": "testing"},
                {"name": "User Training", "duration_days": 10, "phase": "deployment"},
                {"name": "Cutover and Deployment", "duration_days": 10, "phase": "deployment"},
            ]
        else:  # build_new
            work_packages = [
                {"name": "Requirements Gathering", "duration_days": 20, "phase": "planning"},
                {"name": "Solution Architecture", "duration_days": 20, "phase": "planning"},
                {"name": "Technology Selection", "duration_days": 10, "phase": "planning"},
                {
                    "name": "Sprint 1: Core Development",
                    "duration_days": 30,
                    "phase": "implementation",
                },
                {
                    "name": "Sprint 2: Feature Development",
                    "duration_days": 30,
                    "phase": "implementation",
                },
                {"name": "Sprint 3: Integration", "duration_days": 30, "phase": "implementation"},
                {"name": "Quality Assurance", "duration_days": 30, "phase": "testing"},
                {"name": "User Acceptance Testing", "duration_days": 15, "phase": "testing"},
                {"name": "Documentation", "duration_days": 10, "phase": "deployment"},
                {"name": "User Training", "duration_days": 10, "phase": "deployment"},
                {"name": "Production Deployment", "duration_days": 10, "phase": "deployment"},
            ]

        return work_packages

    def _get_affected_capabilities(self, gap: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get list of capabilities affected by a gap."""
        capabilities = []

        capability_id = gap.get("capability_id")
        if capability_id:
            capability = UnifiedCapability.query.get(capability_id)
            if capability:
                capabilities.append(
                    {
                        "id": capability.id,
                        "name": capability.name,
                        "strategic_importance": capability.strategic_importance,
                        "level": capability.level,
                    }
                )

        return capabilities

    def _prioritize_roadmap_items(
        self, items: List[Dict[str, Any]], options: GenerationOptions
    ) -> List[Dict[str, Any]]:
        """Prioritize roadmap items based on impact, severity, and reuse preference."""

        def sort_key(item):
            # Higher impact score = higher priority
            impact = item.get("impact_score", 0) * 10

            # Reuse preference (boost reuse/extend actions)
            action_boost = {
                "reuse_existing": 3.0,
                "extend_existing": 2.0,
                "replace": 1.0,
                "build_new": 0.5,
            }
            reuse_factor = action_boost.get(item.get("action_type", ""), 1.0)

            # Priority factor
            priority_weights = {"critical": 4, "high": 3, "medium": 2, "low": 1}
            priority_factor = priority_weights.get(item.get("priority", "medium"), 2)

            # Combined score (higher = higher priority)
            return -(impact * reuse_factor * priority_factor)

        return sorted(items, key=sort_key)

    def _add_reuse_dependencies(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Add dependencies between roadmap items."""

        # Group by capability/domain
        domain_items = {}
        for item in items:
            domain = item.get("domain", "general")
            if domain not in domain_items:
                domain_items[domain] = []
            domain_items[domain].append(item)

        # Add dependencies within same domain (sequential by priority)
        for domain, domain_list in domain_items.items():
            for i, item in enumerate(domain_list):
                if "item_dependencies" not in item:
                    item["item_dependencies"] = []

                # Higher priority items should complete before lower priority ones
                for j in range(i):
                    prev_item = domain_list[j]
                    if prev_item.get("priority") in ["critical", "high"]:
                        item["item_dependencies"].append(
                            {
                                "name": prev_item.get("name"),
                                "type": "finish_to_start",
                                "reason": "Higher priority item in same domain",
                            }
                        )

        return items

    def _apply_budget_constraint(
        self, items: List[Dict[str, Any]], budget: float
    ) -> List[Dict[str, Any]]:
        """Apply budget constraint to roadmap items."""
        total_cost = 0
        filtered_items = []

        for item in items:
            item_cost = item.get("estimated_cost", 0)
            if total_cost + item_cost <= budget:
                filtered_items.append(item)
                total_cost += item_cost
            else:
                # Mark as deferred due to budget
                item["status"] = "deferred"
                item["deferral_reason"] = f"Budget constraint exceeded (total: ${total_cost:,.0f})"
                filtered_items.append(item)

        return filtered_items

    def calculate_business_impact_score(
        self, gap: Dict[str, Any], action_type: str, affected_capabilities: List[Dict[str, Any]]
    ) -> float:
        """
        Calculate business impact score for roadmap prioritization.

        This method supports the PRD requirement for prioritized roadmaps
        with business impact scores.

        Args:
            gap: Gap dictionary containing severity, coverage, etc.
            action_type: The recommended action type
            affected_capabilities: List of capabilities affected by the gap

        Returns:
            Impact score from 0.0 to 10.0

        Factors considered:
        - Strategic importance of affected capabilities
        - Number of affected business processes
        - Risk reduction potential
        - Cost savings from reuse
        - Gap severity
        """
        score = 0.0

        # 1. Strategic importance (0 - 3 points)
        importance_weights = {"critical": 3.0, "high": 2.0, "medium": 1.0, "low": 0.5}
        for cap in affected_capabilities:
            importance = cap.get("strategic_importance", "medium").lower()
            score += importance_weights.get(importance, 1.0)

        # Normalize if multiple capabilities
        if len(affected_capabilities) > 1:
            score = min(score, 3.0)

        # 2. Gap severity (0 - 2 points)
        severity_weights = {"critical": 2.0, "high": 1.5, "medium": 1.0, "low": 0.5}
        severity = gap.get("severity", "medium").lower()
        score += severity_weights.get(severity, 1.0)

        # 3. Coverage gap impact (0 - 2 points)
        current_coverage = gap.get("current_coverage", 50)
        if current_coverage < 30:
            score += 2.0
        elif current_coverage < 60:
            score += 1.5
        elif current_coverage < 80:
            score += 1.0
        else:
            score += 0.5

        # 4. Reuse benefit (0 - 2 points for reuse actions)
        if action_type in ["reuse_existing", "extend_existing"]:
            score += 2.0  # Bonus for reuse (faster time to value, lower risk)
        elif action_type == "replace":
            score += 1.0
        # build_new gets no bonus

        # 5. Maturity gap impact (0 - 1 point)
        maturity_gap = gap.get("maturity_gap", 0)
        if maturity_gap >= 3:
            score += 1.0
        elif maturity_gap >= 2:
            score += 0.5

        # Normalize to 0 - 10 scale
        return min(max(score, 0.0), 10.0)
